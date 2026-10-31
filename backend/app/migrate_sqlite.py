"""Fail-closed, offline importer for a fully normalized legacy SQLite database."""
import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import BigInteger, Float, Integer, Text, create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert

from .config import SECRET_KEY_FILE, database_url
from .models import (Base, Campaign, Credential, Creative, Lead, Message, Metric, SCHEMA, SessionRecord,
                     Setting, User, Video)

TABLES = [User, Campaign, SessionRecord, Credential, Metric, Lead, Message, Creative, Video, Setting]
BY_NAME = {model.__tablename__: model for model in TABLES}
LEGACY_COLUMNS = {name: [c.name for c in model.__table__.columns if c.name != "clerk_user_id"]
                  for name, model in BY_NAME.items()}
SEQUENCE_TABLES = ("users", "campaigns", "leads", "messages", "creatives", "videos")
BATCH_SIZE = 1000
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
FLOAT_FIELDS = {"daily_budget", "spend", "revenue", "value", "cost_usd"}
FLOAT_FIELDS_BY_TABLE = {"campaigns": {"daily_budget"}, "metrics": {"spend", "revenue"},
                         "leads": {"value"}, "videos": {"cost_usd"}}
INT_FIELDS = {"id", "user_id", "campaign_id", "lead_id", "impressions", "clicks", "conversions", "demo",
              "handoff", "opted_out", "followups", "width", "height", "duration"}
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class MigrationError(RuntimeError):
    pass


def _source(path: Path):
    if not path.is_file():
        raise MigrationError("source file does not exist or is not a regular file")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db
    except sqlite3.Error as exc:
        raise MigrationError("could not open source SQLite database read-only") from exc


def _key():
    raw = os.getenv("YOURADS_SECRET_KEY", "").encode()
    if not raw and SECRET_KEY_FILE.is_file():
        raw = SECRET_KEY_FILE.read_bytes().strip()
    if not raw:
        return None
    try:
        return Fernet(raw)
    except Exception as exc:
        raise MigrationError("configured credential encryption key is invalid") from exc


def _check_target(connection):
    schema = connection.execute(text("SELECT to_regnamespace(:s)"), {"s": SCHEMA}).scalar()
    head = connection.execute(text(f'SELECT version_num FROM "{SCHEMA}".alembic_version')).scalar_one_or_none() if schema else None
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    expected = ScriptDirectory.from_config(cfg).get_current_head()
    if head != expected:
        raise MigrationError("target is not at the required application schema revision")


def _table_columns(db, name):
    return [r["name"] for r in db.execute(f'PRAGMA table_info("{name}")')]


def _validate_source(db, key):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if not r[0].startswith("sqlite_")}
    expected_tables = set(BY_NAME)
    if tables != expected_tables:
        raise MigrationError("source schema does not match the complete supported account-era layout")
    counts = {}
    for name, model in BY_NAME.items():
        actual = _table_columns(db, name)
        expected = LEGACY_COLUMNS[name]
        if set(actual) != set(expected):
            raise MigrationError(f"source table {name} has an unsupported column layout")
        declared = {row["name"]: row["type"].upper() for row in db.execute(f'PRAGMA table_info("{name}")')}
        for column in model.__table__.columns:
            if column.name not in expected:
                continue
            kind = declared[column.name]
            if isinstance(column.type, (Integer, BigInteger)):
                valid = "INT" in kind
            elif isinstance(column.type, Float):
                valid = any(token in kind for token in ("REAL", "FLOAT", "DOUBLE"))
            elif isinstance(column.type, Text):
                valid = any(token in kind for token in ("TEXT", "CHAR", "CLOB"))
            else:
                valid = False
            if not valid:
                raise MigrationError(f"source table {name} has an unexpected declared type")
            if (not column.nullable or column.primary_key) and db.execute(
                    f'SELECT 1 FROM "{name}" WHERE "{column.name}" IS NULL LIMIT 1').fetchone():
                raise MigrationError(f"source table {name} contains a missing required value")
        counts[name] = db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
    integrity = db.execute("PRAGMA integrity_check").fetchall()
    if len(integrity) != 1 or integrity[0][0] != "ok":
        raise MigrationError("source integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MigrationError("source foreign key check failed")
    foreign_keys = (("sessions", "user_id", "users", "id"), ("credentials", "user_id", "users", "id"),
                    ("campaigns", "user_id", "users", "id"), ("metrics", "campaign_id", "campaigns", "id"),
                    ("leads", "user_id", "users", "id"), ("leads", "campaign_id", "campaigns", "id"),
                    ("messages", "lead_id", "leads", "id"), ("creatives", "user_id", "users", "id"),
                    ("creatives", "campaign_id", "campaigns", "id"), ("videos", "user_id", "users", "id"))
    for table, column, parent, parent_column in foreign_keys:
        invalid = db.execute(f'SELECT 1 FROM "{table}" child LEFT JOIN "{parent}" parent '
                             f'ON parent."{parent_column}"=child."{column}" '
                             f'WHERE child."{column}" IS NOT NULL AND parent."{parent_column}" IS NULL LIMIT 1').fetchone()
        if invalid:
            raise MigrationError("source foreign key check failed")
    unique_sets = {
        "users": (("id",), ("email",), ("webhook_key",)), "sessions": (("token_hash",),),
        "credentials": (("user_id", "service"),), "metrics": (("campaign_id", "day"),),
        "campaigns": (("id",),), "leads": (("id",), ("user_id", "phone")),
        "messages": (("id",), ("wa_id",)), "creatives": (("id",),), "videos": (("id",),),
        "settings": (("key",),),
    }
    for table, groups in unique_sets.items():
        for columns in groups:
            cols = ",".join(f'"{c}"' for c in columns)
            nonnull = " AND ".join(f'"{c}" IS NOT NULL' for c in columns)
            if db.execute(f'SELECT 1 FROM "{table}" WHERE {nonnull} GROUP BY {cols} HAVING count(*)>1 LIMIT 1').fetchone():
                raise MigrationError(f"source uniqueness validation failed for {table}")
    if db.execute("SELECT 1 FROM leads l JOIN campaigns c ON c.id=l.campaign_id "
                  "WHERE l.campaign_id IS NOT NULL AND l.user_id IS NOT c.user_id LIMIT 1").fetchone():
        raise MigrationError("source contains cross-owner lead campaign references")
    if db.execute("SELECT 1 FROM creatives cr JOIN campaigns c ON c.id=cr.campaign_id "
                  "WHERE cr.campaign_id IS NOT NULL AND cr.user_id IS NOT c.user_id LIMIT 1").fetchone():
        raise MigrationError("source contains cross-owner creative campaign references")
    # An explicit numeric settings prefix must refer to an existing account. Unprefixed operator legacy keys remain valid.
    for row in db.execute("SELECT key FROM settings WHERE key GLOB '[0-9]*:*'"):
        prefix = row[0].split(":", 1)[0]
        if prefix.isdigit() and not db.execute("SELECT 1 FROM users WHERE id=?", (int(prefix),)).fetchone():
            raise MigrationError("source contains a setting with an unknown account prefix")

    for table in BY_NAME:
        model = BY_NAME[table]
        for row in db.execute(f'SELECT * FROM "{table}"'):
            values = dict(row)
            for column in model.__table__.columns:
                if column.name not in LEGACY_COLUMNS[table]:
                    continue
                value = values[column.name]
                if value is None:
                    continue
                if isinstance(column.type, (Integer, BigInteger)):
                    if not isinstance(value, int) or isinstance(value, bool):
                        raise MigrationError(f"source contains invalid integer data in {table}")
                    minimum, maximum = ((-(2**63), 2**63 - 1) if isinstance(column.type, BigInteger)
                                        else (-(2**31), 2**31 - 1))
                    if not minimum <= value <= maximum:
                        raise MigrationError(f"source integer is outside the supported range in {table}")
                if isinstance(column.type, Float):
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        raise MigrationError(f"source contains invalid numeric data in {table}")
                    try:
                        finite = math.isfinite(float(value))
                    except (TypeError, ValueError) as exc:
                        raise MigrationError(f"source contains invalid numeric data in {table}") from exc
                    if not finite:
                        raise MigrationError(f"source contains non-finite numeric data in {table}")
                if isinstance(column.type, Text) and not isinstance(value, str):
                    raise MigrationError(f"source contains invalid text data in {table}")
            if table == "metrics":
                day = values["day"] or ""
                try:
                    if not DAY_RE.fullmatch(day):
                        raise ValueError()
                    date.fromisoformat(day)
                except (TypeError, ValueError):
                    raise MigrationError("source contains an invalid metric day")
            for column in ("created_at", "updated_at", "expires_at", "last_inbound_at", "started_at"):
                value = values.get(column)
                if value is not None:
                    try:
                        if not TIMESTAMP_RE.fullmatch(value):
                            raise ValueError()
                        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                    except (TypeError, ValueError):
                        raise MigrationError(f"source contains an invalid timestamp in {table}")
    credentials = db.execute("SELECT data FROM credentials").fetchall()
    if credentials:
        if key is None:
            raise MigrationError("encrypted credentials exist but the existing Fernet key is unavailable")
        try:
            for row in credentials:
                key.decrypt(row[0].encode())
        except (InvalidToken, ValueError, AttributeError) as exc:
            raise MigrationError("a stored credential cannot be decrypted with the configured key") from exc
    return counts


def _canonical(value, field, table):
    if value is None:
        return None
    if field in FLOAT_FIELDS_BY_TABLE.get(table, set()):
        val = float(value)
        if not math.isfinite(val):
            raise MigrationError("non-finite number encountered during parity verification")
        return {"float64": val.hex()}
    if field in INT_FIELDS:
        return int(value)
    return str(value)


def _source_digest(db, name):
    model = BY_NAME[name]
    columns = LEGACY_COLUMNS[name]
    primary = [c.name for c in model.__table__.primary_key.columns]
    order = ",".join(f'"{c}"' for c in primary)
    cursor = db.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in columns)} FROM "{name}" ORDER BY {order}')
    digest = hashlib.sha256()
    while True:
        batch = cursor.fetchmany(BATCH_SIZE)
        if not batch:
            break
        for raw in batch:
            row = dict(raw)
            digest.update(json.dumps([_canonical(row[c], c, name) for c in columns], ensure_ascii=False,
                                     separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _target_count(connection, name):
    model = BY_NAME[name]
    return connection.execute(select(func.count()).select_from(model.__table__)).scalar_one()


def _target_digest(connection, name):
    table = BY_NAME[name].__table__
    primary = [column.name for column in table.primary_key.columns]
    statement = select(table).order_by(*(table.c[c] for c in primary))
    digest = hashlib.sha256()
    result = connection.execute(statement)
    columns = LEGACY_COLUMNS[name]
    while True:
        batch = result.fetchmany(BATCH_SIZE)
        if not batch:
            break
        for raw in batch:
            row = dict(raw._mapping)
            digest.update(json.dumps([_canonical(row[c], c, name) for c in columns], ensure_ascii=False,
                                     separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _validate_target_counts(connection, counts, empty=False):
    for name in BY_NAME:
        count = _target_count(connection, name)
        if empty and count:
            raise MigrationError("target application tables must all be empty before import")
        if not empty and name in counts and count != counts[name]:
            raise MigrationError("target table count does not match the source")


def _set_sequences(connection):
    for table in SEQUENCE_TABLES:
        seq = connection.execute(text("SELECT pg_get_serial_sequence(:table, 'id')"),
                                 {"table": f"{SCHEMA}.{table}"}).scalar_one_or_none()
        if not seq:
            raise MigrationError(f"identity sequence missing for {table}")
        maximum = connection.execute(text(f'SELECT max(id) FROM "{SCHEMA}"."{table}"')).scalar_one()
        if maximum is None:
            connection.execute(text("SELECT setval(CAST(:sequence AS regclass), 1, false)"), {"sequence": seq})
        else:
            connection.execute(text("SELECT setval(CAST(:sequence AS regclass), :value, true)"),
                                {"sequence": seq, "value": maximum})


def run(path: Path, mode: str):
    if not path.is_file():
        raise MigrationError("source file does not exist or is not a regular file")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise MigrationError("source exceeds the supported single-transaction import size")
    source = _source(path)
    try:
        counts = _validate_source(source, _key())
        engine = create_engine(database_url("MIGRATION_DATABASE_URL"), pool_pre_ping=True,
                               pool_size=1, max_overflow=0, pool_timeout=10, echo=False)
        try:
            if mode in ("check", "verify"):
                with engine.connect() as connection:
                    _check_target(connection)
                    if mode == "check":
                        _validate_target_counts(connection, {}, empty=True)
                    else:
                        _validate_target_counts(connection, counts)
                        for name in BY_NAME:
                            if _source_digest(source, name) != _target_digest(connection, name):
                                raise MigrationError(f"parity digest mismatch for {name}")
            else:
                with engine.begin() as connection:
                    _check_target(connection)
                    _validate_target_counts(connection, {}, empty=True)
                    for model in TABLES:
                        name = model.__tablename__
                        columns = LEGACY_COLUMNS[name]
                        primary = ",".join(f'"{c}"' for c in model.__table__.primary_key.columns.keys())
                        cursor = source.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in columns)} FROM "{name}" ORDER BY {primary}')
                        while True:
                            rows = cursor.fetchmany(BATCH_SIZE)
                            if not rows:
                                break
                            values = [{column: row[column] for column in columns} for row in rows]
                            connection.execute(insert(model.__table__), values)
                        if counts[name] != _target_count(connection, name) or _source_digest(source, name) != _target_digest(connection, name):
                            raise MigrationError(f"import validation failed for {name}")
                    _validate_target_counts(connection, counts)
                    _set_sequences(connection)
        finally:
            engine.dispose()
        print(json.dumps({"mode": mode, "table_counts": counts, "validation": "passed",
                          "clerk_mapping_required": True, "legacy_sessions_authenticatable": False}, sort_keys=True))
    finally:
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preflight, import, or verify a private normalized SQLite snapshot.")
    parser.add_argument("--source", required=True, type=Path, help="existing SQLite snapshot; always opened read-only")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true", help="read-only source/target preflight")
    modes.add_argument("--import", dest="do_import", action="store_true", help="import into an empty migrated target")
    modes.add_argument("--verify", action="store_true", help="read-only count and canonical digest comparison")
    args = parser.parse_args(argv)
    mode = "check" if args.check else "import" if args.do_import else "verify"
    try:
        run(args.source, mode)
    except (MigrationError, OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"migration stopped: {exc}", file=sys.stderr)
        return 1
    except ValueError:
        print("migration stopped: invalid source data; row details withheld", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("migration stopped: database operation failed; connection details withheld", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
