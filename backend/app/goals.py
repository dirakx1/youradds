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


def build_plan(goal: str, platform: str, product: str, daily_budget: float, ideal_customer: str, variants: list[dict],
               landing_url: str = "", cop: bool = True) -> dict:
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
    return {"goal": goal, "summary": summary, "campaigns": out}
