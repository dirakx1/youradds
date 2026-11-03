import unittest
from unittest.mock import patch

from app import ai


class StudioCopyTests(unittest.TestCase):
    def test_templates_cover_every_creative_language_with_four_angles(self):
        from app.creatives import LANGS
        for code in LANGS:
            with self.subTest(code=code), patch("app.ai.llm_available", return_value=False):
                out = ai.generate_copy("meta", "Zapatos", "corredores", "bold", 4, LANGS[code], code)
                self.assertEqual(len(out), 4)
                self.assertTrue(all(v["headline"] and v["body"] and v["cta"] for v in out))
                self.assertTrue(all("{" not in v["headline"] + v["body"] for v in out))

    def test_templates_are_actually_translated_and_respect_limits(self):
        with patch("app.ai.llm_available", return_value=False):
            en = ai.generate_copy("google", "Shoes", "Runners", "bold", 4, "English", "en")
            es = ai.generate_copy("google", "Shoes", "Runners", "bold", 4, "Spanish", "es")
            self.assertNotEqual(en[1]["headline"], es[1]["headline"])
            self.assertTrue(all(len(v["headline"]) <= 30 and len(v["body"]) <= 90 for v in es))

    def test_default_language_is_unchanged_english(self):
        with patch("app.ai.llm_available", return_value=False):
            self.assertEqual(ai.generate_copy("meta", "Shoes", "Runners", "bold")[0]["headline"], "Shoes: Made for You")

    def test_llm_prompt_requests_the_chosen_language(self):
        seen = {}
        def fake(system, user, *a):
            seen["s"], seen["u"] = system, user
            return '[{"headline":"Hola","body":"Cuerpo","cta":"Ya","angle":"x"}]'
        with patch("app.ai.llm_available", return_value=True), patch("app.ai._ask", side_effect=fake):
            out = ai.generate_copy("meta", "Shoes", "Runners", "bold", 1, "Spanish", "es")
        self.assertIn("Spanish", seen["s"]); self.assertIn("in Spanish", seen["u"])
        self.assertEqual(out[0]["headline"], "Hola")


if __name__ == "__main__":
    unittest.main()
