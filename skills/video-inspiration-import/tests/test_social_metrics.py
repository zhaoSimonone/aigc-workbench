import argparse
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import urllib.error
from unittest.mock import patch


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics_module = load("social_metrics", "scripts/social_metrics.py")
workflow = load("import_video", "scripts/import_video.py")

IG_URL = "https://www.instagram.com/reel/Dfa1yPBq7jZ/"
TIKTOK_URL = "https://www.tiktok.com/@example/video/123456"


class ShortcodeTests(unittest.TestCase):
    def test_shortcode_matches_the_ytdlp_conversion(self):
        try:
            from yt_dlp.extractor.instagram import _id_to_pk
        except ImportError:
            self.skipTest("yt-dlp is not importable")
        for shortcode in ("Dfa1yPBq7jZ", "De4FroJSD0r", "DfePTDDiDx4"):
            with self.subTest(shortcode=shortcode):
                self.assertEqual(metrics_module.shortcode_to_pk(shortcode), _id_to_pk(shortcode))

    def test_shortcode_to_pk_uses_the_documented_media_id(self):
        self.assertEqual(metrics_module.shortcode_to_pk("Dfa1yPBq7jZ"), 4024765765582043353)

    def test_invalid_shortcode_is_rejected(self):
        with self.assertRaises(ValueError):
            metrics_module.shortcode_to_pk("not a shortcode")


class CookieTests(unittest.TestCase):
    NETSCAPE = "\n".join([
        "# Netscape HTTP Cookie File",
        "# comment line",
        ".instagram.com\tTRUE\t/\tTRUE\t13470133439500826\tsessionid\t00000000000%3AFAKE",
        ".instagram.com\tTRUE\t/\tTRUE\t1828863835\tcsrftoken\tfake-csrf-token",
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t13470133439500827\tds_user_id\t00000000000",
        ".tiktok.com\tTRUE\t/\tTRUE\t13470133439500828\tsessionid\tfake-tiktok-session",
    ])

    def test_only_the_requested_domain_is_kept(self):
        jar = metrics_module.parse_cookie_jar(self.NETSCAPE, "instagram.com")
        self.assertEqual(set(jar), {"sessionid", "csrftoken", "ds_user_id"})

    def test_values_are_url_decoded(self):
        jar = metrics_module.parse_cookie_jar(self.NETSCAPE, "instagram.com")
        self.assertEqual(jar["sessionid"], "00000000000:FAKE")

    def test_http_only_cookies_are_read(self):
        jar = metrics_module.parse_cookie_jar(self.NETSCAPE, "instagram.com")
        self.assertEqual(jar["ds_user_id"], "00000000000")


class ExportCookieGuardTests(unittest.TestCase):
    """导出浏览器登录态时的账号护栏：同一 profile 可能留着多个账号，不能拿错。"""

    JAR = "\n".join([
        "# Netscape HTTP Cookie File",
        ".instagram.com\tTRUE\t/\tTRUE\t13470133439500826\tsessionid\tfake-session-value",
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t13470133439500827\tds_user_id\t52498625541",
        ".tiktok.com\tTRUE\t/\tTRUE\t13470133439500828\tsessionid\tother-site-session",
    ])

    def _export(self, jar_text, **kwargs):
        def fake_run(command, **_ignored):
            path = Path(command[command.index("--cookies") + 1])
            path.write_text(jar_text, encoding="utf-8")
            return None
        with patch.object(metrics_module.subprocess, "run", fake_run):
            return metrics_module.export_browser_cookies("chrome", "yt-dlp", **kwargs)

    def test_matching_account_id_is_accepted(self):
        text = self._export(self.JAR, expected_user_id="52498625541")
        self.assertIn("sessionid", text)

    def test_a_different_account_is_refused(self):
        with self.assertRaises(RuntimeError) as caught:
            self._export(self.JAR, expected_user_id="11111111111")
        message = str(caught.exception)
        self.assertIn("52498625541", message)
        self.assertIn("11111111111", message)
        # 护栏信息里不能带出 cookie 值
        self.assertNotIn("fake-session-value", message)

    def test_without_an_expected_id_the_export_still_works(self):
        self.assertIn("sessionid", self._export(self.JAR))

    def test_a_session_without_instagram_is_refused(self):
        jar = "\n".join([
            "# Netscape HTTP Cookie File",
            ".tiktok.com\tTRUE\t/\tTRUE\t13470133439500828\tsessionid\tother-site-session",
        ])
        with self.assertRaises(RuntimeError):
            self._export(jar, expected_user_id="52498625541")


class BrowserAccountGuardTests(unittest.TestCase):
    """prepare 开始前先校验浏览器登录态账号，避免整批请求打到主账号。"""

    def test_guard_is_skipped_without_an_expected_id(self):
        args = argparse.Namespace(cookies_from_browser="chrome", expected_ig_user_id="",
                                  proxy="", timeout=60)
        workflow.verify_browser_account(args)

    def test_guard_is_skipped_without_a_browser_session(self):
        args = argparse.Namespace(cookies_from_browser="", expected_ig_user_id="52498625541",
                                  proxy="", timeout=60)
        workflow.verify_browser_account(args)

    def test_guard_checks_the_expected_account(self):
        """护栏把期望账号交给导出层校验，账号不符时导出层直接报错。"""
        captured = {}

        def fake_export(*_args, **kwargs):
            captured.update(kwargs)
            return ""
        args = argparse.Namespace(cookies_from_browser="chrome", expected_ig_user_id="52498625541",
                                  proxy="", timeout=60)
        with patch.object(workflow.social_metrics, "export_browser_cookies", fake_export):
            workflow.verify_browser_account(args)
        self.assertEqual(captured.get("expected_user_id"), "52498625541")
        self.assertIn("chrome", captured.get("cookies_from_browser", "") or "chrome")


class TiktokCountTests(unittest.TestCase):
    INFO = {
        "extractor": "TikTok", "id": "7671816463304461599",
        "like_count": 12400, "comment_count": 214, "repost_count": 1813,
        "save_count": 2561, "view_count": 70700,
    }

    def test_ytdlp_fields_map_onto_the_shared_count_keys(self):
        counts = metrics_module.collect_tiktok(self.INFO)
        self.assertEqual(counts["like"], 12400)
        self.assertEqual(counts["comment"], 214)
        self.assertEqual(counts["share"], 1813)
        self.assertEqual(counts["save"], 2561)
        self.assertEqual(counts["play"], 70700)

    def test_tiktok_does_not_separate_reposts_from_shares(self):
        counts = metrics_module.collect_tiktok(self.INFO)
        self.assertIsNone(counts["repost"])

    def test_missing_fields_stay_none_instead_of_zero(self):
        counts = metrics_module.collect_tiktok({"like_count": 10})
        self.assertEqual(counts["like"], 10)
        self.assertIsNone(counts["comment"])
        self.assertIsNone(counts["play"])


class InstagramCountTests(unittest.TestCase):
    def test_two_app_ids_are_merged_into_one_snapshot(self):
        calls = []

        def fake_get(url, headers, proxy=None, timeout=40):
            calls.append((url, headers))
            app_id = headers["x-ig-app-id"]
            if app_id == metrics_module.IG_APP_ID_FULL:
                return {"items": [{"like_count": 596, "comment_count": 6, "reshare_count": 41,
                                    "save_count": 194, "play_count": 6762}]}
            return {"items": [{"media_repost_count": 11}]}

        with patch.object(metrics_module, "_http_get_json", side_effect=fake_get):
            counts = metrics_module.collect_instagram("Dfa1yPBq7jZ", {"sessionid": "s", "csrftoken": "c"})
        self.assertEqual(counts, {"like": 596, "comment": 6, "repost": 11,
                                  "share": 41, "save": 194, "play": 6762})
        app_ids = {headers["x-ig-app-id"] for _, headers in calls}
        self.assertIn(metrics_module.IG_APP_ID_FULL, app_ids)
        self.assertIn(metrics_module.IG_APP_ID_REPOST, app_ids)
        for url, _ in calls:
            self.assertIn("4024765765582043353", url)

    def test_a_failing_endpoint_does_not_lose_the_other_counts(self):
        def fake_get(url, headers, proxy=None, timeout=40):
            if headers["x-ig-app-id"] == metrics_module.IG_APP_ID_FULL:
                return {"items": [{"like_count": 5, "comment_count": 1}]}
            raise OSError("network down")

        with patch.object(metrics_module, "_http_get_json", side_effect=fake_get):
            counts = metrics_module.collect_instagram("Dfa1yPBq7jZ", {"sessionid": "s"})
        self.assertEqual(counts["like"], 5)
        self.assertIsNone(counts["repost"])

    def test_no_usable_endpoint_raises(self):
        with patch.object(metrics_module, "_http_get_json", side_effect=OSError("network down")):
            with self.assertRaises(RuntimeError):
                metrics_module.collect_instagram("Dfa1yPBq7jZ", {"sessionid": "s"})

    def test_an_account_challenge_is_raised_instead_of_being_swallowed(self):
        """challenge_required 会被 HTTPError 吞掉，必须单独识别并抛出去。"""

        def fake_get(url, headers, proxy=None, timeout=40):
            raise urllib.error.HTTPError(
                url, 400, "Bad Request", {},
                io.BytesIO(b'{"message":"challenge_required","challenge":{"lock":true}}'))

        with patch.object(metrics_module, "_http_get_json", side_effect=fake_get):
            with self.assertRaises(metrics_module.AccountChallengeError):
                metrics_module.collect_instagram("Dfa1yPBq7jZ", {"sessionid": "s"})

    def test_a_checkpoint_is_raised_the_same_way(self):
        """网页端返回的 checkpoint_required 同样是整批中止的信号。"""

        def fake_get(url, headers, proxy=None, timeout=40):
            raise urllib.error.HTTPError(
                url, 400, "Bad Request", {},
                io.BytesIO(b'{"message":"checkpoint_required"}'))

        with patch.object(metrics_module, "_http_get_json", side_effect=fake_get):
            with self.assertRaises(metrics_module.AccountChallengeError) as caught:
                metrics_module.collect_instagram("Dfa1yPBq7jZ", {"sessionid": "s"})
        self.assertIn("checkpoint_required", str(caught.exception))

    def test_an_ordinary_http_error_is_still_treated_as_a_missing_source(self):
        def fake_get(url, headers, proxy=None, timeout=40):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))

        with patch.object(metrics_module, "_http_get_json", side_effect=fake_get):
            with self.assertRaises(RuntimeError) as caught:
                metrics_module.collect_instagram("Dfa1yPBq7jZ", {"sessionid": "s"})
        self.assertNotIsInstance(caught.exception, metrics_module.AccountChallengeError)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_has_a_fixed_shape_and_marks_missing_counts(self):
        snapshot = metrics_module.build_snapshot(
            "instagram", {"like": 596, "comment": None, "repost": 11}, "instagram-mobile-info")
        self.assertEqual(snapshot["platform"], "instagram")
        self.assertEqual(snapshot["source"], "instagram-mobile-info")
        self.assertEqual(snapshot["like"], 596)
        self.assertIsNone(snapshot["comment"])
        self.assertIsNone(snapshot["save"])
        self.assertTrue(snapshot["capturedAt"].endswith("Z"))

    def test_has_metrics_only_counts_real_numbers(self):
        self.assertTrue(metrics_module.has_metrics({"like": 1, "comment": None}))
        self.assertFalse(metrics_module.has_metrics({"like": None, "comment": None}))


class FakeUploader:
    DEFAULT_API = "https://shelf.example.test"
    DEFAULT_COOKIE_FILE = "/tmp/fake-session.json"

    class Client:
        def __init__(self, api, cookie_file):
            self.api = api
            self.cookie_file = cookie_file
            self.requests = []

        def require(self, method, path, body=None, headers=None, expected=200):
            self.requests.append((method, path, body))
            return {}

        def request(self, method, path, body=None, headers=None):
            self.requests.append((method, path, body))
            payload = json.loads(body.decode("utf-8")) if body else {}
            return 200, {"asset": {"id": "asset-1", "socialMetrics": payload.get("socialMetrics", {})}}


class FakeMetricsClient(FakeUploader.Client):
    pass


class WorkflowIntegrationTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(
            root=self.root, engine="yt-dlp", urls=[IG_URL],
            report=self.root / "report.json", proxy="", cookies_from_browser="",
            timeout=60, metrics_timeout=10,
        )

    def fake_download(self, url, directory, args):
        path = directory / "media.mp4"
        shutil.copyfile(self.video, path)
        return path, {"engine": "yt-dlp", "expectedDuration": 1, "expectedAudio": True,
                      "remoteId": "Dfa1yPBq7jZ", "socialMetrics": {}}

    def prepare_instagram_item(self):
        counts = {"like": 596, "comment": 6, "repost": 11, "share": 41, "save": 194, "play": 6762}

        class FakeCollector:
            def __init__(self, args):
                self.args = args

            def instagram(self, shortcode):
                self.shortcode = shortcode
                return counts

        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download), \
                patch.object(workflow, "social_collector", return_value=FakeCollector(self.args)):
            return workflow.prepare_one(IG_URL, self.args)

    def test_prepare_stores_the_instagram_counts_in_the_state(self):
        item = self.prepare_instagram_item()
        metrics = item["socialMetrics"]
        self.assertEqual(metrics["platform"], "instagram")
        self.assertEqual(metrics["source"], "instagram-mobile-info")
        self.assertEqual(metrics["like"], 596)
        self.assertEqual(metrics["save"], 194)
        self.assertNotIn("socialMetricsError", item)

    def test_a_failed_count_collection_keeps_the_download(self):
        class BrokenCollector:
            def __init__(self, args):
                pass

            def instagram(self, shortcode):
                raise RuntimeError("Instagram 计数接口没有返回数据")

        with patch.object(workflow, "download_ytdlp", side_effect=self.fake_download), \
                patch.object(workflow, "social_collector", return_value=BrokenCollector(self.args)):
            item = workflow.prepare_one(IG_URL, self.args)
        self.assertTrue(Path(item["path"]).is_file())
        self.assertIn("socialMetricsError", item)

    def test_upload_metadata_carries_the_counts(self):
        item = self.prepare_instagram_item()
        item.update(name="测试视频", tags=["舞蹈", "红裙", "室内"])
        _, state, _, metadata = workflow.validate_item(item)
        self.assertEqual(metadata["socialMetrics"]["like"], 596)

    def test_upload_readback_compares_the_counts(self):
        item = self.prepare_instagram_item()
        item.update(name="测试视频", tags=["舞蹈", "红裙", "室内"])

        class Client:
            def __init__(self):
                self.assets = {}
                self.posts = 0

            def find_existing(self, hashes):
                return {}

            def upload(self, path, metadata):
                self.posts += 1
                asset = {"id": "asset-1", **metadata}
                self.assets[asset["id"]] = asset
                return asset

            def get_asset(self, asset_id):
                return self.assets[asset_id]

        client = Client()
        result = workflow.upload_one(item, client)
        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(result["asset"]["socialMetrics"]["like"], 596)

    def test_upload_survives_a_server_without_the_column(self):
        item = self.prepare_instagram_item()
        item.update(name="测试视频", tags=["舞蹈", "红裙", "室内"])

        class Client:
            def __init__(self):
                self.asset = {}

            def find_existing(self, hashes):
                return {}

            def upload(self, path, metadata):
                # 服务端还没有 social_metrics 列：只回显其它字段。
                self.asset = {"id": "asset-1", **{key: value for key, value in metadata.items()
                                                  if key != "socialMetrics"}}
                return self.asset

            def get_asset(self, asset_id):
                return self.asset

        result = workflow.upload_one(item, Client())
        self.assertEqual(result["status"], "uploaded")
        self.assertIn("socialMetrics", result["warning"])

    def test_metrics_command_updates_state_manifest_and_server(self):
        item = self.prepare_instagram_item()
        item.update(name="测试视频", tags=["舞蹈", "红裙", "室内"])
        state = workflow.read_json(item["stateFile"])
        state["asset"] = {"id": "asset-1"}
        workflow.write_json(item["stateFile"], state)
        manifest = self.root / "manifest.json"
        workflow.write_json(manifest, {"items": [item], "errors": []})
        results_file = self.root / "metrics-results.json"

        class FakeCollector:
            def __init__(self, args):
                pass

            def instagram(self, shortcode):
                return {"like": 700, "comment": 6, "repost": 11, "share": 41, "save": 194, "play": 9000}

        uploader = FakeUploader()
        args = argparse.Namespace(manifest=manifest, results=results_file,
                                  api="https://shelf.example.test", cookie_file="",
                                  proxy="", cookies_from_browser="", timeout=60, metrics_timeout=10)
        with patch.object(workflow, "load_uploader", return_value=uploader), \
                patch.object(workflow, "social_collector", return_value=FakeCollector(args)):
            code = workflow.metrics(args)
        self.assertEqual(code, 0)
        report = workflow.read_json(manifest)
        self.assertEqual(report["items"][0]["socialMetrics"]["like"], 700)
        state = workflow.read_json(item["stateFile"])
        self.assertEqual(state["socialMetrics"]["like"], 700)
        outcome = workflow.read_json(results_file)["results"][0]
        self.assertEqual(outcome["status"], "updated")
        self.assertEqual(outcome["socialMetrics"]["like"], 700)

    def test_an_account_challenge_stops_the_whole_batch(self):
        """账号被要求验证时整批停下，不能继续请求后面的条目。"""
        item = self.prepare_instagram_item()
        state = workflow.read_json(item["stateFile"])
        state["asset"] = {"id": "asset-1"}
        workflow.write_json(item["stateFile"], state)
        second_state_file = self.root / "second-state.json"
        second_state = dict(state)
        second_state["videoId"] = "SecondCode123"
        workflow.write_json(second_state_file, second_state)
        second = {"stateFile": str(second_state_file), "videoId": "SecondCode123",
                  "sourceUrl": "https://www.instagram.com/reel/SecondCode123/"}
        manifest = self.root / "manifest.json"
        workflow.write_json(manifest, {"items": [item, second], "errors": []})
        results_file = self.root / "metrics-results.json"
        called = []

        class ChallengedCollector:
            def __init__(self, args):
                pass

            def instagram(self, shortcode):
                called.append(shortcode)
                # 用 workflow 里的那个类：测试文件单独加载了一份 social_metrics，
                # 两份模块的异常类不是同一个类型。
                raise workflow.social_metrics.AccountChallengeError(
                    "Instagram 要求账号完成验证（challenge_required）")

        uploader = FakeUploader()
        args = argparse.Namespace(manifest=manifest, results=results_file,
                                  api="https://shelf.example.test", cookie_file="",
                                  proxy="", cookies_from_browser="", timeout=60, metrics_timeout=10)
        with patch.object(workflow, "load_uploader", return_value=uploader), \
                patch.object(workflow, "social_collector", return_value=ChallengedCollector(args)):
            code = workflow.metrics(args)
        self.assertNotEqual(code, 0)
        # 第二条根本没被请求
        self.assertEqual(len(called), 1)
        results = workflow.read_json(results_file)["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "stopped")
        self.assertIn("challenge_required", results[0]["error"])

    def test_metrics_keeps_the_local_snapshot_when_the_server_cannot_store_it(self):
        item = self.prepare_instagram_item()
        item.update(name="测试视频", tags=["舞蹈", "红裙", "室内"])
        state = workflow.read_json(item["stateFile"])
        state["asset"] = {"id": "asset-1"}
        workflow.write_json(item["stateFile"], state)
        manifest = self.root / "manifest.json"
        workflow.write_json(manifest, {"items": [item], "errors": []})
        results_file = self.root / "metrics-results.json"

        class FakeCollector:
            def __init__(self, args):
                pass

            def instagram(self, shortcode):
                return {"like": 700, "comment": 6, "repost": 11, "share": 41, "save": 194, "play": 9000}

        class OldServerUploader(FakeUploader):
            class Client(FakeUploader.Client):
                def request(self, method, path, body=None, headers=None):
                    # 服务端还没上线 social_metrics：忽略这个字段，回读里也没有它。
                    return 200, {"asset": {"id": "asset-1"}}

        args = argparse.Namespace(manifest=manifest, results=results_file,
                                  api="https://shelf.example.test", cookie_file="",
                                  proxy="", cookies_from_browser="", timeout=60, metrics_timeout=10)
        with patch.object(workflow, "load_uploader", return_value=OldServerUploader()), \
                patch.object(workflow, "social_collector", return_value=FakeCollector(args)):
            code = workflow.metrics(args)
        self.assertEqual(code, 0)
        outcome = workflow.read_json(results_file)["results"][0]
        self.assertEqual(outcome["status"], "local-only")
        self.assertIn("socialMetrics", outcome["error"])
        # 本地快照仍然保留，服务端上线后可以直接再推一次。
        self.assertEqual(workflow.read_json(manifest)["items"][0]["socialMetrics"]["like"], 700)
        self.assertEqual(workflow.read_json(item["stateFile"])["socialMetrics"]["like"], 700)


if __name__ == "__main__":
    unittest.main()
