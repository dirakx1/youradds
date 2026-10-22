"""WhatsApp sales agent: replies to leads, qualifies them, escalates to a human."""
import json
import re
from . import ai
from .db import conn

DEFAULT_KNOWLEDGE = ("Business: Demo Store. Product: organic running shoes, $89, free shipping over $50, "
                     "30-day returns. Ship in 2 days. Payment link is sent once the customer confirms the order.")

SYSTEM = """You are a friendly, concise WhatsApp sales assistant. Use ONLY the business info provided; if you
don't know something, say a teammate will follow up and set handoff=true. Never invent prices, stock or policies.
Ask at most one question per message. Keep replies under 400 characters.
Reply with ONLY JSON: {"reply": str, "stage": "engaged|qualified|won|lost", "handoff": bool}.
stage: engaged = chatting; qualified = clear buying intent; lost = declined/unsubscribed.
handoff=true when the customer asks for a human, is upset, or needs something outside the business info."""


def get_setting(user_id: int, key: str, default: str = "") -> str:
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (f"{user_id}:{key}",)).fetchone()
    return r["value"] if r else default


def set_setting(user_id: int, key: str, value: str):
    with conn() as c:
        c.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"{user_id}:{key}", value))


def _rules(text: str, knowledge: str) -> dict:
    t = text.lower()
    if re.search(r"\b(stop|unsubscribe|cancel)\b", t):
        return {"reply": "", "stage": "lost", "handoff": False, "opt_out": True}
    if re.search(r"\b(human|agent|person|someone|manager)\b", t):
        return {"reply": "Of course - a teammate will join this chat shortly.", "stage": "engaged", "handoff": True}
    if re.search(r"\b(buy|order|yes|take it|purchase)\b", t):
        return {"reply": "Great choice! Please confirm your size and delivery address and I'll send the payment link.",
                "stage": "qualified", "handoff": False}
    if re.search(r"\b(price|cost|how much)\b", t):
        return {"reply": f"Here's what I have: {knowledge[:250]} Want me to reserve one for you?",
                "stage": "engaged", "handoff": False}
    return {"reply": "Hi! Thanks for reaching out. What are you looking for, and what's your budget?",
            "stage": "engaged", "handoff": False}


def decide(user_id: int, history: list[dict], text: str) -> dict:
    """history: [{'sender','body'}] oldest first, already including the new inbound message."""
    knowledge = get_setting(user_id, "knowledge", DEFAULT_KNOWLEDGE)
    out = None
    if ai.llm_available():
        try:
            convo = "\n".join(f"{'Customer' if m['sender'] == 'lead' else 'You'}: {m['body']}" for m in history[-20:])
            raw = ai._ask(SYSTEM, f"Business info:\n{knowledge}\n\nConversation:\n{convo}\n\nWrite your next reply.", 500)
            out = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            if not isinstance(out.get("reply"), str) or out.get("stage") not in ("engaged", "qualified", "lost"):
                out = None
        except Exception:
            out = None
    if out is None:
        out = _rules(text, knowledge)
    # opt-out words are honored even when the LLM answers
    if re.search(r"^\s*(stop|unsubscribe)\s*[.!]?\s*$", text, re.I):
        out = {"reply": "", "stage": "lost", "handoff": False, "opt_out": True}
    out["handoff"] = bool(out.get("handoff"))
    return out
