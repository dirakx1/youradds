"""Accounts: email + password, server-side sessions in an HttpOnly cookie."""
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .config import ALLOW_SIGNUP, COOKIE_SECURE, SESSION_DAYS
from .db import conn, seed_demo

router = APIRouter(prefix="/api/auth")
COOKIE = "yourads_session"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_DUMMY = None


ITERATIONS = 600_000      # PBKDF2-HMAC-SHA256, OWASP 2023 guidance; stdlib so it works on any Python build


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(got.hex(), h)
    except ValueError:
        return False


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _start_session(c, user_id: int, response: Response, request: Request):
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
    c.execute("INSERT INTO sessions (token_hash,user_id,expires_at) VALUES (?,?,?)", (_tok_hash(token), user_id, exp))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=COOKIE_SECURE or request.url.scheme == "https", path="/")


def current_user(request: Request) -> dict:
    """FastAPI dependency: the logged-in user, or 401."""
    token = request.cookies.get(COOKIE)
    if token:
        with conn() as c:
            r = c.execute("SELECT u.id,u.email,u.name,u.webhook_key FROM sessions s JOIN users u ON u.id=s.user_id"
                          " WHERE s.token_hash=? AND s.expires_at>?", (_tok_hash(token), _now())).fetchone()
        if r:
            return dict(r)
    raise HTTPException(401, "not logged in")


# ---- brute-force throttle (per process; good enough for a single-instance deployment) ----
_fails: dict = {}


def _throttle(key: str):
    recent = [t for t in _fails.get(key, []) if time.time() - t < 900]
    _fails[key] = recent
    if len(recent) >= 8:
        raise HTTPException(429, "too many attempts, try again in a few minutes")


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=200)
    name: str = Field("", max_length=80)


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, response: Response):
    if not ALLOW_SIGNUP:
        raise HTTPException(403, "sign-ups are disabled on this server")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "enter a valid email")
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    with conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "an account with this email already exists")
        first = not c.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        uid = c.execute("INSERT INTO users (email,name,password_hash,webhook_key) VALUES (?,?,?,?)",
                        (email, body.name.strip(), hash_password(body.password), secrets.token_urlsafe(18))).lastrowid
        if first:   # the first account inherits data created before accounts existed
            for t in ("campaigns", "creatives", "videos", "leads"):
                c.execute(f"UPDATE {t} SET user_id=? WHERE user_id IS NULL", (uid,))
            c.execute("UPDATE settings SET key=? || key WHERE key NOT LIKE '%:%'", (f"{uid}:",))
        if not c.execute("SELECT 1 FROM campaigns WHERE user_id=?", (uid,)).fetchone():
            seed_demo(c, uid)
        _start_session(c, uid, response, request)
    return {"id": uid, "email": email}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    global _DUMMY
    email = body.email.strip().lower()
    key = f"{request.client.host if request.client else '?'}|{email}"
    _throttle(key)
    with conn() as c:
        u = c.execute("SELECT id,password_hash FROM users WHERE email=?", (email,)).fetchone()
        _DUMMY = _DUMMY or hash_password("dummy-password")
        ok = verify_password(body.password, u["password_hash"] if u else _DUMMY) and u is not None
        if not ok:
            _fails.setdefault(key, []).append(time.time())
            raise HTTPException(401, "wrong email or password")
        _fails.pop(key, None)
        _start_session(c, u["id"], response, request)
    return {"id": u["id"], "email": email}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_tok_hash(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"]}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=8, max_length=200)


@router.post("/password")
def change_password(body: PasswordIn, request: Request, response: Response, user: dict = Depends(current_user)):
    with conn() as c:
        row = c.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not verify_password(body.current, row["password_hash"]):
            raise HTTPException(400, "current password is wrong")
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new), user["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))   # sign out everywhere
        _start_session(c, user["id"], response, request)
    return {"ok": True}
