"""'Mi marca': the brand bible a user fills in once. Creatives, video, copy, the Copilot and the WhatsApp agent
read it instead of asking for product / audience / tone / language again."""
import json
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import ai
from .auth import current_user
from .db import session_scope
from .models import Brand

router = APIRouter(prefix="/api/brand", dependencies=[Depends(current_user)])

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
JSON_FIELDS = ("palette", "products", "reviews")
TEXT_FIELDS = ("name", "story", "tone", "language", "currency", "fonts", "ideal_customer", "objections", "limits")

# The guided interview: (field, Spanish question, hint). The frontend walks these in order and saves as it goes.
INTERVIEW = [
    ("name", "¿Cómo se llama tu negocio o marca?", "Ej.: Café La Montaña"),
    ("story", "Cuéntame su historia en pocas líneas: ¿quién la creó, por qué y qué la hace distinta?", ""),
    ("ideal_customer", "¿Quién es tu cliente ideal? Edad, dónde vive, qué le importa.", "Ej.: Mujeres de 25 a 45 en Medellín que quieren café de origen."),
    ("tone", "¿Cómo le hablas a tus clientes?", "Ej.: cercano, divertido, profesional, elegante."),
    ("products", "¿Qué vendes? Escribe un producto por línea con precio: nombre | descripción corta | precio", "Ej.: Café 500 g | Grano de origen, tostión media | $38.000"),
    ("objections", "¿Qué dudas o excusas ponen tus clientes para no comprar?", "Ej.: “Es caro”, “no sé si llega a mi ciudad”."),
    ("reviews", "¿Qué reseñas reales te han dejado? Una por línea: nombre: comentario. Solo reales, nunca inventadas.", "Puedes dejarlo vacío."),
    ("limits", "¿Qué nunca debemos decir o mostrar en tus anuncios?", "Ej.: no prometer resultados médicos, no hablar de la competencia."),
    ("palette", "¿Cuáles son los colores de tu marca? Hasta tres, en formato #RRGGBB separados por coma.", "Ej.: #7A4A2B, #F3E9DC, #D9822B"),
]


class Product(BaseModel):
    name: str = Field(max_length=80)
    description: str = Field("", max_length=500)
    price: str = Field("", max_length=20)


class Review(BaseModel):
    name: str = Field(max_length=40)
    text: str = Field(max_length=140)


class BrandIn(BaseModel):
    name: str = Field("", max_length=80)
    story: str = Field("", max_length=1500)
    tone: str = Field("", max_length=60)
    language: str = Field("es", max_length=5)
    currency: str = Field("COP", max_length=3)
    palette: list[str] = Field(default_factory=list, max_length=3)
    fonts: str = Field("", max_length=80)
    ideal_customer: str = Field("", max_length=500)
    objections: str = Field("", max_length=800)
    limits: str = Field("", max_length=800)
    products: list[Product] = Field(default_factory=list, max_length=10)
    reviews: list[Review] = Field(default_factory=list, max_length=10)

    @field_validator("palette")
    @classmethod
    def _hex(cls, v):
        if any(not HEX.match(c) for c in v):
            raise ValueError("colors must look like #RRGGBB")
        return [c.lower() for c in v]


def _row_dict(row: Brand) -> dict:
    out = {k: getattr(row, k) or "" for k in TEXT_FIELDS}
    for k in JSON_FIELDS:
        try:
            out[k] = json.loads(getattr(row, k) or "[]")
        except ValueError:
            out[k] = []
    out["completed"] = bool(row.completed_at)
    return out


def get(user_id: int) -> Optional[dict]:
    with session_scope() as session:
        row = session.get(Brand, user_id)
        return _row_dict(row) if row else None


def is_complete(b: dict) -> bool:
    """Minimum the generators need: a name, one product and a customer description."""
    return bool(b["name"].strip() and any(p["name"].strip() for p in b["products"]) and b["ideal_customer"].strip())


@router.get("")
def read(user: dict = Depends(current_user)):
    return {"brand": get(user["id"]), "interview": [{"field": f, "question": q, "hint": h} for f, q, h in INTERVIEW]}


@router.put("")
def save(body: BrandIn, user: dict = Depends(current_user)):
    data = body.model_dump()
    products = [p for p in data["products"] if p["name"].strip()]
    reviews = [r for r in data["reviews"] if r["name"].strip() and r["text"].strip()]
    data["products"], data["reviews"] = products, reviews
    complete = is_complete(data)
    values = {k: data[k].strip() for k in TEXT_FIELDS}
    values.update({k: json.dumps(data[k], ensure_ascii=False) for k in JSON_FIELDS})
    with session_scope() as session:
        existing = session.get(Brand, user["id"])
        done = existing.completed_at if existing and existing.completed_at else None
        values["completed_at"] = (done or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")) if complete else None
        stmt = insert(Brand).values(user_id=user["id"], **values)
        session.execute(stmt.on_conflict_do_update(index_elements=[Brand.user_id],
                                                  set_={**values, "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}))
    return get(user["id"])


class RefineIn(BaseModel):
    story: str = Field("", max_length=1500)
    tone: str = Field("", max_length=60)


@router.post("/refine")
def refine(body: RefineIn, user: dict = Depends(current_user)):
    """Optional: tighten the story the owner typed. Uses only what they wrote; without an LLM it returns it unchanged."""
    story = body.story.strip()
    if not story or not ai.llm_available():
        return {"story": story, "llm": False}
    try:
        out = ai._ask("You edit a small business's brand story in Spanish. Keep every fact exactly; add none. "
                      "Reply with ONLY the improved text, max 600 characters.", f"Tone: {body.tone or 'cercano'}\n\n{story}", 400)
        return {"story": out.strip()[:600], "llm": True}
    except Exception:
        return {"story": story, "llm": False}


# ---- how the rest of the app uses the brand -------------------------------------------------------------

def theme_for(b: dict) -> Optional[str]:
    """Encoded theme string ('brand:#aaaaaa,#bbbbbb,#cccccc') understood by render.resolve_theme, or None."""
    return "brand:" + ",".join(b["palette"]) if b and b["palette"] else None


def fill(user_id: int, body, *, kind: str) -> Optional[dict]:
    """Fill the fields the request left empty from the brand (explicit values always win), validate what's still
    required, and return the brand (or None when the account has none yet). Mutates ``body`` in place so a stored
    request (video jobs) replays with the same values."""
    b = get(user_id)
    if b is None or not b["completed"]:
        if not getattr(body, "product", "").strip():
            raise HTTPException(400, "Create your brand first (Mi marca) or enter a product")
        b = b if b and b["completed"] else None
    if b:
        product = next((p for p in b["products"] if p["name"] == body.product), None)
        if not body.product.strip():
            product = b["products"][0]
            body.product = product["name"]
        if product:
            if hasattr(body, "description") and not body.description:
                body.description = product["description"]
            if hasattr(body, "price") and not body.price:
                body.price = product["price"]
        if hasattr(body, "audience") and not body.audience:
            body.audience = b["ideal_customer"][:120]
        if hasattr(body, "tone") and not body.tone:
            body.tone = b["tone"] or "friendly"
        if not body.language:
            body.language = b["language"]
        if hasattr(body, "theme") and not body.theme:
            body.theme = theme_for(b) or "purple"
        if hasattr(body, "testimonials") and not body.testimonials and kind == "creative":
            body.testimonials = b["reviews"][:3]     # the owner's own real reviews, never generated ones
    if not body.language:
        body.language = "es"
    if hasattr(body, "theme") and not body.theme:
        body.theme = "purple"
    return b


def prompt_block(b: Optional[dict]) -> str:
    """Brand facts and limits for LLM prompts. Only what the owner wrote."""
    if not b:
        return ""
    lines = [f"Brand: {b['name']}"]
    for label, key in (("Story", "story"), ("Tone of voice", "tone"), ("Ideal customer", "ideal_customer"),
                       ("Common objections", "objections")):
        if b.get(key):
            lines.append(f"{label}: {b[key]}")
    if b.get("limits"):
        lines.append(f"NEVER say or show (hard rule): {b['limits']}")
    return "\n".join(lines)
