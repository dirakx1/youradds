"""Product-photo background removal.
- rembg (IS-Net) gives a correct silhouette (drops floor shadows) but can make white parts translucent.
- On white studio backdrops we also run GrabCut (solid whites, e.g. shoe soles) and intersect it with the model's
  outline, so the product stays solid and the shadow is removed. Other backgrounds use the model result alone."""
import base64
import binascii
import io
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

from .auth import current_user

router = APIRouter(prefix="/api/tools", dependencies=[Depends(current_user)])
MAX_B64 = 8 * 1024 * 1024 * 4 // 3 + 16
_session = None
_lock = threading.Lock()


def available() -> bool:
    try:
        import cv2, numpy, rembg  # noqa: F401
        return True
    except Exception:
        return False


def _get_session():
    global _session
    with _lock:
        if _session is None:
            from rembg import new_session
            _session = new_session("isnet-general-use")   # downloads ~180MB on first use
        return _session


def _white_backdrop(rgb) -> bool:
    import numpy as np
    edge = np.concatenate([rgb[:4].reshape(-1, 3), rgb[-4:].reshape(-1, 3),
                           rgb[:, :4].reshape(-1, 3), rgb[:, -4:].reshape(-1, 3)])
    return bool(edge.min(axis=1).mean() >= 240 and edge.std() < 14)


def _grabcut_alpha(rgb, f):
    import cv2
    import numpy as np
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    big = cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    H, W = big.shape[:2]
    mask = np.full((H, W), cv2.GC_PR_FGD, np.uint8)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    mask[(hsv[:, :, 1] < 22) & (hsv[:, :, 2] >= 190)] = cv2.GC_PR_BGD
    near_white = (big.min(axis=2) >= 248).astype(np.uint8)
    _, lab = cv2.connectedComponents(near_white)
    border = (set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])) - {0}
    bg = np.isin(lab, list(border))
    mask[bg] = cv2.GC_BGD
    mask[:6, :] = mask[-6:, :] = mask[:, :6] = mask[:, -6:] = cv2.GC_BGD
    core = cv2.erode((~bg & (big.min(axis=2) < 225)).astype(np.uint8), np.ones((9, 9), np.uint8))
    mask[core > 0] = cv2.GC_FGD
    cv2.grabCut(big, mask, None, np.zeros((1, 65)), np.zeros((1, 65)), 5, cv2.GC_INIT_WITH_MASK)
    alpha = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    # keep the real blobs and fill their holes: white parts enclosed by the product (soles, labels) stay solid
    cnts, _ = cv2.findContours(alpha, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    keep = np.zeros_like(alpha)
    for c in cnts:
        if cv2.contourArea(c) > 0.01 * H * W:
            cv2.drawContours(keep, [c], -1, 255, -1)
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((2 * f + 1, 2 * f + 1), np.uint8))
    return cv2.erode(keep, np.ones((3, 3), np.uint8))


def remove_background(img: Image.Image) -> Image.Image:
    import cv2
    import numpy as np
    from rembg import remove
    img = img.convert("RGB")
    img.thumbnail((2000, 2000), Image.LANCZOS)
    rgb = np.array(img)
    h, w = rgb.shape[:2]
    rem = np.array(remove(img, session=_get_session()).convert("RGBA"))
    alpha = rem[:, :, 3]
    if _white_backdrop(rgb):
        f = max(1, min(3, 2400 // max(w, h)))
        ga = _grabcut_alpha(rgb, f)
        ra = cv2.resize(alpha, (w * f, h * f), interpolation=cv2.INTER_CUBIC)
        sil = (ra > 8).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(sil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        fill = np.zeros_like(sil)
        for c in cnts:
            if cv2.contourArea(c) > 0.01 * sil.size:
                cv2.drawContours(fill, [c], -1, 255, -1)
        k = 2 * f + 1
        fill = cv2.dilate(fill, np.ones((k, k), np.uint8))
        a = cv2.min(ga, fill)
        a = cv2.GaussianBlur(a, (0, 0), 0.55 * f)
        a = cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA)
        # sanity: if the combined mask lost most of the model's silhouette, trust the model alone
        if a.astype(float).sum() >= 0.8 * alpha.astype(float).sum():
            alpha = a
    out = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    bbox = out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox:
        pad = int(0.02 * max(w, h))
        out = out.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(w, bbox[2] + pad), min(h, bbox[3] + pad)))
    return out


class CutIn(BaseModel):
    image: str


@router.get("/cutout/status")
def status():
    return {"available": available()}


@router.post("/cutout")
def cutout(body: CutIn):
    if not available():
        raise HTTPException(501, "Background removal isn't installed: pip install rembg onnxruntime "
                                 "opencv-python-headless numpy")
    b64 = body.image.split(",", 1)[1] if "," in body.image[:100] else body.image
    if len(b64) > MAX_B64:
        raise HTTPException(413, "image too large (max 8MB)")
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64, validate=True)))
        if img.width * img.height > 40_000_000:
            raise HTTPException(400, "image dimensions too large")
        img.load()
    except HTTPException:
        raise
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "invalid image")
    try:
        out = remove_background(img)
    except Exception as e:
        raise HTTPException(502, f"background removal failed: {e.__class__.__name__}: {str(e)[:150]}")
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return {"image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "width": out.width, "height": out.height}
