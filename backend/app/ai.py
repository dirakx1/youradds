import json
import re
from .config import ANTHROPIC_API_KEY, AI_MODEL

LIMITS = {  # (headline chars, body chars)
    "meta": (40, 125), "tiktok": (40, 100), "google": (30, 90)}


def llm_available() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _ask(system: str, user: str, max_tokens: int = 1500) -> str:
    from anthropic import Anthropic
    msg = Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
        model=AI_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if b.type == "text")


def generate_copy(platform: str, product: str, audience: str, tone: str, n: int = 4) -> list[dict]:
    h_lim, b_lim = LIMITS[platform]
    if llm_available():
        try:
            out = _ask(
                "You are an expert performance-marketing copywriter. Reply with ONLY a JSON array.",
                f"Write {n} {platform} ad variants for: {product}\nAudience: {audience}\nTone: {tone}\n"
                f"Each item: {{\"headline\" (<= {h_lim} chars), \"body\" (<= {b_lim} chars), \"cta\", \"angle\"}}. "
                "Use a different angle in each (benefit, urgency, social proof, curiosity...).")
            data = json.loads(re.search(r"\[.*\]", out, re.S).group(0))
            return [{**v, "headline": v["headline"][:h_lim], "body": v["body"][:b_lim]} for v in data][:n]
        except Exception:
            pass  # fall through to templates
    angles = [
        ("benefit", f"{product}: Made for You", f"Finally, {product.lower()} that fits {audience.lower()}. See why people switch.", "Learn More"),
        ("urgency", f"Last Chance: {product}", f"Today only - get {product.lower()} before it's gone.", "Shop Now"),
        ("social proof", f"Join Thousands Using {product}", f"Rated 4.8/5 by {audience.lower()}. Find out why.", "Get Started"),
        ("curiosity", f"The {product} Secret", f"What {audience.lower()} already know about {product.lower()}.", "See How"),
    ]
    return [{"headline": h[:h_lim], "body": b[:b_lim], "cta": cta, "angle": a, "tone": tone}
            for a, h, b, cta in angles[:n]]


def kpis(m: dict) -> dict:
    spend, clicks, imps = m["spend"], m["clicks"], m["impressions"]
    conv, rev = m["conversions"], m["revenue"]
    return {"ctr": clicks / imps * 100 if imps else 0, "cpc": spend / clicks if clicks else 0,
            "cpa": spend / conv if conv else None, "roas": rev / spend if spend else 0}


def recommend(campaigns: list[dict]) -> list[dict]:
    """Rule-based optimizer over last-7-day aggregates. Each item carries an applicable action."""
    recs = []
    for c in campaigns:
        if c["status"] != "active" or c["spend"] < 20:
            continue
        k = kpis(c)
        base = {"campaign_id": c["id"], "campaign": c["name"], "platform": c["platform"], "roas": round(k["roas"], 2)}
        if k["roas"] < 0.8:
            recs.append({**base, "action": "pause", "severity": "high",
                         "reason": f"ROAS {k['roas']:.2f} is below break-even after ${c['spend']:.0f} spend."})
        elif k["roas"] < 1.5:
            recs.append({**base, "action": "budget", "value": round(c["daily_budget"] * 0.8, 2), "severity": "medium",
                         "reason": f"ROAS {k['roas']:.2f} is marginal; cut budget 20% and test new creative."})
        elif k["roas"] > 2.5:
            recs.append({**base, "action": "budget", "value": round(c["daily_budget"] * 1.2, 2), "severity": "low",
                         "reason": f"ROAS {k['roas']:.2f} is strong; scale budget +20%."})
        if k["ctr"] < 0.8:
            recs.append({**base, "action": "creative", "severity": "medium",
                         "reason": f"CTR {k['ctr']:.2f}% is low; refresh headline/creative in AI Studio."})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(recs, key=lambda r: order[r["severity"]])


def chat(question: str, context: dict) -> str:
    if llm_available():
        try:
            return _ask("You are an ads-optimization copilot for Meta, TikTok and Google Ads. "
                        "Be concise and specific; use the data provided.",
                        f"Account data (last 7 days):\n{json.dumps(context)}\n\nQuestion: {question}", 800)
        except Exception as e:
            return f"AI error: {e}"
    best = max(context["campaigns"], key=lambda c: c["roas"], default=None)
    worst = min(context["campaigns"], key=lambda c: c["roas"], default=None)
    if not best:
        return "No campaign data yet."
    return (f"(Demo mode - set ANTHROPIC_API_KEY for full AI answers.) Total spend ${context['totals']['spend']:.0f}, "
            f"ROAS {context['totals']['roas']:.2f}. Best: {best['name']} ({best['roas']:.2f}). "
            f"Worst: {worst['name']} ({worst['roas']:.2f}).")


STYLE = {
    "meta": "Benefit-led and clear, suited to a Facebook/Instagram feed. Headline <= {h} chars.",
    "tiktok": "Native, casual and punchy; the hook must land in the first 3 words. Headline <= {h} chars.",
    "google": "Direct and informative, like a display ad; no slang. Headline <= {h} chars.",
}
FALLBACK = {
    "en": ("{p}: Made for You", "Discover {p}", ["Easy to use", "Great value", "Fast delivery"], "Shop Now"),
    "es": ("{p}: Hecho para ti", "Descubre {p}", ["Fácil de usar", "Gran valor", "Envío rápido"], "Compra ya"),
}


def creative_copy(platform: str, product: str, description: str, audience: str, tone: str,
                  language: str, lang_code: str = "en") -> dict:
    """Copy for an image ad. Never invents statistics, reviews, awards or medical/health claims."""
    h_lim = LIMITS[platform][0]
    if llm_available():
        try:
            out = _ask(
                "You write image-ad copy. Reply with ONLY a JSON object. Hard rules: do not invent statistics, "
                "customer counts, reviews, testimonials, awards, discounts or prices; do not make health, weight-loss "
                "or income claims; use only facts from the brief.",
                f"Language: {language}\nPlatform style: {STYLE[platform].format(h=h_lim)}\nProduct: {product}\n"
                f"Description: {description or 'n/a'}\nAudience: {audience or 'general'}\nTone: {tone}\n"
                "JSON keys: headline, accent (one word copied from the headline to highlight), "
                "subheadline (<= 60 chars), bullets (exactly 3 items, <= 18 chars each, based only on the brief), "
                "cta (<= 18 chars).", 600)
            d = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
            d["headline"] = str(d["headline"])[:h_lim + 15]
            d["bullets"] = [str(b)[:22] for b in d.get("bullets", [])][:3]
            d["subheadline"] = str(d.get("subheadline", ""))[:70]
            d["cta"] = str(d.get("cta", "Learn More"))[:20]
            d["accent"] = str(d.get("accent", ""))
            return d
        except Exception:
            pass
    h, sub, bullets, cta = FALLBACK.get(lang_code, FALLBACK["en"])
    h = h.format(p=product)
    return {"headline": h[:h_lim + 15], "accent": product.split()[0] if product else "",
            "subheadline": sub.format(p=product)[:70], "bullets": bullets, "cta": cta}


def scene_prompt(product: str, description: str, audience: str, tone: str, theme: str, hint: str) -> str:
    """Prompt for a product-less background scene (the real product photo is composited later)."""
    # The product name/description are deliberately NOT put in the fallback: image models draw whatever they are
    # told about, and the real product photo is composited on top afterwards.
    fallback = (f"Cinematic empty lifestyle background, {hint or 'clean modern interior with a table surface'}, "
                f"soft dramatic lighting, shallow depth of field, {theme} color palette, empty space in the center, "
                "no objects in the middle, no text, no letters, no logos, no watermark")
    if not llm_available():
        return fallback[:780]
    try:
        out = _ask(
            "You write prompts for a text-to-image model. Reply with ONLY the prompt (max 600 chars), English.",
            f"Product: {product}\nDescription: {description or 'n/a'}\nAudience: {audience or 'general'}\nTone: {tone}\n"
            f"Palette: {theme}\nArt direction from the advertiser: {hint or 'none'}\n"
            "Describe a photorealistic advertising BACKGROUND scene for this product: setting, lighting, mood, camera. "
            "Rules: do not name or describe the product, its category or any packaged goods - only the setting; the product must NOT appear; leave calm empty space in the middle third; keep the top 20% "
            "and bottom 20% uncluttered; absolutely no text, letters, numbers, logos, packaging, screens or watermarks; "
            "no before/after imagery, medical imagery or body close-ups.", 400).strip().strip('"')
        return (out + ", no text, no letters, no logos, no product")[:780]
    except Exception:
        return fallback[:780]


def qa_scene(img) -> tuple[bool, str]:
    """Claude vision check on a generated scene. Returns (ok, reason). Fails open if the check itself errors."""
    if not llm_available():
        return True, ""
    try:
        import base64
        import io
        from anthropic import Anthropic
        im = img.convert("RGB")
        im.thumbnail((768, 768))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        msg = Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
            model=AI_MODEL, max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": "This is a background for an ad; a product and text will be added on top. "
                 'Reply ONLY JSON {"ok": bool, "reason": str}. ok=false if it contains any text/letters/logos/watermark, '
                 "distorted faces or hands, a prominent product-like object, or a cluttered center."}]}])
        d = json.loads(re.search(r"\{.*\}", "".join(b.text for b in msg.content if b.type == "text"), re.S).group(0))
        return bool(d.get("ok")), str(d.get("reason", ""))[:120]
    except Exception:
        return True, ""


def video_prompt(description: str, tone: str, theme: str, hint: str, duration: int) -> str:
    """Prompt for a product-less background video. Product name/description are deliberately not used."""
    base = (f"Cinematic empty lifestyle background footage, {hint or 'clean modern interior with a table surface'}, "
            f"slow gentle camera push-in, subtle ambient motion, soft dramatic lighting, {theme} color palette, "
            "empty calm space in the center, no people close-up, no text, no letters, no logos, no watermark")
    if not llm_available():
        return base
    try:
        out = _ask(
            "You write prompts for a text-to-video model. Reply with ONLY the prompt (max 500 chars), English.",
            f"Tone: {tone}\nPalette: {theme}\nAdvertiser's scene idea: {hint or 'none'}\nClip length: {duration}s\n"
            "Describe ONE continuous photorealistic background shot for an ad: setting, lighting, slow camera move, mood. "
            "Rules: do not name or describe any product, category or packaged goods; keep the center third calm and "
            "empty, top and bottom uncluttered; no text, letters, numbers, logos, screens or watermarks; no medical "
            "imagery, no body close-ups, no before/after.", 350).strip().strip('"')
        return (out + ", no text, no logos, no product")[:900]
    except Exception:
        return base
