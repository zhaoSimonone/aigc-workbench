import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow = load("import_video", "scripts/import_video.py")


class PacingTests(unittest.TestCase):
    def test_pause_is_zero_without_a_delay(self):
        self.assertEqual(workflow.pace_pause(0, 0), 0)
        self.assertEqual(workflow.pace_pause(None, 10), 0)

    def test_pause_keeps_the_delay_and_adds_jitter(self):
        for _ in range(20):
            pause = workflow.pace_pause(30, 15)
            self.assertGreaterEqual(pause, 30)
            self.assertLessEqual(pause, 45)

    def test_pause_without_jitter_is_exact(self):
        self.assertEqual(workflow.pace_pause(12, 0), 12)

    def test_prepare_waits_between_videos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(
                root=root, engine="yt-dlp", urls=["https://www.instagram.com/p/AAA/", "https://www.instagram.com/p/BBB/"],
                report=root / "report.json", delay=5, delay_jitter=0,
                cookies_from_browser="", proxy="", no_social_metrics=True,
            )
            waits = []
            with patch.object(workflow, "prepare_one", side_effect=RuntimeError("download failed")) as prepare_one, \
                    patch.object(workflow.time, "sleep", side_effect=waits.append):
                workflow.prepare(args)
            # One failure is recorded per item and the batch keeps going; the wait
            # still happens before the second URL.
            self.assertEqual(prepare_one.call_count, 2)
            self.assertEqual(waits, [5])

    def test_prepare_does_not_wait_before_the_first_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(
                root=root, engine="yt-dlp", urls=["https://www.instagram.com/p/AAA/"],
                report=root / "report.json", delay=5, delay_jitter=0,
                cookies_from_browser="", proxy="", no_social_metrics=True,
            )
            waits = []
            with patch.object(workflow, "prepare_one", side_effect=RuntimeError("download failed")), \
                    patch.object(workflow.time, "sleep", side_effect=waits.append):
                workflow.prepare(args)
            self.assertEqual(waits, [])

    def test_ytdlp_pacing_flags_are_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(proxy="", cookies_from_browser="", timeout=60)
            command = workflow.build_ytdlp_command("https://example.test/x", Path(directory), args)
            self.assertNotIn("--sleep-requests", command)
            self.assertNotIn("--limit-rate", command)

    def test_ytdlp_pacing_flags_are_passed_through(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(proxy="", cookies_from_browser="", timeout=60,
                                     ytdlp_sleep_requests=2, ytdlp_sleep_interval=3.5,
                                     ytdlp_limit_rate="800K")
            command = workflow.build_ytdlp_command("https://example.test/x", Path(directory), args)
            self.assertIn("--sleep-requests", command)
            self.assertIn("--sleep-interval", command)
            self.assertIn("--limit-rate", command)
            self.assertEqual(command[command.index("--sleep-requests") + 1], "2")
            self.assertEqual(command[command.index("--limit-rate") + 1], "800K")


if __name__ == "__main__":
    unittest.main()
