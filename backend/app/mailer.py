"""Outgoing email through Amazon SES (SMTP interface). Never blocks or breaks a request: failures are logged and dropped."""
import logging
import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from . import config

log = logging.getLogger("yourads.mail")


def _settings() -> dict:
    return {"host": config.env("SES_SMTP_HOST").strip() or "email-smtp.us-east-1.amazonaws.com",
            "user": config.env("SES_SMTP_USER").strip(), "password": config.env("SES_SMTP_PASSWORD").strip(),
            "sender": config.env("MAIL_FROM").strip(), "reply_to": config.env("MAIL_REPLY_TO").strip()}


def enabled() -> bool:
    s = _settings()
    return bool(s["user"] and s["password"] and s["sender"])


def _smtp_send(msg: EmailMessage) -> None:
    s = _settings()
    with smtplib.SMTP(s["host"], 587, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(s["user"], s["password"])
        smtp.send_message(msg)


def _build(to: list[str], subject: str, text: str) -> EmailMessage:
    s = _settings()
    msg = EmailMessage()
    name, address = parseaddr(s["sender"])
    msg["From"] = formataddr((name or "youradds", address))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if s["reply_to"]:
        msg["Reply-To"] = s["reply_to"]
    msg.set_content(text)
    return msg


def send_now(to: list[str], subject: str, text: str) -> bool:
    """Send and report whether it worked (used where the caller must know, such as the activity digest)."""
    to = [a for a in dict.fromkeys(x.strip() for x in to) if a]
    if not to or not enabled():
        return False
    try:
        _smtp_send(_build(to, subject, text))
        return True
    except Exception as exc:                      # SES refused, network down, sandbox recipient not verified...
        log.warning("email to %d recipient(s) not sent: %s", len(to), exc.__class__.__name__)
        return False


def send(to: list[str], subject: str, text: str) -> None:
    """Fire and forget, so a slow mail server never slows a request down."""
    if not enabled():
        return
    if os.environ.get("MAIL_SYNC") == "1":        # tests
        send_now(to, subject, text)
        return
    threading.Thread(target=send_now, args=(to, subject, text), daemon=True).start()
