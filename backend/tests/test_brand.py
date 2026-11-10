"""'Mi marca' logic that needs no database: filling empty request fields from the brand, theme encoding, prompts."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import brand, creatives, render

BRAND = {"name": "Café La Montaña", "story": "", "tone": "cercano", "language": "es", "currency": "COP",
         "palette": ["#7a4a2b", "#f3e9dc", "#d9822b"], "fonts": "", "ideal_customer": "Mujeres 25-45 en Medellín",
         "objections": "", "limits": "no hablar de la competencia",
         "products": [{"name": "Café 500 g", "description": "Grano de origen", "price": "$38.000"}],
         "reviews": [{"name": "Ana", "text": "Delicioso"}], "completed": True}


class FillTests(unittest.TestCase):
    def fill(self, brand_value, **kw):
        body = creatives.GenIn(platforms=["meta"], **kw)
        with patch.object(brand, "get", return_value=brand_value):
            return body, brand.fill(1, body, kind="creative")

    def test_empty_fields_come_from_the_brand(self):
        body, b = self.fill(BRAND)
        self.assertEqual((body.product, body.description, body.price), ("Café 500 g", "Grano de origen", "$38.000"))
        self.assertEqual((body.audience, body.tone, body.language), ("Mujeres 25-45 en Medellín", "cercano", "es"))
        self.assertTrue(body.theme.startswith("brand:"))
        self.assertTrue(render.valid_theme(body.theme))
        self.assertEqual(body.testimonials, BRAND["reviews"])      # the owner's own reviews only

    def test_explicit_values_win(self):
        body, _ = self.fill(BRAND, product="Otro", language="en", theme="night", tone="bold")
        self.assertEqual((body.product, body.language, body.theme, body.tone), ("Otro", "en", "night", "bold"))
        self.assertEqual(body.price, "")                            # not the brand product's price

    def test_no_brand_needs_a_product_and_defaults_sensibly(self):
        with patch.object(brand, "get", return_value=None), self.assertRaises(HTTPException) as cm:
            brand.fill(1, creatives.GenIn(platforms=["meta"]), kind="creative")
        self.assertEqual(cm.exception.status_code, 400)
        body, b = self.fill(None, product="X")
        self.assertIsNone(b)
        self.assertEqual((body.language, body.theme), ("es", "purple"))


class ThemeAndPromptTests(unittest.TestCase):
    def test_brand_theme_resolves_to_colors(self):
        top, bottom, accent, tx, btn = render.resolve_theme("brand:#7a4a2b,#f3e9dc,#d9822b")
        self.assertEqual(top, (0x7a, 0x4a, 0x2b))
        self.assertEqual(accent, (0xd9, 0x82, 0x2b))
        self.assertLess(sum(bottom), sum(top))
        self.assertFalse(render.valid_theme("brand:red"))
        self.assertTrue(render.valid_theme("night"))

    def test_prompt_block_carries_limits_and_only_written_facts(self):
        text = brand.prompt_block(BRAND)
        self.assertIn("no hablar de la competencia", text)
        self.assertNotIn("Story", text)                             # empty fields are left out
        self.assertEqual(brand.prompt_block(None), "")

    def test_palette_must_be_hex(self):
        with self.assertRaises(ValidationError):
            brand.BrandIn(palette=["rojo"])
        self.assertEqual(brand.BrandIn(palette=["#AABBCC"]).palette, ["#aabbcc"])


if __name__ == "__main__":
    unittest.main()
