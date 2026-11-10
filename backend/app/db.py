"""PostgreSQL engine, short-lived ORM sessions, and demo data helpers."""
import random
from contextlib import contextmanager
from datetime import date, timedelta
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from .config import DEFAULT_CURRENCY, database_url
from .models import Campaign, Metric, SCHEMA


@lru_cache(maxsize=1)
def engine():
    return create_engine(database_url(), pool_size=5, max_overflow=5, pool_timeout=15,
                         pool_pre_ping=True, pool_recycle=1800, connect_args={"connect_timeout": 10}, echo=False)


@lru_cache(maxsize=1)
def _sessions():
    return sessionmaker(bind=engine(), expire_on_commit=False)


@contextmanager
def session_scope():
    """One transaction and one session for a single request/helper unit of work."""
    with _sessions().begin() as session:
        yield session


def dispose_engine():
    if engine.cache_info().currsize:
        engine().dispose()
        _sessions.cache_clear()
        engine.cache_clear()


def check_database_revision() -> None:
    """Fail closed unless the application schema is at the single required Alembic head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(root / "alembic"))
    expected = ScriptDirectory.from_config(alembic_config).get_current_head()
    with engine().connect() as connection:
        schema = connection.execute(text("SELECT to_regnamespace(:schema)"), {"schema": SCHEMA}).scalar()
        if schema is None:
            raise RuntimeError("Database schema is not migrated; run Alembic upgrade before startup")
        version_table = connection.execute(
            text("SELECT to_regclass(:table)"), {"table": f"{SCHEMA}.alembic_version"}).scalar()
        if version_table is None:
            raise RuntimeError("Database schema is not migrated; run Alembic upgrade before startup")
        current = connection.execute(text(f'SELECT version_num FROM "{SCHEMA}".alembic_version')).scalar()
        if current != expected:
            raise RuntimeError("Database migration revision is not current; run Alembic upgrade before startup")


# Sample data is written in USD-sized numbers and scaled to the display currency (1 USD ~ 4000 COP).
DEMO_UNIT = 4000 if DEFAULT_CURRENCY == "COP" else 1


def synth_metrics(session, campaign_id: int, budget: float, quality: float, days: int = 14):
    """``budget`` is in the display currency; CPM and order value are scaled to match."""
    unit = DEMO_UNIT
    rnd = random.Random(campaign_id)
    values = []
    for i in range(days):
        day = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        spend = budget * rnd.uniform(0.75, 1.0)
        cpm = rnd.uniform(6, 14) * unit
        imps = int(spend / cpm * 1000)
        clicks = int(imps * rnd.uniform(0.008, 0.025) * quality)
        conv = int(clicks * rnd.uniform(0.02, 0.06) * quality)
        rev = conv * rnd.uniform(28, 60) * unit
        values.append({"campaign_id": campaign_id, "day": day, "impressions": imps, "clicks": clicks,
                       "spend": round(spend, 2), "conversions": conv, "revenue": round(rev, 2)})
    stmt = insert(Metric.__table__).values(values)
    stmt = stmt.on_conflict_do_update(index_elements=[Metric.campaign_id, Metric.day], set_={
        "impressions": stmt.excluded.impressions, "clicks": stmt.excluded.clicks,
        "spend": stmt.excluded.spend, "conversions": stmt.excluded.conversions,
        "revenue": stmt.excluded.revenue,
    })
    session.execute(stmt)


def seed_demo(session, user_id: int):
    demo = [
        ("meta", "Día de la Madre - Público similar", "sales", 50, 1.3),
        ("meta", "Retargeting - Carrito abandonado", "sales", 30, 1.6),
        ("tiktok", "Video de clientes - Gen Z", "traffic", 40, 0.7),
        ("tiktok", "Spark Ads - Más vendidos", "sales", 35, 1.1),
        ("google", "Búsqueda - Nombre de la marca", "sales", 25, 1.8),
        ("google", "Performance Max - Todos los productos", "sales", 60, 0.5),
    ]
    for platform, name, objective, usd_budget, quality in demo:
        budget = usd_budget * DEMO_UNIT
        campaign = Campaign(user_id=user_id, demo=1, platform=platform, name=name, objective=objective,
                            daily_budget=budget, headline="Conoce la nueva colección",
                            body="Oferta por tiempo limitado. Envío gratis hoy.", cta="Comprar ahora",
                            landing_url="https://example.com", audience="Mujeres 25-44, Colombia")
        session.add(campaign)
        session.flush()
        synth_metrics(session, campaign.id, budget, quality)
