"""Managed mode: connect a client who shared their ad account and Page with youradds' business (staff only)."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import creds
from .auth import audit, staff_user
from .db import session_scope
from .models import User
from .platforms import make as make_platform

router = APIRouter(prefix="/api/staff/accounts")

# Meta's ad account status: 1 is the only one that can run ads.
STATUS = {1: "active", 2: "disabled", 3: "unsettled", 7: "pending risk review", 8: "pending settlement",
          9: "grace period", 100: "pending closure", 101: "closed", 201: "any active", 202: "any closed"}


class PartnerIn(BaseModel):
    ad_account_id: str = Field(max_length=40)
    page_id: str = Field(max_length=40)


def _digits(value: str, what: str) -> str:
    d = re.sub(r"^act_", "", value.strip(), flags=re.I)
    if not d.isdigit():
        raise HTTPException(400, f"{what} must be a number")
    return d


def inspect(ad_account: str, page: str) -> dict:
    """Ask Meta, with the system token, what the client really shared. problems lists every blocker (empty = ready)."""
    token = creds.system_token()
    if not token:
        return {"ready": False, "problems": ["no_token"], "account": None, "page": None}
    p = make_platform("meta", {"access_token": token, "ad_account_id": f"act_{ad_account}"})
    problems, account, page_info = [], None, None
    try:
        a = p._call("GET", f"act_{ad_account}", fields="name,currency,account_status,funding_source_details")
        status = int(a.get("account_status", 0) or 0)
        account = {"name": a.get("name", ""), "currency": a.get("currency", ""), "status": STATUS.get(status, str(status))}
        if status != 1:
            problems.append("restricted")
        elif not a.get("funding_source_details"):
            problems.append("no_payment")
    except RuntimeError:
        problems.append("not_shared")
    try:
        g = p._call("GET", page, fields="id,name,instagram_business_account{id,username}")
        ig = (g.get("instagram_business_account") or {}).get("id", "")
        page_info = {"id": g.get("id", page), "name": g.get("name", ""), "instagram_id": ig}
    except RuntimeError:
        problems.append("page_not_shared")
    return {"ready": not problems, "problems": problems, "account": account, "page": page_info}


def _managed(account_id: int):
    with session_scope() as session:
        mode = session.scalar(select(User.mode).where(User.id == account_id))
    if mode is None:
        raise HTTPException(404, "account not found")
    if mode != "managed":
        raise HTTPException(403, "that account is not managed by youradds")


@router.get("/{account_id}/partner")
def current(account_id: int, user: dict = Depends(staff_user)):
    _managed(account_id)
    v = creds.get(account_id, "meta", raw=True)
    return {"partner": v.get("partner") == "1", "ad_account_id": v.get("ad_account_id", ""), "page_id": v.get("page_id", ""),
            "other_connection": bool(v) and v.get("partner") != "1", "system_token": bool(creds.system_token())}


@router.post("/{account_id}/partner/verify")
def verify(account_id: int, body: PartnerIn, user: dict = Depends(staff_user)):
    _managed(account_id)
    return inspect(_digits(body.ad_account_id, "The ad account ID"), _digits(body.page_id, "The Page ID"))


@router.post("/{account_id}/partner/connect")
def connect(account_id: int, body: PartnerIn, user: dict = Depends(staff_user)):
    _managed(account_id)
    ad, page = _digits(body.ad_account_id, "The ad account ID"), _digits(body.page_id, "The Page ID")
    if creds.get(account_id, "meta", raw=True).get("partner") != "1" and creds.get(account_id, "meta", raw=True):
        raise HTTPException(409, "the client already has their own Meta connection; they must disconnect it first")
    result = inspect(ad, page)
    if not result["ready"]:
        raise HTTPException(409, "not ready: " + ", ".join(result["problems"]))
    try:
        creds.save_partner(account_id, f"act_{ad}", page, result["page"]["instagram_id"])
    except creds.AlreadyConnected:
        raise HTTPException(409, "That ad account is already connected to another youradds account.")
    audit(user, account_id, f"connected partner ad account act_{ad}")
    return {"ok": True, **result}


@router.post("/{account_id}/partner/disconnect")
def disconnect(account_id: int, user: dict = Depends(staff_user)):
    _managed(account_id)
    if creds.get(account_id, "meta", raw=True).get("partner") != "1":
        raise HTTPException(409, "there is no partner connection to remove")
    creds._save(account_id, "meta", {})
    audit(user, account_id, "disconnected partner ad account")
    return {"ok": True}
