import os
import unittest

from PIL import Image, ImageDraw

from app import render


class FontTests(unittest.TestCase):
    def test_bundled_font_has_every_spanish_letter(self):
        for bold in (False, True):
            f = render.font(40, bold)
            self.assertEqual(os.path.basename(os.path.dirname(f.path)), "fonts")   # bundled DejaVu, not machine-dependent

            def pixels(ch):
                im = Image.new("L", (80, 80), 0)
                ImageDraw.Draw(im).text((10, 5), ch, font=f, fill=255)
                return im.tobytes()
            missing = pixels("\U0010ffff")                                  # a character no font has: the "missing" box
            for ch in "áéíóúüñÁÉÍÓÚÜÑ¿¡ãõçâêôàèìòù":
                self.assertNotEqual(pixels(ch), missing, f"{ch} renders as a missing glyph")

    def test_accented_ad_renders(self):
        copy = {"headline": "¡Más pequeño, más sabor!", "cta": "Pídelo ya", "product_name": "Camiseta Ñandú",
                "product_desc": "Algodón peruano: cómodo y durable."}
        for size in ((1080, 1080), (1080, 1920), (1200, 628)):
            self.assertEqual(render.render(size, copy, None, "purple", "$38.000").size, size)


if __name__ == "__main__":
    unittest.main()
