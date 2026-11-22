"""Managed mode, client side: ask youradds to manage your ads and see what to share with our business."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update

from . import config, creds
from .auth import current_user, staff_user
from .db import session_scope
from .models import User

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
        session.execute(update(User).where(User.id == user["id"], User.managed_requested_at.is_(None)).values(managed_requested_at=now))
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
