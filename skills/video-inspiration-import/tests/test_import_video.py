import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "import_video", Path(__file__).resolve().parents[1] / "scripts/import_video.py"
)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
URL = "https://www.tiktok.com/@example/video/123456"


class FakeClient:
    def __init__(self, lose_response=False):
        self.assets = {}
        self.posts = 0
        self.lose_response = lose_response

    def find_existing(self, hashes):
        return {key: self.assets[key] for key in hashes if key in self.assets}

    def upload(self, path, metadata):
        self.posts += 1
        asset = {"id": "asset-1", **metadata}
        self.assets[workflow.digest(path)] = asset
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("Response lost after server committed the asset")
        return asset


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.video = Path(cls.fixture_dir.name) / "fixture.mp4"
        workflow.run([
            workflow.tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "color=c=red:size=180x320:rate=24", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100", "-t", "1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.video),
        ])
        cls.vp9 = Path(cls.fixture_dir.name) / "vp9.mp4"
        workflow.run([
            workflow.tool("ffmpeg"), "-v", "error", "-i", str(cls.video),
            "-c:v", "libvpx-vp9", "-c:a", "copy", str(cls.vp9),
        ])

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(
            root=self.root, engine="yt-dlp", urls=[URL],
            report=self.root / "report.json",
        )

    def fake_download(self, url, directory, args):
        path = directory / "media.mp4"
        shutil.copyfile(self.video, path)
        return path, {
            "engine": "yt-dlp", "expectedDuration": 1,
            "expectedAudio": True, "remoteId": "123456",
        }

    def prepare_item(self):
        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download):
            item = workflow.prepare_one(URL, self.args)
        item.update(name="测试视频", tags=["舞蹈"])
        return item

    def test_link_normalization_and_host_validation(self):
        self.assertEqual(workflow.identify(URL + "?sender=abc"), workflow.identify(URL))
        self.assertEqual(
            workflow.identify("https://www.instagram.com/reels/ABC_123/?share=x"),
            ("instagram", "ABC_123", "https://www.instagram.com/reel/ABC_123/"),
        )
        self.assertEqual(workflow.identify("https://instagram.com/p/ABC/")[2],
                         "https://www.instagram.com/p/ABC/")
        for url in ("https://tiktok.com.evil.test/@a/video/1",
                    "https://instagram.com/profile/",
                    "https://user:secret@www.tiktok.com/@a/video/1",
                    "http://127.0.0.1/video.mp4"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                workflow.identify(url)

    def test_real_probe_previews_and_cache_reuse(self):
        item = self.prepare_item()
        self.assertEqual((item["media"]["width"], item["media"]["height"]), (180, 320))
        self.assertEqual(item["media"]["audioCodecs"], ["aac"])
        self.assertTrue(Path(item["preview"]).is_file())
        self.assertEqual(Path(item["stateFile"]).stat().st_mode & 0o777, 0o600)
        with patch.object(workflow, "download_ytdlp", side_effect=AssertionError("cache not reused")):
            again = workflow.prepare_one(URL + "?sender=other", self.args)
        self.assertEqual(item["sha256"], again["sha256"])

    def test_corrupted_cache_is_downloaded_again(self):
        item = self.prepare_item()
        Path(item["path"]).write_bytes(b"corrupted")
        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download) as download:
            again = workflow.prepare_one(URL, self.args)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(item["sha256"], again["sha256"])

    def test_vp9_download_keeps_source_and_delivers_h264(self):
        with patch.object(self, "video", self.vp9):
            item = self.prepare_item()
        self.assertEqual(workflow.digest(item["sourcePath"]), workflow.digest(self.vp9))
        self.assertEqual(item["sourceMedia"]["videoCodec"], "vp9")
        self.assertTrue(workflow.compatible(item["media"]))
        self.assertTrue(item["fullDecodeVerified"])
        self.assertEqual(item["media"]["frameRate"], item["sourceMedia"]["frameRate"])
        self.assertAlmostEqual(item["media"]["duration"], item["sourceMedia"]["duration"], places=2)

    def test_compatible_download_is_not_reencoded(self):
        item = self.prepare_item()
        self.assertEqual(item["sha256"], workflow.digest(self.video))
        self.assertNotIn("sourcePath", item)

    def legacy_cache(self):
        directory = self.root / "tiktok/123456"
        directory.mkdir(parents=True)
        path = directory / "media.mp4"
        shutil.copyfile(self.vp9, path)
        state = {"platform": "tiktok", "videoId": "123456", "sourceUrl": URL,
                 "path": str(path), "sha256": workflow.digest(path), "media": workflow.probe(path)}
        workflow.write_json(directory / "state.json", state)
        return directory, state

    def test_legacy_vp9_cache_migrates_offline_and_reuses_source_receipt(self):
        directory, old = self.legacy_cache()
        with patch.object(workflow, "download_ytdlp", side_effect=AssertionError("offline")):
            item = workflow.prepare_one(URL, self.args)
        self.assertTrue(workflow.compatible(item["media"]))
        self.assertEqual(workflow.digest(old["path"]), old["sha256"])
        item.update(name="测试视频", tags=["舞蹈"])
        client = FakeClient()
        client.assets[old["sha256"]] = {"id": "previous-vp9"}
        result = workflow.upload_one(item, client)
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["asset"]["id"], "previous-vp9")
        self.assertEqual(client.posts, 0)

    def test_failed_cache_conversion_preserves_old_state(self):
        directory, old = self.legacy_cache()
        with patch.object(workflow, "delivery_copy", side_effect=RuntimeError("encoding failed")):
            with self.assertRaisesRegex(RuntimeError, "encoding failed"):
                workflow.prepare_one(URL, self.args)
        self.assertEqual(workflow.read_json(directory / "state.json"), old)
        self.assertEqual(workflow.digest(old["path"]), old["sha256"])

    def test_decode_failure_blocks_upload_plan(self):
        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download), \
                patch.object(workflow, "full_decode", side_effect=RuntimeError("decode error")):
            with self.assertRaisesRegex(RuntimeError, "decode error"):
                workflow.prepare_one(URL, self.args)
        self.assertFalse((self.root / "tiktok/123456/state.json").exists())

    def test_hdr_is_not_silently_converted_to_sdr(self):
        media = workflow.probe(self.vp9)
        media["colorTransfer"] = "smpte2084"
        with self.assertRaisesRegex(ValueError, "HDR"):
            workflow.delivery_copy(self.vp9, media, self.root / "converted.mp4")

    def test_failed_quality_refresh_preserves_previous_download(self):
        item = self.prepare_item()
        self.args.refresh_download = True
        with patch.object(workflow, "download_ytdlp", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                workflow.prepare_one(URL, self.args)
        self.assertEqual(workflow.digest(item["path"]), item["sha256"])
        self.assertEqual(workflow.read_json(item["stateFile"])["sha256"], item["sha256"])

    def test_auto_tries_clipdock_session_before_browser_capture(self):
        self.args.engine = "auto"
        self.args.cookies_from_browser = ""
        self.args.no_clipdock_cookies = False
        attempts = []

        def downloader(url, directory, args):
            attempts.append(args.cookies_from_browser)
            if not args.cookies_from_browser:
                raise RuntimeError("Login required")
            return self.fake_download(url, directory, args)

        with patch.object(workflow, "CLIPDOCK_PROFILE", self.root), \
                patch.object(workflow, "download_ytdlp", side_effect=downloader), \
                patch.object(workflow, "download_clipdock", side_effect=AssertionError("unneeded capture")):
            item = workflow.prepare_one(URL, self.args)
        self.assertEqual(attempts, ["", f"chrome:{self.root}"])
        self.assertEqual(item["engine"], "yt-dlp")

    def test_invalid_media_never_enters_upload_plan(self):
        def bad_download(url, directory, args):
            path = directory / "media.mp4"
            path.write_text("<html>login required</html>")
            return path, {}
        with patch.object(workflow, "download_ytdlp", side_effect=bad_download):
            with self.assertRaises(RuntimeError):
                workflow.prepare_one(URL, self.args)
        self.assertFalse((self.root / "tiktok/123456/state.json").exists())

    def test_missing_audio_rejected_when_source_has_audio(self):
        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download), \
                patch.object(workflow, "probe", return_value={"duration": 1, "audioCodecs": []}):
            with self.assertRaisesRegex(RuntimeError, "Source has audio"):
                workflow.prepare_one(URL, self.args)

    def test_batch_keeps_success_when_another_link_fails(self):
        self.args.urls = ["https://invalid.test/", URL, URL + "?sender=duplicate"]
        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(workflow.prepare(self.args), 1)
        report = workflow.read_json(self.args.report)
        self.assertEqual(len(report["items"]), 1)
        self.assertEqual(len(report["errors"]), 1)

    def test_upload_preserves_source_and_reconciles_lost_response(self):
        item = self.prepare_item()
        client = FakeClient(lose_response=True)
        with self.assertRaises(TimeoutError):
            workflow.upload_one(item, client)
        result = workflow.upload_one(item, client)
        self.assertEqual(result["status"], "existing")
        self.assertEqual(client.posts, 1)
        self.assertEqual(result["asset"]["sourceUrl"], URL)
        self.assertEqual(result["asset"]["folder"], "灵感收集")
        self.assertEqual(workflow.read_json(item["stateFile"])["asset"]["id"], "asset-1")

    def test_changed_file_is_rejected_before_upload(self):
        item = self.prepare_item()
        Path(item["path"]).write_bytes(b"changed")
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "changed"):
            workflow.upload_one(item, client)
        self.assertEqual(client.posts, 0)

    def test_cached_receipt_does_not_skip_current_account_check(self):
        item = self.prepare_item()
        self.assertEqual(workflow.upload_one(item, FakeClient())["status"], "uploaded")
        another_account = FakeClient()
        self.assertEqual(workflow.upload_one(item, another_account)["status"], "uploaded")
        self.assertEqual(another_account.posts, 1)

    def test_dedup_failure_stops_upload(self):
        item = self.prepare_item()
        client = FakeClient()
        with patch.object(client, "find_existing", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                workflow.upload_one(item, client)
        self.assertEqual(client.posts, 0)

    def test_clipdock_waits_for_audio_pairing_before_download(self):
        waiting = {"id": "capture-1", "ready": True, "status": "waiting"}
        captured = {
            "id": "capture-1", "ready": True, "status": "captured",
            "media": {"hasAudio": True},
        }
        self.args.clipdock = "http://127.0.0.1:3457"
        self.args.capture_timeout = 10
        self.args.timeout = 10
        body = io.BytesIO(self.video.read_bytes())
        body.headers = {"Content-Length": str(len(body.getvalue()))}
        with patch.object(workflow, "clipdock_json", side_effect=[waiting, captured]) as api, \
                patch.object(workflow, "clipdock_request", return_value=body), \
                patch.object(workflow.time, "sleep"):
            path, metadata = workflow.download_clipdock("instagram", URL, self.root, self.args)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(workflow.digest(path), workflow.digest(self.video))
        self.assertTrue(metadata["expectedAudio"])

    def test_highest_resolution_wins_and_separate_audio_is_selected(self):
        info_file = self.root / "formats.json"
        workflow.write_json(info_file, {
            "id": "fixture", "title": "fixture", "extractor": "generic",
            "webpage_url": "https://example.invalid/video",
            "formats": [
                {"format_id": "low", "url": "https://example.invalid/low.mp4",
                 "ext": "mp4", "width": 720, "height": 1280, "fps": 60,
                 "tbr": 5000, "vcodec": "h264", "acodec": "aac", "preference": 10},
                {"format_id": "high", "url": "https://example.invalid/high.mp4",
                 "ext": "mp4", "width": 1080, "height": 1920, "fps": 30,
                 "tbr": 2000, "vcodec": "h264", "acodec": "none", "preference": -10},
                {"format_id": "audio", "url": "https://example.invalid/audio.m4a",
                 "ext": "m4a", "vcodec": "none", "acodec": "aac", "abr": 128},
            ],
        })
        selected = workflow.run([
            workflow.tool("yt-dlp"), "--ignore-config", "--simulate", "--no-warnings",
            "--load-info-json", str(info_file), "-f", workflow.FORMAT, "-S", workflow.SORT,
            "--format-sort-force", "--print", "%(format_id)s",
            "--ffmpeg-location", str(Path(workflow.tool("ffmpeg")).parent),
        ])
        self.assertEqual(selected.strip(), "high+audio")


if __name__ == "__main__":
    unittest.main()
