"""'Connect with Facebook': the owner signs in to Meta and picks an ad account instead of pasting tokens.

Flow: POST /start returns the Meta login URL (its `state` is an encrypted, 10-minute, per-user value) -> Meta sends the
browser to GET /callback with a one-time code -> we swap it for a long-lived token, save it in the user's encrypted
credentials and, when there is only one ad account, select it -> otherwise the page shows a picker (/accounts, /select).

Needs a Meta app (META_APP_ID / META_APP_SECRET) whose "Valid OAuth Redirect URIs" include
{PUBLIC_BASE_URL}/api/oauth/meta/callback. Without them the feature is off and the manual token form stays in use."""
import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import creds, pages
from .auth import current_user
from .config import env
from .platforms import meta as meta_api

log = logging.getLogger("yourads.oauth")
router = APIRouter(prefix="/api/oauth/meta")
public = APIRouter(prefix="/api/oauth/meta")       # called by the browser coming back from Meta: no session header
SCOPES = "ads_management,ads_read,business_management,pages_show_list,pages_read_engagement,pages_manage_ads,instagram_basic"
STATE_TTL = 600


def enabled() -> bool:
    return bool(env("META_APP_ID") and env("META_APP_SECRET"))


def _redirect_uri() -> str:
    return (env("PUBLIC_BASE_URL") or "https://youradds.co").rstrip("/") + "/api/oauth/meta/callback"


def _version() -> str:
    return env("META_GRAPH_VERSION") or meta_api.GRAPH_VERSION


def make_state(user_id: int) -> str:
    return creds._fernet().encrypt(json.dumps({"u": user_id, "p": "meta"}).encode()).decode()


def read_state(state: str) -> int:
    try:
        data = json.loads(creds._fernet().decrypt(state.encode(), ttl=STATE_TTL))
        if data.get("p") != "meta":
            raise ValueError("wrong provider")
        return int(data["u"])
    except (InvalidToken, ValueError, KeyError, TypeError) as e:
        raise ValueError("invalid or expired state") from e


def _graph(path: str, **params) -> dict:
    """GET on the Graph API. Errors carry only Meta's message: the raw httpx error would include the token."""
    r = httpx.get(f"{meta_api.base_url()}/{path}", params=params, timeout=30)
    if r.is_error:
        try:
            msg = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = f"HTTP {r.status_code}"
        raise RuntimeError(f"Meta: {msg}"[:300])
    return r.json()


def exchange_code(code: str) -> tuple[str, str]:
    """One-time code -> short-lived token -> long-lived (about 60 days) token. Returns (token, expires_at ISO or '')."""
    app = {"client_id": env("META_APP_ID"), "client_secret": env("META_APP_SECRET")}
    short = _graph("oauth/access_token", redirect_uri=_redirect_uri(), code=code, **app)["access_token"]
    long = _graph("oauth/access_token", grant_type="fb_exchange_token", fb_exchange_token=short, **app)
    seconds = long.get("expires_in")
    expires = (datetime.now(timezone.utc) + timedelta(seconds=int(seconds))).strftime("%Y-%m-%d") if seconds else ""
    return long["access_token"], expires


def list_assets(token: str) -> dict:
    accounts = _graph("me/adaccounts", access_token=token, limit=100, fields="account_id,name,currency,account_status")
    pages_ = _graph("me/accounts", access_token=token, limit=100, fields="id,name,instagram_business_account")
    return {
        "ad_accounts": [{"id": f"act_{a['account_id']}", "name": a.get("name") or f"act_{a['account_id']}",
                         "currency": a.get("currency", ""), "active": a.get("account_status") == 1}
                        for a in accounts.get("data", [])],
        "pages": [{"id": p["id"], "name": p.get("name", p["id"]),
                   "instagram_account_id": (p.get("instagram_business_account") or {}).get("id", "")}
                  for p in pages_.get("data", [])]}


def identity(token: str) -> dict:
    """Who the Facebook login is and which permissions they granted (stored with the connection)."""
    me = _graph("me", access_token=token, fields="id,name")
    granted = [p["permission"] for p in _graph("me/permissions", access_token=token).get("data", []) if p.get("status") == "granted"]
    return {"meta_user_id": str(me["id"]), "meta_user_name": me.get("name", ""), "scopes": ",".join(sorted(granted))}


def _apply(values: dict, assets: dict, ad_account_id: str = "", page_id: str = "") -> None:
    acct = next((a for a in assets["ad_accounts"] if a["id"] == ad_account_id), None)
    if acct:
        values.update(ad_account_id=acct["id"], ad_account_name=acct["name"], ad_account_currency=acct["currency"])
    page = next((p for p in assets["pages"] if p["id"] == page_id), None)
    if page:
        values.update(page_id=page["id"], instagram_account_id=page["instagram_account_id"])


@router.get("/state")
def state(user: dict = Depends(current_user)):
    v = creds.get(user["id"], "meta")
    days = None
    if v.get("expires_at"):
        days = (datetime.fromisoformat(v["expires_at"]).date() - datetime.now(timezone.utc).date()).days
    return {"enabled": enabled(), "oauth": v.get("oauth") == "1", "partner": v.get("partner") == "1", "has_token": bool(v.get("access_token")),
            "needs_pick": bool(v.get("access_token")) and not v.get("ad_account_id"),
            "ad_account_id": v.get("ad_account_id", ""), "ad_account_name": v.get("ad_account_name", ""),
            "page_id": v.get("page_id", ""), "expires_in_days": days,
            "meta_user_name": v.get("meta_user_name", ""), "scopes": [x for x in v.get("scopes", "").split(",") if x]}


@router.post("/start")
def start(user: dict = Depends(current_user)):
    if not enabled():
        raise HTTPException(400, "Facebook login is not configured on this server")
    dialog = env("META_OAUTH_DIALOG_URL") or f"https://www.facebook.com/{_version()}/dialog/oauth"
    params = {"client_id": env("META_APP_ID"), "redirect_uri": _redirect_uri(), "state": make_state(user["id"]),
              "response_type": "code"}
    if env("META_LOGIN_CONFIG_ID"):       # Facebook Login for Business: the permissions live in the configuration, not in `scope`
        params.update(config_id=env("META_LOGIN_CONFIG_ID"), override_default_response_type="true")
    else:
        params["scope"] = SCOPES
    return {"url": f"{dialog}?" + urlencode(params)}


@public.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    lang = pages.preferred_lang(request)

    def back(flag: str):
        return RedirectResponse(f"/{lang}/?meta={flag}#connections", status_code=303, headers={"Cache-Control": "no-store"})

    if error:                                   # the owner pressed "Cancel" on Facebook
        return back("denied")
    try:
        user_id = read_state(state)
    except ValueError:
        return back("invalid")
    if not code or not enabled():
        return back("failed")
    try:
        token, expires = exchange_code(code)
        assets = list_assets(token)
        values = {"access_token": token, "oauth": "1", "expires_at": expires, **identity(token)}
        pick = len(assets["ad_accounts"]) == 1
        if pick:
            _apply(values, assets, assets["ad_accounts"][0]["id"], assets["pages"][0]["id"] if len(assets["pages"]) == 1 else "")
        try:
            creds._save(user_id, "meta", values)         # a new login replaces the previous Meta connection
        except creds.AlreadyConnected as taken:
            if str(taken) == "user":                     # this Facebook login already belongs to another youradds account
                return back("taken")
            for k in ("ad_account_id", "ad_account_name", "ad_account_currency", "page_id", "instagram_account_id"):
                values.pop(k, None)                      # keep the login; the ad account is chosen (and refused) in the picker
            try:
                creds._save(user_id, "meta", values)
            except creds.AlreadyConnected:
                return back("taken")
            return back("taken")
    except Exception as e:
        log.warning("facebook login failed for user %s: %s", user_id, e)
        return back("failed")
    return back("connected")


@router.get("/accounts")
def accounts(user: dict = Depends(current_user)):
    v = creds.get(user["id"], "meta")
    if v.get("partner") == "1":                 # the system token reaches every client: a client never browses accounts with it
        raise HTTPException(403, "this connection is managed by youradds staff")
    if not v.get("access_token"):
        raise HTTPException(400, "Connect with Facebook first")
    try:
        return {**list_assets(v["access_token"]), "selected": {"ad_account_id": v.get("ad_account_id", ""),
                                                               "page_id": v.get("page_id", "")}}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


class PickIn(BaseModel):
    ad_account_id: str = Field(max_length=40)
    page_id: str = Field("", max_length=40)


@router.post("/select")
def select(body: PickIn, user: dict = Depends(current_user)):
    v = creds.get(user["id"], "meta")
    if v.get("partner") == "1":                 # the system token reaches every client: a client never browses accounts with it
        raise HTTPException(403, "this connection is managed by youradds staff")
    if not v.get("access_token"):
        raise HTTPException(400, "Connect with Facebook first")
    try:
        assets = list_assets(v["access_token"])
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    if body.ad_account_id not in {a["id"] for a in assets["ad_accounts"]}:
        raise HTTPException(400, "That ad account is not available to your Facebook login")
    if body.page_id and body.page_id not in {p["id"] for p in assets["pages"]}:
        raise HTTPException(400, "That page is not available to your Facebook login")
    for k in ("page_id", "instagram_account_id"):
        v.pop(k, None)
    _apply(v, assets, body.ad_account_id, body.page_id)
    try:
        creds._save(user["id"], "meta", v)
    except creds.AlreadyConnected:
        raise HTTPException(409, "That ad account is already connected to another youradds account. "
                                 "If it is yours, disconnect it there first or contact contact@youradds.co.")
    return {"ok": True, "ad_account_id": v["ad_account_id"]}


# ---- Meta's compliance callbacks: the user removed the app in Facebook, or asked Facebook to delete their data ----
def parse_signed_request(signed: str) -> dict:
    """Verify Meta's `signed_request` (HMAC-SHA256 with the app secret) and return its payload."""
    try:
        sig, payload = signed.split(".", 1)
        pad = lambda x: x + "=" * (-len(x) % 4)
        expected = hmac.new(env("META_APP_SECRET").encode(), payload.encode(), hashlib.sha256).digest()
        if not env("META_APP_SECRET") or not hmac.compare_digest(base64.urlsafe_b64decode(pad(sig)), expected):
            raise ValueError("bad signature")
        data = json.loads(base64.urlsafe_b64decode(pad(payload)))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, "invalid signed_request") from e
    if not data.get("user_id"):
        raise HTTPException(400, "invalid signed_request")
    return data


def forget_meta_user(meta_user_id: str) -> int:
    """Delete the saved Meta connection (token, ids, ownership links) of the account linked to this Facebook user."""
    from sqlalchemy import delete, select
    from .db import session_scope
    from .models import ConnectionLink, Credential
    with session_scope() as session:
        owners = list(session.scalars(select(ConnectionLink.user_id).where(
            ConnectionLink.service == "meta", ConnectionLink.kind == "user",
            ConnectionLink.external_id == creds._external_id("meta", "meta_user_id", {"meta_user_id": meta_user_id}))))
        for uid in owners:
            session.execute(delete(Credential).where(Credential.user_id == uid, Credential.service == "meta"))
            session.execute(delete(ConnectionLink).where(ConnectionLink.user_id == uid, ConnectionLink.service == "meta"))
    return len(owners)


async def _signed(request: Request) -> dict:
    """Meta posts a form with one field, signed_request (read by hand: no multipart dependency needed)."""
    form = parse_qs((await request.body()).decode("utf-8", "ignore"))
    return parse_signed_request((form.get("signed_request") or [""])[0])


@public.post("/deauthorize")
async def deauthorize(request: Request):
    forget_meta_user(str((await _signed(request))["user_id"]))
    return {"ok": True}


@public.post("/data-deletion")
async def data_deletion(request: Request):
    user = str((await _signed(request))["user_id"])
    forget_meta_user(user)
    code = hmac.new(env("META_APP_SECRET").encode(), f"del:{user}".encode(), hashlib.sha256).hexdigest()[:20]
    base = (env("PUBLIC_BASE_URL") or "https://youradds.co").rstrip("/")
    return JSONResponse({"url": f"{base}/api/oauth/meta/deletion-status?code={code}", "confirmation_code": code})


@public.get("/deletion-status", response_class=HTMLResponse)
def deletion_status(code: str = ""):
    ok = code.isalnum() and 8 <= len(code) <= 64
    msg = ("Your Meta connection data was deleted from youradds." if ok else "We could not find that deletion request.")
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>youradds</title><p style='font:16px sans-serif;margin:40px'>{msg}</p>")
