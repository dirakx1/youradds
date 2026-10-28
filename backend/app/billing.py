"""Plans, usage limits and the credit ledger.

Each plan has monthly allowances (creatives, video credits, WhatsApp AI messages) and feature flags. Every use is
recorded in `ledger`; the allowance left = plan limit - usage in the current period, plus any purchased/granted
top-up credits (which don't expire). Usage is reserved *before* paid work starts and refunded if the work fails.
Payments (Stripe) are not wired yet: an admin assigns plans and grants credits."""
import math
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import current_user
from .config import env
from .db import conn

RESOURCES = {"creatives": "static creatives", "video_credits": "video credits", "wa_messages": "WhatsApp AI messages"}

# price_* in USD. One video credit = 1 second of 480p video; 720p costs 2.2 credits per second.
PLANS = {
    "free": {"name": "Free", "price_month": 0, "price_year": 0,
             "limits": {"creatives": 10, "video_credits": 0, "wa_messages": 0},
             "features": {"scenes": False, "video": False, "ad_platforms": False, "whatsapp": False}},
    "starter": {"name": "Starter", "price_month": 29, "price_year": 24 * 12,
                "limits": {"creatives": 150, "video_credits": 15, "wa_messages": 500},
                "features": {"scenes": True, "video": True, "ad_platforms": True, "whatsapp": True}},
    "growth": {"name": "Growth", "price_month": 79, "price_year": 66 * 12,
               "limits": {"creatives": 600, "video_credits": 60, "wa_messages": 2000},
               "features": {"scenes": True, "video": True, "ad_platforms": True, "whatsapp": True}},
    "scale": {"name": "Scale", "price_month": 199, "price_year": 166 * 12,
              "limits": {"creatives": 2500, "video_credits": 200, "wa_messages": 5000},
              "features": {"scenes": True, "video": True, "ad_platforms": True, "whatsapp": True}},
    "agency": {"name": "Agency", "price_month": 499, "price_year": 416 * 12,
               "limits": {"creatives": 8000, "video_credits": 500, "wa_messages": 20000},
               "features": {"scenes": True, "video": True, "ad_platforms": True, "whatsapp": True}},
}
FEATURE_LABEL = {"scenes": "AI scene backgrounds", "video": "video generation",
                 "ad_platforms": "connecting ad platforms", "whatsapp": "WhatsApp automation"}
RES_MULT = {"480p": 1.0, "720p": 2.2}
TOPUP_PACKS = [{"credits": 100, "usd": 39}, {"credits": 300, "usd": 99}, {"credits": 1000, "usd": 290}]


class LimitExceeded(Exception):
    """A plan limit or feature gate stopped the action. Mapped to HTTP 402 by the app."""


def video_credits(duration: int, resolution: str) -> int:
    return math.ceil(duration * RES_MULT.get(resolution, 2.2))


# ---------- periods ----------
def _add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def period_for(anchor: date, today: date):
    n = max((today.year - anchor.year) * 12 + today.month - anchor.month, 0)
    if _add_months(anchor, n) > today:
        n -= 1
    return _add_months(anchor, max(n, 0)), _add_months(anchor, max(n, 0) + 1)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def state(user_id: int) -> dict:
    with conn() as c:
        s = c.execute("SELECT plan,billing,anchor FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        u = c.execute("SELECT created_at FROM users WHERE id=?", (user_id,)).fetchone()
    plan = s["plan"] if s and s["plan"] in PLANS else (env("DEFAULT_PLAN") if env("DEFAULT_PLAN") in PLANS else "free")
    anchor = date.fromisoformat((s["anchor"] if s else (u["created_at"] if u else str(_today())))[:10])
    start, end = period_for(anchor, _today())
    return {"plan": plan, "spec": PLANS[plan], "billing": s["billing"] if s else "monthly",
            "start": start.isoformat(), "end": end.isoformat()}


# ---------- ledger ----------
def _balances(c, user_id: int, resource: str, st: dict) -> dict:
    limit = st["spec"]["limits"][resource]
    used = -(c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=? AND resource=? AND pool='plan'"
                       " AND period=?", (user_id, resource, st["start"])).fetchone()[0])
    topup = c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=? AND resource=? AND pool='topup'",
                      (user_id, resource)).fetchone()[0]
    plan_left = max(limit - used, 0)
    return {"limit": limit, "used": max(used, 0), "plan_left": plan_left, "topup": max(topup, 0),
            "remaining": plan_left + max(topup, 0)}


def _too_much(resource: str, st: dict, need: int, bal: dict) -> LimitExceeded:
    return LimitExceeded(f"Not enough {RESOURCES[resource]} on the {st['spec']['name']} plan: need {need}, "
                         f"{bal['remaining']} left this period. Upgrade your plan or add credits.")


def require_feature(user_id: int, feature: str):
    st = state(user_id)
    if not st["spec"]["features"].get(feature):
        raise LimitExceeded(f"{FEATURE_LABEL[feature][0].upper() + FEATURE_LABEL[feature][1:]} isn't included in the {st['spec']['name']} plan. "
                            "Upgrade to use it.")


def check(user_id: int, resource: str, amount: int):
    st = state(user_id)
    with conn() as c:
        bal = _balances(c, user_id, resource, st)
    if amount > bal["remaining"]:
        raise _too_much(resource, st, amount, bal)


def spend(user_id: int, resource: str, amount: int, ref: Optional[str] = None, note: str = ""):
    """Atomically take `amount` from the monthly allowance first, then from top-up credits."""
    if amount <= 0:
        return
    st = state(user_id)
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")      # serialize concurrent spends so two requests can't both pass the check
        bal = _balances(c, user_id, resource, st)
        if amount > bal["remaining"]:
            raise _too_much(resource, st, amount, bal)
        from_plan = min(amount, bal["plan_left"])
        for pool, n in (("plan", from_plan), ("topup", amount - from_plan)):
            if n:
                c.execute("INSERT INTO ledger (user_id,resource,delta,pool,kind,period,ref,note)"
                          " VALUES (?,?,?,?,'usage',?,?,?)", (user_id, resource, -n, pool, st["start"], ref, note))


def refund(user_id: int, ref: str):
    """Give back whatever `ref` consumed (idempotent): to the same pool and period it was taken from."""
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("SELECT resource,pool,period,SUM(delta) net FROM ledger WHERE user_id=? AND ref=?"
                         " AND kind IN ('usage','refund') GROUP BY resource,pool,period", (user_id, ref)).fetchall()
        for r in rows:
            if r["net"] < 0:
                c.execute("INSERT INTO ledger (user_id,resource,delta,pool,kind,period,ref,note)"
                          " VALUES (?,?,?,?,'refund',?,?,'refund')", (user_id, r["resource"], -r["net"], r["pool"],
                                                                     r["period"], ref))


def grant(user_id: int, resource: str, amount: int, note: str = "", kind: str = "grant"):
    with conn() as c:
        c.execute("INSERT INTO ledger (user_id,resource,delta,pool,kind,note) VALUES (?,?,?,'topup',?,?)",
                  (user_id, resource, amount, kind, note))


def set_plan(user_id: int, plan: str, billing: str = "monthly"):
    with conn() as c:
        c.execute("INSERT INTO subscriptions (user_id,plan,billing,anchor) VALUES (?,?,?,?) ON CONFLICT(user_id) DO"
                  " UPDATE SET plan=excluded.plan, billing=excluded.billing, anchor=excluded.anchor,"
                  " updated_at=CURRENT_TIMESTAMP", (user_id, plan, billing, _today().isoformat()))


def summary(user_id: int) -> dict:
    st = state(user_id)
    with conn() as c:
        usage = {r: _balances(c, user_id, r, st) for r in RESOURCES}
        log = [dict(x) for x in c.execute(
            "SELECT created_at,resource,delta,pool,kind,note,ref FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 25",
            (user_id,))]
    return {"plan": st["plan"], "plan_name": st["spec"]["name"], "billing": st["billing"], "period_start": st["start"],
            "period_end": st["end"], "features": st["spec"]["features"], "usage": usage, "ledger": log}


# ---------- API ----------
router = APIRouter(prefix="/api/billing")
admin = APIRouter(prefix="/api/admin")


def is_admin(user: dict) -> bool:
    emails = {e.strip().lower() for e in env("ADMIN_EMAILS").split(",") if e.strip()}
    if emails:
        return user["email"].lower() in emails
    with conn() as c:   # no list configured: the first account is the operator
        return user["id"] == c.execute("SELECT MIN(id) FROM users").fetchone()[0]


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not is_admin(user):
        raise HTTPException(403, "admin only")
    return user


@router.get("")
def my_billing(user: dict = Depends(current_user)):
    return {**summary(user["id"]), "is_admin": is_admin(user), "resources": RESOURCES, "topup_packs": TOPUP_PACKS,
            "plans": {k: {"name": v["name"], "price_month": v["price_month"], "price_year": v["price_year"],
                          "limits": v["limits"], "features": v["features"]} for k, v in PLANS.items()},
            "feature_labels": FEATURE_LABEL}


@admin.get("/users")
def users(_: dict = Depends(require_admin)):
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id,email,name,created_at FROM users ORDER BY id")]
    for r in rows:
        s = state(r["id"])
        r.update(plan=s["plan"], billing=s["billing"])
    return rows


class PlanIn(BaseModel):
    plan: str
    billing: str = "monthly"


@admin.put("/users/{uid}/plan")
def admin_plan(uid: int, body: PlanIn, _: dict = Depends(require_admin)):
    if body.plan not in PLANS or body.billing not in ("monthly", "annual"):
        raise HTTPException(400, "unknown plan or billing period")
    with conn() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(404, "user not found")
    set_plan(uid, body.plan, body.billing)
    return summary(uid)


class GrantIn(BaseModel):
    resource: str
    amount: int = Field(ge=1, le=1_000_000)
    note: str = Field("", max_length=200)


@admin.post("/users/{uid}/grant")
def admin_grant(uid: int, body: GrantIn, _: dict = Depends(require_admin)):
    if body.resource not in RESOURCES:
        raise HTTPException(400, "unknown resource")
    with conn() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(404, "user not found")
    grant(uid, body.resource, body.amount, body.note or "admin grant")
    return summary(uid)
