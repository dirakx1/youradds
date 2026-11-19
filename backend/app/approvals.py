"""Spend approval: staff may prepare a managed client's campaigns, but the client confirms before money is spent."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update

from .auth import current_user
from .db import session_scope
from .models import Approval, Campaign, User

router = APIRouter(prefix="/api/approvals")


def gate(user: dict, cid: int) -> dict | None:
    """For a staff member starting a campaign: queue a request for the client unless the client allowed staff to activate."""
    acting = user.get("acting_as")
    if not acting or user.get("staff_activation"):
        return None
    with session_scope() as session:
        found = session.scalar(select(Approval.id).where(Approval.campaign_id == cid, Approval.account_id == user["id"],
                                                         Approval.status == "pending"))
        if found is None:
            found = Approval(account_id=user["id"], campaign_id=cid, requested_by=acting["staff_email"])
            session.add(found)
            session.flush()
            found = found.id
    return {"ok": False, "approval": "pending", "approval_id": found, "sync_error": None}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@router.get("")
def pending(user: dict = Depends(current_user)):
    with session_scope() as session:
        rows = session.execute(
            select(Approval.id, Approval.campaign_id, Approval.requested_by, Approval.requested_at,
                   Campaign.name, Campaign.platform, Campaign.daily_budget)
            .join(Campaign, Campaign.id == Approval.campaign_id)
            .where(Approval.account_id == user["id"], Approval.status == "pending").order_by(Approval.id)).mappings().all()
    return [dict(r) for r in rows]


def _decide(user: dict, approval_id: int, to: str) -> dict:
    with session_scope() as session:
        row = session.execute(select(Approval.campaign_id, Approval.status)
                              .where(Approval.id == approval_id, Approval.account_id == user["id"])).one_or_none()
    if not row or row.status != "pending":
        raise HTTPException(404, "request not found")
    result = {"ok": True}
    if to == "approved":
        from .main import set_status               # the same code path as pressing Activate yourself
        result = set_status(row.campaign_id, "active", user)
        if not result.get("ok"):
            return result                          # Meta refused: the request stays open so it can be retried
    with session_scope() as session:
        session.execute(update(Approval).where(Approval.id == approval_id, Approval.status == "pending")
                        .values(status=to, decided_at=_now()))
    return result


@router.post("/{approval_id}/approve")
def approve(approval_id: int, user: dict = Depends(current_user)):
    return _decide(user, approval_id, "approved")


@router.post("/{approval_id}/decline")
def decline(approval_id: int, user: dict = Depends(current_user)):
    return _decide(user, approval_id, "declined")


@router.get("/settings")
def settings(user: dict = Depends(current_user)):
    with session_scope() as session:
        allowed = session.scalar(select(User.staff_activation).where(User.id == user["id"]))
    return {"mode": user["mode"], "staff_activation": bool(allowed)}


@router.put("/settings")
def save_settings(body: dict, user: dict = Depends(current_user)):
    if user["mode"] != "managed":
        raise HTTPException(403, "only accounts managed by youradds have this setting")
    with session_scope() as session:
        session.execute(update(User).where(User.id == user["id"]).values(staff_activation=bool((body or {}).get("staff_activation"))))
    return settings(user)
