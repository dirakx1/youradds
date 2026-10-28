import random
import sqlite3
from datetime import date, timedelta
from contextlib import contextmanager
from .config import DB_PATH

LEADS_DDL = """CREATE TABLE IF NOT EXISTS {name} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  phone TEXT NOT NULL,
  name TEXT,
  campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
  referral TEXT,
  stage TEXT NOT NULL DEFAULT 'new',      -- new|engaged|qualified|won|lost
  handoff INTEGER NOT NULL DEFAULT 0,     -- 1 = human handles the chat, AI stays silent
  opted_out INTEGER NOT NULL DEFAULT 0,
  value REAL,
  followups INTEGER NOT NULL DEFAULT 0,
  last_inbound_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, phone)
);"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  name TEXT,
  password_hash TEXT NOT NULL,
  webhook_key TEXT NOT NULL UNIQUE,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS credentials (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL,                  -- meta|tiktok|google|whatsapp|higgsfield
  data TEXT NOT NULL,                     -- Fernet-encrypted JSON
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, service)
);
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  external_id TEXT,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  demo INTEGER NOT NULL DEFAULT 0,
  name TEXT NOT NULL,
  objective TEXT NOT NULL DEFAULT 'sales',
  status TEXT NOT NULL DEFAULT 'active',
  daily_budget REAL NOT NULL DEFAULT 20,
  headline TEXT, body TEXT, cta TEXT, landing_url TEXT, audience TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS metrics (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  impressions INTEGER, clicks INTEGER, spend REAL, conversions INTEGER, revenue REAL,
  PRIMARY KEY (campaign_id, day)
);
""" + LEADS_DDL.format(name="leads") + """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,                -- in|out
  sender TEXT NOT NULL DEFAULT 'lead',    -- lead|ai|human|system
  body TEXT NOT NULL,
  wa_id TEXT UNIQUE,
  status TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS creatives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  format TEXT NOT NULL,
  width INTEGER, height INTEGER,
  file TEXT NOT NULL,
  headline TEXT, subheadline TEXT, cta TEXT, language TEXT,
  campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  platform TEXT NOT NULL, format TEXT NOT NULL, width INTEGER, height INTEGER,
  ratio TEXT, duration INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued|generating|compositing|done|failed
  error TEXT, file TEXT, headline TEXT, cta TEXT, cost_usd REAL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subscriptions (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  plan TEXT NOT NULL,
  billing TEXT NOT NULL DEFAULT 'monthly',   -- monthly|annual (allowances reset monthly either way)
  anchor TEXT NOT NULL,                      -- date the current plan started; usage periods are monthly from here
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resource TEXT NOT NULL,                    -- creatives|video_credits|wa_messages
  delta INTEGER NOT NULL,                    -- negative = consumed, positive = refund/top-up/grant
  pool TEXT NOT NULL,                        -- plan = monthly allowance, topup = purchased/granted credits
  kind TEXT NOT NULL,                        -- usage|refund|topup|grant
  period TEXT,                               -- start date of the allowance period (plan pool)
  ref TEXT, note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger (user_id, resource, pool, period);
CREATE INDEX IF NOT EXISTS ledger_ref ON ledger (ref);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);   -- key is "<user_id>:<name>"
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _migrate_leads():
    """Old leads table had UNIQUE(phone) and no owner; rebuild it with per-user uniqueness (FKs off, as SQLite needs)."""
    c = sqlite3.connect(DB_PATH)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(leads)")}
        if cols and "user_id" not in cols:
            c.execute("PRAGMA foreign_keys=OFF")
            c.executescript(LEADS_DDL.format(name="leads_new") +
                            "INSERT INTO leads_new (id,phone,name,campaign_id,referral,stage,handoff,opted_out,value,"
                            "followups,last_inbound_at,created_at) SELECT id,phone,name,campaign_id,referral,stage,"
                            "handoff,opted_out,value,followups,last_inbound_at,created_at FROM leads;"
                            "DROP TABLE leads; ALTER TABLE leads_new RENAME TO leads;")
    finally:
        c.close()


def _add_cols(c, table: str, cols: dict):
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    for col, ddl in cols.items():
        if col not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db():
    _migrate_leads()
    with conn() as c:
        c.executescript(SCHEMA)
        _add_cols(c, "videos", {k: "TEXT" for k in ("job", "request_id", "status_url", "cancel_url",
                                                    "provider_status", "params", "started_at")})
        for t in ("campaigns", "creatives", "videos"):
            _add_cols(c, t, {"user_id": "INTEGER REFERENCES users(id) ON DELETE CASCADE"})
        if "demo" not in {r["name"] for r in c.execute("PRAGMA table_info(campaigns)")}:
            c.execute("ALTER TABLE campaigns ADD COLUMN demo INTEGER NOT NULL DEFAULT 0")
            c.execute("UPDATE campaigns SET demo=1 WHERE external_id IS NULL")  # pre-accounts rows were synthetic


def synth_metrics(c, campaign_id: int, budget: float, quality: float, days: int = 14):
    rnd = random.Random(campaign_id)
    for i in range(days):
        day = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        spend = budget * rnd.uniform(0.75, 1.0)
        cpm = rnd.uniform(6, 14)
        imps = int(spend / cpm * 1000)
        clicks = int(imps * rnd.uniform(0.008, 0.025) * quality)
        conv = int(clicks * rnd.uniform(0.02, 0.06) * quality)
        rev = conv * rnd.uniform(28, 60)
        c.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?,?)",
                  (campaign_id, day, imps, clicks, round(spend, 2), conv, round(rev, 2)))


def seed_demo(c, user_id: int):
    demo = [
        ("meta", "Spring Sale - Lookalike", "sales", 50, 1.3),
        ("meta", "Retargeting - Cart Abandoners", "sales", 30, 1.6),
        ("tiktok", "UGC Video - Gen Z", "traffic", 40, 0.7),
        ("tiktok", "Spark Ads - Best Sellers", "sales", 35, 1.1),
        ("google", "Search - Brand Terms", "sales", 25, 1.8),
        ("google", "Performance Max - All Products", "sales", 60, 0.5),
    ]
    for p, name, obj, budget, q in demo:
        cur = c.execute(
            "INSERT INTO campaigns (user_id,demo,platform,name,objective,daily_budget,headline,body,cta,landing_url,audience)"
            " VALUES (?,1,?,?,?,?,?,?,?,?,?)",
            (user_id, p, name, obj, budget, "Shop the new collection", "Limited-time offer. Free shipping today.",
             "Shop Now", "https://example.com", "Women 25-44, US"))
        synth_metrics(c, cur.lastrowid, budget, q)
