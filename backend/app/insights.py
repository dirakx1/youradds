"""Real results for connected ad accounts: pulls daily per-campaign numbers from the platform and stores them in the
same metrics table the Panel, Optimizer and Copilot already read. Only campaigns the app already knows (created in the
Estudio or imported) are filled; demo campaigns keep their sample numbers."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import config, creds
from .db import session_scope
from .models import Campaign, Credential, Metric
from .platforms import make as make_platform

log = logging.getLogger("yourads.insights")
SYNCED = ("meta",)                       # platforms with a results connector
LOOP_SECONDS = int(config.env("INSIGHTS_SYNC_SECONDS") or 3 * 3600)
RESYNC_DAYS = 7                          # Meta revises conversions for days after the click, so recent days are re-pulled


def sync_user(user_id: int, platform: str = "meta", days: int = RESYNC_DAYS) -> dict:
    """Pull the last ``days`` days and upsert them. Raises on platform errors (callers decide how to show them)."""
    if platform not in SYNCED:
        raise NotImplementedError(f"{platform} results sync is not available yet")
    if not creds.connected(user_id, platform):
        raise LookupError(f"{platform} is not connected - add your credentials first")
    today = datetime.now(timezone.utc).date()
    rows = make_platform(platform, creds.get(user_id, platform)).insights(
        (today - timedelta(days=days - 1)).isoformat(), today.isoformat())
    saved, unmatched = 0, set()
    with session_scope() as session:
        ids = dict(session.execute(select(Campaign.external_id, Campaign.id).where(
            Campaign.user_id == user_id, Campaign.platform == platform, Campaign.external_id.is_not(None))).all())
        for r in rows:
            cid = ids.get(r["external_id"])
            if cid is None:
                unmatched.add(r["external_id"])
                continue
            vals = {k: r[k] for k in ("impressions", "clicks", "spend", "conversions", "revenue")}
            stmt = insert(Metric).values(campaign_id=cid, day=r["day"], **vals)
            session.execute(stmt.on_conflict_do_update(index_elements=[Metric.campaign_id, Metric.day], set_=vals))
            saved += 1
    currency = next((r["currency"] for r in rows if r["currency"]), "")
    return {"days": saved, "campaigns": len(ids), "unmatched": len(unmatched), "currency": currency,
            "currency_mismatch": bool(currency) and currency != config.DEFAULT_CURRENCY}


def run_all() -> int:
    """Every account with a connected results platform; one failing account never stops the others."""
    with session_scope() as session:
        users = [r for r in session.execute(select(Credential.user_id, Credential.service)
                                            .where(Credential.service.in_(SYNCED))).all()]
    done = 0
    for user_id, platform in users:
        try:
            if creds.connected(user_id, platform):
                sync_user(user_id, platform)
                done += 1
        except Exception as e:                       # token expired, rate limit, network: try again next round
            log.warning("results sync failed for user %s (%s): %s", user_id, platform, e)
    return done


async def sync_loop():
    await asyncio.sleep(90)
    while True:
        try:
            await asyncio.to_thread(run_all)
        except Exception:
            log.exception("results sync round failed")
        await asyncio.sleep(LOOP_SECONDS)
