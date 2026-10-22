"""Image-generation provider (Higgsfield). Used only for background *scenes*; the real product photo is
composited on top by render.py, so the product label is never redrawn by the model."""
from __future__ import annotations
import io
import math
import time
import uuid
import httpx
from PIL import Image
from .config import env

BASE = "https://api.higgsfield.ai"
RATIOS = {"1:1": 1.0, "2:3": 2 / 3, "3:2": 1.5, "3:4": 0.75, "4:3": 4 / 3, "9:16": 9 / 16, "16:9": 16 / 9}

# model path -> request body builder. Z-Image Turbo is the cheap default; Soul is portrait/editorial-leaning.
MODELS = {
    "z-image/turbo": lambda p, ar, res: {"prompt": p[:800], "aspect_ratio": ar,
                                         "resolution": "2k" if res in ("2k", "1080p") else "1k"},
    "higgsfield-ai/soul/v2/standard": lambda p, ar, res: {"prompt": p, "aspect_ratio": ar,
                                                          "resolution": "1080p" if res in ("2k", "1080p") else "720p"},
}


class ImageGenError(Exception):
    pass


def nearest_ratio(w: int, h: int) -> str:
    return min(RATIOS, key=lambda k: abs(math.log(RATIOS[k] / (w / h))))


def _auth(key: str) -> str:
    return f"Key {key}"


def available(key: str) -> bool:
    return ":" in (key or "")


def _model() -> str:
    m = env("HIGGSFIELD_MODEL") or "z-image/turbo"
    if m not in MODELS:
        raise ImageGenError(f"HIGGSFIELD_MODEL must be one of {list(MODELS)}")
    return m


def _body(prompt: str, ratio: str) -> dict:
    return MODELS[_model()](prompt, ratio, env("HIGGSFIELD_RESOLUTION") or "1k")


def _check(r: httpx.Response):
    if r.status_code in (401, 403):
        raise ImageGenError("Higgsfield rejected the API key (expected ID:SECRET - check the key in Connections)")
    if r.status_code == 402 or (r.status_code == 400 and "balance" in r.text.lower()):
        raise ImageGenError("Higgsfield balance too low - top up your API balance")
    if r.status_code >= 400:
        raise ImageGenError(f"Higgsfield error {r.status_code}: {r.text[:200]}")


def estimate_usd(prompt: str, ratio: str, key: str) -> float | None:
    """Cost of one image per Higgsfield's estimate endpoint; None if it can't be determined."""
    try:
        r = httpx.post(f"{BASE}/estimate/{_model()}", json=_body(prompt, ratio), timeout=20,
                       headers={"Authorization": _auth(key)})
        return float(r.json()["usd"]) if r.status_code < 300 else None
    except Exception:
        return None


def submit(path: str, body: dict, key: str) -> dict:
    """Submit a request; returns {request_id, status_url, cancel_url}."""
    h = {"Authorization": _auth(key), "Idempotency-Key": str(uuid.uuid4())}
    try:
        r = httpx.post(f"{BASE}/{path}", json=body, headers=h, timeout=30)
        _check(r)
        d = r.json()
        for k in ("status_url", "cancel_url"):
            if d.get(k) and not d[k].startswith(BASE + "/"):
                raise ImageGenError("unexpected URL from provider")
        return {"request_id": d.get("request_id"), "status_url": d["status_url"], "cancel_url": d.get("cancel_url")}
    except httpx.HTTPError as e:
        raise ImageGenError(f"network error: {e.__class__.__name__}")


def wait(status_url: str, timeout: int, key: str, interval: float = 2, on_status=None) -> dict:
    """Poll status_url until terminal; returns the completed JSON. Transient network errors are retried."""
    deadline = time.time() + timeout
    errors = 0
    while time.time() < deadline:
        try:
            s = httpx.get(status_url, headers={"Authorization": _auth(key)}, timeout=30)
            _check(s)
            errors = 0
        except httpx.HTTPError:
            errors += 1
            if errors > 10:
                raise ImageGenError("network error while polling")
            time.sleep(interval)
            continue
        d = s.json()
        st = d.get("status")
        if on_status:
            on_status(st)
        if st == "completed":
            return d
        if st in ("failed", "nsfw", "canceled"):
            raise ImageGenError(f"generation {st}" + (" (rejected by content moderation)" if st == "nsfw" else ""))
        time.sleep(interval)
    raise ImageGenError("timed out")


def _submit_wait(path: str, body: dict, timeout: int, key: str, interval: float = 2) -> dict:
    return wait(submit(path, body, key)["status_url"], timeout, key, interval)


def cancel(cancel_url: str, key: str) -> bool:
    """Cancel a request that is still queued (refunded by Higgsfield). False if it already started."""
    if not cancel_url.startswith(BASE + "/"):
        return False
    try:
        return httpx.post(cancel_url, headers={"Authorization": _auth(key)}, timeout=20).status_code == 202
    except httpx.HTTPError:
        return False


def _https(url: str) -> str:
    if not url.startswith("https://"):
        raise ImageGenError("unexpected media URL")
    return url


def generate(prompt: str, ratio: str, key: str, timeout: int = 150) -> Image.Image:
    d = _submit_wait(_model(), _body(prompt, ratio), timeout, key)
    try:
        img = httpx.get(_https(d["images"][0]["url"]), timeout=60, follow_redirects=True)
        img.raise_for_status()
    except (httpx.HTTPError, KeyError, IndexError):
        raise ImageGenError("could not download the generated image")
    if len(img.content) > 25_000_000:
        raise ImageGenError("image too large")
    return Image.open(io.BytesIO(img.content)).convert("RGBA")


# ---- video (Seedance 2.5 text-to-video) ----
VIDEO_MODEL = "bytedance/seedance-2.5/text-to-video"
VIDEO_RATIOS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:4": 0.75, "9:16": 9 / 16, "21:9": 21 / 9}


RES = {"480p": 480, "720p": 720, "1080p": 1080}
TOKEN_USD = {"480p": 0.0214, "720p": 0.0214, "1080p": 0.0234}   # per 1,000 video tokens (Higgsfield pricing text)


def _vbody(prompt: str, ratio: str, duration: int, resolution: str) -> dict:
    return {"prompt": prompt[:1500], "duration": int(duration), "aspect_ratio": ratio,
            "resolution": resolution if resolution in RES else "720p",
            "output_format": "mp4", "generate_audio": False}


def estimate_video_usd(ratio: str, duration: int, resolution: str) -> float:
    """Local estimate from Higgsfield's published formula: tokens = ceil(H*W*seconds*24/1024) at $/1k tokens.
    (Their estimate endpoint returns only prose for this model.) +5% margin for dimension rounding; the
    published rate is before any customer discount."""
    short = RES.get(resolution, 720)
    rw, rh = (int(x) for x in ratio.split(":"))
    w, h = (short * rw / min(rw, rh), short * rh / min(rw, rh))
    tokens = math.ceil(h * w * duration * 24 / 1024)
    return round(tokens / 1000 * TOKEN_USD.get(resolution, 0.0214) * 1.05, 4)


def download_video(d: dict, dest) -> None:
    """Stream the completed request's video to `dest` (capped at 150MB)."""
    try:
        url = _https(d["video"]["url"])
        total = 0
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r, open(dest, "wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                total += len(chunk)
                if total > 150_000_000:
                    raise ImageGenError("video too large")
                f.write(chunk)
    except (httpx.HTTPError, KeyError):
        raise ImageGenError("could not download the generated video")
