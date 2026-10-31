import os
import sqlite3
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from sqlalchemy import text

from tests.support import DatabaseTestCase


SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL UNIQUE,name TEXT,password_hash TEXT NOT NULL,webhook_key TEXT NOT NULL UNIQUE,created_at TEXT);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires_at TEXT NOT NULL,created_at TEXT);
CREATE TABLE credentials (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,service TEXT NOT NULL,data TEXT NOT NULL,updated_at TEXT,PRIMARY KEY(user_id,service));
CREATE TABLE campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT,platform TEXT NOT NULL,external_id TEXT,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,demo INTEGER NOT NULL DEFAULT 0,name TEXT NOT NULL,objective TEXT NOT NULL DEFAULT 'sales',status TEXT NOT NULL DEFAULT 'active',daily_budget REAL NOT NULL DEFAULT 20,headline TEXT,body TEXT,cta TEXT,landing_url TEXT,audience TEXT,created_at TEXT);
CREATE TABLE metrics (campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,day TEXT NOT NULL,impressions INTEGER,clicks INTEGER,spend REAL,conversions INTEGER,revenue REAL,PRIMARY KEY(campaign_id,day));
CREATE TABLE leads (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,phone TEXT NOT NULL,name TEXT,campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,referral TEXT,stage TEXT NOT NULL DEFAULT 'new',handoff INTEGER NOT NULL DEFAULT 0,opted_out INTEGER NOT NULL DEFAULT 0,value REAL,followups INTEGER NOT NULL DEFAULT 0,last_inbound_at TEXT,created_at TEXT,UNIQUE(user_id,phone));
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,direction TEXT NOT NULL,sender TEXT NOT NULL DEFAULT 'lead',body TEXT NOT NULL,wa_id TEXT UNIQUE,status TEXT,created_at TEXT);
CREATE TABLE creatives (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,platform TEXT NOT NULL,format TEXT NOT NULL,width INTEGER,height INTEGER,file TEXT NOT NULL,headline TEXT,subheadline TEXT,cta TEXT,language TEXT,campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,created_at TEXT);
CREATE TABLE videos (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,platform TEXT NOT NULL,format TEXT NOT NULL,width INTEGER,height INTEGER,ratio TEXT,duration INTEGER,status TEXT NOT NULL DEFAULT 'queued',error TEXT,file TEXT,headline TEXT,cta TEXT,cost_usd REAL,created_at TEXT,job TEXT,request_id TEXT,status_url TEXT,cancel_url TEXT,provider_status TEXT,params TEXT,started_at TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT);
"""


class ImportTests(DatabaseTestCase):
    def fixture(self, path):
        db = sqlite3.connect(path)
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)
        key = os.environ["YOURADS_SECRET_KEY"].encode()
        from cryptography.fernet import Fernet
        encrypted = Fernet(key).encrypt(b'{"access_token":"fixture-secret"}').decode()
        db.execute("INSERT INTO users VALUES (51,'fixture@example.test','Fixture','password-hash','fixture-hook','2026-10-25 00:00:00')")
        db.execute("INSERT INTO campaigns (id,platform,user_id,demo,name,daily_budget) VALUES (73,'meta',51,1,'Fixture campaign',25)")
        db.execute("INSERT INTO credentials VALUES (51,'meta',?,'2026-10-25 00:00:00')", (encrypted,))
        db.execute("INSERT INTO sessions VALUES ('session-hash',51,'2026-11-25 00:00:00','2026-10-25 00:00:00')")
        db.execute("INSERT INTO metrics VALUES (73,'2026-10-25',10,2,1.25,1,9.5)")
        db.execute("INSERT INTO leads VALUES (91,51,'+15555550001','Fixture',73,'external','engaged',0,0,NULL,0,'2026-10-25 00:00:00','2026-10-25 00:00:00')")
        db.execute("INSERT INTO messages VALUES (99,91,'in','lead','hello',NULL,NULL,'2026-10-25 00:00:00')")
        db.execute("INSERT INTO creatives VALUES (81,51,'meta','square',1080,1080,'asset.png','Headline','Sub','Buy','en',73,'2026-10-25 00:00:00')")
        db.execute("INSERT INTO videos VALUES (82,51,'meta','reel',1080,1920,'9:16',5,'failed',NULL,NULL,NULL,NULL,0.8,'2026-10-25 00:00:00','job','req','status-url','cancel-url','queued','{}','2026-10-25 00:00:00')")
        db.execute("INSERT INTO settings VALUES ('51:knowledge','fixture facts')")
        db.commit()
        db.close()

    def test_import_verify_id_sequences_and_duplicate_refusal(self):
        from app.migrate_sqlite import MigrationError, run
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snapshot.sqlite"
            self.fixture(source)
            run(source, "check")
            run(source, "import")
            run(source, "verify")
            with self.engine.connect() as c:
                self.assertEqual(c.execute(text("SELECT id FROM yourads.users")).scalar_one(), 51)
                self.assertEqual(c.execute(text("SELECT job FROM yourads.videos")).scalar_one(), "job")
            from app.db import session_scope
            from app.models import Campaign, Creative, Lead, Message, User, Video
            with session_scope() as s:
                user = s.get(User, 51)
                new_user = User(email="after-import@example.test", password_hash="hash", webhook_key="after-hook")
                s.add(new_user)
                s.flush()
                campaign = Campaign(user_id=user.id, platform="meta", name="after import")
                s.add(campaign)
                s.flush()
                lead = Lead(user_id=user.id, campaign_id=campaign.id, phone="+15555550002")
                s.add(lead)
                s.flush()
                message = Message(lead_id=lead.id, direction="in", body="hi")
                creative = Creative(user_id=user.id, platform="meta", format="square", file="new.png")
                video = Video(user_id=user.id, platform="meta", format="reel")
                s.add_all((message, creative, video))
                s.flush()
                self.assertGreater(new_user.id, 51)
                self.assertGreater(campaign.id, 73)
                self.assertGreater(lead.id, 91)
                self.assertGreater(message.id, 99)
                self.assertGreater(creative.id, 81)
                self.assertGreater(video.id, 82)
            with self.assertRaises(MigrationError):
                run(source, "import")
            with self.engine.connect() as c:
                self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.users")).scalar_one(), 2)

    def test_empty_import_and_verification(self):
        from app.migrate_sqlite import run
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "empty.sqlite"
            db = sqlite3.connect(source)
            db.executescript(SCHEMA)
            db.close()
            run(source, "check")
            run(source, "import")
            run(source, "verify")

    def test_failed_copy_rolls_back_all_rows(self):
        from unittest.mock import patch
        from sqlalchemy import text as sql_text
        from sqlalchemy.exc import SQLAlchemyError
        import app.migrate_sqlite as importer
        real_insert = importer.insert
        def fail_campaign(table):
            if table.name == "campaigns":
                return sql_text("INSERT INTO yourads.missing_table VALUES (1)")
            return real_insert(table)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snapshot.sqlite"
            self.fixture(source)
            with patch("app.migrate_sqlite.insert", side_effect=fail_campaign):
                with self.assertRaises(SQLAlchemyError):
                    importer.run(source, "import")
            with self.engine.connect() as c:
                for table in ("users", "campaigns", "sessions", "credentials", "metrics", "leads", "messages",
                              "creatives", "videos", "settings"):
                    self.assertEqual(c.execute(text(f"SELECT count(*) FROM yourads.{table}")).scalar_one(), 0)

    def test_preflight_rejects_wrong_key_cross_owner_and_invalid_numeric_data(self):
        from app.migrate_sqlite import MigrationError, run
        from cryptography.fernet import Fernet
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snapshot.sqlite"
            self.fixture(source)
            original = os.environ["YOURADS_SECRET_KEY"]
            try:
                os.environ["YOURADS_SECRET_KEY"] = Fernet.generate_key().decode()
                with self.assertRaises(MigrationError):
                    run(source, "check")
            finally:
                os.environ["YOURADS_SECRET_KEY"] = original
            db = sqlite3.connect(source)
            db.execute("INSERT INTO users VALUES (52,'other@example.test','Other','hash','other-hook','2026-10-25 00:00:00')")
            db.execute("UPDATE campaigns SET user_id=52 WHERE id=73")
            db.commit()
            db.close()
            with self.assertRaises(MigrationError):
                run(source, "check")
            db = sqlite3.connect(source)
            db.execute("UPDATE campaigns SET user_id=51 WHERE id=73")
            db.execute("UPDATE metrics SET spend=1e999 WHERE campaign_id=73")
            db.commit()
            db.close()
            with self.assertRaises(MigrationError):
                run(source, "check")

    def test_missing_source_and_read_only_contract(self):
        from app.migrate_sqlite import MigrationError, run
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.sqlite"
            with self.assertRaises(MigrationError):
                run(missing, "check")
            self.assertFalse(missing.exists())

    def test_check_cli_rejects_nonempty_target_without_changing_it(self):
        from app.db import session_scope
        from app.migrate_sqlite import main
        from app.models import User
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snapshot.sqlite"
            self.fixture(source)
            with session_scope() as session:
                session.add(User(email="existing@example.test", password_hash="hash", webhook_key="existing-hook"))
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["--source", str(source), "--check"])
            self.assertEqual(status, 1)
            self.assertNotIn('"validation": "passed"', stdout.getvalue())
            self.assertIn("target application tables must all be empty", stderr.getvalue())
            with self.engine.connect() as c:
                self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.users")).scalar_one(), 1)
                self.assertEqual(c.execute(text("SELECT email FROM yourads.users")).scalar_one(), "existing@example.test")

    def test_metric_days_require_real_calendar_dates_before_import(self):
        from app.migrate_sqlite import MigrationError, run
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snapshot.sqlite"
            self.fixture(source)
            db = sqlite3.connect(source)
            db.execute("UPDATE metrics SET day='2024-02-29'")
            db.commit()
            db.close()
            run(source, "check")
            for invalid_day in ("2026-02-29", "2026-13-01", "2026-04-31"):
                db = sqlite3.connect(source)
                db.execute("UPDATE metrics SET day=?", (invalid_day,))
                db.commit()
                db.close()
                with self.assertRaisesRegex(MigrationError, "invalid metric day"):
                    run(source, "check")
                with self.assertRaisesRegex(MigrationError, "invalid metric day"):
                    run(source, "import")
                with self.engine.connect() as c:
                    self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.users")).scalar_one(), 0)

    def test_integer_values_must_fit_postgres_destination_types(self):
        from app.migrate_sqlite import MigrationError, run
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snapshot.sqlite"
            self.fixture(source)
            db = sqlite3.connect(source)
            db.execute("UPDATE creatives SET width=?", (2**31 - 1,))
            db.execute("UPDATE metrics SET impressions=?", (2**63 - 1,))
            db.commit()
            db.close()
            run(source, "check")
            for table, column in (("creatives", "width"), ("videos", "duration")):
                db = sqlite3.connect(source)
                db.execute("UPDATE creatives SET width=1080")
                db.execute("UPDATE videos SET duration=5")
                db.execute(f"UPDATE {table} SET {column}=?", (2**31,))
                db.commit()
                db.close()
                with self.assertRaisesRegex(MigrationError, "outside the supported range"):
                    run(source, "check")
                with self.assertRaisesRegex(MigrationError, "outside the supported range"):
                    run(source, "import")
                with self.engine.connect() as c:
                    self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.users")).scalar_one(), 0)
