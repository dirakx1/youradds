"""Image-ad compositor (Pillow). Lays out headline, product shot, bullets, optional proof/testimonials,
price badge and CTA for a given canvas size. Platform UI safe zones are respected on 9:16 formats."""
import math
import os
from functools import lru_cache
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

FORMATS = {
    "meta": {"feed_square": (1080, 1080), "feed_portrait": (1080, 1350), "story_reel": (1080, 1920)},
    "tiktok": {"vertical": (1080, 1920)},
    "google": {"landscape": (1200, 628), "square": (1200, 1200), "medium_rectangle": (300, 250)},
}

# top, bottom, accent, text, button-text
THEMES = {
    "purple": ((96, 58, 210), (28, 14, 78), (255, 205, 90), (255, 255, 255), (40, 20, 0)),
    "night": ((24, 38, 74), (7, 9, 22), (255, 170, 60), (255, 255, 255), (30, 15, 0)),
    "pink": ((222, 64, 152), (68, 14, 84), (255, 238, 120), (255, 255, 255), (60, 20, 0)),
    "light": ((252, 248, 242), (228, 216, 202), (214, 62, 42), (28, 24, 22), (255, 255, 255)),
    "dark": ((34, 34, 40), (6, 6, 8), (110, 224, 160), (255, 255, 255), (0, 40, 20)),
}

_BOLD = [os.getenv("CREATIVE_FONT_BOLD", ""), "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]
_REG = [os.getenv("CREATIVE_FONT_REGULAR", ""), "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"]


@lru_cache(maxsize=256)
def font(size: int, bold: bool = False):
    size = max(8, int(size))
    for p in (_BOLD if bold else _REG):
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _tw(f, t):
    return f.getlength(t)


def wrap(words, f, maxw):
    lines, cur = [], []
    for w in words:
        if cur and _tw(f, " ".join(cur + [w])) > maxw:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def fit_text(text, maxw, maxh, start, bold, max_lines, lh=1.12):
    """Largest font size (<= start) where the wrapped text fits the box. Returns (font, lines)."""
    words = text.split()
    size = int(start)
    while True:
        f = font(size, bold)
        lines = wrap(words, f, maxw)
        fits = (len(lines) <= max_lines and len(lines) * size * lh <= maxh
                and all(_tw(f, w) <= maxw for w in words))
        if fits or size <= 9:
            return f, lines
        size -= 2


def draw_lines(d, lines, f, x0, x1, y, color, accent=None, accent_word="", align="center", lh=1.12):
    aw = "".join(ch for ch in accent_word.lower() if ch.isalnum())
    for ln in lines:
        total = _tw(f, " ".join(ln))
        x = x0 + ((x1 - x0) - total) / 2 if align == "center" else x0
        for i, word in enumerate(ln):
            key = "".join(ch for ch in word.lower() if ch.isalnum())
            col = accent if (accent and aw and key == aw) else color
            d.text((x, y), word, font=f, fill=col)
            x += _tw(f, word + " ")
        y += f.size * lh
    return y


def star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy - rr * math.sin(ang)))
    d.polygon(pts, fill=fill)


def _gradient(w, h, top, bottom):
    mask = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), mask).convert("RGBA")


def _scene(w, h, scene, top, bottom):
    """Cover-fit a generated scene, then add theme-colored scrims top and bottom so text stays readable."""
    return _scrim(ImageOps.fit(scene.convert("RGBA"), (w, h), Image.LANCZOS), top, bottom)


def _scrim(img, top, bottom):
    w, h = img.size
    g = Image.linear_gradient("L").resize((w, h))
    top_a = g.point(lambda v: max(0, int((1 - v / 255 / 0.42) * 0.8 * 255)))
    bot_a = g.point(lambda v: max(0, int((v / 255 - 0.5) / 0.5 * 0.85 * 255)))
    for color, mask in ((top, top_a), (bottom, bot_a)):
        layer = Image.new("RGBA", (w, h), color + (255,))
        layer.putalpha(mask)
        img.alpha_composite(layer)
    return img


def _paste_product(base, prod, box, accent, glow_on=True):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 20 or bh < 20:
        return
    p = prod.copy()
    p.thumbnail((bw, bh), Image.LANCZOS)
    px, py = x0 + (bw - p.width) // 2, y0 + (bh - p.height) // 2
    if glow_on:
        glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([px - p.width * 0.25, py - p.height * 0.05, px + p.width * 1.25, py + p.height * 1.05],
                   fill=accent + (70,))
        base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(10, p.width // 6))))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([px + p.width * 0.1, py + p.height * 0.92, px + p.width * 0.9, py + p.height * 1.02],
                               fill=(0, 0, 0, 120))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(max(4, p.width // 30))))
    base.alpha_composite(p, (px, py))
    return px, py, p.width, p.height


def render(size, copy, product, theme="purple", price="", social_proof="", rating=None, testimonials=(), scene=None, overlay=False):
    """copy: headline, accent, subheadline, bullets[list], cta. product: RGBA PIL image or None.
    social_proof/rating/testimonials are shown only when the caller supplies them (never invented)."""
    w, h = size
    top, bottom, accent, tx, btn_tx = THEMES.get(theme, THEMES["purple"])
    if overlay:  # transparent layer (scrims + text + product) to be composited over a video
        img = _scrim(Image.new("RGBA", (w, h), (0, 0, 0, 0)), top, bottom)
    else:
        img = _scene(w, h, scene, top, bottom) if scene is not None else _gradient(w, h, top, bottom)
    s = min(w, h) / 1080
    s = max(s, 0.2)
    tall, land = h / w > 1.6, w / h > 1.4
    small = s < 0.5
    side = int(w * 0.07)
    top_m = int(h * 0.13) if tall else int(h * 0.05)   # keep clear of Story/TikTok top UI
    bot_m = int(h * 0.17) if tall else int(h * 0.04)   # ...and the caption/CTA UI at the bottom
    d = ImageDraw.Draw(img)

    bullets = [b for b in copy.get("bullets", []) if b][:3]
    cta = copy.get("cta", "")

    def cta_pill(cx, y, maxw, left=None):
        f = font(46 * s, True)
        tw = _tw(f, cta)
        pw, ph = min(maxw, tw + 90 * s), 96 * s
        if left is not None:
            cx = left + pw / 2
        d.rounded_rectangle([cx - pw / 2, y, cx + pw / 2, y + ph], radius=ph / 2, fill=accent)
        d.text((cx - tw / 2, y + (ph - f.size) / 2 - 3 * s), cta, font=f, fill=btn_tx)
        return ph

    def rating_line(x0, x1, y, align="center"):
        """5 stars + the user's text, shrunk to fit [x0, x1]; stays legible on tiny formats. Returns its height."""
        fs = max(32 * s, 12)
        while True:
            fp = font(fs, False)
            total = 5 * fs * 1.15 + fs * 0.4 + _tw(fp, social_proof)
            if total <= x1 - x0 or fs <= 9:
                break
            fs -= 1
        x = x0 + ((x1 - x0) - total) / 2 if align == "center" else x0
        for i in range(5):
            star(d, x + fs * 0.55 + i * fs * 1.15, y + fs * 0.6, fs * 0.55, accent)
        d.text((x + 5 * fs * 1.15 + fs * 0.4, y + fs * 0.05), social_proof, font=fp, fill=tx)
        return fs * 1.2

    rfs = max(32 * s, 12)

    if land:
        colw = w * 0.5 - side
        y = top_m
        f, lines = fit_text(copy["headline"], colw, h * 0.42, 120 * s, True, 4)
        y = draw_lines(d, lines, f, side, side + colw, y, tx, accent, copy.get("accent", ""), "left")
        if copy.get("subheadline"):
            f2, l2 = fit_text(copy["subheadline"], colw, h * 0.2, 40 * s, False, 3)
            y = draw_lines(d, l2, f2, side, side + colw, y + 10 * s, tx, align="left", lh=1.2)
        yb = y + 12 * s
        for b in bullets:
            if yb + 40 * s > h - bot_m - 110 * s - (rfs * 1.8 if social_proof else 0):
                break
            fb = font(36 * s, True)
            d.text((side, yb), "+ " + b, font=fb, fill=tx)
            yb += 46 * s
        if social_proof:
            rating_line(side, side + colw, h - bot_m - (96 * s if cta else 0) - rfs * 1.5 - 8 * s, "left")
        if cta:
            cta_pill(0, h - bot_m - 96 * s, colw, left=side)
        box = (int(w * 0.5), int(h * 0.06), w - side, int(h * 0.94))
        placed = _paste_product(img, product, box, accent, scene is None and not overlay) if product else None
        badge_anchor = (box[2], box[3])
    else:
        # header
        y = top_m
        hbox = h * (0.17 if tall else 0.2)
        f, lines = fit_text(copy["headline"], w - 2 * side, hbox, 118 * s, True, 4)
        y = draw_lines(d, lines, f, side, w - side, y, tx, accent, copy.get("accent", ""))
        if copy.get("subheadline"):
            f2, l2 = fit_text(copy["subheadline"], w - 2 * side, h * 0.08, 40 * s, False, 2)
            y = draw_lines(d, l2, f2, side, w - side, y + 8 * s, tx, lh=1.2)
        header_bottom = y + 14 * s
        # bottom stack, built upward
        yb = h - bot_m
        if cta:
            yb -= 96 * s
            cta_pill(w / 2, yb, w - 2 * side)
            yb -= 20 * s
        if social_proof:
            yb -= rfs * 1.3
            rating_line(side, w - side, yb)
            yb -= 14 * s
        if testimonials and not small:
            for t in list(testimonials)[:2][::-1]:
                ch = 104 * s
                yb -= ch
                ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(ov).rounded_rectangle([side, yb, w - side, yb + ch], radius=22 * s,
                                                     fill=(255, 255, 255, 46) if theme != "light" else (0, 0, 0, 22))
                img.alpha_composite(ov)
                d = ImageDraw.Draw(img)
                d.text((side + 22 * s, yb + 10 * s), t["name"], font=font(26 * s, True), fill=tx)
                ft, tl = fit_text(t["text"], w - 2 * side - 44 * s, ch - 46 * s, 26 * s, False, 2, 1.15)
                draw_lines(d, tl, ft, side + 22 * s, w - side, yb + 44 * s, tx, align="left", lh=1.15)
                yb -= 12 * s
        if bullets and not small:
            yb -= 40 * s
            fb = font(34 * s, True)
            parts = ["+ " + b for b in bullets]
            while len(parts) > 1 and sum(_tw(fb, p) for p in parts) + 36 * s * (len(parts) - 1) > w - 2 * side:
                parts.pop()
            tot = sum(_tw(fb, p) for p in parts) + 36 * s * (len(parts) - 1)
            x = (w - tot) / 2
            for p in parts:
                d.text((x, yb), p, font=fb, fill=tx)
                x += _tw(fb, p) + 36 * s
            yb -= 16 * s
        box = (side, int(header_bottom), w - side, int(yb))
        placed = _paste_product(img, product, box, accent, scene is None and not overlay) if product else None
        badge_anchor = (box[2], box[3])
    d = ImageDraw.Draw(img)
    if price:
        fpz = font(max(44 * s, 13), True)
        bw, bh = _tw(fpz, price) + fpz.size * 1.0, fpz.size * 1.65
        if placed:
            px, py, pw_, ph_ = placed
            bx1 = min(badge_anchor[0], px + pw_ + bw * 0.3)
            by1 = py + ph_
        else:   # no product photo: put the badge in the middle of the product area
            bx1 = (box[0] + box[2]) / 2 + bw / 2
            by1 = (box[1] + box[3]) / 2 + bh / 2
        d.rounded_rectangle([bx1 - bw, by1 - bh, bx1, by1], radius=bh / 2, fill=accent)
        d.text((bx1 - bw + fpz.size * 0.5, by1 - bh + (bh - fpz.size) / 2 - fpz.size * 0.06), price, font=fpz,
               fill=btn_tx)
    return img if overlay else img.convert("RGB")
