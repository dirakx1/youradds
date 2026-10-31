"""Video ads: Seedance background clip (Higgsfield) + real product photo, headline, price and CTA composited
with FFmpeg. Runs as a background job because generation takes minutes."""
import json
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import imageio_ffmpeg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, select, update

from . import ai, creds, imagegen
from .auth import current_user
from .config import CREATIVES_DIR, env
from .creatives import LANGS, _decode_image
from .db import session_scope
from .models import Video
from PIL import Image
from .render import THEMES, render

router = APIRouter(prefix="/api/videos", dependencies=[Depends(current_user)])
VIDEO_DIR = CREATIVES_DIR.parent / "videos"
JOBS_DIR = CREATIVES_DIR.parent / "jobs"
# platform -> format -> (width, height, seedance aspect ratio)
VIDEO_FORMATS = {
    "meta": {"reel": (1080, 1920, "9:16"), "feed_square": (1080, 1080, "1:1")},
    "tiktok": {"vertical": (1080, 1920, "9:16")},
    "google": {"youtube_landscape": (1920, 1080, "16:9")},
}


def _video_dict(row):
    return {column.name: getattr(row, column.name) for column in Video.__table__.columns}


class VidIn(BaseModel):
    platforms: list[str] = Field(min_length=1)
    product: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=500)
    audience: str = Field("", max_length=120)
    tone: str = Field("friendly", max_length=30)
    language: str = "en"
    theme: str = "purple"
    price: str = Field("", max_length=20)
    social_proof: str = Field("", max_length=50)
    scene_hint: str = Field("", max_length=200)
    duration: int = Field(5, ge=4, le=15)
    resolution: str = "720p"                  # 480p = cheap draft, 720p = standard
    product_image: Optional[str] = None


def _cap() -> float:
    try:
        return float(env("VIDEO_MAX_USD") or 3.0)
    except ValueError:
        return 3.0


def _validate(body: VidIn, user_id: int) -> str:
    key = creds.higgsfield_key(user_id)
    if not imagegen.available(key):
        raise HTTPException(400, "Video needs your Higgsfield API key (Connections page)")
    if any(p not in VIDEO_FORMATS for p in body.platforms):
        raise HTTPException(400, "unknown platform")
    if body.resolution not in ("480p", "720p"):
        raise HTTPException(400, "resolution must be 480p or 720p")
    if body.theme not in THEMES or body.language not in LANGS:
        raise HTTPException(400, "unknown theme or language")
    return key


def _plan(body: VidIn):
    prompt = ai.video_prompt(body.description, body.tone, body.theme, body.scene_hint, body.duration)
    ratios = sorted({v[2] for p in dict.fromkeys(body.platforms) for v in VIDEO_FORMATS[p].values()})
    est = {r: imagegen.estimate_video_usd(r, body.duration, body.resolution) for r in ratios}
    return prompt, est, round(sum(est.values()), 4)


@router.post("/estimate")
def estimate(body: VidIn, user: dict = Depends(current_user)):
    _validate(body, user["id"])
    _, est, total = _plan(body)
    return {"per_ratio": est, "total_usd": total, "cap_usd": _cap()}


@router.post("/generate", status_code=202)
def generate(body: VidIn, bg: BackgroundTasks, user: dict = Depends(current_user)):
    _validate(body, user["id"])
    product = _decode_image(body.product_image) if body.product_image else None
    prompt, est, total = _plan(body)
    if total > _cap():
        raise HTTPException(400, f"estimated cost ${total:.2f} exceeds VIDEO_MAX_USD (${_cap():.2f})")
    job = uuid.uuid4().hex
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if product is not None:
        product.save(JOBS_DIR / f"{job}.png")
    params = json.dumps({**body.model_dump(exclude={"product_image"}), "prompt": prompt})
    ids = []
    with session_scope() as session:
        for p in dict.fromkeys(body.platforms):
            for fmt, (w, h, ratio) in VIDEO_FORMATS[p].items():
                share = est[ratio] / sum(1 for q in dict.fromkeys(body.platforms)
                                         for v in VIDEO_FORMATS[q].values() if v[2] == ratio)
                video = Video(user_id=user["id"], platform=p, format=fmt, width=w, height=h, ratio=ratio,
                              duration=body.duration, cost_usd=round(share, 4), job=job, params=params,
                              started_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
                session.add(video)
                session.flush()
                ids.append(video.id)
    bg.add_task(run_job, ids)
    return {"videos": ids, "estimated_usd": total}


def _set(ids, **kw):
    allowed = set(Video.__table__.columns.keys()) - {"id"}
    if set(kw) - allowed:
        raise ValueError("unsupported video update field")
    if not ids:
        return
    with session_scope() as session:
        session.execute(update(Video).where(Video.id.in_(ids)).values(**kw))


def _composite(clip: Path, overlay_png: Path, out: Path, w: int, h: int, dur: int):
    """Cover-fit the clip to WxH, fade the overlay in, encode H.264 without audio."""
    fc = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[v];"
          "[1:v]format=rgba,fade=t=in:st=0.3:d=0.7:alpha=1[o];[v][o]overlay=0:0:shortest=1[out]")
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(clip), "-loop", "1",
           "-t", str(dur), "-i", str(overlay_png), "-filter_complex", fc, "-map", "[out]", "-an",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
           "-movflags", "+faststart", "-t", str(dur), str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("ffmpeg failed: " + r.stderr[-200:])


GEN_TIMEOUT = 2400          # seconds to wait for Higgsfield before giving up (request keeps its id, can be resumed)
TERMINAL_BAD = ("failed", "nsfw", "canceled")


def run_job(ids):
    """Generate (or resume waiting for) the clips for these rows, then composite each one. Safe to call again
    for rows whose Higgsfield request is still pending: it re-attaches via the stored status_url."""
    if not ids:
        return
    with session_scope() as session:
        rows = [_video_dict(r) for r in session.scalars(select(Video).where(Video.id.in_(ids))).all()]
    if not rows:
        return
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    params = json.loads(rows[0]["params"])
    key = creds.higgsfield_key(rows[0]["user_id"])
    body = VidIn(**{k: v for k, v in params.items() if k != "prompt"})
    prompt = params["prompt"]
    pimg = JOBS_DIR / f"{rows[0]['job']}.png"
    product = Image.open(pimg).convert("RGBA") if pimg.exists() else None
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        results, errors = {}, {}

        def gen(ratio):
            grp = [r["id"] for r in rows if r["ratio"] == ratio]
            try:
                url = next((r["status_url"] for r in rows if r["ratio"] == ratio and r["status_url"]), None)
                if not url:   # not submitted yet
                    _set(grp, status="generating")
                    sub = imagegen.submit(imagegen.VIDEO_MODEL, imagegen._vbody(prompt, ratio, body.duration,
                                                                               body.resolution), key)
                    _set(grp, request_id=sub["request_id"], status_url=sub["status_url"],
                         cancel_url=sub["cancel_url"], provider_status="queued")
                    url = sub["status_url"]
                else:
                    _set(grp, status="generating", error=None)
                d = imagegen.wait(url, GEN_TIMEOUT, key, interval=5, on_status=lambda st: _set(grp, provider_status=st))
                dest = tmp / f"{ratio.replace(':', 'x')}.mp4"
                imagegen.download_video(d, dest)
                results[ratio] = dest
            except Exception as e:
                msg = str(e)[:200]
                if msg == "timed out":
                    msg = "still processing at Higgsfield after 40 min - use 'Check again' later"
                errors[ratio] = msg

        ratios = sorted({r["ratio"] for r in rows})
        with ThreadPoolExecutor(max_workers=len(ratios)) as ex:
            list(ex.map(gen, ratios))
        copies = {}
        for r in rows:
            if r["ratio"] not in results:
                _set([r["id"]], status="failed", error=errors.get(r["ratio"], "generation failed"))
                continue
            try:
                _set([r["id"]], status="compositing")
                if r["platform"] not in copies:
                    copies[r["platform"]] = ai.creative_copy(r["platform"], body.product, body.description,
                                                             body.audience, body.tone, LANGS[body.language],
                                                             body.language)
                copy = copies[r["platform"]]
                ov = render((r["width"], r["height"]), copy, product, body.theme, body.price, body.social_proof,
                            overlay=True)
                png = tmp / f"ov_{r['id']}.png"
                ov.save(png)
                fname = f"{uuid.uuid4().hex}.mp4"
                _composite(results[r["ratio"]], png, VIDEO_DIR / fname, r["width"], r["height"], body.duration)
                _set([r["id"]], status="done", file=fname, headline=copy["headline"], cta=copy.get("cta", ""),
                     error=None)
            except Exception as e:
                _set([r["id"]], status="failed", error=str(e)[:200])


def recover():
    """After a restart: re-attach to clips already submitted to Higgsfield; fail rows that never were."""
    with session_scope() as session:
        session.execute(update(Video).where(Video.status.in_(("queued", "generating", "compositing")),
                                            (Video.status_url.is_(None)) | (Video.status_url == ""))
                        .values(status="failed", error="interrupted by server restart before submission"))
        pending = list(session.scalars(select(Video.id).where(Video.status.in_(("generating", "compositing")),
                                                              Video.status_url.is_not(None))))
    if pending:
        threading.Thread(target=run_job, args=(pending,), daemon=True).start()


def _resumable(r) -> bool:
    return bool(r["status"] == "failed" and r["status_url"] and r["provider_status"] not in TERMINAL_BAD
                and not r["file"])


@router.post("/{vid}/resume", status_code=202)
def resume(vid: int, bg: BackgroundTasks, user: dict = Depends(current_user)):
    """Re-check a request that timed out locally; if Higgsfield finished it, the video gets downloaded and composited."""
    with session_scope() as session:
        record = session.scalar(select(Video).where(Video.id == vid, Video.user_id == user["id"]))
        r = _video_dict(record) if record else None
        if not r or not _resumable(r):
            raise HTTPException(400, "this video cannot be resumed")
        ids = list(session.scalars(select(Video.id).where(Video.job == r["job"], Video.request_id == r["request_id"],
                                                          Video.status == "failed")))
        session.execute(update(Video).where(Video.id.in_(ids)).values(status="generating", error=None))
    bg.add_task(run_job, ids)
    return {"resumed": ids}


@router.post("/{vid}/cancel")
def cancel(vid: int, user: dict = Depends(current_user)):
    """Cancel while still queued at Higgsfield (they refund it). Too late once rendering has started."""
    with session_scope() as session:
        record = session.scalar(select(Video).where(Video.id == vid, Video.user_id == user["id"]))
        r = _video_dict(record) if record else None
    if not r or not r["cancel_url"]:
        raise HTTPException(400, "nothing to cancel")
    if not imagegen.cancel(r["cancel_url"], creds.higgsfield_key(user["id"])):
        raise HTTPException(409, "cannot cancel: Higgsfield already started rendering this clip")
    with session_scope() as session:
        session.execute(update(Video).where(Video.job == r["job"], Video.request_id == r["request_id"])
                        .values(status="failed", error="canceled (refunded)", provider_status="canceled", cost_usd=0))
    return {"ok": True}


@router.get("")
def list_videos(user: dict = Depends(current_user)):
    columns = [Video.id, Video.platform, Video.format, Video.width, Video.height, Video.ratio, Video.duration,
               Video.status, Video.error, Video.headline, Video.cta, Video.cost_usd, Video.created_at,
               Video.request_id, Video.provider_status, Video.status_url, Video.file, Video.started_at]
    with session_scope() as session:
        rows = [dict(r) for r in session.execute(select(*columns).where(Video.user_id == user["id"])
                                                 .order_by(Video.id.desc()).limit(100)).mappings()]
    now = datetime.now(timezone.utc)
    for r in rows:
        started = r["started_at"]
        try:
            parsed = datetime.fromisoformat(started.replace(" ", "T")).replace(tzinfo=timezone.utc) if started else None
            r["elapsed_s"] = int((now - parsed).total_seconds()) if parsed else None
        except (ValueError, TypeError):
            r["elapsed_s"] = None
    for r in rows:
        r["resumable"] = _resumable(r)
        r.pop("status_url"), r.pop("file")
    return rows


@router.get("/{vid}/file")
def video_file(vid: int, download: bool = False, user: dict = Depends(current_user)):
    with session_scope() as session:
        r = session.execute(select(Video.file, Video.platform, Video.format)
                            .where(Video.id == vid, Video.user_id == user["id"], Video.status == "done")).mappings().one_or_none()
    if not r or not (VIDEO_DIR / r["file"]).is_file():
        raise HTTPException(404, "video not found")
    return FileResponse(VIDEO_DIR / r["file"], media_type="video/mp4",
                        filename=f"{r['platform']}-{r['format']}-{vid}.mp4" if download else None)


@router.delete("/{vid}", status_code=204)
def delete(vid: int, user: dict = Depends(current_user)):
    with session_scope() as session:
        r = session.execute(select(Video.file).where(Video.id == vid, Video.user_id == user["id"])).scalar_one_or_none()
        session.execute(sa_delete(Video).where(Video.id == vid, Video.user_id == user["id"]))
    if r:
        (VIDEO_DIR / r).unlink(missing_ok=True)
