"""Publish a campaign's attached creatives to Meta as a real ad: ad set + one ad whose creative picks the right
image per placement. Everything is created PAUSED; money only moves after an explicit activation. Each step's id is
saved on the campaign, so a failure part-way is resumed, never duplicated."""
import json
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from . import config, creds, fx
from .auth import current_user
from .creatives import CREATIVES_DIR
from .db import session_scope
from .models import Campaign, Creative
from .platforms import make as make_platform
from .platforms.meta import minor_per_unit

router = APIRouter(prefix="/api/campaigns", dependencies=[Depends(current_user)])
conn_router = APIRouter(prefix="/api/connections/meta", dependencies=[Depends(current_user)])

# placement -> (publisher platform, targeting key, Meta position, image group)
PLACEMENTS = {
    "facebook_feed": ("facebook", "facebook_positions", "feed", "feed"),
    "facebook_story": ("facebook", "facebook_positions", "story", "story"),
    "facebook_reels": ("facebook", "facebook_positions", "facebook_reels", "story"),
    "instagram_feed": ("instagram", "instagram_positions", "stream", "feed"),
    "instagram_story": ("instagram", "instagram_positions", "story", "story"),
    "instagram_reels": ("instagram", "instagram_positions", "reels", "story"),
}
DEFAULT_PLACEMENTS = ["facebook_feed", "instagram_feed", "facebook_story", "instagram_story"]

FRIENDLY = {   # Meta error sub-codes -> what to do about it
    "1885183": "Your Meta app is still in development mode. In Meta for Developers, switch the app to Live, then try again.",
    "2446886": "Your Facebook Page is not linked to a WhatsApp Business account. Link it in Meta (Page settings, Linked accounts), "
               "or choose 'Website' as the destination.",
    "1487246": "That WhatsApp number is not linked to your Meta account.",
    "3858385": "Meta wants you to verify your account before it allows new ads. Open Ads Manager, follow the "
               "'Authenticate your account' prompt, then try again.",
}


def friendly(msg: str) -> str:
    m = re.search(r"\[(\d+)\]\s*$", msg)
    return FRIENDLY.get(m.group(1), re.sub(r"\s*\[\d+\]\s*$", "", msg)) if m else msg


class PublishIn(BaseModel):
    destination: Literal["website", "whatsapp"] = "website"
    placements: list[str] = Field(default_factory=lambda: list(DEFAULT_PLACEMENTS))
    countries: list[str] = Field(default_factory=lambda: ["CO"], max_length=10)
    age_min: int = Field(18, ge=13, le=65)
    age_max: int = Field(65, ge=13, le=65)
    daily_budget: Optional[float] = Field(None, gt=0, le=100_000_000)     # in the app currency; blank = the campaign's own
    validate_only: bool = False


def cta_type(label: str, destination: str) -> str:
    """The free-text button -> one of Meta's button types."""
    if destination == "whatsapp":
        return "WHATSAPP_MESSAGE"
    t = (label or "").lower()
    for words, kind in ((("compra", "comprar", "shop", "buy"), "SHOP_NOW"), (("pide", "pedir", "ordena", "order"), "ORDER_NOW"),
                        (("registr", "sign up", "inscrib"), "SIGN_UP"), (("contact", "llama", "escrib"), "CONTACT_US")):
        if any(w in t for w in words):
            return kind
    return "LEARN_MORE"


def whatsapp_number(url: str) -> str:
    m = re.match(r"^https?://(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{7,15})", url or "")
    return m.group(1) if m else ""


def _campaign(cid: int, user_id: int) -> dict:
    with session_scope() as s:
        row = s.execute(select(*Campaign.__table__.columns).where(Campaign.id == cid, Campaign.user_id == user_id)).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "campaign not found")
    return dict(row)


def _state(row: dict) -> dict:
    try:
        return json.loads(row.get("publish") or "{}")
    except ValueError:
        return {}


def _save_state(cid: int, user_id: int, state: dict):
    with session_scope() as s:
        s.execute(update(Campaign).where(Campaign.id == cid, Campaign.user_id == user_id).values(publish=json.dumps(state)))


def _images(cid: int, user_id: int) -> dict:
    """Attached Meta creatives by format -> {format: {id, file, image_hash}}."""
    with session_scope() as s:
        rows = s.execute(select(Creative.id, Creative.format, Creative.file, Creative.image_hash)
                         .where(Creative.campaign_id == cid, Creative.user_id == user_id, Creative.platform == "meta")
                         .order_by(Creative.id)).mappings().all()
    return {r["format"]: dict(r) for r in rows}            # the newest creative of each format wins


def pick_group_images(images: dict) -> dict:
    """Image group -> creative: feed uses the 4:5 portrait (falls back to the square), stories/reels the 9:16."""
    return {"feed": images.get("feed_portrait") or images.get("feed_square"), "story": images.get("story_reel")}


def _targeting(opts: PublishIn) -> dict:
    t = {"geo_locations": {"countries": [c.upper() for c in opts.countries]}, "age_min": opts.age_min,
         "age_max": opts.age_max, "targeting_automation": {"advantage_audience": 1}}
    plats = []
    for name in opts.placements:
        pub, key, pos, _ = PLACEMENTS[name]
        if pub not in plats:
            plats.append(pub)
        t.setdefault(key, [])
        if pos not in t[key]:
            t[key].append(pos)
    t["publisher_platforms"] = plats
    return t


def _rules(opts: PublishIn) -> list[dict]:
    """One customization rule per image group: those placements show that group's image."""
    out = []
    for group in ("feed", "story"):
        spec = {}
        for name in opts.placements:
            pub, key, pos, g = PLACEMENTS[name]
            if g != group:
                continue
            spec.setdefault("publisher_platforms", [])
            if pub not in spec["publisher_platforms"]:
                spec["publisher_platforms"].append(pub)
            spec.setdefault(key, []).append(pos)
        if spec:
            out.append({"label": group, "spec": spec})
    return out


def inspect(user_id: int, cid: int, opts: PublishIn) -> dict:
    """Everything the screen shows before publishing: ordered checks (ok / warn / block) and the money."""
    row = _campaign(cid, user_id)
    if opts.daily_budget:
        row["daily_budget"] = opts.daily_budget
    state = _state(row)
    checks = []

    def add(level, key, msg):
        checks.append({"level": level, "key": key, "message": msg})
    meta_c = creds.get(user_id, "meta")
    connected = creds.connected(user_id, "meta")
    p = make_platform("meta", meta_c) if connected else None
    if row["platform"] != "meta":
        add("block", "platform", "Only Meta campaigns can be published here.")
    if not connected:
        add("block", "connection", "Connect Meta first (Connections).")
    if not row["external_id"]:
        add("block", "campaign", "This campaign is not in Meta yet. Recreate it so it is created there first.")
    if state.get("ad_id"):
        add("ok", "published", "Already published to Meta (paused until you activate it).")
    bad = [n for n in opts.placements if n not in PLACEMENTS]
    if bad or not opts.placements:
        add("block", "placements", "Choose at least one valid placement.")
    if opts.age_min > opts.age_max:
        add("block", "audience", "The minimum age is higher than the maximum.")
    if not opts.countries:
        add("block", "audience", "Choose at least one country.")
    # Page and Instagram
    page_id, ig, pages = (meta_c.get("page_id") or "").strip(), None, []
    if p:
        try:
            pages = p.pages()
        except Exception as e:
            add("warn", "pages", f"Could not read your Pages from Meta: {friendly(str(e))}")
    page = next((x for x in pages if x["id"] == page_id), None)
    if not page_id.isdigit() or (pages and not page):
        add("block", "page", "Set your Facebook Page: press 'Detect my Page and Instagram' on Connections.")
    elif page:
        add("ok", "page", f"Facebook Page: {page['name']}")
        ig = page["instagram"]
    if page_id.isdigit() and not ig and any(PLACEMENTS.get(n, ("",))[0] == "instagram" for n in opts.placements):
        add("warn", "instagram", "No Instagram account is linked to this Page. Link one in Meta Business Settings if "
            "Meta rejects the Instagram placements.")
    # creatives and images
    groups = pick_group_images(_images(cid, user_id))
    needed = {PLACEMENTS[n][3] for n in opts.placements if n in PLACEMENTS}
    for g, label in (("feed", "a feed image (4:5 or square)"), ("story", "a story/reel image (9:16)")):
        if g in needed and not groups.get(g):
            add("block", f"image_{g}", f"Attach {label} to this campaign. The Studio makes all three formats.")
    # text and link
    if not (row["headline"] or "").strip() or not (row["body"] or "").strip():
        add("block", "text", "The campaign needs an ad title and ad text. Attach a creative to copy them.")
    link = (row["landing_url"] or "").strip()
    if opts.destination == "website" and not re.match(r"^https?://", link):
        add("block", "link", "The button needs a web page or a WhatsApp link.")
    if opts.destination == "whatsapp" and not whatsapp_number(link):
        add("block", "whatsapp", "WhatsApp ads need a WhatsApp number as the button link (https://wa.me/57…).")
    # money
    money = {"app_amount": row["daily_budget"], "app_currency": config.DEFAULT_CURRENCY}
    if p:
        try:
            cur = p.account_currency()
            minor = p.to_minor(row["daily_budget"])
            unit = minor_per_unit(cur)
            money.update(account_currency=cur, account_amount=round(minor / unit, 2),
                         rate=round(fx.rate(config.DEFAULT_CURRENCY, cur), 8))
            floor = p.min_daily_budget()
            if floor is not None:
                money["minimum"] = round(floor / unit, 2)
                if minor < floor:
                    add("block", "budget", f"Meta's minimum daily budget for this account is {money['minimum']} {cur}.")
        except Exception as e:
            add("block", "budget", friendly(str(e)))
    if not row["daily_budget"] or row["daily_budget"] <= 0:
        add("block", "budget", "Set a daily budget on the campaign first.")
    return {"checks": checks, "ok": not any(c["level"] == "block" for c in checks), "money": money, "state": state,
            "options": opts.model_dump(), "placements": list(PLACEMENTS), "page": {"id": page_id, "ig": ig},
            "images": {g: (groups[g]["format"] if groups.get(g) else None) for g in sorted(needed)}}


@router.post("/{cid}/publish/check")
def check(cid: int, body: PublishIn, user: dict = Depends(current_user)):
    return inspect(user["id"], cid, body)


@router.post("/{cid}/publish")
def publish(cid: int, body: PublishIn, user: dict = Depends(current_user)):
    report = inspect(user["id"], cid, body)
    if not report["ok"]:
        raise HTTPException(400, "; ".join(c["message"] for c in report["checks"] if c["level"] == "block"))
    row = _campaign(cid, user["id"])
    if body.daily_budget:
        row["daily_budget"] = body.daily_budget
    state = _state(row)
    p = make_platform("meta", creds.get(user["id"], "meta"))
    meta_c = creds.get(user["id"], "meta")
    page = next((x for x in p.pages() if x["id"] == meta_c["page_id"]), None)
    steps = []

    def run(name, fn):
        try:
            out = fn()
            steps.append({"step": name, "ok": True})
            return out
        except HTTPException:
            raise
        except Exception as e:
            _save_state(cid, user["id"], state)            # keep what already worked so a retry resumes here
            raise HTTPException(400, f"{name}: {friendly(str(e))}")

    groups = pick_group_images(_images(cid, user["id"]))
    needed = {PLACEMENTS[n][3] for n in body.placements}
    hashes = {}
    for g in needed:                                          # 1. images (uploaded once per creative)
        cr = groups[g]
        def up(cr=cr):
            if not cr["image_hash"]:
                h = p.upload_image(cr["file"], (CREATIVES_DIR / cr["file"]).read_bytes())
                with session_scope() as s:
                    s.execute(update(Creative).where(Creative.id == cr["id"]).values(image_hash=h))
                cr["image_hash"] = h
            return cr["image_hash"]
        hashes[g] = run(f"image ({g})", up)
    link = row["landing_url"]
    adset_spec = {"name": f"{row['name']} · {body.destination}"[:100], "daily_budget": row["daily_budget"],
                  "destination": body.destination, "targeting": _targeting(body), "page_id": page["id"],
                  "whatsapp_number": whatsapp_number(link)}
    creative_spec = {"name": row["name"][:100], "page_id": page["id"], "instagram_id": (page["instagram"] or {}).get("id"),
                     "images": hashes, "rules": _rules(body), "body": row["body"], "title": row["headline"], "link": link,
                     "cta": cta_type(row["cta"], body.destination), "destination": body.destination}
    if body.validate_only:                                    # ask Meta to check, create nothing
        run("ad set (check)", lambda: p.create_adset(row["external_id"], adset_spec, validate=True))
        run("creative (check)", lambda: p.create_creative(creative_spec, validate=True))
        return {"validated": True, "steps": steps}
    state["options"] = body.model_dump()
    if body.daily_budget:                                     # the campaign keeps the budget the ad set was made with
        with session_scope() as sess:
            sess.execute(update(Campaign).where(Campaign.id == cid, Campaign.user_id == user["id"]).values(daily_budget=body.daily_budget))
    if not state.get("adset_id"):
        state["adset_id"] = run("ad set", lambda: p.create_adset(row["external_id"], adset_spec))
        _save_state(cid, user["id"], state)
    if not state.get("creative_id"):
        state["creative_id"] = run("creative", lambda: p.create_creative(creative_spec))
        _save_state(cid, user["id"], state)
    if not state.get("ad_id"):
        state["ad_id"] = run("ad", lambda: p.create_ad(row["name"][:100], state["adset_id"], state["creative_id"]))
    _save_state(cid, user["id"], state)
    return {"published": True, "steps": steps, "state": state}


def activate_objects(p, state: dict, external_id: str):
    """Ad, ad set and campaign all ACTIVE: any one left paused means nothing delivers."""
    for oid in (state.get("ad_id"), state.get("adset_id"), external_id):
        if oid:
            p._call("POST", oid, status="ACTIVE")


@router.post("/{cid}/activate")
def activate(cid: int, user: dict = Depends(current_user)):
    row = _campaign(cid, user["id"])
    state = _state(row)
    if not state.get("ad_id"):
        raise HTTPException(400, "Publish the ad to Meta first.")
    p = make_platform("meta", creds.get(user["id"], "meta"))
    try:
        activate_objects(p, state, row["external_id"])
    except Exception as e:
        raise HTTPException(400, friendly(str(e)))
    with session_scope() as s:
        s.execute(update(Campaign).where(Campaign.id == cid, Campaign.user_id == user["id"]).values(status="active"))
    return {"ok": True}


@conn_router.post("/detect")
def detect(user: dict = Depends(current_user)):
    """Read the Facebook Pages (and linked Instagram accounts) this token can use. A single Page is saved right away."""
    c = creds.get(user["id"], "meta")
    if not creds.connected(user["id"], "meta"):
        raise HTTPException(400, "Connect Meta first")
    try:
        pages = make_platform("meta", c).pages()
    except Exception as e:
        raise HTTPException(400, friendly(str(e)))
    if not pages:
        raise HTTPException(400, "No Facebook Pages are available to this token. Give the system user access to your Page in Meta Business Settings.")
    if len(pages) == 1:
        use_page(user["id"], pages[0])
    return {"pages": pages, "saved": len(pages) == 1}


class UsePageIn(BaseModel):
    page_id: str


@conn_router.post("/use-page")
def use_page_route(body: UsePageIn, user: dict = Depends(current_user)):
    try:
        pages = make_platform("meta", creds.get(user["id"], "meta")).pages()
    except Exception as e:
        raise HTTPException(400, friendly(str(e)))
    page = next((x for x in pages if x["id"] == body.page_id), None)
    if not page:
        raise HTTPException(404, "That Page is not available to this token")
    use_page(user["id"], page)
    return {"saved": True, "page": page}


def use_page(user_id: int, page: dict):
    c = creds.get(user_id, "meta")
    c["page_id"] = page["id"]
    if page.get("instagram"):
        c["instagram_account_id"] = page["instagram"]["id"]
    else:
        c.pop("instagram_account_id", None)
    creds._save(user_id, "meta", c)
