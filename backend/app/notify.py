"""Emails for managed mode: requests, approvals, endings and a digest of what staff changed. Bilingual (Spanish, then English)."""
import asyncio
import logging
import re

from sqlalchemy import select, update

from . import config, mailer
from .db import session_scope
from .models import AuditLog, Campaign, User

log = logging.getLogger("yourads.mail")
DIGEST_EVERY = 600          # seconds


def _base() -> str:
    return config.env("PUBLIC_BASE_URL").strip().rstrip("/") or "https://youradds.co"


def _body(es: str, en: str) -> str:
    return (f"{es}\n\n— — —\n\n{en}\n\n--\nyouradds · KubeAI LLC · contact@youradds.co\n"
            "Recibes este correo porque tu cuenta está administrada por youradds. / You receive this because youradds manages your account.")


def _account(account_id: int) -> dict | None:
    with session_scope() as session:
        row = session.execute(select(User.email, User.name).where(User.id == account_id)).mappings().one_or_none()
    return dict(row) if row else None


def staff_new_request(user: dict) -> None:
    to = sorted(config.staff_emails())
    mailer.send(to, "Nueva solicitud de administración / New management request",
                f"{user['email']} pidió que youradds maneje sus anuncios.\n{user['email']} asked youradds to manage their ads.\n\n"
                f"{_base()}/#staff")


def client_now_managed(account_id: int) -> None:
    a = _account(account_id)
    if not a:
        return
    url = f"{_base()}/#connections"
    mailer.send([a["email"]], "youradds ya administra tus anuncios / youradds now manages your ads", _body(
        f"Hola,\n\nyouradds ya administra tus anuncios. Para que podamos trabajar, comparte tu cuenta publicitaria y tu Página con nosotros siguiendo la lista en:\n{url}\n\n"
        "Tú conservas tu cuenta y tu tarjeta, y apruebas cada campaña antes de que se gaste dinero.",
        f"Hello,\n\nyouradds now manages your ads. So we can work, share your ad account and your Page with us following the checklist at:\n{url}\n\n"
        "You keep your account and your card, and you approve every campaign before money is spent."))


def client_approval_needed(account_id: int, campaign_id: int, kind: str) -> None:
    a = _account(account_id)
    if not a:
        return
    with session_scope() as session:
        c = session.execute(select(Campaign.name, Campaign.daily_budget).where(Campaign.id == campaign_id)).mappings().one_or_none()
    name, budget = (c["name"], c["daily_budget"]) if c else ("", None)
    es = "iniciar la campaña" if kind == "activate" else "subir el presupuesto de la campaña"
    en = "start the campaign" if kind == "activate" else "raise the budget of the campaign"
    mailer.send([a["email"]], "youradds espera tu aprobación / youradds is waiting for your approval", _body(
        f"youradds quiere {es} «{name}» (presupuesto diario actual: {budget}).\nNo pasará nada hasta que la apruebes: {_base()}/#campaigns",
        f"youradds wants to {en} \"{name}\" (current daily budget: {budget}).\nNothing happens until you approve it: {_base()}/#campaigns"))


ENDED_ES = ("Para que youradds pierda todo acceso, en la configuración de Meta Business abre Socios, busca a youradds y quítalo; "
            "quítalo también de tu cuenta publicitaria y de tu Página si aún aparece. Las campañas que ya están en marcha siguen funcionando: páusalas en la página Campañas si quieres detenerlas.")
ENDED_EN = ("So youradds loses all access, in Meta Business settings open Partners, find youradds and remove it; remove it from your ad account and Page too if it still appears. "
            "Campaigns that are already running keep running: pause them on the Campaigns page if you want them stopped.")


def client_ended(account_id: int, by: str) -> None:
    a = _account(account_id)
    if not a:
        return
    mailer.send([a["email"]], "youradds ya no administra tus anuncios / youradds no longer manages your ads", _body(
        ("Terminaste la administración. " if by == "client" else "El equipo de youradds terminó la administración de tu cuenta. ") + ENDED_ES,
        ("You ended management. " if by == "client" else "The youradds team ended management of your account. ") + ENDED_EN))
    if by == "client":
        mailer.send(sorted(config.staff_emails()), "Un cliente terminó la administración / A client ended management",
                    f"{a['email']} terminó la administración y el acceso se retiró.\n{a['email']} ended management and the access was removed.")


# What staff did, in plain words (audit rows hold the technical request).
_FRIENDLY = [
    (r"^opened the account$", "Abrió tu cuenta / Opened your account"),
    (r"^POST /api/campaigns/\d+/publish", "Publicó una campaña en Meta (en pausa) / Published a campaign to Meta (paused)"),
    (r"^POST /api/campaigns/\d+/activate", "Pidió iniciar una campaña / Asked to start a campaign"),
    (r"^POST /api/campaigns/\d+/status", "Cambió el estado de una campaña / Changed a campaign's status"),
    (r"^POST /api/campaigns", "Creó una campaña / Created a campaign"),
    (r"^DELETE /api/campaigns", "Eliminó una campaña / Deleted a campaign"),
    (r"^POST /api/ai/apply", "Aplicó un cambio del optimizador / Applied an optimizer change"),
    (r"^connected partner", "Conectó tu cuenta publicitaria / Connected your ad account"),
    (r"^disconnected partner", "Desconectó tu cuenta publicitaria / Disconnected your ad account"),
]


def friendly(action: str) -> str:
    for pattern, text in _FRIENDLY:
        if re.search(pattern, action):
            return text
    return "Hizo un cambio en tu cuenta / Made a change in your account"


def send_digests() -> int:
    """One email per managed account listing what staff did since the last one. Returns how many were sent."""
    if not mailer.enabled():
        return 0
    sent = 0
    with session_scope() as session:
        accounts = session.execute(select(User.id, User.email, User.activity_notified_id).where(User.mode == "managed")).all()
    for account_id, email, last in accounts:
        with session_scope() as session:
            rows = session.execute(select(AuditLog.id, AuditLog.at, AuditLog.staff_email, AuditLog.action)
                                   .where(AuditLog.account_id == account_id, AuditLog.id > (last or 0))
                                   .order_by(AuditLog.id).limit(200)).all()
        if not rows:
            continue
        staff = [r for r in rows if not r.staff_email.endswith("(client)") and not r.action.startswith("set mode")]
        if staff:
            lines = "\n".join(f"- {r.at[:16]} UTC · {friendly(r.action)}" for r in staff)
            ok = mailer.send_now([email], "Cambios hechos por youradds en tu cuenta / Changes youradds made in your account", _body(
                f"Esto hizo el equipo de youradds en tu cuenta:\n\n{lines}\n\nNo se gasta dinero sin tu aprobación. Si algo no te cuadra, escríbenos.",
                f"This is what the youradds team did in your account:\n\n{lines}\n\nNo money is spent without your approval. If anything looks wrong, write to us."))
            if not ok:
                continue                                   # try again next round
            sent += 1
        with session_scope() as session:
            session.execute(update(User).where(User.id == account_id).values(activity_notified_id=rows[-1].id))
    return sent


async def digest_loop():
    while True:
        await asyncio.sleep(DIGEST_EVERY)
        try:
            await asyncio.to_thread(send_digests)
        except Exception:
            log.exception("activity digest failed")
