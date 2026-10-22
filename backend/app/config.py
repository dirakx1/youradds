import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_legacy = Path(__file__).resolve().parents[1] / "siadds.db"
DB_PATH = os.getenv("YOURADS_DB", str(Path(__file__).resolve().parents[1] / "yourads.db"))
if _legacy.exists() and not Path(DB_PATH).exists():   # one-time rename from the old project name
    _legacy.rename(DB_PATH)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5-5")
PLATFORMS = ("meta", "tiktok", "google")


def env(name: str) -> str:
    return os.getenv(name, "")
CREATIVES_DIR = Path(os.getenv("YOURADS_CREATIVES", str(Path(__file__).resolve().parents[1] / "data" / "creatives")))

ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").lower() not in ("0", "false", "no")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
# Server-wide Higgsfield key is NOT used for users unless explicitly enabled (it would spend the owner's money).
SHARED_HIGGSFIELD = os.getenv("SHARED_HIGGSFIELD", "").lower() in ("1", "true", "yes")
SECRET_KEY_FILE = Path(os.getenv("YOURADS_SECRET_KEY_FILE", str(Path(__file__).resolve().parents[1] / "data" / ".secret_key")))
