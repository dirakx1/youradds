import os
from pathlib import Path
from dotenv import load_dotenv

if os.getenv("YOURADS_SKIP_DOTENV") != "1":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5-5")
PLATFORMS = ("meta", "tiktok", "google")


def env(name: str) -> str:
    return os.getenv(name, "")
CREATIVES_DIR = Path(os.getenv("YOURADS_CREATIVES", str(Path(__file__).resolve().parents[1] / "data" / "creatives")))

ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").lower() not in ("0", "false", "no")
CLERK_PUBLISHABLE_KEY = env("CLERK_PUBLISHABLE_KEY")
CLERK_SECRET_KEY = env("CLERK_SECRET_KEY")
CLERK_JWT_KEY = env("CLERK_JWT_KEY")
CLERK_ISSUER = env("CLERK_ISSUER").rstrip("/")
CLERK_AUTHORIZED_PARTIES = tuple(x.strip() for x in env("CLERK_AUTHORIZED_PARTIES").split(",") if x.strip())


def validate_clerk_config() -> None:
    required = (CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY, CLERK_JWT_KEY, CLERK_ISSUER,
                *CLERK_AUTHORIZED_PARTIES)
    if not all(required) or not CLERK_AUTHORIZED_PARTIES:
        raise RuntimeError("Clerk authentication configuration is incomplete")
    if not CLERK_ISSUER.startswith("https://") or any(not x.startswith("http://") and not x.startswith("https://")
                                                       for x in CLERK_AUTHORIZED_PARTIES):
        raise RuntimeError("Clerk issuer and authorized parties must be explicit URLs")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        load_pem_public_key(CLERK_JWT_KEY.encode())
    except Exception as exc:
        raise RuntimeError("Clerk JWT public key is invalid") from exc


def clerk_frontend_api() -> str:
    try:
        import base64
        encoded = CLERK_PUBLISHABLE_KEY.split("_")[2]
        host = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")
    except Exception as exc:
        raise RuntimeError("Clerk publishable key is invalid") from exc
    if not host or "/" in host or ":" in host:
        raise RuntimeError("Clerk publishable key is invalid")
    return "https://" + host
# Server-wide Higgsfield key is NOT used for users unless explicitly enabled (it would spend the owner's money).
SHARED_HIGGSFIELD = os.getenv("SHARED_HIGGSFIELD", "").lower() in ("1", "true", "yes")
SECRET_KEY_FILE = Path(os.getenv("YOURADS_SECRET_KEY_FILE", str(Path(__file__).resolve().parents[1] / "data" / ".secret_key")))


def database_url(name: str = "DATABASE_URL") -> str:
    """Return a validated SQLAlchemy URL without exposing its value in errors."""
    from sqlalchemy.engine import make_url
    raw = os.getenv(name, "").strip()
    if not raw:
        raise RuntimeError(f"{name} is required")
    try:
        url = make_url(raw)
    except Exception as exc:
        raise RuntimeError(f"{name} is malformed") from exc
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    elif url.drivername == "postgresql+psycopg":
        pass
    else:
        raise RuntimeError(f"{name} must use PostgreSQL (postgresql:// or postgres://)")
    if not url.host or not url.database:
        raise RuntimeError(f"{name} must include a PostgreSQL host and database")
    if url.host not in ("localhost", "127.0.0.1", "::1"):
        sslmode = url.query.get("sslmode", "")
        if isinstance(sslmode, tuple):
            sslmode = sslmode[-1]
        if sslmode not in ("require", "verify-ca", "verify-full"):
            raise RuntimeError(f"{name} must require TLS for remote PostgreSQL connections")
    return url.render_as_string(hide_password=False)
