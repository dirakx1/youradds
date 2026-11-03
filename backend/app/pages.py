"""Language-prefixed page routes: the app shell and static pages live under /en/ and /es/; the old un-prefixed
URLs redirect to the visitor's language. No database or auth imports, so it is cheap to test on its own."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

UI_LANGS = ("en", "es")
APP_PAGES = ("", "sign-in", "sign-up", "forgot-password", "sso-callback", "account")   # served by the SPA shell
STATIC_PAGES = ("about", "contact", "privacy", "terms")                                  # frontend/<lang>/<page>/index.html
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def preferred_lang(request: Request) -> str:
    """Language for un-prefixed URLs: the visitor's saved choice, else the browser's Accept-Language, else English."""
    saved = request.cookies.get("lang", "")
    if saved in UI_LANGS:
        return saved
    ranked = []
    for part in request.headers.get("accept-language", "").split(","):
        tag, _, q = part.strip().partition(";q=")
        try:
            ranked.append((-float(q or 1), tag.strip().lower()[:2]))
        except ValueError:
            continue
    return next((tag for _, tag in sorted(ranked) if tag in UI_LANGS), "en")


def _shell(lang: str, frontend: Path):
    html = (frontend / "index.html").read_text(encoding="utf-8").replace('<html lang="en">', f'<html lang="{lang}">', 1)
    return HTMLResponse(html, headers={"Content-Language": lang, "Cache-Control": "no-cache"})


def register(app: FastAPI, frontend: Path = FRONTEND):
    def add(path, endpoint):
        app.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False)

    for lang in UI_LANGS:
        for page in APP_PAGES:
            add(f"/{lang}/{page}" if page else f"/{lang}/", lambda lang=lang: _shell(lang, frontend))
        add(f"/{lang}", lambda lang=lang: RedirectResponse(f"/{lang}/"))

    def legacy(target: str):
        def go(request: Request):
            qs = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(f"/{preferred_lang(request)}{target}{qs}", status_code=302)
        return go

    for page in APP_PAGES:
        target = f"/{page}" if page else "/"
        add(target, legacy(target))
    for page in STATIC_PAGES:
        for path in (f"/{page}", f"/{page}/"):
            add(path, legacy(f"/{page}/"))
