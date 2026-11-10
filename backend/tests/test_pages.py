import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app import pages

REAL_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class LanguageRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        pages.register(app)
        app.mount("/", StaticFiles(directory=REAL_FRONTEND, html=True), name="frontend")
        cls.client = TestClient(app, follow_redirects=False)

    def get(self, path, **headers):
        return self.client.get(path, headers=headers)

    def test_shell_is_served_in_each_language_for_every_app_page(self):
        for lang in pages.UI_LANGS:
            for page in pages.APP_PAGES:
                with self.subTest(lang=lang, page=page):
                    r = self.get(f"/{lang}/{page}")
                    self.assertEqual(r.status_code, 200)
                    self.assertIn(f'<html lang="{lang}">', r.text)
                    self.assertEqual(r.headers["content-language"], lang)
                    self.assertIn("/app.js", r.text)

    def test_static_pages_exist_in_both_languages_and_cross_link(self):
        for lang in pages.UI_LANGS:
            for page in pages.STATIC_PAGES:
                with self.subTest(lang=lang, page=page):
                    r = self.get(f"/{lang}/{page}/")
                    self.assertEqual(r.status_code, 200)
                    self.assertIn(f'<html lang="{lang}">', r.text)
                    self.assertIn(f'href="/en/{page}/"', r.text)       # language switcher
                    self.assertIn(f'href="/es/{page}/"', r.text)
                    self.assertIn("hreflang", r.text)
                    for other in ("about", "contact", "terms", "privacy"):
                        self.assertIn(f'href="/{lang}/{other}/"', r.text)   # footer stays in-language

    def test_spanish_pages_are_actually_spanish_and_complete(self):
        es = {p: self.get(f"/es/{p}/").text for p in pages.STATIC_PAGES}
        self.assertIn("Política de Privacidad", es["privacy"])
        self.assertIn("Términos del Servicio", es["terms"])
        self.assertIn("Contáctanos", es["contact"])
        self.assertIn("Quién está detrás", es["about"])
        for p in pages.STATIC_PAGES:
            self.assertIn("contact@youradds.co", es[p])
            self.assertNotIn("[Operator", es[p])
        en = {p: self.get(f"/en/{p}/").text for p in ("privacy", "terms")}
        for p in ("privacy", "terms"):   # same structure: one <h2> per numbered section
            self.assertEqual(en[p].count("<h2>"), es[p].count("<h2>"), p)

    def test_unprefixed_urls_redirect_to_the_visitors_language(self):
        r = self.get("/", **{"Accept-Language": "es-CO,es;q=0.9,en;q=0.5"})
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/es/"))
        r = self.get("/about/", **{"Accept-Language": "fr,en;q=0.8"})
        self.assertEqual(r.headers["location"], "/en/about/")
        self.assertEqual(self.get("/privacy").headers["location"], "/es/privacy/")           # no header: Spanish (default)
        self.assertEqual(self.get("/terms", **{"Accept-Language": "es"}).headers["location"], "/es/terms/")

    def test_saved_language_cookie_beats_accept_language(self):
        r = self.get("/", Cookie="lang=en", **{"Accept-Language": "es"})
        self.assertEqual(r.headers["location"], "/en/")
        r = self.get("/contact/", Cookie="lang=es", **{"Accept-Language": "en"})
        self.assertEqual(r.headers["location"], "/es/contact/")
        r = self.get("/", Cookie="lang=xx", **{"Accept-Language": "es"})                      # bogus cookie ignored
        self.assertEqual(r.headers["location"], "/es/")

    def test_legacy_auth_urls_redirect_and_keep_the_query_string(self):
        r = self.get("/sign-in?redirect=1", **{"Accept-Language": "es"})
        self.assertEqual(r.headers["location"], "/es/sign-in?redirect=1")
        r = self.get("/sso-callback?__clerk_status=verified")
        self.assertEqual(r.headers["location"], "/es/sso-callback?__clerk_status=verified")

    def test_accept_language_parsing_handles_junk(self):
        class Req:
            def __init__(self, header, cookie=""):
                self.headers = {"accept-language": header}; self.cookies = {"lang": cookie} if cookie else {}
        self.assertEqual(pages.preferred_lang(Req("")), "es")
        self.assertEqual(pages.preferred_lang(Req("es;q=abc, en")), "en")
        self.assertEqual(pages.preferred_lang(Req("en;q=0.4, es;q=0.8")), "es")
        self.assertEqual(pages.preferred_lang(Req("*")), "es")


if __name__ == "__main__":
    unittest.main()
