import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from . import ai, auth, brand, config, creds
from .auth import current_user
from .config import PLATFORMS, validate_clerk_config
from .db import check_database_revision, dispose_engine, engine, session_scope
from .models import Campaign, Metric
from .platforms import make as make_platform
from . import whatsapp, creatives, videos, cutout, pages, goals, insights


@asynccontextmanager
async def lifespan(app):
    validate_clerk_config()
    await asyncio.to_thread(check_database_revision)
    await asyncio.to_thread(lambda: engine().connect().close())
    await asyncio.to_thread(videos.recover)
    followups = asyncio.create_task(whatsapp.followup_loop())
    results = asyncio.create_task(insights.sync_loop())
    try:
        yield
    finally:
        for task in (followups, results):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(dispose_engine)


app = FastAPI(title="youradds - open-source AI ads manager", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(creds.router)
app.include_router(brand.router)
app.include_router(whatsapp.hook)
app.include_router(whatsapp.router)
app.include_router(creatives.router)
app.include_router(videos.router)
app.include_router(cutout.router)


pages.register(app)


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Pages, scripts and styles carry no cache lifetime of their own; without this browsers reuse old copies for
    hours after a deploy (stale app.js showed the dashboard for new screens). 'no-cache' still allows 304 via ETag."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


Platform = Literal["meta", "tiktok", "google"]


class CampaignIn(BaseModel):
    platform: Platform
    name: str = Field(min_length=1)
    objective: Literal["sales", "traffic", "leads", "awareness", "whatsapp"] = "sales"
    daily_budget: float = Field(default_factory=lambda: 50000.0 if config.DEFAULT_CURRENCY == "COP" else 20.0, gt=0)
    headline: str = ""
    body: str = ""
    cta: str = "Más información"
    landing_url: str = ""
    audience: str = ""


class GoalIn(BaseModel):
    goal: Literal["awareness", "sales", "recover"]
    platform: Platform = "meta"
    product: str = ""                  # empty = the brand's first product
    daily_budget: float = Field(default_factory=lambda: 50000.0 if config.DEFAULT_CURRENCY == "COP" else 20.0, gt=0)
    landing_url: str = ""
    audience: str = ""
    tone: str = ""
    language: str = ""


class CopyIn(BaseModel):
    platform: Platform
    product: str = ""                  # empty = the brand's first product
    audience: str = ""                 # empty = the brand's ideal customer
    tone: str = ""                     # empty = the brand's tone
    n: int = Field(4, ge=1, le=8)
    language: str = ""                 # empty = the brand's language


class ApplyIn(BaseModel):
    campaign_id: int
    action: Literal["pause", "budget"]
    value: Optional[float] = None


class ChatIn(BaseModel):
    question: str


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()


def _campaigns(user_id: int, days: int = 7):
    cutoff = _cutoff(days)
    totals = (select(Metric.campaign_id.label("campaign_id"),
                     func.coalesce(func.sum(Metric.impressions), 0).label("impressions"),
                     func.coalesce(func.sum(Metric.clicks), 0).label("clicks"),
                     func.coalesce(func.sum(Metric.spend), 0).label("spend"),
                     func.coalesce(func.sum(Metric.conversions), 0).label("conversions"),
                     func.coalesce(func.sum(Metric.revenue), 0).label("revenue"))
               .where(Metric.day >= cutoff).group_by(Metric.campaign_id).subquery())
    columns = [getattr(Campaign, c.name) for c in Campaign.__table__.columns]
    statement = (select(*columns, totals.c.impressions, totals.c.clicks, totals.c.spend,
                        totals.c.conversions, totals.c.revenue)
                 .outerjoin(totals, totals.c.campaign_id == Campaign.id)
                 .where(Campaign.user_id == user_id).order_by(Campaign.id.desc()))
    with session_scope() as session:
        rows = [dict(r) for r in session.execute(statement).mappings()]
    for row in rows:
        for k in ("impressions", "clicks", "conversions"):
            row[k] = int(row[k] or 0)
        for k in ("spend", "revenue"):
            row[k] = float(row[k] or 0)
        kpis = ai.kpis(row)
        row.update(ctr=round(kpis["ctr"], 2), cpc=round(kpis["cpc"], 2), roas=round(kpis["roas"], 2),
                   cpa=round(kpis["cpa"], 2) if kpis["cpa"] else None)
    return rows


def _sync(user_id: int, platform: str, fn, *args):
    """Run a connector call if this user connected the platform; return error text instead of raising."""
    if not creds.connected(user_id, platform):
        return None, None
    try:
        return fn(make_platform(platform, creds.get(user_id, platform)), *args), None
    except Exception as e:
        return None, str(e)


def _own(session, cid: int, user_id: int):
    row = session.execute(select(*Campaign.__table__.columns)
                          .where(Campaign.id == cid, Campaign.user_id == user_id)).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "campaign not found")
    return dict(row)


@app.get("/api/health")
def health():
    return {"ok": True, "ai": ai.llm_available(), "currency": config.DEFAULT_CURRENCY}


@app.get("/api/campaigns")
def list_campaigns(days: int = 7, user: dict = Depends(current_user)):
    return _campaigns(user["id"], days)


@app.post("/api/campaigns/plan")
def plan_campaigns(body: GoalIn, user: dict = Depends(current_user)):
    """Preview only: the campaign structure for a goal. Nothing is created until the client posts each campaign."""
    b = brand.fill(user["id"], body, kind="copy")
    if body.language not in creatives.LANGS:
        raise HTTPException(400, "unsupported language")
    body.landing_url = creatives.normalize_link(body.landing_url)     # a WhatsApp number becomes a wa.me link
    variants = ai.generate_copy(body.platform, body.product, body.audience or "everyone", body.tone or "friendly", 4,
                                creatives.LANGS[body.language], body.language, brand.prompt_block(b))
    plan = goals.build_plan(body.goal, body.platform, body.product, body.daily_budget, b["ideal_customer"] if b else "",
                            variants, body.landing_url, cop=config.DEFAULT_CURRENCY == "COP", limits=ai.LIMITS[body.platform])
    return {**plan, "llm": ai.llm_available()}


@app.post("/api/campaigns", status_code=201)
def create_campaign(body: CampaignIn, user: dict = Depends(current_user)):
    ext, err = _sync(user["id"], body.platform, lambda p: p.create_campaign(body.model_dump()))
    demo = not ext and not creds.connected(user["id"], body.platform)
    with session_scope() as session:
        campaign = Campaign(user_id=user["id"], demo=int(demo), platform=body.platform, external_id=ext,
                            name=body.name, objective=body.objective, status="paused" if ext else "active",
                            daily_budget=body.daily_budget, headline=body.headline, body=body.body, cta=body.cta,
                            landing_url=body.landing_url, audience=body.audience)
        session.add(campaign)
        session.flush()
        cid = campaign.id
        if demo:
            from .db import synth_metrics
            synth_metrics(session, cid, body.daily_budget, 1.0)
    return {"id": cid, "external_id": ext, "sync_error": err}


@app.post("/api/campaigns/{cid}/status")
def set_status(cid: int, status: Literal["active", "paused"], user: dict = Depends(current_user)):
    with session_scope() as session:
        row = _own(session, cid, user["id"])
    err = None
    if row["external_id"]:
        _, err = _sync(user["id"], row["platform"], lambda p: p.set_status(row["external_id"], status))
    if not err:
        with session_scope() as session:
            session.execute(update(Campaign).where(Campaign.id == cid, Campaign.user_id == user["id"]).values(status=status))
    return {"ok": not err, "sync_error": err}


@app.delete("/api/campaigns/{cid}", status_code=204)
def delete_campaign(cid: int, user: dict = Depends(current_user)):
    with session_scope() as session:
        session.execute(Campaign.__table__.delete().where(Campaign.id == cid, Campaign.user_id == user["id"]))


@app.post("/api/connections/{platform}/import")
def import_campaigns(platform: Platform, user: dict = Depends(current_user)):
    items, err = _sync(user["id"], platform, lambda p: p.list_campaigns())
    if err or items is None:
        raise HTTPException(400, err or f"{platform} is not connected - add your credentials first")
    added = 0
    with session_scope() as session:
        for item in items:
            exists = session.scalar(select(Campaign.id).where(Campaign.platform == platform,
                                    Campaign.external_id == item["external_id"], Campaign.user_id == user["id"]).limit(1))
            if exists:
                continue
            session.add(Campaign(user_id=user["id"], platform=platform, external_id=item["external_id"],
                                 name=item["name"], status=item["status"], daily_budget=item["daily_budget"]))
            added += 1
    return {"imported": added}


@app.post("/api/connections/{platform}/sync-metrics")
def sync_metrics(platform: Platform, user: dict = Depends(current_user)):
    """Pull the last 30 days of real results (spend, clicks, sales) for this account's campaigns."""
    try:
        return insights.sync_user(user["id"], platform, days=30)
    except (NotImplementedError, LookupError) as e:
        raise HTTPException(400, str(e).strip("'\""))
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@app.get("/api/dashboard")
def dashboard(days: int = 14, user: dict = Depends(current_user)):
    cutoff = _cutoff(days)
    daily_query = (select(Metric.day, func.sum(Metric.impressions).label("impressions"),
                          func.sum(Metric.clicks).label("clicks"), func.sum(Metric.spend).label("spend"),
                          func.sum(Metric.conversions).label("conversions"), func.sum(Metric.revenue).label("revenue"))
                   .join(Campaign, Campaign.id == Metric.campaign_id)
                   .where(Campaign.user_id == user["id"], Metric.day >= cutoff)
                   .group_by(Metric.day).order_by(Metric.day))
    platform_query = (select(Campaign.platform, func.sum(Metric.spend).label("spend"),
                             func.sum(Metric.revenue).label("revenue"), func.sum(Metric.conversions).label("conversions"),
                             func.sum(Metric.clicks).label("clicks"), func.sum(Metric.impressions).label("impressions"))
                      .join(Metric, Metric.campaign_id == Campaign.id)
                      .where(Campaign.user_id == user["id"], Metric.day >= cutoff).group_by(Campaign.platform))
    with session_scope() as session:
        daily = [dict(r) for r in session.execute(daily_query).mappings()]
        by_platform = [dict(r) for r in session.execute(platform_query).mappings()]
        has_demo = session.scalar(select(Campaign.id).where(Campaign.user_id == user["id"], Campaign.demo == 1).limit(1)) is not None
    for row in daily + by_platform:
        for key in ("impressions", "clicks", "conversions"):
            if key in row:
                row[key] = int(row[key] or 0)
        for key in ("spend", "revenue"):
            if key in row:
                row[key] = float(row[key] or 0)
    totals = {k: sum(d[k] or 0 for d in daily) for k in ("impressions", "clicks", "spend", "conversions", "revenue")}
    totals.update(ai.kpis(totals))
    return {"totals": totals, "daily": daily, "by_platform": by_platform, "has_demo": has_demo}


@app.delete("/api/demo")
def clear_demo(user: dict = Depends(current_user)):
    with session_scope() as session:
        result = session.execute(Campaign.__table__.delete().where(Campaign.user_id == user["id"], Campaign.demo == 1))
        removed = result.rowcount
    return {"removed": removed}


@app.post("/api/ai/copy")
def ai_copy(body: CopyIn, user: dict = Depends(current_user)):
    b = brand.fill(user["id"], body, kind="copy")
    if body.language not in creatives.LANGS:
        raise HTTPException(400, "unsupported language")
    return {"variants": ai.generate_copy(body.platform, body.product, body.audience or "everyone", body.tone or "friendly",
                                         body.n, creatives.LANGS[body.language], body.language, brand.prompt_block(b)),
            "llm": ai.llm_available()}


@app.get("/api/ai/recommendations")
def recommendations(user: dict = Depends(current_user)):
    return ai.recommend(_campaigns(user["id"], 7))


@app.post("/api/ai/apply")
def apply(body: ApplyIn, user: dict = Depends(current_user)):
    with session_scope() as session:
        row = _own(session, body.campaign_id, user["id"])
    err = None
    if body.action == "pause":
        if row["external_id"]:
            _, err = _sync(user["id"], row["platform"], lambda p: p.set_status(row["external_id"], "paused"))
        values = {"status": "paused"}
    else:
        if body.value is None or body.value <= 0:
            raise HTTPException(400, "budget value required")
        if row["external_id"]:
            _, err = _sync(user["id"], row["platform"], lambda p: p.set_budget(row["external_id"], body.value))
        values = {"daily_budget": body.value}
    if not err:
        with session_scope() as session:
            session.execute(update(Campaign).where(Campaign.id == body.campaign_id,
                                                   Campaign.user_id == user["id"]).values(**values))
    return {"ok": not err, "sync_error": err}


@app.post("/api/chat")
def chat(body: ChatIn, user: dict = Depends(current_user)):
    campaigns = _campaigns(user["id"], 7)
    totals = {k: sum(c[k] for c in campaigns) for k in ("spend", "revenue")}
    totals["roas"] = totals["revenue"] / totals["spend"] if totals["spend"] else 0
    slim = [{k: c[k] for k in ("name", "platform", "status", "daily_budget", "spend", "ctr", "cpa", "roas")} for c in campaigns]
    return {"answer": ai.chat(body.question, {"totals": totals, "campaigns": slim}, brand.prompt_block(brand.get(user["id"])))}


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
