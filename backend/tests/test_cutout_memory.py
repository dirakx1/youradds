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

    def _body(self):
        import base64, io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10)).save(buf, "PNG")
        return cutout.CutIn(image=base64.b64encode(buf.getvalue()).decode())

    def test_model_is_released_after_every_cutout_even_when_it_fails(self):
        from PIL import Image
        for result in (lambda img: Image.new("RGBA", (4, 4)), RuntimeError("boom")):
            cutout._session = object()
            fn = result if callable(result) else (lambda img, e=result: (_ for _ in ()).throw(e))
            with patch.object(cutout, "available", return_value=True), patch.object(cutout, "free_mb", return_value=900), \
                    patch.object(cutout, "remove_background", side_effect=fn):
                try:
                    cutout.cutout(self._body())
                except HTTPException as e:
                    self.assertEqual(e.status_code, 502)
            self.assertIsNone(cutout._session)


if __name__ == "__main__":
    unittest.main()
