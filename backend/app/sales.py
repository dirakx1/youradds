"""WhatsApp sales agent: replies to leads, qualifies them, escalates to a human."""
import json
import re
from . import ai, brand as brandmod
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .db import session_scope
from .models import Setting

DEFAULT_KNOWLEDGE = ("Negocio: Café La Montaña (Medellín). Producto: café de origen en grano o molido, bolsa de 500 g a "
                     "$38.000 COP. Envío gratis en compras desde $120.000. Despachamos en 2 días hábiles a toda Colombia. "
                     "Devolución dentro de 30 días. El link de pago se envía cuando el cliente confirma el pedido.")

SYSTEM = """You are a friendly, concise WhatsApp sales assistant. Use ONLY the business info provided; if you
don't know something, say a teammate will follow up and set handoff=true. Never invent prices, stock or policies.
Reply in the customer's language (Spanish unless they write in another). Ask at most one question per message. Keep replies under 400 characters.
Reply with ONLY JSON: {"reply": str, "stage": "engaged|qualified|won|lost", "handoff": bool}.
stage: engaged = chatting; qualified = clear buying intent; lost = declined/unsubscribed.
handoff=true when the customer asks for a human, is upset, or needs something outside the business info."""


def get_setting(user_id: int, key: str, default: str = "") -> str:
    with session_scope() as session:
        r = session.execute(select(Setting.value).where(Setting.key == f"{user_id}:{key}")).scalar_one_or_none()
    return r if r is not None else default


def set_setting(user_id: int, key: str, value: str):
    with session_scope() as session:
        stmt = insert(Setting).values(key=f"{user_id}:{key}", value=value)
        session.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": stmt.excluded.value}))


def _rules(text: str, knowledge: str) -> dict:
    t = text.lower()
    if re.search(r"\b(stop|unsubscribe|cancel|parar|detener|cancelar)\b", t):
        return {"reply": "", "stage": "lost", "handoff": False, "opt_out": True}
    if re.search(r"\b(human|agent|person|someone|manager|humano|asesor|persona|alguien|gerente)\b", t):
        return {"reply": "¡Claro! Una persona de nuestro equipo se une a este chat en un momento.", "stage": "engaged", "handoff": True}
    if re.search(r"\b(buy|order|yes|take it|purchase|comprar|pedido|pedir|quiero|lo llevo)\b", t):
        return {"reply": "¡Excelente elección! Confírmame la cantidad y tu dirección de entrega y te envío el link de pago.",
                "stage": "qualified", "handoff": False}
    if re.search(r"\b(price|cost|how much|precio|cuesta|cuánto|cuanto|valor)\b", t):
        return {"reply": f"Esto es lo que tengo: {knowledge[:250]} ¿Quieres que te reserve uno?",
                "stage": "engaged", "handoff": False}
    return {"reply": "¡Hola! Gracias por escribirnos. ¿Qué estás buscando?",
            "stage": "engaged", "handoff": False}


def decide(user_id: int, history: list[dict], text: str) -> dict:
    """history: [{'sender','body'}] oldest first, already including the new inbound message."""
    knowledge = get_setting(user_id, "knowledge", DEFAULT_KNOWLEDGE)
    block = brandmod.prompt_block(brandmod.get(user_id))
    out = None
    if ai.llm_available():
        try:
            convo = "\n".join(f"{'Customer' if m['sender'] == 'lead' else 'You'}: {m['body']}" for m in history[-20:])
            raw = ai._ask(SYSTEM, f"{block}\nBusiness info:\n{knowledge}\n\nConversation:\n{convo}\n\nWrite your next reply.", 500)
            out = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            if not isinstance(out.get("reply"), str) or out.get("stage") not in ("engaged", "qualified", "lost"):
                out = None
        except Exception:
            out = None
    if out is None:
        out = _rules(text, knowledge)
    # opt-out words are honored even when the LLM answers
    if re.search(r"^\s*(stop|unsubscribe|parar|detener|baja)\s*[.!]?\s*$", text, re.I):
        out = {"reply": "", "stage": "lost", "handoff": False, "opt_out": True}
    out["handoff"] = bool(out.get("handoff"))
    return out
