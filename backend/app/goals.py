"""Goal-based campaign planning: the owner says what they want ("que me conozcan", "que me compren", "recuperar a los
que no compraron") and this builds the campaign structure: which campaigns, with what objective, audience and
budget split. Pure functions, no database, so it is cheap to test. Audiences are plain-language descriptions of who
to reach; nothing here invents numbers or claims."""
from typing import Optional

GOALS = ("awareness", "sales", "recover")

# goal -> (summary, stages). A stage is (name suffix, objective, share of the daily budget, audience kind, why)
PLANS = {
    "awareness": ("Que más gente te conozca: primero llegas a tu público nuevo y luego traes a los interesados a tu página.", [
        ("Que me conozcan · público nuevo", "awareness", 0.7, "cold", "Muestra tu marca a personas parecidas a tu cliente ideal."),
        ("Que me conozcan · visitas a tu página", "traffic", 0.3, "cold", "Lleva a los interesados a conocer tu producto."),
    ]),
    "sales": ("Vender más: una campaña busca clientes nuevos y otra insiste con quienes ya te visitaron.", [
        ("Que me compren · público nuevo", "sales", 0.7, "cold", "Anuncios de venta para personas parecidas a tu cliente ideal."),
        ("Que me compren · visitantes recientes", "sales", 0.3, "warm", "Recuerda tu producto a quienes ya entraron y no compraron."),
    ]),
    "recover": ("Recuperar ventas: vuelves a hablarle a quienes mostraron interés y no compraron.", [
        ("Recuperar · visitantes sin compra", "sales", 0.6, "warm", "Anuncios para quienes visitaron tu página o redes y no compraron."),
        ("Recuperar · conversaciones de WhatsApp", "whatsapp", 0.4, "chat", "Invita a retomar la conversación a quienes te escribieron y no cerraron."),
    ]),
}
WARM = "Personas que visitaron tu página o interactuaron con tu marca en los últimos 30 días y no compraron"
CHAT = "Personas que te escribieron por WhatsApp en los últimos 30 días y no compraron"


def _split(total: float, share: float, cop: bool) -> float:
    v = total * share
    return float(max(1000, round(v, -3))) if cop else round(max(1.0, v), 2)


CTAS = {True: ["Compra ahora", "Escríbenos por WhatsApp", "Más información", "Conoce más", "Pide el tuyo"],
        False: ["Shop now", "Message us on WhatsApp", "Learn more", "Get yours", "See more"]}
AUDIENCES = {
    "cold": ["Personas en tu ciudad interesadas en {p}", "Personas parecidas a quienes ya te compraron",
             "Hombres y mujeres de 25 a 45 años interesados en {p}"],
    "warm": ["Quienes vieron {p} en los últimos 7 días y no compraron", "Quienes agregaron al carrito y no compraron",
             "Quienes interactuaron con tus redes en los últimos 30 días"],
    "chat": ["Quienes te escribieron por WhatsApp y no cerraron la compra", "Clientes que te escribieron hace más de una semana"],
}


def _suggestions(kind: str, product: str, current: dict, variants: list[dict], cop: bool, ideal: str) -> dict:
    """Alternatives the owner can click to replace a field: other copy angles, stock buttons, other audiences."""
    def pick(key, extra=()):
        seen, out = {str(current.get(key, "")).strip().lower()}, []
        for v in [*(x.get(key, "") for x in variants), *extra]:
            v = str(v).strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
        return out[:4]
    aud = [a.format(p=product) for a in AUDIENCES[kind]]
    if kind == "cold" and ideal.strip():
        aud.insert(0, ideal.strip()[:120])
    return {"headline": pick("headline"), "body": pick("body"), "cta": pick("cta", CTAS[cop]),
            "audience": [a for a in aud if a != current.get("audience")][:3]}


def build_plan(goal: str, platform: str, product: str, daily_budget: float, ideal_customer: str, variants: list[dict],
               landing_url: str = "", cop: bool = True, limits: Optional[tuple] = None) -> dict:
    """Return {"goal", "summary", "campaigns": [CampaignIn-shaped dicts + "why"]}."""
    summary, stages = PLANS[goal]
    cold = ideal_customer.strip()[:120] or "Personas parecidas a tu cliente ideal"
    out = []
    for i, (name, objective, share, kind, why) in enumerate(stages):
        v = variants[i % len(variants)] if variants else {}
        out.append({"platform": platform, "name": f"{product} · {name}"[:80], "objective": objective,
                    "daily_budget": _split(daily_budget, share, cop),
                    "headline": v.get("headline", product), "body": v.get("body", ""), "cta": v.get("cta") or "Más información",
                    "landing_url": landing_url, "audience": {"cold": cold, "warm": WARM, "chat": CHAT}[kind], "why": why})
        out[-1]["suggestions"] = _suggestions(kind, product, out[-1], variants, cop, ideal_customer)
    return {"goal": goal, "summary": summary, "campaigns": out,
            "limits": {"headline": limits[0], "body": limits[1]} if limits else None}
