"""Clerk session verification and mapping to application-owned accounts."""
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import config
from .db import seed_demo, session_scope
from .models import AuditLog, Campaign, User

router = APIRouter(prefix="/api/auth")
PUBLIC_AUTH_PATHS = ("/sign-in", "/sign-up", "/forgot-password", "/sso-callback", "/account")


def _sdk():
    from clerk_backend_api import Clerk
    return Clerk(bearer_auth=config.CLERK_SECRET_KEY)


def verify_clerk_request(request: Request) -> dict[str, Any]:
    """Verify the actual request with Clerk's SDK; local JWT verification is fail-closed."""
    config.validate_clerk_config()
    authorization = request.headers.get("authorization")
    if authorization and not authorization.startswith("Bearer "):
        raise HTTPException(401, "not authenticated")
    token = authorization.removeprefix("Bearer ") if authorization else None
    is_cookie_read = request.method.upper() in ("GET", "HEAD") and not authorization
    cookie = request.cookies.get("__session") if is_cookie_read else None
    if not token and not cookie:
        raise HTTPException(401, "not authenticated")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    cookies = {"__session": cookie} if cookie else {}
    sdk_request = httpx.Request(request.method, str(request.url), headers=headers, cookies=cookies)
    try:
        from clerk_backend_api.security.types import AuthenticateRequestOptions
        with _sdk() as sdk:
            result = sdk.authenticate_request(sdk_request, AuthenticateRequestOptions(
                jwt_key=config.CLERK_JWT_KEY,
                secret_key=config.CLERK_SECRET_KEY,
                authorized_parties=list(config.CLERK_AUTHORIZED_PARTIES),
                accepts_token=["session_token"],
            ))
    except HTTPException:
        raise
    except Exception as exc:
        # SDK verification diagnostics can include provider details; do not expose them.
        raise HTTPException(503, "authentication provider unavailable") from exc
    claims = result.payload if result and result.is_signed_in else None
    if not claims:
        raise HTTPException(401, "not authenticated")
    if (claims.get("iss", "").rstrip("/") != config.CLERK_ISSUER or
            not isinstance(claims.get("sub"), str) or not claims["sub"] or
            not isinstance(claims.get("sid"), str) or not claims["sid"] or
            claims.get("sts", "active") != "active"):
        raise HTTPException(401, "not authenticated")
    return claims


def _provider_user(clerk_user_id: str):
    try:
        with _sdk() as sdk:
            return sdk.users.get(user_id=clerk_user_id)
    except Exception as exc:
        raise HTTPException(503, "authentication provider unavailable") from exc


def _profile(user: Any) -> tuple[str, str | None]:
    emails = getattr(user, "email_addresses", None) or []
    primary_id = getattr(user, "primary_email_address_id", None)
    primary = next((item for item in emails if getattr(item, "id", None) == primary_id), None)
    if not primary or getattr(getattr(primary, "verification", None), "status", None) != "verified":
        raise HTTPException(401, "a verified primary email is required")
    email = (getattr(primary, "email_address", "") or "").strip().lower()
    if not email:
        raise HTTPException(401, "a verified primary email is required")
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    return email, (f"{first} {last}".strip() or None)


def _resolve_user(clerk_user_id: str) -> dict:
    with session_scope() as session:
        row = session.execute(select(User.id, User.email, User.name, User.webhook_key, User.mode)
                              .where(User.clerk_user_id == clerk_user_id)).mappings().one_or_none()
        if row:
            return dict(row)
    email, name = _profile(_provider_user(clerk_user_id))
    try:
        with session_scope() as session:
            # Serialize first provisioning so demo rows are seeded once, even across workers.
            session.execute(select(func.pg_advisory_xact_lock(714206392)))
            row = session.execute(select(User.id, User.email, User.name, User.webhook_key, User.mode)
                                  .where(User.clerk_user_id == clerk_user_id)).mappings().one_or_none()
            if row:
                return dict(row)
            if not config.ALLOW_SIGNUP:
                raise HTTPException(403, "new account provisioning is disabled")
            if session.scalar(select(User.id).where(User.email == email).limit(1)):
                raise HTTPException(409, "account requires explicit operator linking")
            account = User(email=email, name=name, password_hash=None, clerk_user_id=clerk_user_id,
                           webhook_key=secrets.token_urlsafe(24))
            session.add(account)
            session.flush()
            if session.scalar(select(Campaign.id).where(Campaign.user_id == account.id).limit(1)) is None:
                seed_demo(session, account.id)
            return {"id": account.id, "email": account.email, "name": account.name,
                    "webhook_key": account.webhook_key, "mode": account.mode}
    except IntegrityError as exc:
        # A subject/email uniqueness race is not an authorization path to another user.
        raise HTTPException(409, "account mapping conflict; explicit operator linking is required") from exc


def real_user(request: Request) -> dict:
    """The signed-in person, ignoring any act-as request."""
    user = _resolve_user(verify_clerk_request(request)["sub"])
    user["is_staff"] = user["email"].lower() in config.staff_emails()
    return user


def audit(staff: dict, account_id: int, action: str) -> None:
    with session_scope() as session:
        session.add(AuditLog(staff_email=staff["email"], account_id=account_id, action=action[:300]))


# A client's own sign-in and Meta tokens are never touched by staff acting for them.
ACT_AS_BLOCKED = ("/api/connections", "/api/oauth/", "/api/approvals", "/api/managed")


def current_user(request: Request) -> dict:
    user = real_user(request)
    target = request.headers.get("x-act-as", "").strip()
    if not target:
        return user
    if not user["is_staff"]:
        raise HTTPException(403, "staff only")
    try:
        target_id = int(target)
    except ValueError:
        raise HTTPException(400, "invalid account") from None
    with session_scope() as session:
        row = session.execute(select(User.id, User.email, User.name, User.webhook_key, User.mode, User.staff_activation)
                              .where(User.id == target_id)).mappings().one_or_none()
    if not row or row["mode"] != "managed":
        raise HTTPException(403, "that account is not managed by youradds")
    method, path = request.method.upper(), request.url.path
    if method != "GET" and path.startswith(ACT_AS_BLOCKED):
        raise HTTPException(403, "connections, approvals and permissions are changed by the account owner, not by staff")
    if method != "GET":
        audit(user, target_id, f"{method} {path}")
    acting = dict(row)
    acting["is_staff"] = False
    acting["acting_as"] = {"staff_email": user["email"]}
    return acting


def staff_user(request: Request) -> dict:
    user = real_user(request)
    if not user["is_staff"]:
        raise HTTPException(403, "staff only")
    return user


staff_router = APIRouter(prefix="/api/staff")


@staff_router.get("/accounts")
def staff_accounts(user: dict = Depends(staff_user)):
    with session_scope() as session:
        rows = session.execute(select(User.id, User.email, User.name, User.mode)
                               .where(User.mode == "managed").order_by(User.id)).mappings().all()
    return [dict(r) for r in rows]


@staff_router.get("/lookup")
def staff_lookup(email: str, user: dict = Depends(staff_user)):
    """Find accounts by part of an email (at least 3 characters) so staff can mark one as managed."""
    term = email.strip().lower()
    if len(term) < 3:
        raise HTTPException(400, "type at least 3 characters")
    like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with session_scope() as session:
        rows = session.execute(select(User.id, User.email, User.name, User.mode)
                               .where(func.lower(User.email).like(like, escape="\\")).order_by(User.email).limit(10)).mappings().all()
    return [dict(r) for r in rows]


@staff_router.post("/accounts/{account_id}/mode")
def staff_set_mode(account_id: int, body: dict, user: dict = Depends(staff_user)):
    mode = (body or {}).get("mode")
    if mode not in ("self", "managed"):
        raise HTTPException(400, "mode must be self or managed")
    with session_scope() as session:
        account = session.get(User, account_id)
        if not account:
            raise HTTPException(404, "account not found")
        account.mode = mode
        if mode == "managed":
            account.managed_requested_at = None        # the request is answered
    if mode == "self":                    # ending management also ends the access youradds had to the client's ad account
        from . import creds
        if creds.get(account_id, "meta", raw=True).get("partner") == "1":
            creds._save(account_id, "meta", {})
            audit(user, account_id, "disconnected partner ad account")
    audit(user, account_id, f"set mode {mode}")
    return {"id": account_id, "mode": mode}


@staff_router.post("/accounts/{account_id}/open")
def staff_open(account_id: int, user: dict = Depends(staff_user)):
    with session_scope() as session:
        account = session.get(User, account_id)
        if not account or account.mode != "managed":
            raise HTTPException(403, "that account is not managed by youradds")
        info = {"id": account.id, "email": account.email, "name": account.name}
    audit(user, account_id, "opened the account")
    return info


@staff_router.get("/audit")
def staff_audit(account_id: int, user: dict = Depends(staff_user)):
    with session_scope() as session:
        rows = session.execute(select(AuditLog.at, AuditLog.staff_email, AuditLog.action)
                               .where(AuditLog.account_id == account_id).order_by(AuditLog.id.desc()).limit(200)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/config")
def auth_config():
    try:
        config.validate_clerk_config()
        frontend_api = config.clerk_frontend_api()
    except RuntimeError as exc:
        raise HTTPException(503, "authentication is not configured") from exc
    return {"publishableKey": config.CLERK_PUBLISHABLE_KEY,
            "frontendApi": frontend_api, "authPaths": list(PUBLIC_AUTH_PATHS)}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "mode": user["mode"],
            "is_staff": bool(user.get("is_staff")), "acting_as": user.get("acting_as")}
