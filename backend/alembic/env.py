from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import database_url
from app.models import Base, SCHEMA

config = context.config
target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == SCHEMA
    return parent_names.get("schema_name") == SCHEMA


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return getattr(obj, "schema", None) == SCHEMA
    table = getattr(obj, "table", None)
    if table is not None:
        return table.schema == SCHEMA
    return True


def run_migrations_offline():
    context.configure(url=database_url("MIGRATION_DATABASE_URL"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"}, include_schemas=True,
                      include_name=include_name, include_object=include_object, version_table_schema=SCHEMA)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connection = config.attributes.get("connection")
    if connection is None:
        cfg = config.get_section(config.config_ini_section) or {}
        cfg["sqlalchemy.url"] = database_url("MIGRATION_DATABASE_URL")
        connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
        try:
            # This outer transaction owns preflight reads, bootstrap DDL, and every
            # Alembic operation so the Connection context cannot roll them back.
            with connectable.begin() as conn:
                _run(conn)
        finally:
            connectable.dispose()
    else:
        _run(connection)


def _run(connection):
    exists = connection.execute(text("SELECT to_regnamespace(:schema)"), {"schema": SCHEMA}).scalar() is not None
    version = connection.execute(text("SELECT to_regclass(:table)"),
                                 {"table": f"{SCHEMA}.alembic_version"}).scalar() if exists else None
    if exists and version is None:
        raise RuntimeError("Refusing to migrate an existing yourads schema without Alembic history")
    if not exists:
        command = getattr(getattr(config, "cmd_opts", None), "cmd", None)
        command_name = getattr(command, "__name__", "upgrade")
        if command_name not in ("upgrade",):
            raise RuntimeError("Application schema is not initialized; run Alembic upgrade head")
        connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
        connection.execute(text(f'REVOKE ALL ON SCHEMA "{SCHEMA}" FROM PUBLIC'))
    context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True,
                      include_name=include_name, include_object=include_object,
                      version_table="alembic_version", version_table_schema=SCHEMA,
                      compare_type=True, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
