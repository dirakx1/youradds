from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from tests.support import DatabaseTestCase


class DatabaseTests(DatabaseTestCase):
    def test_schema_inventory_constraints_and_defaults(self):
        inspector = inspect(self.engine)
        self.assertEqual(set(inspector.get_table_names(schema="yourads")), {
            "alembic_version", "users", "sessions", "credentials", "campaigns", "metrics", "leads",
            "messages", "creatives", "videos", "settings", "brands"})
        self.assertIn("started_at", {c["name"] for c in inspector.get_columns("videos", schema="yourads")})
        self.assertIn("clerk_user_id", {c["name"] for c in inspector.get_columns("users", schema="yourads")})
        self.assertTrue(next(c for c in inspector.get_columns("users", schema="yourads")
                             if c["name"] == "password_hash")["nullable"])
        self.assertIn("ix_sessions_expires_at", {i["name"] for i in inspector.get_indexes("sessions", schema="yourads")})
        fks = inspector.get_foreign_keys("leads", schema="yourads")
        self.assertTrue(any(fk["referred_table"] == "campaigns" and fk["options"].get("ondelete") == "SET NULL" for fk in fks))
        with self.engine.connect() as c:
            created_at = c.execute(text("SELECT column_default FROM information_schema.columns "
                                        "WHERE table_schema='yourads' AND table_name='users' AND column_name='created_at'")).scalar_one()
            self.assertIn("AT TIME ZONE 'UTC'", created_at)
            self.assertIsNone(c.execute(text("SELECT has_schema_privilege('anon','yourads','USAGE') "
                                             "WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon')")).scalar())

    def test_runtime_role_dml_allowed_and_ddl_denied(self):
        role = "yourads_test_" + uuid.uuid4().hex[:12]
        with self.engine.begin() as c:
            c.execute(text(f'CREATE ROLE "{role}" LOGIN'))
            c.execute(text(f'GRANT USAGE ON SCHEMA yourads TO "{role}"'))
            c.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA yourads TO "{role}"'))
            c.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA yourads TO "{role}"'))
            c.execute(text(f'GRANT SELECT ON yourads.alembic_version TO "{role}"'))
            c.execute(text(f'SET ROLE "{role}"'))
            c.execute(text("INSERT INTO yourads.users (email,password_hash,webhook_key) "
                           "VALUES ('runtime@example.test','hash','runtime-hook')"))
            with self.assertRaises(Exception):
                with c.begin_nested():
                    c.execute(text("CREATE TABLE yourads.runtime_must_not_create (id integer)"))
            c.execute(text("RESET ROLE"))
        with self.engine.begin() as c:
            c.execute(text(f'REVOKE ALL ON SCHEMA yourads FROM "{role}"'))
            c.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA yourads FROM "{role}"'))
            c.execute(text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA yourads FROM "{role}"'))
            c.execute(text(f'DROP ROLE "{role}"'))

    def test_sessions_are_thread_local_and_pool_is_bounded(self):
        from app.db import session_scope, engine
        barrier = Barrier(2)
        def query():
            with session_scope() as session:
                session_id = id(session)
                pid = session.scalar(text("SELECT pg_backend_pid()"))
                barrier.wait(timeout=5)
                return session_id, pid
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: query(), range(2)))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertNotEqual(results[0][1], results[1][1])
        self.assertEqual(engine().pool.size(), 5)
        self.assertEqual(engine().pool._max_overflow, 5)

    def test_session_commit_rollback_generated_id_and_fk_actions(self):
        from app.db import session_scope
        from app.models import Campaign, Lead, Metric, User
        with session_scope() as s:
            user = User(email="one@example.test", name="One", password_hash="hash", webhook_key="hook")
            s.add(user)
            s.flush()
            uid = user.id
            campaign = Campaign(user_id=uid, platform="meta", name="campaign")
            s.add(campaign)
            s.flush()
            cid = campaign.id
        self.assertGreater(uid, 0)
        self.assertGreater(cid, 0)
        with self.assertRaises(RuntimeError):
            with session_scope() as s:
                s.add(User(email="rollback@example.test", password_hash="h", webhook_key="rollback"))
                raise RuntimeError("rollback")
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.users")).scalar_one(), 1)
        with session_scope() as s:
            s.add(Lead(user_id=uid, phone="+15555550100", campaign_id=cid))
            s.add(Metric(campaign_id=cid, day="2026-10-25", conversions=1, revenue=10.0))
        with session_scope() as s:
            s.delete(s.get(Campaign, cid))
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.metrics")).scalar_one(), 0)
            self.assertIsNone(c.execute(text("SELECT campaign_id FROM yourads.leads")).scalar_one())

    def test_nullable_unique_whatsapp_ids_and_composite_metrics(self):
        from app.db import session_scope
        from app.models import Campaign, Lead, Message, Metric, User
        with session_scope() as s:
            user = User(email="one@example.test", password_hash="h", webhook_key="hook")
            s.add(user)
            s.flush()
            campaign = Campaign(user_id=user.id, platform="meta", name="campaign")
            s.add(campaign)
            s.flush()
            lead = Lead(user_id=user.id, phone="+15555550100")
            s.add(lead)
            s.flush()
            for _ in range(2):
                s.add(Message(lead_id=lead.id, direction="in", body="hello", wa_id=None))
            s.add(Metric(campaign_id=campaign.id, day="2026-10-25", impressions=1))
        with self.assertRaises(IntegrityError):
            with session_scope() as s:
                s.add(Message(lead_id=lead.id, direction="in", body="duplicate", wa_id="duplicate"))
                s.add(Message(lead_id=lead.id, direction="in", body="duplicate", wa_id="duplicate"))
                s.flush()
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.messages")).scalar_one(), 2)
            self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.metrics")).scalar_one(), 1)

    def test_repeat_migration_and_metadata_match(self):
        from alembic import command
        from alembic.config import Config
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")
        command.check(cfg)
        # The additive downgrade is allowed while no Clerk-only user has a NULL password.
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

    def test_clerk_downgrade_refuses_null_password_but_allows_legacy_rows(self):
        from alembic import command
        from alembic.config import Config
        from app.db import session_scope
        from app.models import User
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        with session_scope() as session:
            session.add(User(email="clerk-only@example.test", password_hash=None, clerk_user_id="user_new",
                             webhook_key="clerk-only-hook"))
        with self.assertRaises(Exception):
            command.downgrade(cfg, "0001_postgres")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT version_num FROM yourads.alembic_version")).scalar_one(),
                             "0003_brand")
        with session_scope() as session:
            session.execute(text("DELETE FROM yourads.users WHERE email='clerk-only@example.test'"))
        command.downgrade(cfg, "0001_postgres")
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT version_num FROM yourads.alembic_version")).scalar_one(), "0001_postgres")
            self.assertIsNotNone(c.execute(text("SELECT to_regclass('yourads.users')")).scalar_one())
        command.upgrade(cfg, "head")
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT version_num FROM yourads.alembic_version")).scalar_one(), "0003_brand")

    def test_supplied_alembic_connection_keeps_transaction_caller_owned(self):
        from alembic import command
        from alembic.config import Config
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        with self.engine.begin() as owner_connection:
            cfg.attributes["connection"] = owner_connection
            command.downgrade(cfg, "base")
            with self.engine.connect() as observer:
                self.assertEqual(observer.execute(text("SELECT version_num FROM yourads.alembic_version")).scalar_one(),
                                 "0003_brand")
                self.assertIsNotNone(observer.execute(text("SELECT to_regclass('yourads.users')")).scalar_one())
        cfg.attributes.pop("connection", None)
        with self.engine.connect() as observer:
            self.assertIsNone(observer.execute(text("SELECT to_regclass('yourads.users')")).scalar_one())
            self.assertIsNone(observer.execute(text("SELECT version_num FROM yourads.alembic_version")).scalar_one_or_none())
        command.upgrade(cfg, "head")
