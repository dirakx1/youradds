"""Live connection checks: ask each provider whether the saved credentials actually work.
States: ok | invalid (rejected credentials) | no_balance | error (provider unreachable / unexpected) ."""
import hashlib
import json
import threading
import time

import httpx

from . import imagegen

TTL = 300           # seconds a result is reused; the UI's "Re-check" button bypasses it
TIMEOUT = 12
GRAPH = "https://graph.facebook.com/v21.0"
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def _res(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail}


def _graph(path: str, token: str, what: str) -> dict:
    r = httpx.get(f"{GRAPH}/{path}", params={"access_token": token, "fields": "id"}, timeout=TIMEOUT)
    if r.status_code in (400, 401, 403):
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        return _res("invalid", msg[:160] or f"{what} rejected the token")
    if r.status_code >= 400:
        return _res("error", f"{what} error {r.status_code}")
    return _res("ok")


def check_meta(v: dict) -> dict:
    acct = v["ad_account_id"].strip()
    return _graph(acct if acct.startswith("act_") else f"act_{acct}", v["access_token"], "Meta")


def check_whatsapp(v: dict) -> dict:
    return _graph(v["phone_id"].strip(), v["token"], "WhatsApp")


def check_tiktok(v: dict) -> dict:
    r = httpx.get("https://business-api.tiktok.com/open_api/v1.3/advertiser/info/", timeout=TIMEOUT,
                  headers={"Access-Token": v["access_token"]},
                  params={"advertiser_ids": json.dumps([v["advertiser_id"]])})
    if r.status_code >= 500:
        return _res("error", f"TikTok error {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        return _res("error", "TikTok returned an unreadable response")
    if body.get("code") != 0:
        return _res("invalid", str(body.get("message", ""))[:160])
    return _res("ok")


def check_google(v: dict) -> dict:
    headers = {"Authorization": f"Bearer {v['access_token']}", "developer-token": v["developer_token"]}
    if v.get("login_customer_id"):
        headers["login-customer-id"] = v["login_customer_id"].replace("-", "")
    r = httpx.get("https://googleads.googleapis.com/v17/customers:listAccessibleCustomers",
                  headers=headers, timeout=TIMEOUT)
    if r.status_code in (400, 401, 403):
        return _res("invalid", "Google rejected the token or developer token")
    if r.status_code >= 400:
        return _res("error", f"Google error {r.status_code}")
    return _res("ok")


def check_higgsfield(v: dict) -> dict:
    """The estimate endpoint authenticates and reports balance problems without generating (or billing) anything."""
    key = v["api_key"]
    if ":" not in key:
        return _res("invalid", "Key must look like ID:SECRET")
    r = httpx.post(f"{imagegen.BASE}/estimate/{imagegen._model()}", json=imagegen._body("status check", "1:1"),
                   headers={"Authorization": imagegen._auth(key)}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        return _res("invalid", "Higgsfield rejected the API key")
    if r.status_code == 402 or (r.status_code == 400 and "balance" in r.text.lower()):
        return _res("no_balance", "Top up your Higgsfield API balance")
    if r.status_code >= 400:
        return _res("error", f"Higgsfield error {r.status_code}")
    return _res("ok")


CHECKS = {"meta": check_meta, "tiktok": check_tiktok, "google": check_google,
          "whatsapp": check_whatsapp, "higgsfield": check_higgsfield}


def check(service: str, values: dict, force: bool = False) -> dict:
    """Cached per credential contents, so editing a key invalidates its result immediately."""
    ck = (service, hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest())
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
    if hit and not force and now - hit[0] < TTL:
        return hit[1]
    try:
        out = CHECKS[service](values)
    except httpx.HTTPError as e:
        out = _res("error", f"Could not reach {service}: {type(e).__name__}")
    except Exception as e:                      # a probe must never break the Connections page
        out = _res("error", f"Check failed: {type(e).__name__}")
    out["checked_at"] = int(now)
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[ck] = (now, out)
    return out
