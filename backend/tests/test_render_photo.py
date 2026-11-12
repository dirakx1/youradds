import unittest

from PIL import Image, ImageDraw

from app import render


class PhotoPlacementTests(unittest.TestCase):
    def test_transparent_padding_does_not_push_the_product_off_centre(self):
        prod = Image.new("RGBA", (1000, 1000), (0, 0, 0, 0))
        ImageDraw.Draw(prod).rectangle([600, 100, 900, 800], fill=(200, 40, 40, 255))   # far right inside the file
        base = Image.new("RGBA", (1080, 1080), (0, 0, 0, 255))
        px, py, pw, ph = render._paste_product(base, prod, (75, 340, 1005, 780), (255, 200, 80), glow_on=False)
        self.assertAlmostEqual(px + pw / 2, 540, delta=2)               # centred in the box
        self.assertAlmostEqual(py + ph / 2, 560, delta=2)

    def test_opaque_photo_gets_soft_corners(self):
        photo = Image.new("RGBA", (900, 700), (255, 255, 255, 255))
        base = Image.new("RGBA", (1080, 1080), (0, 0, 0, 255))
        px, py, pw, ph = render._paste_product(base, photo, (75, 340, 1005, 780), (255, 200, 80), glow_on=False)
        self.assertEqual(base.getpixel((px + 1, py + 1))[:3], (0, 0, 0))        # corner pixel stays background
        self.assertEqual(base.getpixel((px + pw // 2, py + ph // 2))[:3], (255, 255, 255))

    def test_blank_image_does_not_crash(self):
        render.render((1080, 1080), {"headline": "Hola", "cta": "Ya"}, Image.new("RGBA", (50, 50), (0, 0, 0, 0)), "purple", "")


if __name__ == "__main__":
    unittest.main()
