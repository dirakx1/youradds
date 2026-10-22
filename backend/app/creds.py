"""Per-user connection credentials (ad platforms, WhatsApp, Higgsfield), encrypted at rest with Fernet.
Secret values are never sent back to the browser - only whether they are set and their last 4 characters."""
import json
import os
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import current_user
from .config import SECRET_KEY_FILE, SHARED_HIGGSFIELD, env
from .db import conn

# service -> ordered fields. secret=True values are masked in API responses. required = needed to be "connected".
SERVICES = {
    "meta": {"title": "Meta (Facebook + Instagram)", "fields": [
        {"name": "access_token", "label": "Access token", "secret": True, "required": True,
         "hint": "System-user or long-lived token with ads_management"},
        {"name": "ad_account_id", "label": "Ad account ID", "required": True, "hint": "act_1234567890"},
        {"name": "page_id", "label": "Facebook Page ID", "hint": "Needed later to publish ads"},
        {"name": "instagram_account_id", "label": "Instagram account ID", "hint": "Needed later to run ads on Instagram"},
    ]},
    "tiktok": {"title": "TikTok Ads", "fields": [
        {"name": "access_token", "label": "Access token", "secret": True, "required": True},
        {"name": "advertiser_id", "label": "Advertiser ID", "required": True},
    ]},
    "google": {"title": "Google Ads", "fields": [
        {"name": "access_token", "label": "OAuth access token", "secret": True, "required": True},
        {"name": "developer_token", "label": "Developer token", "secret": True, "required": True},
        {"name": "customer_id", "label": "Customer ID", "required": True, "hint": "123-456-7890"},
        {"name": "login_customer_id", "label": "Manager (login) customer ID", "hint": "Only if you use an MCC"},
    ]},
    "whatsapp": {"title": "WhatsApp Cloud API", "fields": [
        {"name": "token", "label": "Access token", "secret": True, "required": True},
        {"name": "phone_id", "label": "Phone number ID", "required": True},
        {"name": "app_secret", "label": "App secret", "secret": True, "hint": "Verifies webhook signatures - strongly recommended"},
        {"name": "verify_token", "label": "Webhook verify token", "hint": "Auto-generated; paste it into Meta's webhook settings"},
        {"name": "followup_template", "label": "Approved template name", "hint": "For messages outside the 24h window"},
    ]},
    "higgsfield": {"title": "Higgsfield (AI scenes + video)", "fields": [
        {"name": "api_key", "label": "API key", "secret": True, "required": True, "hint": "ID:SECRET - generations are billed to this account"},
    ]},
}
AD_PLATFORMS = ("meta", "tiktok", "google")


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.getenv("YOURADS_SECRET_KEY", "").encode()
    if not key:
        if SECRET_KEY_FILE.exists():
            key = SECRET_KEY_FILE.read_bytes().strip()
        else:
            SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            fd = os.open(SECRET_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
    return Fernet(key)


def get(user_id: int, service: str) -> dict:
    with conn() as c:
        r = c.execute("SELECT data FROM credentials WHERE user_id=? AND service=?", (user_id, service)).fetchone()
    if not r:
        return {}
    try:
        return json.loads(_fernet().decrypt(r["data"].encode()))
    except (InvalidToken, ValueError):
        return {}     # key changed / corrupted: treat as not connected rather than crash


def _save(user_id: int, service: str, values: dict):
    with conn() as c:
        if not values:
            c.execute("DELETE FROM credentials WHERE user_id=? AND service=?", (user_id, service))
            return
        enc = _fernet().encrypt(json.dumps(values).encode()).decode()
        c.execute("INSERT INTO credentials (user_id,service,data) VALUES (?,?,?) ON CONFLICT(user_id,service)"
                  " DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP", (user_id, service, enc))


def connected(user_id: int, service: str) -> bool:
    v = get(user_id, service)
    return bool(v) and all(v.get(f["name"]) for f in SERVICES[service]["fields"] if f.get("required"))


def higgsfield_key(user_id: int) -> str:
    """The user's own key; the server's key only if the operator explicitly opted in to sharing it."""
    k = get(user_id, "higgsfield").get("api_key", "")
    if not k and SHARED_HIGGSFIELD:
        k = env("HIGGSFIELD_API_KEY")
        if k and ":" not in k and env("HIGGSFIELD_API_SECRET"):
            k = f"{k}:{env('HIGGSFIELD_API_SECRET')}"
    return k if ":" in k else ""


def _mask(v: str) -> str:
    return "•" * 8 + v[-4:] if len(v) > 8 else "•" * len(v)


router = APIRouter(prefix="/api/connections")


def _describe(user: dict, service: str) -> dict:
    spec, vals = SERVICES[service], get(user["id"], service)
    fields = []
    for f in spec["fields"]:
        v = vals.get(f["name"], "")
        fields.append({**f, "set": bool(v), "value": "" if f.get("secret") else v,
                       "masked": _mask(v) if f.get("secret") and v else ""})
    out = {"service": service, "title": spec["title"], "connected": connected(user["id"], service), "fields": fields}
    if service == "whatsapp":
        out["webhook_path"] = f"/api/whatsapp/webhook/{user['webhook_key']}"
    if service == "higgsfield" and not out["connected"] and higgsfield_key(user["id"]):
        out["connected"], out["shared"] = True, True
    return out


@router.get("")
def list_connections(user: dict = Depends(current_user)):
    return [_describe(user, s) for s in SERVICES]


class ConnIn(BaseModel):
    values: dict[str, str]


@router.put("/{service}")
def save(service: str, body: ConnIn, user: dict = Depends(current_user)):
    if service not in SERVICES:
        raise HTTPException(404, "unknown service")
    names = {f["name"]: f for f in SERVICES[service]["fields"]}
    cur = get(user["id"], service)
    for k, v in body.values.items():
        if k not in names:
            raise HTTPException(400, f"unknown field: {k}")
        v = v.strip()
        if len(v) > 2000:
            raise HTTPException(400, f"{k} is too long")
        if names[k].get("secret") and not v:
            continue              # blank secret = keep the stored one
        if v:
            cur[k] = v
        else:
            cur.pop(k, None)
    if service == "higgsfield" and cur.get("api_key") and ":" not in cur["api_key"]:
        raise HTTPException(400, "Higgsfield key must look like ID:SECRET")
    if service == "whatsapp" and cur.get("phone_id") and not cur.get("verify_token"):
        cur["verify_token"] = secrets.token_urlsafe(16)
    _save(user["id"], service, cur)
    return _describe(user, service)


@router.delete("/{service}")
def clear(service: str, user: dict = Depends(current_user)):
    if service not in SERVICES:
        raise HTTPException(404, "unknown service")
    _save(user["id"], service, {})
    return _describe(user, service)
