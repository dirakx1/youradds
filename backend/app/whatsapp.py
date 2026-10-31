import hashlib
import hmac
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from . import creds, sales
from .auth import current_user
from .db import session_scope
from .models import Campaign, Lead, Message, Metric, User

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])
hook = APIRouter(prefix="/api")      # public: called by Meta, authenticated by per-user key + signature
GRAPH = "https://graph.facebook.com/v21.0"
WINDOW = timedelta(hours=24)
FOLLOWUP_AFTER = timedelta(hours=4)
MAX_FOLLOWUPS = 2


def live(user_id: int) -> bool:
    return creds.connected(user_id, "whatsapp")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace(" ", "T")).replace(tzinfo=timezone.utc) if "+" not in ts else datetime.fromisoformat(ts)


def _post(cfg: dict, payload: dict) -> str:
    r = httpx.post(f"{GRAPH}/{cfg['phone_id']}/messages", timeout=30,
                   headers={"Authorization": f"Bearer {cfg['token']}"},
                   json={"messaging_product": "whatsapp", **payload})
    r.raise_for_status()
    return r.json()["messages"][0]["id"]


def _store_out(lead_id: int, sender: str, body: str, status: str, wa_id: Optional[str] = None):
    with session_scope() as session:
        session.add(Message(lead_id=lead_id, direction="out", sender=sender, body=body, wa_id=wa_id, status=status))


def send(lead: dict, body: str, sender: str, template: Optional[str] = None) -> Optional[str]:
    """Send (or in demo mode just record) an outbound message. Returns an error string or None.
    Free-form text is only allowed within 24h of the lead's last message; otherwise a template is required."""
    if lead["opted_out"]:
        return "lead opted out"
    last = _parse(lead["last_inbound_at"])
    in_window = bool(last and _now() - last < WINDOW)
    if not in_window and not template:
        return "outside the 24h window: an approved template is required"
    cfg = creds.get(lead["user_id"], "whatsapp")
    if not live(lead["user_id"]):
        _store_out(lead["id"], sender, body, "simulated")
        return None
    try:
        payload = ({"to": lead["phone"], "type": "template",
                    "template": {"name": template, "language": {"code": "en"}}} if not in_window else
                   {"to": lead["phone"], "type": "text", "text": {"body": body}})
        _store_out(lead["id"], sender, body, "sent", _post(cfg, payload))
        return None
    except Exception as e:
        _store_out(lead["id"], sender, body, f"failed: {e}")
        return str(e)


def _lead(session, lead_id: int, user_id: int) -> dict:
    r = session.execute(select(*Lead.__table__.columns).where(Lead.id == lead_id, Lead.user_id == user_id)).mappings().one_or_none()
    if not r:
        raise HTTPException(404, "lead not found")
    return dict(r)


def handle_inbound(user_id: int, phone: str, text: str, name: str = "", wa_id: Optional[str] = None,
                   referral: Optional[str] = None, campaign_id: Optional[int] = None) -> int:
    """Store an inbound message, then let the AI agent answer unless a human owns the chat."""
    with session_scope() as session:
        if wa_id and session.scalar(select(Message.id).where(Message.wa_id == wa_id)) is not None:
            return -1
        if referral and not campaign_id:
            campaign_id = session.scalar(select(Campaign.id).where(Campaign.external_id == referral,
                                                                    Campaign.user_id == user_id).limit(1))
        if campaign_id and session.scalar(select(Campaign.id).where(Campaign.id == campaign_id,
                                                                    Campaign.user_id == user_id)) is None:
            campaign_id = None
        lead_insert = insert(Lead).values(user_id=user_id, phone=phone, name=name or None,
                                           campaign_id=campaign_id, referral=referral)
        session.execute(lead_insert.on_conflict_do_nothing(index_elements=[Lead.user_id, Lead.phone]))
        lead_id = session.scalar(select(Lead.id).where(Lead.phone == phone, Lead.user_id == user_id))
        message_insert = insert(Message).values(lead_id=lead_id, direction="in", sender="lead", body=text, wa_id=wa_id)
        if wa_id:
            message_id = session.scalar(message_insert.on_conflict_do_nothing(index_elements=[Message.wa_id])
                                        .returning(Message.id))
            if message_id is None:
                return -1
        else:
            session.execute(message_insert)
        now = _now().strftime("%Y-%m-%d %H:%M:%S")
        session.execute(update(Lead).where(Lead.id == lead_id).values(
            last_inbound_at=now, followups=0, name=func.coalesce(func.nullif(name, ""), Lead.name)))
        lead = _lead(session, lead_id, user_id)
        history = [dict(r) for r in session.execute(select(Message.sender, Message.body)
                                                    .where(Message.lead_id == lead_id).order_by(Message.id)).mappings()]
    if lead["handoff"] or lead["opted_out"] or lead["stage"] in ("won",):
        return lead["id"]
    d = sales.decide(user_id, history, text)
    if d.get("opt_out"):
        with session_scope() as session:
            session.execute(update(Lead).where(Lead.id == lead["id"], Lead.user_id == user_id)
                            .values(opted_out=1, stage="lost"))
        return lead["id"]
    if d["reply"]:
        send(lead, d["reply"], "ai")
    with session_scope() as session:
        stage = d["stage"] if lead["stage"] != "qualified" or d["stage"] != "engaged" else "qualified"
        session.execute(update(Lead).where(Lead.id == lead["id"], Lead.user_id == user_id)
                        .values(stage=stage, handoff=int(d["handoff"])))
    return lead["id"]


# ---- webhook (Meta calls these; one URL per user: /api/whatsapp/webhook/<webhook_key>) ----
def _hook_user(key: str) -> int:
    with session_scope() as session:
        uid = session.scalar(select(User.id).where(User.webhook_key == key))
    if not uid:
        raise HTTPException(404, "unknown webhook")
    return uid


@hook.get("/whatsapp/webhook/{key}")
def verify(key: str, request: Request):
    q = request.query_params
    expected = creds.get(_hook_user(key), "whatsapp").get("verify_token", "")
    if expected and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), expected):
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "verification failed")


@hook.post("/whatsapp/webhook/{key}")
async def webhook(key: str, request: Request, bg: BackgroundTasks):
    uid = await asyncio.to_thread(_hook_user, key)
    cfg = await asyncio.to_thread(creds.get, uid, "whatsapp")
    raw = await request.body()
    secret = cfg.get("app_secret", "")
    if secret:
        sig = request.headers.get("x-hub-signature-256", "")
        good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            raise HTTPException(403, "bad signature")
    data = await request.json()
    for entry in data.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                if m.get("type") != "text":
                    text = f"[{m.get('type')} message]"
                else:
                    text = m["text"]["body"]
                ref = (m.get("referral") or {}).get("source_id")
                bg.add_task(handle_inbound, uid, m["from"], text, names.get(m["from"], ""), m.get("id"), ref)
    return {"ok": True}


# ---- app API ----
class SimIn(BaseModel):
    phone: str = Field(min_length=5, max_length=20)
    text: str = Field(min_length=1, max_length=2000)
    name: str = ""
    campaign_id: Optional[int] = None


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class WonIn(BaseModel):
    value: float = Field(gt=0)


class SettingsIn(BaseModel):
    knowledge: str = Field(max_length=8000)


@router.get("/whatsapp/status")
def status(user: dict = Depends(current_user)):
    cfg = creds.get(user["id"], "whatsapp")
    return {"live": live(user["id"]), "mode": "live" if live(user["id"]) else "demo",
            "followup_template": bool(cfg.get("followup_template")), "signed": bool(cfg.get("app_secret"))}


@router.get("/whatsapp/settings")
def get_settings(user: dict = Depends(current_user)):
    return {"knowledge": sales.get_setting(user["id"], "knowledge", sales.DEFAULT_KNOWLEDGE)}


@router.put("/whatsapp/settings")
def put_settings(body: SettingsIn, user: dict = Depends(current_user)):
    sales.set_setting(user["id"], "knowledge", body.knowledge)
    return {"ok": True}


@router.post("/whatsapp/simulate")
def simulate(body: SimIn, user: dict = Depends(current_user)):
    """Demo mode: pretend a customer messaged you (e.g. after clicking a Click-to-WhatsApp ad)."""
    lid = handle_inbound(user["id"], body.phone.strip(), body.text, body.name, campaign_id=body.campaign_id)
    return {"lead_id": lid}


@router.get("/leads")
def leads(user: dict = Depends(current_user)):
    last_message = (select(Message.body).where(Message.lead_id == Lead.id)
                    .order_by(Message.id.desc()).limit(1).scalar_subquery())
    statement = (select(*Lead.__table__.columns, Campaign.name.label("campaign"), Campaign.platform,
                        last_message.label("last_message"))
                 .outerjoin(Campaign, Campaign.id == Lead.campaign_id)
                 .where(Lead.user_id == user["id"])
                 .order_by(func.coalesce(Lead.last_inbound_at, Lead.created_at).desc()))
    with session_scope() as session:
        return [dict(r) for r in session.execute(statement).mappings()]


@router.get("/leads/{lid}/messages")
def messages(lid: int, user: dict = Depends(current_user)):
    with session_scope() as session:
        _lead(session, lid, user["id"])
        return [dict(r) for r in session.execute(select(*Message.__table__.columns)
                                                 .where(Message.lead_id == lid).order_by(Message.id)).mappings()]


@router.post("/leads/{lid}/send")
def human_send(lid: int, body: SendIn, user: dict = Depends(current_user)):
    """A human replies; this also takes the chat away from the AI."""
    with session_scope() as session:
        lead = _lead(session, lid, user["id"])
        session.execute(update(Lead).where(Lead.id == lid, Lead.user_id == user["id"]).values(handoff=1))
    err = send(lead, body.text, "human", creds.get(user["id"], "whatsapp").get("followup_template") or None)
    return {"ok": not err, "error": err}


@router.post("/leads/{lid}/handoff")
def handoff(lid: int, on: bool, user: dict = Depends(current_user)):
    with session_scope() as session:
        _lead(session, lid, user["id"])
        session.execute(update(Lead).where(Lead.id == lid, Lead.user_id == user["id"]).values(handoff=int(on)))
    return {"ok": True}


@router.post("/leads/{lid}/won")
def won(lid: int, body: WonIn, user: dict = Depends(current_user)):
    """Mark a sale and credit it to the originating campaign, so ROAS reflects real WhatsApp revenue."""
    with session_scope() as session:
        lead = session.execute(select(*Lead.__table__.columns).where(Lead.id == lid, Lead.user_id == user["id"])
                               .with_for_update()).mappings().one_or_none()
        if not lead:
            raise HTTPException(404, "lead not found")
        lead = dict(lead)
        if lead["stage"] == "won":
            raise HTTPException(409, "already marked as won")
        session.execute(update(Lead).where(Lead.id == lid, Lead.user_id == user["id"])
                        .values(stage="won", value=body.value))
        if lead["campaign_id"]:
            stmt = insert(Metric).values(campaign_id=lead["campaign_id"],
                                         day=datetime.now(timezone.utc).date().isoformat(),
                                         impressions=0, clicks=0, spend=0, conversions=1, revenue=body.value)
            session.execute(stmt.on_conflict_do_update(index_elements=[Metric.campaign_id, Metric.day], set_={
                "conversions": Metric.__table__.c.conversions + 1,
                "revenue": Metric.__table__.c.revenue + stmt.excluded.revenue,
            }))
    return {"ok": True, "attributed": bool(lead["campaign_id"])}


def run_followups(user_id: Optional[int] = None) -> int:
    """Nudge quiet leads. Only free-form inside the 24h window; stops after MAX_FOLLOWUPS or any reply."""
    cutoff = (_now() - FOLLOWUP_AFTER).strftime("%Y-%m-%d %H:%M:%S")
    floor = (_now() - WINDOW).strftime("%Y-%m-%d %H:%M:%S")
    statement = select(*Lead.__table__.columns).where(
        Lead.handoff == 0, Lead.opted_out == 0, Lead.stage.in_(("engaged", "qualified")),
        Lead.followups < MAX_FOLLOWUPS, Lead.last_inbound_at <= cutoff, Lead.last_inbound_at > floor)
    if user_id:
        statement = statement.where(Lead.user_id == user_id)
    with session_scope() as session:
        due = [dict(r) for r in session.execute(statement).mappings()]
    n = 0
    for lead in due:
        with session_scope() as session:
            last = session.execute(select(Message.direction, Message.created_at).where(Message.lead_id == lead["id"])
                                   .order_by(Message.id.desc()).limit(1)).mappings().one_or_none()
        # skip if we already nudged recently
        if last and last["direction"] == "out" and last["created_at"] > cutoff:
            continue
        msg = "Hi! Just checking in - still interested? Happy to answer any questions."
        if not send(lead, msg, "ai"):
            with session_scope() as session:
                session.execute(update(Lead).where(Lead.id == lead["id"]).values(followups=Lead.followups + 1))
            n += 1
    return n


@router.post("/whatsapp/followups/run")
def followups_now(user: dict = Depends(current_user)):
    return {"sent": run_followups(user["id"])}


async def followup_loop():
    while True:
        await asyncio.sleep(600)
        try:
            await asyncio.to_thread(run_followups)
        except Exception:
            pass
