import hashlib
import hmac
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import creds, sales
from .auth import current_user
from .db import conn

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
    with conn() as c:
        c.execute("INSERT INTO messages (lead_id,direction,sender,body,wa_id,status) VALUES (?,?,?,?,?,?)",
                  (lead_id, "out", sender, body, wa_id, status))


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


def _lead(c, lead_id: int, user_id: int) -> dict:
    r = c.execute("SELECT * FROM leads WHERE id=? AND user_id=?", (lead_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "lead not found")
    return dict(r)


def handle_inbound(user_id: int, phone: str, text: str, name: str = "", wa_id: Optional[str] = None,
                   referral: Optional[str] = None, campaign_id: Optional[int] = None) -> int:
    """Store an inbound message, then let the AI agent answer unless a human owns the chat."""
    with conn() as c:
        if wa_id and c.execute("SELECT 1 FROM messages WHERE wa_id=?", (wa_id,)).fetchone():
            return -1  # duplicate webhook delivery
        if referral and not campaign_id:
            r = c.execute("SELECT id FROM campaigns WHERE external_id=? AND user_id=?", (referral, user_id)).fetchone()
            campaign_id = r["id"] if r else None
        if campaign_id and not c.execute("SELECT 1 FROM campaigns WHERE id=? AND user_id=?",
                                         (campaign_id, user_id)).fetchone():
            campaign_id = None
        c.execute("INSERT OR IGNORE INTO leads (user_id,phone,name,campaign_id,referral) VALUES (?,?,?,?,?)",
                  (user_id, phone, name, campaign_id, referral))
        lead = dict(c.execute("SELECT * FROM leads WHERE phone=? AND user_id=?", (phone, user_id)).fetchone())
        c.execute("UPDATE leads SET last_inbound_at=CURRENT_TIMESTAMP, followups=0, name=COALESCE(NULLIF(?,''),name)"
                  " WHERE id=?", (name, lead["id"]))
        c.execute("INSERT INTO messages (lead_id,direction,sender,body,wa_id) VALUES (?,?,?,?,?)",
                  (lead["id"], "in", "lead", text, wa_id))
        lead = _lead(c, lead["id"], user_id)
        history = [dict(r) for r in c.execute(
            "SELECT sender, body FROM messages WHERE lead_id=? ORDER BY id", (lead["id"],))]
    if lead["handoff"] or lead["opted_out"] or lead["stage"] in ("won",):
        return lead["id"]
    d = sales.decide(user_id, history, text)
    if d.get("opt_out"):
        with conn() as c:
            c.execute("UPDATE leads SET opted_out=1, stage='lost' WHERE id=?", (lead["id"],))
        return lead["id"]
    if d["reply"]:
        send(lead, d["reply"], "ai")
    with conn() as c:
        stage = d["stage"] if lead["stage"] != "qualified" or d["stage"] != "engaged" else "qualified"
        c.execute("UPDATE leads SET stage=?, handoff=? WHERE id=?", (stage, int(d["handoff"]), lead["id"]))
    return lead["id"]


# ---- webhook (Meta calls these; one URL per user: /api/whatsapp/webhook/<webhook_key>) ----
def _hook_user(key: str) -> int:
    with conn() as c:
        r = c.execute("SELECT id FROM users WHERE webhook_key=?", (key,)).fetchone()
    if not r:
        raise HTTPException(404, "unknown webhook")
    return r["id"]


@hook.get("/whatsapp/webhook/{key}")
def verify(key: str, request: Request):
    q = request.query_params
    expected = creds.get(_hook_user(key), "whatsapp").get("verify_token", "")
    if expected and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), expected):
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "verification failed")


@hook.post("/whatsapp/webhook/{key}")
async def webhook(key: str, request: Request, bg: BackgroundTasks):
    uid = _hook_user(key)
    cfg = creds.get(uid, "whatsapp")
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
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT l.*, c.name campaign, c.platform, (SELECT body FROM messages WHERE lead_id=l.id ORDER BY id DESC LIMIT 1) last_message"
            " FROM leads l LEFT JOIN campaigns c ON c.id=l.campaign_id WHERE l.user_id=?"
            " ORDER BY COALESCE(l.last_inbound_at,l.created_at) DESC", (user["id"],))]


@router.get("/leads/{lid}/messages")
def messages(lid: int, user: dict = Depends(current_user)):
    with conn() as c:
        _lead(c, lid, user["id"])
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY id", (lid,))]


@router.post("/leads/{lid}/send")
def human_send(lid: int, body: SendIn, user: dict = Depends(current_user)):
    """A human replies; this also takes the chat away from the AI."""
    with conn() as c:
        lead = _lead(c, lid, user["id"])
        c.execute("UPDATE leads SET handoff=1 WHERE id=?", (lid,))
    err = send(lead, body.text, "human", creds.get(user["id"], "whatsapp").get("followup_template") or None)
    return {"ok": not err, "error": err}


@router.post("/leads/{lid}/handoff")
def handoff(lid: int, on: bool, user: dict = Depends(current_user)):
    with conn() as c:
        _lead(c, lid, user["id"])
        c.execute("UPDATE leads SET handoff=? WHERE id=?", (int(on), lid))
    return {"ok": True}


@router.post("/leads/{lid}/won")
def won(lid: int, body: WonIn, user: dict = Depends(current_user)):
    """Mark a sale and credit it to the originating campaign, so ROAS reflects real WhatsApp revenue."""
    with conn() as c:
        lead = _lead(c, lid, user["id"])
        if lead["stage"] == "won":
            raise HTTPException(409, "already marked as won")
        c.execute("UPDATE leads SET stage='won', value=? WHERE id=?", (body.value, lid))
        if lead["campaign_id"]:
            c.execute("INSERT INTO metrics (campaign_id,day,impressions,clicks,spend,conversions,revenue)"
                      " VALUES (?,date('now'),0,0,0,1,?) ON CONFLICT(campaign_id,day) DO UPDATE SET"
                      " conversions=conversions+1, revenue=revenue+excluded.revenue", (lead["campaign_id"], body.value))
    return {"ok": True, "attributed": bool(lead["campaign_id"])}


def run_followups(user_id: Optional[int] = None) -> int:
    """Nudge quiet leads. Only free-form inside the 24h window; stops after MAX_FOLLOWUPS or any reply."""
    cutoff = (_now() - FOLLOWUP_AFTER).strftime("%Y-%m-%d %H:%M:%S")
    floor = (_now() - WINDOW).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        due = [dict(r) for r in c.execute(
            "SELECT * FROM leads WHERE handoff=0 AND opted_out=0 AND stage IN ('engaged','qualified')"
            " AND followups<? AND last_inbound_at<=? AND last_inbound_at>?"
            + (" AND user_id=?" if user_id else ""), (MAX_FOLLOWUPS, cutoff, floor) + ((user_id,) if user_id else ()))]
    n = 0
    for lead in due:
        with conn() as c:
            last = c.execute("SELECT direction, created_at FROM messages WHERE lead_id=? ORDER BY id DESC LIMIT 1",
                             (lead["id"],)).fetchone()
        # skip if we already nudged recently
        if last and last["direction"] == "out" and last["created_at"] > cutoff:
            continue
        msg = "Hi! Just checking in - still interested? Happy to answer any questions."
        if not send(lead, msg, "ai"):
            with conn() as c:
                c.execute("UPDATE leads SET followups=followups+1 WHERE id=?", (lead["id"],))
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
