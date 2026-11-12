import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import cutout


class CutoutMemoryTests(unittest.TestCase):
    def test_light_model_on_small_servers_and_forced_override(self):
        with patch.object(cutout, "free_mb", return_value=334):
            self.assertEqual(cutout.model_name(), "u2netp")
        with patch.object(cutout, "free_mb", return_value=4000):
            self.assertEqual(cutout.model_name(), "isnet-general-use")
        with patch.object(cutout, "free_mb", return_value=334), patch.dict("os.environ", {"CUTOUT_MODEL": "silueta"}):
            self.assertEqual(cutout.model_name(), "silueta")

    def test_refuses_instead_of_crashing_when_memory_is_nearly_gone(self):
        import base64, io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10)).save(buf, "PNG")
        body = cutout.CutIn(image=base64.b64encode(buf.getvalue()).decode())
        with patch.object(cutout, "available", return_value=True), patch.object(cutout, "free_mb", return_value=100):
            with self.assertRaises(HTTPException) as c:
                cutout.cutout(body)
        self.assertEqual(c.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
