"""Shared guarded setup for integration tests against an explicitly disposable PostgreSQL database."""
import os
import secrets
import tempfile
import unittest
from pathlib import Path


def configure_test_environment():
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    if os.getenv("YOURADS_TEST_DATABASE_DISPOSABLE") != "1" or not url:
        raise RuntimeError("Set TEST_DATABASE_URL to a disposable PostgreSQL database and YOURADS_TEST_DATABASE_DISPOSABLE=1")
    if not url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL")
    os.environ["YOURADS_SKIP_DOTENV"] = "1"
    os.environ["DATABASE_URL"] = url
    os.environ["MIGRATION_DATABASE_URL"] = url
    os.environ["YOURADS_SECRET_KEY"] = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
    os.environ["ALLOW_SIGNUP"] = "true"
    os.environ["CLERK_PUBLISHABLE_KEY"] = "pk_test_ZXhhbXBsZS5jb20k"
    os.environ["CLERK_SECRET_KEY"] = "sk_test_disposable"
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    test_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    os.environ["CLERK_JWT_KEY"] = test_private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    os.environ["CLERK_ISSUER"] = "https://example.clerk.accounts.dev"
    os.environ["CLERK_AUTHORIZED_PARTIES"] = "http://localhost:8000"
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["SHARED_HIGGSFIELD"] = "false"
    media = tempfile.TemporaryDirectory(prefix="yourads-test-media-")
    os.environ["YOURADS_CREATIVES"] = str(Path(media.name) / "creatives")
    os.environ["YOURADS_SECRET_KEY_FILE"] = str(Path(media.name) / ".secret_key")
    return media


def prepare_schema():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    # Tests can only touch the explicitly supplied TEST_DATABASE_URL, never app/admin fallbacks.
    from app.config import database_url
    engine = create_engine(database_url("TEST_DATABASE_URL"), pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS "yourads" CASCADE'))
    engine.dispose()
    command.upgrade(cfg, "head")


class DatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = configure_test_environment()
        prepare_schema()
        from app.db import engine
        cls.engine = engine()

    @classmethod
    def tearDownClass(cls):
        try:
            from app.db import dispose_engine
            dispose_engine()
            from sqlalchemy import create_engine, text
            from app.config import database_url
            engine = create_engine(database_url("TEST_DATABASE_URL"))
            with engine.begin() as connection:
                connection.execute(text('DROP SCHEMA IF EXISTS "yourads" CASCADE'))
            engine.dispose()
        finally:
            cls._media.cleanup()

    def setUp(self):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            connection.execute(text('TRUNCATE TABLE "yourads".users CASCADE'))
            connection.execute(text('TRUNCATE TABLE "yourads".campaigns CASCADE'))
            connection.execute(text('TRUNCATE TABLE "yourads".leads CASCADE'))
            connection.execute(text('TRUNCATE TABLE "yourads".videos, "yourads".creatives, "yourads".settings RESTART IDENTITY CASCADE'))
