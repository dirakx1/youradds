"""Live connection checks: ask each provider whether the saved credentials actually work.
States: ok | invalid (rejected credentials) | no_balance | error (provider unreachable / unexpected) ."""
import hashlib
import json
import threading
import time

import httpx

from . import imagegen
from .platforms import google as google_api, meta as meta_api, tiktok as tiktok_api

TTL = 300           # seconds a result is reused; the UI's "Re-check" button bypasses it
TIMEOUT = 12
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def _res(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail}


# Meta error codes: token problems and missing permissions mean the credentials are bad; throttling means nothing about them.
META_RATE_LIMIT = {4, 17, 32, 341, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80014}
TIKTOK_BAD_CREDENTIALS = {40001, 40104, 40105}      # no permission / token incorrect or revoked
TIKTOK_RATE_LIMIT = {40100}


def _graph(path: str, token: str, what: str) -> dict:
    r = httpx.get(f"{meta_api.base_url()}/{path}", params={"access_token": token, "fields": "id"}, timeout=TIMEOUT)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code, msg = err.get("code"), str(err.get("message", ""))[:160]
        if code in META_RATE_LIMIT:
            return _res("error", f"{what} is rate limiting requests; try again in a few minutes")
        if r.status_code in (400, 401, 403) and (code in (100, 102, 10, 190) or (isinstance(code, int) and 200 <= code < 300)):
            return _res("invalid", msg or f"{what} rejected the token")
        if r.status_code in (401, 403):
            return _res("invalid", msg or f"{what} rejected the token")
        return _res("error", msg or f"{what} error {r.status_code}")
    return _res("ok")


def check_meta(v: dict) -> dict:
    acct = v["ad_account_id"].strip()
    return _graph(acct if acct.startswith("act_") else f"act_{acct}", v["access_token"], "Meta")


def check_whatsapp(v: dict) -> dict:
    return _graph(v["phone_id"].strip(), v["token"], "WhatsApp")


def check_tiktok(v: dict) -> dict:
    r = httpx.get(f"{tiktok_api.base_url()}/advertiser/info/", timeout=TIMEOUT,
                  headers={"Access-Token": v["access_token"]},
                  params={"advertiser_ids": json.dumps([v["advertiser_id"]])})
    if r.status_code >= 500:
        return _res("error", f"TikTok error {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        return _res("error", "TikTok returned an unreadable response")
    code = body.get("code")
    if code == 0:
        return _res("ok")
    msg = str(body.get("message", ""))[:160]
    if code in TIKTOK_RATE_LIMIT:
        return _res("error", "TikTok is rate limiting requests; try again in a few minutes")
    return _res("invalid" if code in TIKTOK_BAD_CREDENTIALS else "error", msg)


def check_google(v: dict) -> dict:
    headers = {"Authorization": f"Bearer {v['access_token']}", "developer-token": v["developer_token"]}
    if v.get("login_customer_id"):
        headers["login-customer-id"] = v["login_customer_id"].replace("-", "")
    r = httpx.get(f"{google_api.base_url()}/customers:listAccessibleCustomers",
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
