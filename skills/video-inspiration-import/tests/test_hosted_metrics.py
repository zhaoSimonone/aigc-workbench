#!/usr/bin/env python3
"""托管采集通道的离线单测：不联网，传输层全部注入假实现。"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hosted_metrics as hm  # noqa: E402

FAKE_KEY = "fake-key-for-tests-never-real"


def make_transport(status=200, payload=None, recorder=None):
    """构造一个记录调用次数的假传输层。"""
    body = json.dumps(payload if payload is not None else {}).encode("utf-8")

    def transport(url, headers, timeout):
        if recorder is not None:
            recorder.append({"url": url, "headers": dict(headers), "timeout": timeout})
        if isinstance(status, Exception):
            raise status
        return status, body

    return transport


class NormalizeCountsTests(unittest.TestCase):
    def test_plain_field_names(self):
        counts = hm.normalize_counts({
            "like_count": 5253, "comment_count": 17, "media_repost_count": 46,
            "reshare_count": 598, "save_count": 1396, "play_count": 195605,
        })
        self.assertEqual(counts, {
            "like": 5253, "comment": 17, "repost": 46,
            "share": 598, "save": 1396, "play": 195605,
        })

    def test_alternative_names(self):
        counts = hm.normalize_counts({
            "likes": 12, "comments": 3, "share_count": 5,
            "saved_count": 7, "view_count": 900,
        })
        self.assertEqual(counts["like"], 12)
        self.assertEqual(counts["comment"], 3)
        self.assertEqual(counts["share"], 5)
        self.assertEqual(counts["save"], 7)
        self.assertEqual(counts["play"], 900)

    def test_nested_edge_paths(self):
        counts = hm.normalize_counts({
            "edge_liked_by": {"count": 42},
            "edge_media_to_comment": {"count": 2},
        })
        self.assertEqual(counts["like"], 42)
        self.assertEqual(counts["comment"], 2)

    def test_video_view_count_from_apify_style(self):
        counts = hm.normalize_counts({"videoViewCount": 8416})
        self.assertEqual(counts["play"], 8416)

    def test_missing_is_none_not_zero(self):
        counts = hm.normalize_counts({"like_count": 5})
        self.assertEqual(counts["like"], 5)
        self.assertIsNone(counts["comment"])
        self.assertIsNone(counts["play"])

    def test_non_numeric_is_skipped(self):
        counts = hm.normalize_counts({"like_count": "n/a", "likes": 9})
        self.assertEqual(counts["like"], 9)

    def test_non_dict_input(self):
        self.assertEqual(hm.normalize_counts(None), {k: None for k in hm.METRIC_KEYS})


class VideoUrlTests(unittest.TestCase):
    def test_video_url(self):
        urls = hm.extract_video_urls({"video_url": "https://scontent.cdninstagram.com/a.mp4"})
        self.assertEqual(urls, ["https://scontent.cdninstagram.com/a.mp4"])

    def test_video_versions_list(self):
        urls = hm.extract_video_urls({"video_versions": [
            {"url": "https://cdn/one.mp4", "width": 1080},
            {"url": "https://cdn/two.mp4", "width": 720},
        ]})
        self.assertEqual(urls, ["https://cdn/one.mp4", "https://cdn/two.mp4"])

    def test_dash_manifest_is_not_a_file(self):
        urls = hm.extract_video_urls({"video_dash_manifest_playlist": "https://cdn/manifest.mpd"})
        self.assertEqual(urls, [])

    def test_dedupe_keeps_order(self):
        item = {"video_url": "https://cdn/x.mp4",
                "video_versions": [{"url": "https://cdn/x.mp4"}, {"url": "https://cdn/y.mp4"}]}
        self.assertEqual(hm.extract_video_urls(item), ["https://cdn/x.mp4", "https://cdn/y.mp4"])

    def test_photo_has_no_urls(self):
        self.assertEqual(hm.extract_video_urls({"image_versions2": {"candidates": []}}), [])


class MediaItemsTests(unittest.TestCase):
    def test_bare_list(self):
        self.assertEqual(len(hm.media_items([{"code": "a"}])), 1)

    def test_items_key(self):
        self.assertEqual(len(hm.media_items({"items": [{"code": "a"}, {"code": "b"}]})), 2)

    def test_response_wrapper(self):
        # 官方 /v2/user/clips 的真实形状：{"response": {"items": [...]}, "next_page_id": ...}
        payload = {"response": {"items": [{"code": "a"}, {"code": "b"}]}, "next_page_id": "cur"}
        self.assertEqual(len(hm.media_items(payload)), 2)

    def test_unknown_shape_is_empty(self):
        self.assertEqual(hm.media_items({"foo": 1}), [])


class ShortcodeTests(unittest.TestCase):
    def test_code_preferred(self):
        self.assertEqual(hm.shortcode_of({"code": "DffUwugBd8w", "pk": "1"}), "DffUwugBd8w")

    def test_shortcode_fallback(self):
        self.assertEqual(hm.shortcode_of({"shortcode": "abc"}), "abc")

    def test_pk_fallback(self):
        self.assertEqual(hm.shortcode_of({"pk": 123}), "123")


class SnapshotTests(unittest.TestCase):
    def test_shape_matches_social_metrics(self):
        counts = {k: None for k in hm.METRIC_KEYS}
        counts["play"] = 100
        snapshot = hm.build_snapshot(counts, captured_at="2026-11-12T08:00:00Z")
        self.assertEqual(snapshot["platform"], "instagram")
        self.assertEqual(snapshot["source"], hm.SOURCE_HOSTED)
        self.assertEqual(snapshot["capturedAt"], "2026-11-12T08:00:00Z")
        self.assertEqual(snapshot["play"], 100)
        for key in hm.METRIC_KEYS:
            self.assertIn(key, snapshot)


class ClientAuthTests(unittest.TestCase):
    def test_missing_key_raises_before_network(self):
        calls = []
        client = hm.HostedClient(api_key="", transport=make_transport(200, {}, calls))
        with self.assertRaises(hm.HostedAuthError) as ctx:
            client.profile("someone")
        self.assertEqual(calls, [])  # 没 key 就不该发出任何请求
        self.assertIn("HIKERAPI_KEY", str(ctx.exception))

    def test_key_from_env_name(self):
        with mock.patch.dict("os.environ", {"MY_TEST_KEY": "abc"}):
            client = hm.HostedClient(api_key=None, key_env="MY_TEST_KEY")
        self.assertTrue(client.has_key)

    def test_key_not_leaked_in_auth_error(self):
        client = hm.HostedClient(api_key=FAKE_KEY, transport=make_transport(401, {"message": "no"}))
        with self.assertRaises(hm.HostedAuthError) as ctx:
            client.profile("someone")
        self.assertNotIn(FAKE_KEY, str(ctx.exception))


class ClientRequestTests(unittest.TestCase):
    def test_profile_sends_key_header_and_username(self):
        calls = []
        client = hm.HostedClient(api_key=FAKE_KEY,
                                 transport=make_transport(200, {"pk": "123"}, calls))
        client.profile("yunalovescosplay")
        self.assertEqual(len(calls), 1)
        self.assertIn("username=yunalovescosplay", calls[0]["url"])
        self.assertEqual(calls[0]["headers"]["x-access-key"], FAKE_KEY)
        self.assertTrue(calls[0]["url"].startswith("https://api.hikerapi.com/v2/user/by/username"))

    def test_user_id_from_pk(self):
        client = hm.HostedClient(api_key=FAKE_KEY, transport=make_transport(200, {"pk": 16998768578}))
        self.assertEqual(client.user_id("yuna"), "16998768578")

    def test_user_id_missing_raises(self):
        client = hm.HostedClient(api_key=FAKE_KEY, transport=make_transport(200, {"username": "yuna"}))
        with self.assertRaises(hm.HostedRequestError):
            client.user_id("yuna")

    def test_reels_with_username_resolves_pk_first(self):
        calls = []
        def transport(url, headers, timeout):
            calls.append(url)
            if "by/username" in url:
                return 200, json.dumps({"pk": "16998768578"}).encode()
            return 200, json.dumps({"response": {"items": []}}).encode()
        client = hm.HostedClient(api_key=FAKE_KEY, transport=transport)
        client.reels(username="yuna")
        self.assertEqual(len(calls), 2, "clips 只认 user_id，应先解析 pk")
        self.assertIn("user_id=16998768578", calls[1])

    def test_next_page_id_top_level_and_nested(self):
        self.assertEqual(hm.next_page_id({"next_page_id": "a"}), "a")
        self.assertEqual(hm.next_page_id({"response": {"paging_info": {"max_id": "b"}}}), "b")
        self.assertIsNone(hm.next_page_id({"response": {"items": []}}))

    def test_reels_requires_identifier(self):
        client = hm.HostedClient(api_key=FAKE_KEY, transport=make_transport(200, {}))
        with self.assertRaises(ValueError):
            client.reels()

    def test_reels_uses_user_id(self):
        calls = []
        client = hm.HostedClient(api_key=FAKE_KEY,
                                 transport=make_transport(200, {"items": []}, calls))
        client.reels(user_id="16998768578", page_id="p1")
        self.assertIn("user_id=16998768578", calls[0]["url"])
        self.assertIn("page_id=p1", calls[0]["url"])

    def test_429_raises_and_does_not_retry(self):
        calls = []
        client = hm.HostedClient(api_key=FAKE_KEY,
                                 transport=make_transport(429, {"message": "slow down"}, calls))
        with self.assertRaises(hm.HostedRateLimitError):
            client.reels(username="yuna")
        self.assertEqual(len(calls), 1, "429 不应自动重试，连续重试会把限流打死")

    def test_402_is_insufficient_funds(self):
        client = hm.HostedClient(api_key=FAKE_KEY,
                                 transport=make_transport(402, {"error": "Top up your account"}))
        with self.assertRaises(hm.HostedInsufficientFundsError) as ctx:
            client.profile("yuna")
        self.assertIn("402", str(ctx.exception))
        self.assertIn("billing", str(ctx.exception))

    def test_500_is_request_error(self):
        client = hm.HostedClient(api_key=FAKE_KEY, transport=make_transport(500, {"e": 1}))
        with self.assertRaises(hm.HostedRequestError):
            client.profile("yuna")

    def test_non_json_body(self):
        def transport(url, headers, timeout):
            return 200, b"<html>not json</html>"
        client = hm.HostedClient(api_key=FAKE_KEY, transport=transport)
        with self.assertRaises(hm.HostedRequestError):
            client.profile("yuna")


class SummarizeTests(unittest.TestCase):
    def test_summarize_records(self):
        payload = {"items": [
            {"code": "DffUwugBd8w", "play_count": 218000, "like_count": 5253,
             "video_url": "https://cdn/a.mp4"},
            {"code": "Dff9ps1BPYW", "video_view_count": 8416},
        ]}
        records = hm.summarize(payload)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["shortcode"], "DffUwugBd8w")
        self.assertEqual(records[0]["counts"]["play"], 218000)
        self.assertEqual(records[0]["videoUrls"], ["https://cdn/a.mp4"])
        self.assertEqual(records[1]["counts"]["play"], 8416)

    def test_summarize_empty(self):
        self.assertEqual(hm.summarize({"items": []}), [])


class CliTests(unittest.TestCase):
    def test_missing_key_exits_2(self):
        code = hm.main(["profile", "yuna", "--key-env", "DEFINITELY_UNSET_KEY"])
        self.assertEqual(code, 2)

    def test_rate_limit_exits_3(self):
        with mock.patch.object(hm.HostedClient, "_get", side_effect=hm.HostedRateLimitError("429")):
            client_patcher = mock.patch.dict("os.environ", {"TEST_KEY_X": "k"})
            with client_patcher:
                code = hm.main(["reels", "yuna", "--key-env", "TEST_KEY_X"])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
