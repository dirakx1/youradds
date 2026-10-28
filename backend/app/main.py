import asyncio
from pathlib import Path
from typing import Literal, Optional
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, auth, billing, creds
from .auth import current_user
from .config import PLATFORMS
from .db import conn, init_db, synth_metrics
from .platforms import make as make_platform
from . import whatsapp, creatives, videos, cutout

app = FastAPI(title="yourads - open-source AI ads manager")
app.include_router(auth.router)
app.include_router(creds.router)
app.include_router(billing.router)
app.include_router(billing.admin)
app.include_router(whatsapp.hook)
app.include_router(whatsapp.router)
app.include_router(creatives.router)
app.include_router(videos.router)
app.include_router(cutout.router)


@app.exception_handler(billing.LimitExceeded)
async def _limit(_: Request, e: billing.LimitExceeded):
    return JSONResponse({"detail": str(e), "upgrade": True}, status_code=402)


Platform = Literal["meta", "tiktok", "google"]


@app.on_event("startup")
async def _startup():
    init_db()
    videos.recover()
    asyncio.create_task(whatsapp.followup_loop())


class CampaignIn(BaseModel):
    platform: Platform
    name: str = Field(min_length=1)
    objective: Literal["sales", "traffic", "leads", "awareness", "whatsapp"] = "sales"
    daily_budget: float = Field(20, gt=0)
    headline: str = ""
    body: str = ""
    cta: str = "Learn More"
    landing_url: str = ""
    audience: str = ""


class CopyIn(BaseModel):
    platform: Platform
    product: str
    audience: str = "everyone"
    tone: str = "friendly"
    n: int = Field(4, ge=1, le=8)


class ApplyIn(BaseModel):
    campaign_id: int
    action: Literal["pause", "budget"]
    value: Optional[float] = None


class ChatIn(BaseModel):
    question: str


AGG = """SELECT c.*, COALESCE(SUM(m.impressions),0) impressions, COALESCE(SUM(m.clicks),0) clicks,
 COALESCE(SUM(m.spend),0) spend, COALESCE(SUM(m.conversions),0) conversions, COALESCE(SUM(m.revenue),0) revenue
 FROM campaigns c LEFT JOIN metrics m ON m.campaign_id=c.id AND m.day >= date('now', ?)
 WHERE c.user_id=? GROUP BY c.id ORDER BY c.id DESC"""


def _campaigns(user_id: int, days: int = 7):
    with conn() as c:
        rows = [dict(r) for r in c.execute(AGG, (f"-{days - 1} days", user_id))]
    for r in rows:
        k = ai.kpis(r)
        r.update(ctr=round(k["ctr"], 2), cpc=round(k["cpc"], 2), roas=round(k["roas"], 2),
                 cpa=round(k["cpa"], 2) if k["cpa"] else None)
    return rows


def _sync(user_id: int, platform: str, fn, *args):
    """Run a connector call if this user connected the platform; return error text instead of raising."""
    if not creds.connected(user_id, platform):
        return None, None
    try:
        return fn(make_platform(platform, creds.get(user_id, platform)), *args), None
    except Exception as e:
        return None, str(e)


def _own(c, cid: int, user_id: int):
    row = c.execute("SELECT * FROM campaigns WHERE id=? AND user_id=?", (cid, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "campaign not found")
    return row


@app.get("/api/health")
def health():
    return {"ok": True, "ai": ai.llm_available()}


@app.get("/api/campaigns")
def list_campaigns(days: int = 7, user: dict = Depends(current_user)):
    return _campaigns(user["id"], days)


@app.post("/api/campaigns", status_code=201)
def create_campaign(body: CampaignIn, user: dict = Depends(current_user)):
    ext, err = _sync(user["id"], body.platform, lambda p: p.create_campaign(body.model_dump()))
    demo = not ext and not creds.connected(user["id"], body.platform)   # not connected: sample data only
    with conn() as c:
        cur = c.execute(
            "INSERT INTO campaigns (user_id,demo,platform,external_id,name,objective,status,daily_budget,headline,body,cta,landing_url,audience)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], int(demo), body.platform, ext, body.name, body.objective, "paused" if ext else "active", body.daily_budget,
             body.headline, body.body, body.cta, body.landing_url, body.audience))
        cid = cur.lastrowid
        if demo:  # not connected: synthesize performance so the dashboards are populated
            synth_metrics(c, cid, body.daily_budget, 1.0)
    return {"id": cid, "external_id": ext, "sync_error": err}


@app.post("/api/campaigns/{cid}/status")
def set_status(cid: int, status: Literal["active", "paused"], user: dict = Depends(current_user)):
    with conn() as c:
        row = _own(c, cid, user["id"])
        err = None
        if row["external_id"]:
            _, err = _sync(user["id"], row["platform"], lambda p: p.set_status(row["external_id"], status))
        if not err:
            c.execute("UPDATE campaigns SET status=? WHERE id=?", (status, cid))
    return {"ok": not err, "sync_error": err}


@app.delete("/api/campaigns/{cid}", status_code=204)
def delete_campaign(cid: int, user: dict = Depends(current_user)):
    with conn() as c:
        c.execute("DELETE FROM campaigns WHERE id=? AND user_id=?", (cid, user["id"]))


@app.post("/api/connections/{platform}/import")
def import_campaigns(platform: Platform, user: dict = Depends(current_user)):
    """Pull existing campaigns from a live ad account into the local store."""
    items, err = _sync(user["id"], platform, lambda p: p.list_campaigns())
    if err or items is None:
        raise HTTPException(400, err or f"{platform} is not connected - add your credentials first")
    added = 0
    with conn() as c:
        for i in items:
            if c.execute("SELECT 1 FROM campaigns WHERE platform=? AND external_id=? AND user_id=?",
                         (platform, i["external_id"], user["id"])).fetchone():
                continue
            c.execute("INSERT INTO campaigns (user_id,platform,external_id,name,status,daily_budget) VALUES (?,?,?,?,?,?)",
                      (user["id"], platform, i["external_id"], i["name"], i["status"], i["daily_budget"]))
            added += 1
    return {"imported": added}


@app.get("/api/dashboard")
def dashboard(days: int = 14, user: dict = Depends(current_user)):
    with conn() as c:
        daily = [dict(r) for r in c.execute(
            "SELECT m.day, SUM(m.impressions) impressions, SUM(m.clicks) clicks, SUM(m.spend) spend,"
            " SUM(m.conversions) conversions, SUM(m.revenue) revenue FROM metrics m JOIN campaigns c ON c.id=m.campaign_id"
            " WHERE c.user_id=? AND m.day >= date('now', ?) GROUP BY m.day ORDER BY m.day", (user["id"], f"-{days - 1} days"))]
        by_platform = [dict(r) for r in c.execute(
            "SELECT c.platform, SUM(m.spend) spend, SUM(m.revenue) revenue, SUM(m.conversions) conversions,"
            " SUM(m.clicks) clicks, SUM(m.impressions) impressions FROM metrics m JOIN campaigns c ON c.id=m.campaign_id"
            " WHERE c.user_id=? AND m.day >= date('now', ?) GROUP BY c.platform", (user["id"], f"-{days - 1} days"))]
        has_demo = bool(c.execute("SELECT 1 FROM campaigns WHERE user_id=? AND demo=1 LIMIT 1", (user["id"],)).fetchone())
    tot = {k: sum(d[k] or 0 for d in daily) for k in ("impressions", "clicks", "spend", "conversions", "revenue")}
    tot.update(ai.kpis(tot))
    return {"totals": tot, "daily": daily, "by_platform": by_platform, "has_demo": has_demo}


@app.delete("/api/demo")
def clear_demo(user: dict = Depends(current_user)):
    """Remove the synthetic sample campaigns (and their metrics) so only real data remains."""
    with conn() as c:
        n = c.execute("DELETE FROM campaigns WHERE user_id=? AND demo=1", (user["id"],)).rowcount
    return {"removed": n}


@app.post("/api/ai/copy")
def ai_copy(body: CopyIn, user: dict = Depends(current_user)):
    return {"variants": ai.generate_copy(body.platform, body.product, body.audience, body.tone, body.n),
            "llm": ai.llm_available()}


@app.get("/api/ai/recommendations")
def recommendations(user: dict = Depends(current_user)):
    return ai.recommend(_campaigns(user["id"], 7))


@app.post("/api/ai/apply")
def apply(body: ApplyIn, user: dict = Depends(current_user)):
    with conn() as c:
        row = _own(c, body.campaign_id, user["id"])
        err = None
        if body.action == "pause":
            if row["external_id"]:
                _, err = _sync(user["id"], row["platform"], lambda p: p.set_status(row["external_id"], "paused"))
            sql, args = "UPDATE campaigns SET status='paused' WHERE id=?", (body.campaign_id,)
        else:
            if body.value is None or body.value <= 0:
                raise HTTPException(400, "budget value required")
            if row["external_id"]:
                _, err = _sync(user["id"], row["platform"], lambda p: p.set_budget(row["external_id"], body.value))
            sql, args = "UPDATE campaigns SET daily_budget=? WHERE id=?", (body.value, body.campaign_id)
        if not err:
            c.execute(sql, args)
    return {"ok": not err, "sync_error": err}


@app.post("/api/chat")
def chat(body: ChatIn, user: dict = Depends(current_user)):
    cs = _campaigns(user["id"], 7)
    tot = {k: sum(c[k] for c in cs) for k in ("spend", "revenue")}
    tot["roas"] = tot["revenue"] / tot["spend"] if tot["spend"] else 0
    slim = [{k: c[k] for k in ("name", "platform", "status", "daily_budget", "spend", "ctr", "cpa", "roas")} for c in cs]
    return {"answer": ai.chat(body.question, {"totals": tot, "campaigns": slim})}


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
