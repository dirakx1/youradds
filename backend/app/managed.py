"""Managed mode, client side: ask youradds to manage your ads and see what to share with our business."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update

from . import config, creds, notify
from .auth import audit, current_user, staff_user
from .db import session_scope
from .models import Approval, User

router = APIRouter(prefix="/api/managed")
staff_router = APIRouter(prefix="/api/staff")


def _state(user: dict) -> dict:
    with session_scope() as session:
        requested = session.scalar(select(User.managed_requested_at).where(User.id == user["id"]))
    return {"mode": user["mode"], "requested_at": requested,
            "partner": creds.get(user["id"], "meta", raw=True).get("partner") == "1",
            "business_id": config.env("YOURADS_BUSINESS_ID").strip(),
            "business_name": config.env("YOURADS_BUSINESS_NAME").strip() or "youradds"}


@router.get("/state")
def state(user: dict = Depends(current_user)):
    return _state(user)


@router.post("/request")
def request_managed(user: dict = Depends(current_user)):
    if user["mode"] == "managed":
        raise HTTPException(409, "youradds already manages this account")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session_scope() as session:
        fresh = session.execute(update(User).where(User.id == user["id"], User.managed_requested_at.is_(None))
                                .values(managed_requested_at=now)).rowcount
    if fresh:
        notify.staff_new_request(user)
    return _state(user)


@router.delete("/request")
def cancel_request(user: dict = Depends(current_user)):
    with session_scope() as session:
        session.execute(update(User).where(User.id == user["id"]).values(managed_requested_at=None))
    return _state(user)


@staff_router.get("/requests")
def requests(user: dict = Depends(staff_user)):
    with session_scope() as session:
        rows = session.execute(select(User.id, User.email, User.name, User.managed_requested_at)
                               .where(User.managed_requested_at.is_not(None), User.mode == "self")
                               .order_by(User.managed_requested_at)).mappings().all()
    return [{"id": r["id"], "email": r["email"], "name": r["name"], "requested_at": r["managed_requested_at"]} for r in rows]


def end_management(account_id: int) -> bool:
    """Return an account to self-managed: youradds loses its access, open requests are closed, staff permissions reset.
    Campaigns already created or running are left exactly as they are. Returns whether partner access was removed."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session_scope() as session:
        session.execute(update(User).where(User.id == account_id)
                        .values(mode="self", staff_activation=False, managed_requested_at=None))
        session.execute(update(Approval).where(Approval.account_id == account_id, Approval.status == "pending")
                        .values(status="declined", decided_at=now))
    partner = creds.get(account_id, "meta", raw=True).get("partner") == "1"
    if partner:
        creds._save(account_id, "meta", {})
    return partner


@router.post("/end")
def end(user: dict = Depends(current_user)):
    """The client ends management themselves; they still remove youradds in Meta Business settings."""
    if user["mode"] != "managed":
        raise HTTPException(409, "youradds does not manage this account")
    removed = end_management(user["id"])
    audit({"email": f"{user['email']} (client)"}, user["id"], "client ended management" + (" and removed partner access" if removed else ""))
    notify.client_ended(user["id"], "client")
    return {**_state({**user, "mode": "self"}), "partner_removed": removed}
