import base64
import binascii
import io
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from concurrent.futures import ThreadPoolExecutor
from . import ai, creds, imagegen
from .auth import current_user
from .config import CREATIVES_DIR, env
from .db import conn
from .render import FORMATS, THEMES, render

router = APIRouter(prefix="/api/creatives", dependencies=[Depends(current_user)])
MAX_UPLOAD = 8 * 1024 * 1024
LANGS = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian"}


class Testimonial(BaseModel):
    name: str = Field(max_length=40)
    text: str = Field(max_length=140)


class GenIn(BaseModel):
    platforms: list[str] = Field(min_length=1)
    product: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=500)
    audience: str = Field("", max_length=120)
    tone: str = Field("friendly", max_length=30)
    language: str = "en"
    theme: str = "purple"
    price: str = Field("", max_length=20)
    social_proof: str = Field("", max_length=50)       # user-supplied and true, e.g. "4.8/5 from 1,200 reviews"
    testimonials: list[Testimonial] = Field(default_factory=list, max_length=3)  # real ones only
    scene: bool = False                                # AI background scenes via Higgsfield
    scene_hint: str = Field("", max_length=200)        # optional art direction, e.g. "cozy bedroom at night"
    product_image: Optional[str] = None                # base64 (PNG with transparent background works best)


def _decode_image(b64: str) -> Image.Image:
    if "," in b64[:100]:
        b64 = b64.split(",", 1)[1]
    if len(b64) > MAX_UPLOAD * 4 // 3 + 16:
        raise HTTPException(413, "product image too large (max 8MB)")
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64, validate=True)))
        if img.width * img.height > 40_000_000:
            raise HTTPException(400, "image dimensions too large")
        img = img.convert("RGBA")
    except HTTPException:
        raise
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "invalid product image")
    img.thumbnail((2000, 2000))
    return img


@router.get("/options")
def options(user: dict = Depends(current_user)):
    return {"formats": {p: {k: list(v) for k, v in f.items()} for p, f in FORMATS.items()},
            "themes": list(THEMES), "languages": LANGS, "scene_available": imagegen.available(creds.higgsfield_key(user["id"])),
            "scene_cost_cap_usd": _cap()}


def _cap() -> float:
    try:
        return float(env("SCENE_MAX_USD") or 0.5)
    except ValueError:
        return 0.5


def make_scenes(body: "GenIn", ratios: set[str], key: str):
    """One scene per distinct aspect ratio (shared across platforms). Returns (scenes, cost_usd, warnings, prompt)."""
    warnings: list[str] = []
    prompt = ai.scene_prompt(body.product, body.description, body.audience, body.tone, body.theme, body.scene_hint)
    est = [imagegen.estimate_usd(prompt, r, key) for r in sorted(ratios)]
    if all(e is not None for e in est):
        total = sum(est)
        if total > _cap():
            raise HTTPException(400, f"estimated scene cost ${total:.3f} exceeds SCENE_MAX_USD (${_cap():.2f})")
        per = dict(zip(sorted(ratios), est))
    else:
        per = {}
        warnings.append("Could not get a cost estimate from Higgsfield; proceeding (max 5 images + retries).")
    spent = 0.0

    def one(ratio):
        cost = 0.0
        for attempt in range(2):
            img = imagegen.generate(prompt if attempt == 0 else prompt + ", plain background, uncluttered", ratio, key)
            cost += per.get(ratio) or 0
            ok, why = ai.qa_scene(img)
            if ok:
                return ratio, img, cost, None
            warn = f"{ratio} scene rejected by quality check ({why})"
        return ratio, None, cost, warn

    scenes = {}
    with ThreadPoolExecutor(max_workers=min(5, len(ratios))) as ex:
        futs = {r: ex.submit(one, r) for r in ratios}
        for r, f in futs.items():
            try:
                _, img, cost, warn = f.result()
                spent += cost
                if img is not None:
                    scenes[r] = img
                if warn:
                    warnings.append(warn + " - used gradient background")
            except imagegen.ImageGenError as e:
                warnings.append(f"{r} scene failed: {e} - used gradient background")
    return scenes, round(spent, 4), warnings, prompt


@router.post("/generate")
def generate(body: GenIn, user: dict = Depends(current_user)):
    bad = [p for p in body.platforms if p not in FORMATS]
    if bad:
        raise HTTPException(400, f"unknown platform(s): {bad}")
    if body.theme not in THEMES:
        raise HTTPException(400, "unknown theme")
    if body.language not in LANGS:
        raise HTTPException(400, "unsupported language")
    product = _decode_image(body.product_image) if body.product_image else None
    CREATIVES_DIR.mkdir(parents=True, exist_ok=True)
    tests = [t.model_dump() for t in body.testimonials if t.name.strip() and t.text.strip()]
    scenes, spent, warnings, sprompt = {}, 0.0, [], None
    if body.scene:
        key = creds.higgsfield_key(user["id"])
        if not imagegen.available(key):
            raise HTTPException(400, "Scene backgrounds need your Higgsfield API key (Connections page)")
        ratios = {imagegen.nearest_ratio(*size) for p in set(body.platforms) for size in FORMATS[p].values()}
        scenes, spent, warnings, sprompt = make_scenes(body, ratios, key)
    out = []
    for platform in dict.fromkeys(body.platforms):
        copy = ai.creative_copy(platform, body.product, body.description, body.audience, body.tone,
                                LANGS[body.language], body.language)
        for fmt, size in FORMATS[platform].items():
            img = render(size, copy, product, body.theme, body.price, body.social_proof, testimonials=tests,
                         scene=scenes.get(imagegen.nearest_ratio(*size)))
            fname = f"{uuid.uuid4().hex}.png"
            img.save(CREATIVES_DIR / fname, optimize=True)
            with conn() as c:
                cur = c.execute(
                    "INSERT INTO creatives (user_id,platform,format,width,height,file,headline,subheadline,cta,language)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (user["id"], platform, fmt, size[0], size[1], fname, copy["headline"], copy.get("subheadline", ""),
                     copy.get("cta", ""), body.language))
            out.append({"id": cur.lastrowid, "platform": platform, "format": fmt, "width": size[0],
                        "height": size[1], "headline": copy["headline"]})
    return {"creatives": out, "llm": ai.llm_available(), "scene_cost_usd": spent, "warnings": warnings,
            "scene_prompt": sprompt}


@router.get("")
def list_creatives(user: dict = Depends(current_user)):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,platform,format,width,height,headline,subheadline,cta,language,campaign_id,created_at"
            " FROM creatives WHERE user_id=? ORDER BY id DESC LIMIT 200", (user["id"],))]


@router.get("/{cid}/image")
def image(cid: int, download: bool = False, user: dict = Depends(current_user)):
    with conn() as c:
        r = c.execute("SELECT file,platform,format FROM creatives WHERE id=? AND user_id=?", (cid, user["id"])).fetchone()
    if not r or not (CREATIVES_DIR / r["file"]).is_file():
        raise HTTPException(404, "creative not found")
    return FileResponse(CREATIVES_DIR / r["file"], media_type="image/png",
                        filename=f"{r['platform']}-{r['format']}-{cid}.png" if download else None)


class AttachIn(BaseModel):
    campaign_id: int


@router.post("/{cid}/attach")
def attach(cid: int, body: AttachIn, user: dict = Depends(current_user)):
    """Link the creative to a campaign and copy its text onto the campaign."""
    with conn() as c:
        cr = c.execute("SELECT * FROM creatives WHERE id=? AND user_id=?", (cid, user["id"])).fetchone()
        camp = c.execute("SELECT id FROM campaigns WHERE id=? AND user_id=?", (body.campaign_id, user["id"])).fetchone()
        if not cr or not camp:
            raise HTTPException(404, "creative or campaign not found")
        c.execute("UPDATE creatives SET campaign_id=? WHERE id=?", (body.campaign_id, cid))
        c.execute("UPDATE campaigns SET headline=?, body=?, cta=? WHERE id=?",
                  (cr["headline"], cr["subheadline"], cr["cta"], body.campaign_id))
    return {"ok": True}


@router.delete("/{cid}", status_code=204)
def delete(cid: int, user: dict = Depends(current_user)):
    with conn() as c:
        r = c.execute("SELECT file FROM creatives WHERE id=? AND user_id=?", (cid, user["id"])).fetchone()
        c.execute("DELETE FROM creatives WHERE id=? AND user_id=?", (cid, user["id"]))
    if r:
        (CREATIVES_DIR / r["file"]).unlink(missing_ok=True)
