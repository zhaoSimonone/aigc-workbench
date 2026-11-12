import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "import_video", Path(__file__).resolve().parents[1] / "scripts/import_video.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(
            root=self.root / "media", engine="yt-dlp",
            urls=[f"https://www.instagram.com/p/{code}/" for code in ("AAA", "BBB", "CCC")],
            report=self.root / "report.json", delay=30, delay_jitter=30,
            cookies_from_browser="", proxy="", timeout=60)
        self.clock = 1000.0
        self.waits = []
        for patcher in (
            patch.dict(workflow.os.environ, {"AIGC_WORKBENCH_STATE_DIR": str(self.root / "state")}),
            patch.object(workflow.time, "time", side_effect=lambda: self.clock),
            patch.object(workflow.time, "sleep", side_effect=self.sleep),
            patch.object(workflow.random, "uniform", return_value=15),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.clock += seconds

    def test_pause_bounds_and_invalid_settings(self):
        for endpoint in (0, 30):
            with patch.object(workflow.random, "uniform", return_value=endpoint):
                self.assertEqual(workflow.pace_pause(30, 30), 30 + endpoint)
        for delay, jitter in ((0, 0), (29, 30), (30, -1), (float("nan"), 30),
                              (30, float("inf"))):
            with self.subTest(delay=delay, jitter=jitter), self.assertRaises(ValueError):
                workflow.pace_pause(delay, jitter)

    def test_serial_downloads_wait_after_previous_completion(self):
        started = []

        def download(raw, args):
            started.append((raw, self.clock))
            self.clock += 10
            return {"sourceUrl": raw}

        with patch.object(workflow, "prepare_one", side_effect=download):
            self.assertEqual(workflow.prepare(self.args), 0)
        self.assertEqual([url for url, _ in started], self.args.urls)
        self.assertEqual([stamp for _, stamp in started], [1000, 1055, 1110])
        self.assertEqual(self.waits, [45, 45])

    def test_failure_stops_remaining_and_preserves_success(self):
        with patch.object(workflow, "prepare_one",
                          side_effect=[{"videoId": "AAA"}, RuntimeError("challenge_required")]) as download:
            self.assertEqual(workflow.prepare(self.args), 1)
        self.assertEqual(download.call_count, 2)
        report = workflow.read_json(self.args.report)
        self.assertEqual(report["items"], [{"videoId": "AAA"}])
        self.assertEqual(report["errors"][0]["status"], "stopped")
        self.assertEqual(report["notAttempted"], self.args.urls[2:])
        self.assertEqual(self.waits, [45])

    def test_next_command_with_another_root_respects_cooldown(self):
        self.args.urls = self.args.urls[:1]
        with patch.object(workflow, "prepare_one", return_value={"videoId": "AAA"}):
            workflow.prepare(self.args)
            self.assertEqual(self.waits, [])
            self.clock += 10
            self.args.root = self.root / "other-media"
            workflow.prepare(self.args)
        self.assertEqual(self.waits, [35])

    def test_shared_lock_prevents_another_batch_before_requests(self):
        with workflow.locked(self.root / "state/video-download-pacing"), \
                patch.object(workflow, "prepare_one") as download:
            with self.assertRaisesRegex(RuntimeError, "Another video download batch"):
                workflow.prepare(self.args)
        download.assert_not_called()

    def test_repeated_shortcode_is_not_downloaded_twice(self):
        self.args.urls = [self.args.urls[0], "https://www.instagram.com/reels/AAA/?share=x"]
        with patch.object(workflow, "prepare_one", return_value={"videoId": "AAA"}) as download:
            self.assertEqual(workflow.prepare(self.args), 0)
        download.assert_called_once()

    def test_cli_defaults_and_legacy_noop(self):
        with patch.object(workflow.sys, "argv", [
            "import_video.py", "prepare", self.args.urls[0],
            "--report", str(self.args.report), "--no-social-metrics",
        ]):
            args = workflow.parse_args()
        self.assertEqual((args.delay, args.delay_jitter, args.engine), (30, 30, "yt-dlp"))

    def test_metrics_command_and_invalid_delay_are_rejected(self):
        for argv in (["metrics"], ["prepare", self.args.urls[0],
                                    "--report", str(self.args.report), "--delay", "0"]):
            with patch.object(workflow.sys, "argv", ["import_video.py", *argv]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                workflow.parse_args()

    def test_ytdlp_no_comment_expansion_retries_or_parallel_fragments(self):
        command = workflow.build_ytdlp_command(self.args.urls[0], self.root, self.args)
        for flag in ("--retries", "--extractor-retries", "--fragment-retries"):
            self.assertEqual(command[command.index(flag) + 1], "0")
        self.assertEqual(command[command.index("--concurrent-fragments") + 1], "1")
        self.assertIn("--no-write-comments", command)
        self.assertIn("--no-playlist", command)

    def test_download_discards_incidental_social_counts(self):
        path = self.root / "media.mp4"
        path.touch()
        info = dict(filepath=str(path), id="AAA", title="Title", like_count=99,
                    comment_count=88, view_count=77, comments=[{"text": "comment"}])
        with patch.object(workflow, "run", return_value=json.dumps(info)) as run:
            _, metadata = workflow.download_ytdlp(self.args.urls[0], self.root, self.args)
        run.assert_called_once()
        self.assertNotIn("socialMetrics", metadata)
        for key in ("like_count", "comment_count", "comments", "view_count"):
            self.assertNotIn(key, metadata)

    def test_optional_ytdlp_pacing_flags(self):
        self.args.ytdlp_sleep_requests = 2
        self.args.ytdlp_sleep_interval = 3.5
        self.args.ytdlp_limit_rate = "800K"
        command = workflow.build_ytdlp_command(self.args.urls[0], self.root, self.args)
        for flag, value in (("--sleep-requests", "2"), ("--sleep-interval", "3.5"),
                            ("--limit-rate", "800K")):
            self.assertEqual(command[command.index(flag) + 1], value)


if __name__ == "__main__":
    unittest.main()
