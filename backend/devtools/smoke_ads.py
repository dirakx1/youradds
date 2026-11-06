"""Smoke-test the ad-platform connectors end to end, with the same code the app runs.

  python -m devtools.smoke_ads --fake                      # against the local fake (no accounts, no network)
  python -m devtools.smoke_ads --platform meta             # REAL account, read-only: credential probe + list campaigns
  python -m devtools.smoke_ads --platform meta --write     # REAL account: also create one PAUSED campaign + change its budget

Credentials come from the environment or backend/.env (use sandbox / test accounts, never a live budget):
  Meta    META_ACCESS_TOKEN  META_AD_ACCOUNT_ID                         (Meta "test ad account" or a sandbox app)
  TikTok  TIKTOK_ACCESS_TOKEN  TIKTOK_ADVERTISER_ID                     (TikTok Marketing API sandbox advertiser)
  Google  GOOGLE_ADS_ACCESS_TOKEN  GOOGLE_ADS_DEVELOPER_TOKEN  GOOGLE_ADS_CUSTOMER_ID  [GOOGLE_ADS_LOGIN_CUSTOMER_ID]
                                                                        (a Google Ads *test account* + test-level developer token)

Safety: without --write nothing is created. With --write on a real account the script asks for confirmation (or
--yes), creates ONE campaign in PAUSED state named "yourads-smoke-<timestamp>" (no ad set, so it cannot serve or
spend), and never activates it. The connectors have no delete call, so remove that campaign in the platform UI.
Exit status is non-zero if any step fails.
"""
import argparse
import os
import sys
import time

CREDS = {
    "meta": {"access_token": "META_ACCESS_TOKEN", "ad_account_id": "META_AD_ACCOUNT_ID"},
    "tiktok": {"access_token": "TIKTOK_ACCESS_TOKEN", "advertiser_id": "TIKTOK_ADVERTISER_ID"},
    "google": {"access_token": "GOOGLE_ADS_ACCESS_TOKEN", "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
               "customer_id": "GOOGLE_ADS_CUSTOMER_ID", "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID"},
}
OPTIONAL = {"login_customer_id"}
FAKE_CREDS = {"meta": {"access_token": "fake-token", "ad_account_id": "act_1001"},
              "tiktok": {"access_token": "fake-token", "advertiser_id": "7001"},
              "google": {"access_token": "fake-token", "developer_token": "fake-dev", "customer_id": "123-456-7890"}}


def step(results, name, fn):
    t0 = time.time()
    try:
        detail = fn() or ""
        results.append((name, True, str(detail), time.time() - t0))
    except Exception as e:   # report, keep going: one failing step should not hide the others
        results.append((name, False, f"{type(e).__name__}: {str(e)[:300]}", time.time() - t0))
        return False
    return True


def run_platform(platform, creds, write, fake):
    from app import health
    from app.platforms import make, google, meta, tiktok
    target = {"meta": meta, "tiktok": tiktok, "google": google}[platform].base_url()
    print(f"\n== {platform}  target: {target}  ({'LOCAL' if '127.0.0.1' in target or 'localhost' in target else 'REAL'})")
    results, p = [], make(platform, creds)

    def probe():
        v = health.check(platform, creds, force=True)
        if v["state"] != "ok":
            raise RuntimeError(f"{v['state']}: {v.get('detail', '')}")
        return "credentials accepted"
    ok = step(results, "credential probe", probe)
    ok = step(results, "list campaigns", lambda: f"{len(p.list_campaigns())} campaign(s) visible") and ok
    if write and ok:
        name, state = f"yourads-smoke-{int(time.time())}", {}

        def create():
            state["id"] = p.create_campaign({"name": name, "objective": "traffic", "daily_budget": 20})
            return f"created {state['id']} (PAUSED)"
        if step(results, "create paused campaign", create):
            def appears():
                row = next((c for c in p.list_campaigns() if c["external_id"] == state["id"]), None)
                if not row:
                    raise RuntimeError("created campaign is missing from the list")
                if row["status"] != "paused":
                    raise RuntimeError(f"expected paused, got {row['status']}")
                return "found, paused"
            step(results, "campaign appears in list", appears)

            def budget():
                p.set_budget(state["id"], 25)
                row = next(c for c in p.list_campaigns() if c["external_id"] == state["id"])
                if abs(row["daily_budget"] - 25) > 0.01:
                    raise RuntimeError(f"budget reads back as {row['daily_budget']}")
                return "budget 25 reads back"
            step(results, "set budget", budget)
            if fake:   # activating is only safe on the fake
                def toggle():
                    p.set_status(state["id"], "active")
                    assert next(c for c in p.list_campaigns() if c["external_id"] == state["id"])["status"] == "active"
                    p.set_status(state["id"], "paused")
                    assert next(c for c in p.list_campaigns() if c["external_id"] == state["id"])["status"] == "paused"
                    return "activate + pause round trip"
                step(results, "activate / pause", toggle)
            else:
                print(f"   left campaign {state['id']} ('{name}') PAUSED on the account - delete it in the platform UI.")
    for n, passed, detail, dt in results:
        print(f"   {'PASS' if passed else 'FAIL'}  {n:<26} {detail}  ({dt:.1f}s)")
    return all(r[1] for r in results)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--platform", choices=[*CREDS, "all"], default="all")
    ap.add_argument("--fake", action="store_true", help="start the local fake ad APIs and test against them")
    ap.add_argument("--write", action="store_true", help="also create one PAUSED campaign and change its budget")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation before writing to a real account")
    args = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    stop = None
    if args.fake:
        from devtools.fake_ads import env_for, serve_in_thread
        base, stop = serve_in_thread()
        os.environ.update(env_for(base))
        print(f"fake ad APIs running at {base}")
    try:
        platforms = list(CREDS) if args.platform == "all" else [args.platform]
        failures = skipped = 0
        for platform in platforms:
            if args.fake:
                creds = FAKE_CREDS[platform]
            else:
                from app.config import env
                creds = {k: env(v) for k, v in CREDS[platform].items()}
                missing = [v for k, v in CREDS[platform].items() if k not in OPTIONAL and not creds[k]]
                if missing:
                    print(f"\n== {platform}  SKIPPED - set {', '.join(missing)}")
                    skipped += 1
                    continue
                if args.write and not args.yes:
                    who = next(v for k, v in creds.items() if "id" in k)
                    if input(f"\nCreate a PAUSED test campaign on the REAL {platform} account {who}? [y/N] ").strip().lower() != "y":
                        print("   skipped")
                        skipped += 1
                        continue
            failures += not run_platform(platform, creds, args.write, args.fake)
        print(f"\n{'ALL PASSED' if not failures else f'{failures} PLATFORM(S) FAILED'}"
              + (f"  ({skipped} skipped)" if skipped else ""))
        return 1 if failures else 0
    finally:
        if stop:
            stop()


if __name__ == "__main__":
    sys.exit(main())
