import argparse
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics_module = load("social_metrics", "scripts/social_metrics.py")
workflow = load("import_video", "scripts/import_video.py")
# 用 import_video 真正引用的那一份，保证异常类和它捕获的是同一个类型。
browser = workflow.browser_metrics

USER_ID = "52498625541"
SHORTCODE = "Dfa1yPBq7jZ"
MEDIA_ID = "4024765765582043353"


def probe(**overrides):
    payload = {"href": "https://www.instagram.com/simonone.one/", "host": "www.instagram.com",
               "path": "/simonone.one/", "userId": USER_ID, "hasCsrf": True, "title": "Instagram"}
    payload.update(overrides)
    return json.dumps(payload)


def page_result(**overrides):
    payload = {"code": SHORTCODE, "status": 200, "like_count": 2569, "comment_count": 16,
               "media_repost_count": 11, "reshare_count": 58, "save_count": 887,
               "play_count": 28154, "error": "", "challenge": "", "userId": USER_ID}
    payload.update(overrides)
    return json.dumps(payload)


class FakeChrome:
    """假 Chrome：按顺序回放每次脚本调用的返回值。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, js, tab=None, window=None, timeout=45):
        self.calls.append(js)
        if self.responses:
            return self.responses.pop(0)
        return ""


class FakeUploader:
    DEFAULT_API = "https://shelf.example.test"
    DEFAULT_COOKIE_FILE = "/tmp/fake-session.json"

    class Client:
        def __init__(self, api, cookie_file):
            self.requests = []

        def require(self, method, path, body=None, headers=None, expected=200):
            self.requests.append((method, path, body))
            return {}

        def request(self, method, path, body=None, headers=None):
            self.requests.append((method, path, body))
            payload = json.loads(body.decode("utf-8")) if body else {}
            return 200, {"asset": {"id": "asset-1", "socialMetrics": payload.get("socialMetrics", {})}}


class BrowserChannelTests(unittest.TestCase):
    def collect(self, fake, codes=(SHORTCODE,), **kwargs):
        options = {"delay": 0, "jitter": 0, "expected_user_id": USER_ID}
        options.update(kwargs)
        with patch.object(browser, "_osascript", side_effect=fake.run), \
                patch.object(browser, "chrome_running", return_value=True):
            return browser.collect_instagram_in_browser(list(codes), **options)

    def test_it_reads_the_counts_from_the_page(self):
        fake = FakeChrome([probe(), "started", page_result()])
        counts = self.collect(fake)[SHORTCODE]
        self.assertEqual(counts["like"], 2569)
        self.assertEqual(counts["comment"], 16)
        self.assertEqual(counts["repost"], 11)
        self.assertEqual(counts["share"], 58)
        self.assertEqual(counts["save"], 887)
        self.assertEqual(counts["play"], 28154)

    def test_the_page_request_carries_the_media_id_and_the_app_id(self):
        fake = FakeChrome([probe(), "started", page_result()])
        self.collect(fake)
        request = next(js for js in fake.calls if "/api/v1/media/" in js)
        self.assertIn(MEDIA_ID, request)
        self.assertIn(browser.DEFAULT_APP_IDS[0], request)
        self.assertIn("credentials: \"include\"", request)

    def test_a_second_app_id_fills_the_missing_fields(self):
        fake = FakeChrome([
            probe(), "started",
            page_result(media_repost_count=None, reshare_count=None, save_count=None, play_count=None),
            "started",
            page_result(),
        ])
        counts = self.collect(fake)[SHORTCODE]
        self.assertEqual(counts["repost"], 11)
        self.assertEqual(counts["save"], 887)
        self.assertEqual(counts["play"], 28154)

    def test_it_stops_asking_once_every_field_is_there(self):
        fake = FakeChrome([
            probe(), "started",
            page_result(),
        ])
        counts = self.collect(fake)[SHORTCODE]
        self.assertEqual(counts["repost"], 11)
        # 探针 + 启动 + 轮询：六项齐全就不该再试第二个 app-id
        self.assertEqual(len(fake.calls), 3)

    def test_a_challenge_in_the_page_stops_the_whole_batch(self):
        fake = FakeChrome([probe(), "started", page_result(challenge="challenge_required")])
        with self.assertRaises(workflow.social_metrics.AccountChallengeError) as caught:
            self.collect(fake)
        self.assertIn("challenge_required", str(caught.exception))

    def test_a_suspended_account_stops_the_whole_batch(self):
        fake = FakeChrome([probe(path="/accounts/suspended/", href="https://www.instagram.com/accounts/suspended/")])
        with self.assertRaises(workflow.social_metrics.AccountChallengeError):
            self.collect(fake)
        # 页面已经是停用状态，一个请求都不该再发
        self.assertEqual(len(fake.calls), 1)

    def test_another_account_in_the_browser_stops_the_batch(self):
        fake = FakeChrome([probe(userId="99999999999")])
        with self.assertRaises(browser.AccountMismatchError):
            self.collect(fake)
        self.assertEqual(len(fake.calls), 1)

    def test_a_page_outside_instagram_is_rejected(self):
        fake = FakeChrome([probe(host="www.google.com", href="https://www.google.com/")])
        with self.assertRaises(browser.BrowserUnavailableError) as caught:
            self.collect(fake)
        self.assertIn("Instagram", str(caught.exception))

    def test_a_page_without_any_count_is_an_error(self):
        fake = FakeChrome([probe(), "started", page_result(
            like_count=None, comment_count=None, reshare_count=None, save_count=None, play_count=None,
            error="响应不是 JSON（可能被登录墙或风控页替换）")])
        with self.assertRaises(browser.BrowserUnavailableError):
            self.collect(fake, timeout=1)

    def test_the_missing_apple_events_switch_is_explained(self):
        failure = subprocess.CompletedProcess(
            args=["osascript"], returncode=1, stdout="",
            stderr="execution error: (-1723) 不能获得“1+1”。不允许访问。")
        with patch.object(browser.subprocess, "run", return_value=failure):
            with self.assertRaises(browser.BrowserUnavailableError) as caught:
                browser._osascript("1+1")
        self.assertIn("允许来自 Apple 事件的 JavaScript", str(caught.exception))


class MetricsEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_file = self.root / "state.json"
        workflow.write_json(self.state_file, {
            "platform": "instagram", "videoId": SHORTCODE,
            "sourceUrl": f"https://www.instagram.com/reel/{SHORTCODE}/",
            "asset": {"id": "asset-1"},
        })
        self.item = {"stateFile": str(self.state_file), "videoId": SHORTCODE,
                     "sourceUrl": f"https://www.instagram.com/reel/{SHORTCODE}/"}
        self.manifest = self.root / "manifest.json"
        workflow.write_json(self.manifest, {"items": [self.item], "errors": []})
        self.results_file = self.root / "metrics-results.json"

    def args(self, **overrides):
        options = {"manifest": self.manifest, "results": self.results_file,
                   "api": "https://shelf.example.test", "cookie_file": "",
                   "proxy": "", "cookies_from_browser": "", "timeout": 60, "metrics_timeout": 10,
                   "metrics_engine": "browser", "delay": 0, "delay_jitter": 0,
                   "browser_tab": 0, "browser_window": 0, "expected_ig_user_id": USER_ID}
        options.update(overrides)
        return argparse.Namespace(**options)

    def test_the_browser_engine_writes_the_counts_back(self):
        counts = {"Dfa1yPBq7jZ": {"like": 2569, "comment": 16, "repost": 11,
                                   "share": 58, "save": 887, "play": 28154}}
        with patch.object(workflow, "load_uploader", return_value=FakeUploader), \
                patch.object(browser, "collect_instagram_in_browser", return_value=counts) as collect:
            code = workflow.metrics(self.args())
        self.assertEqual(code, 0)
        self.assertEqual(collect.call_args.args[0], [SHORTCODE])
        state = workflow.read_json(self.state_file)
        self.assertEqual(state["socialMetrics"]["like"], 2569)
        self.assertEqual(state["socialMetrics"]["save"], 887)
        outcome = workflow.read_json(self.results_file)["results"][0]
        self.assertEqual(outcome["status"], "updated")

    def test_a_mismatched_account_stops_the_batch(self):
        error = browser.AccountMismatchError("浏览器登录态不是预期账号")
        with patch.object(workflow, "load_uploader", return_value=FakeUploader), \
                patch.object(browser, "collect_instagram_in_browser", side_effect=error):
            code = workflow.metrics(self.args())
        self.assertNotEqual(code, 0)
        outcome = workflow.read_json(self.results_file)["results"][0]
        self.assertEqual(outcome["status"], "stopped")
        self.assertIn("登录态", outcome["error"])


if __name__ == "__main__":
    unittest.main()
