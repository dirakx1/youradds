"""Video pipeline speed/robustness pieces that need no database or Higgsfield: ffmpeg settings, overlay prep during the
render wait, polling backoff, request body, and per-format selection."""
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app import imagegen, videos


def make_body(**kw):
    return videos.VidIn(**{"platforms": ["meta", "tiktok", "google"], "product": "Shoes", **kw})


class FfmpegTests(unittest.TestCase):
    def test_composite_uses_low_memory_fast_settings(self):
        with patch("app.videos.subprocess.run", return_value=MagicMock(returncode=0)) as run, \
                patch.object(Path, "exists", return_value=True):
            videos._composite(Path("c.mp4"), Path("o.png"), Path("out.mp4"), 1080, 1920, 5)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-preset") + 1], "veryfast")
        self.assertEqual(cmd[cmd.index("-threads") + 1], "1")
        self.assertEqual(cmd[cmd.index("-filter_threads") + 1], "1")

    def test_composite_really_produces_a_playable_cover_fit_video(self):
        import imageio_ffmpeg
        from PIL import Image
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=24:duration=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "clip.mp4")], check=True)
            Image.new("RGBA", (270, 480), (108, 92, 231, 120)).save(d / "ov.png")
            videos._composite(d / "clip.mp4", d / "ov.png", d / "out.mp4", 270, 480, 2)
            info = subprocess.run([ff, "-i", str(d / "out.mp4")], capture_output=True, text=True).stderr
            self.assertRegex(info, r"Video: h264.*270x480")
            self.assertNotIn("Audio:", info)
            self.assertGreater((d / "out.mp4").stat().st_size, 1000)


class RequestBodyTests(unittest.TestCase):
    def test_body_is_audio_free_standard_bitrate_mp4(self):
        b = imagegen._vbody("a prompt", "9:16", 5, "720p")
        self.assertEqual((b["generate_audio"], b["bitrate_mode"], b["output_format"]), (False, "standard", "mp4"))
        allowed = {"prompt", "duration", "resolution", "aspect_ratio", "bitrate_mode", "output_format", "generate_audio"}
        self.assertLessEqual(set(b), allowed)      # the API rejects unknown fields (additionalProperties: false)


class PollingBackoffTests(unittest.TestCase):
    def run_wait(self, polls, **kw):
        responses = [MagicMock(status_code=200, **{"json.return_value": {"status": s}}) for s in polls]
        sleeps = []
        with patch("app.imagegen.httpx.get", side_effect=responses), patch("app.imagegen.time.sleep", side_effect=sleeps.append):
            imagegen.wait("https://api.higgsfield.ai/requests/1/status", 60, "id:secret", **kw)
        return sleeps

    def test_backoff_grows_from_2s_to_a_10s_ceiling_with_bounded_jitter(self):
        sleeps = self.run_wait(["queued"] * 9 + ["completed"], interval=2, max_interval=10)
        base, expected = 2.0, []
        for _ in sleeps:
            expected.append(base)
            base = min(10, base * 1.5)
        for got, want in zip(sleeps, expected):
            self.assertGreaterEqual(got, want)
            self.assertLessEqual(got, want + 0.5)
        self.assertEqual(max(expected), 10)
        self.assertGreater(sleeps[-1], sleeps[0] * 4)      # it really did back off

    def test_default_polling_is_unchanged(self):
        self.assertEqual(set(self.run_wait(["queued"] * 3 + ["completed"], interval=2)), {2})


class FormatSelectionTests(unittest.TestCase):
    def test_default_is_every_format_of_the_chosen_platforms(self):
        sel = videos._selected(make_body())
        self.assertEqual([(p, f) for p, f, _ in sel], [("meta", "reel"), ("meta", "feed_square"), ("tiktok", "vertical"), ("google", "youtube_landscape")])
        self.assertEqual(videos._plan(make_body())[1].keys(), {"9:16", "1:1", "16:9"})

    def test_9x16_only_means_one_generation_for_meta_and_tiktok(self):
        body = make_body(platforms=["meta", "tiktok"], formats=["meta:reel", "tiktok:vertical"])
        self.assertEqual([(p, f) for p, f, _ in videos._selected(body)], [("meta", "reel"), ("tiktok", "vertical")])
        _, est, total = videos._plan(body)
        self.assertEqual(list(est), ["9:16"])
        self.assertEqual(total, round(est["9:16"], 4))
        self.assertLess(total, videos._plan(make_body())[2])

    def test_unknown_or_foreign_formats_are_rejected(self):
        with patch("app.videos.creds.higgsfield_key", return_value="id:secret"):
            videos._validate(make_body(formats=["meta:reel"]), 1)
            for bad in (["meta:nope"], ["google:youtube_landscape"], []):
                with self.subTest(formats=bad), self.assertRaises(HTTPException) as cm:
                    videos._validate(make_body(platforms=["meta"], formats=bad), 1)
                self.assertEqual(cm.exception.status_code, 400)


class OverlaysDuringRenderTests(unittest.TestCase):
    ROWS = [{"id": 1, "platform": "meta", "width": 270, "height": 480},
            {"id": 2, "platform": "tiktok", "width": 270, "height": 480}]

    def test_prepare_runs_while_generation_is_still_pending(self):
        gen_started, release, log = threading.Event(), threading.Event(), []

        def gen(ratio):
            gen_started.set()
            release.wait(5)
            log.append("gen done")

        def prepare():
            self.assertTrue(gen_started.wait(5))
            log.append("prepared while rendering" if not log else "prepared too late")
            release.set()
        videos._run_clips(["9:16", "1:1"], gen, prepare)
        self.assertEqual(log[0], "prepared while rendering")
        self.assertEqual(log.count("gen done"), 2)

    def test_prepare_failure_still_waits_for_generation(self):
        done = []
        def gen(ratio):
            threading.Event().wait(0.2)
            done.append(ratio)
        def prepare():
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            videos._run_clips(["9:16"], gen, prepare)
        self.assertEqual(done, ["9:16"])        # the paid-for clip is not abandoned

    def test_overlays_are_rendered_once_per_row_with_one_copy_per_platform(self):
        body = make_body(platforms=["meta", "tiktok"], language="es")
        with tempfile.TemporaryDirectory() as d:
            copies, overlays, errors = videos._prepare_overlays(self.ROWS, body, None, Path(d))
            self.assertEqual((set(copies), set(overlays), errors), ({"meta", "tiktok"}, {1, 2}, {}))
            self.assertTrue(all(p.exists() and p.stat().st_size > 100 for p in overlays.values()))

    def test_one_bad_row_is_reported_without_losing_the_others(self):
        calls = []
        def flaky(size, *a, **k):
            calls.append(size)
            if len(calls) == 1:
                raise ValueError("font missing")
            return MagicMock()
        with tempfile.TemporaryDirectory() as d, patch("app.videos.render", side_effect=flaky):
            _, overlays, errors = videos._prepare_overlays(self.ROWS, make_body(), None, Path(d))
        self.assertEqual((errors, set(overlays)), ({1: "font missing"}, {2}))


if __name__ == "__main__":
    unittest.main()
