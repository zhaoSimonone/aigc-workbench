"""Unit tests for the Baidu Netdisk uploader.

Every test here is offline: no real token, no DNS, no socket. HTTP is faked at
the ``requests.Session`` boundary so the retry ladder, the locateupload host
policy and the planning logic can be exercised deterministically.

Run:
    python3 -m unittest discover -s skills/baidu-netdisk-archive/tests -p 'test_*.py'
"""

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import netdisk_sync as ns                                    # noqa: E402
import netdisk_transport as nt                               # noqa: E402

FAKE_TOKEN = "FAKE-TOKEN-must-never-appear-in-logs-0123456789"
MIB = 1024 * 1024


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload if payload is not None else {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Scripted session: map a substring of the URL to a queue of responses.

    Each queued item may be a FakeResponse, an exception instance (raised), or
    a callable taking the url.
    """

    def __init__(self, routes=None):
        # A route may map to a single response or to a list consumed one per call.
        self.routes = {k: (v if isinstance(v, list) else [v]) for k, v in (routes or {}).items()}
        self.calls = []
        self.headers = {"User-Agent": nt.USER_AGENT}

    def _match(self, url, method):
        for key, queue in self.routes.items():
            if key in url:
                if not queue:
                    continue
                item = queue.pop(0)
                self.calls.append((method, url))
                if isinstance(item, Exception):
                    raise item
                if callable(item):
                    return item(url)
                return item
        self.calls.append((method, url))
        raise AssertionError(f"unexpected {method} {nt.redact_url(url)}")

    def get(self, url, **kwargs):
        return self._match(url, "GET")

    def post(self, url, **kwargs):
        return self._match(url, "POST")

    def close(self):
        pass


def requests_timeout():
    import requests
    return requests.exceptions.ConnectTimeout("connect timed out")


def requests_readtimeout():
    import requests
    return requests.exceptions.ReadTimeout("read timed out")


def make_tmp_file(directory, name, size):
    path = os.path.join(directory, name)
    payload = os.urandom(size)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


class TransportTests(unittest.TestCase):
    """14.4 / 4.4 -- redaction must never leak the token."""

    def test_redact_url_masks_token(self):
        url = f"https://pan.baidu.com/api/list?access_token={FAKE_TOKEN}&page=1"
        redacted = nt.redact_url(url)
        self.assertNotIn(FAKE_TOKEN, redacted)
        self.assertIn("access_token=%2A%2A%2A", redacted)
        self.assertIn("page=1", redacted)

    def test_redact_url_handles_edge_cases(self):
        self.assertEqual(nt.redact_url(""), "")
        self.assertNotIn("x", nt.redact_url("https://h/p?a=1&b=2"))


class SessionTests(unittest.TestCase):
    def test_each_thread_gets_its_own_session(self):
        transport = nt.Transport(pool_size=2)
        seen = {}

        def grab(name):
            seen[name] = id(transport.session())

        threads = [threading.Thread(target=grab, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(seen.values())), 3, "线程之间不应共享 Session")
        transport.close()

    def test_same_thread_reuses_one_session(self):
        transport = nt.Transport(pool_size=2)
        self.assertIs(transport.session(), transport.session())
        transport.close()


class UploadBlockRetryTests(unittest.TestCase):
    """14.3 -- chunk retry ladder."""

    def test_timeout_then_success(self):
        session = FakeSession({"superfile2": [requests_timeout(),
                                              FakeResponse(payload={"errno": 0, "md5": "M"})]})
        reply, retries = nt.upload_block_with_retry(
            session, FAKE_TOKEN, "d.pcs.baidu.com", "legacy", "/AIGC/x.mp4",
            "UID", 0, b"data", "M")
        self.assertEqual(reply["md5"], "M")
        self.assertEqual(retries, 1)

    def test_http_500_then_success(self):
        session = FakeSession({"superfile2": [FakeResponse(status_code=500),
                                              FakeResponse(payload={"errno": 0, "md5": "M"})]})
        reply, retries = nt.upload_block_with_retry(
            session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(retries, 1)

    def test_md5_mismatch_then_success(self):
        session = FakeSession({"superfile2": [FakeResponse(payload={"errno": 0, "md5": "BAD"}),
                                              FakeResponse(payload={"errno": 0, "md5": "M"})]})
        reply, retries = nt.upload_block_with_retry(
            session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(retries, 1)

    def test_non_json_then_success(self):
        session = FakeSession({"superfile2": [FakeResponse(text="<html>err</html>"),
                                              FakeResponse(payload={"errno": 0, "md5": "M"})]})
        reply, retries = nt.upload_block_with_retry(
            session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(retries, 1)

    def test_retry_exhausted_gives_stable_code(self):
        session = FakeSession({"superfile2": [requests_timeout()] * 4})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(caught.exception.code, "network_retry_exhausted")

    def test_token_invalid_is_not_retried(self):
        session = FakeSession({"superfile2": [FakeResponse(payload={"errno": 111})]})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(caught.exception.code, "token_invalid")
        self.assertEqual(len(session.calls), 1, "授权失效不应重试")

    def test_unknown_errno_is_not_retried(self):
        session = FakeSession({"superfile2": [FakeResponse(payload={"errno": 31034})]})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(
                session, FAKE_TOKEN, "d.pcs.baidu.com", "legacy", "/AIGC/x.mp4",
                "UID", 0, b"data", "M")
        self.assertEqual(caught.exception.code, "chunk_upload_failed")
        self.assertEqual(len(session.calls), 1, "明确的参数/路径错误不应重试")

    def test_control_request_reuses_worker_session(self):
        session = FakeSession({"/api/precreate": FakeResponse(payload={"errno": 0, "uploadid": "U"})})
        result = ns.call_retry(
            FAKE_TOKEN, "/api/precreate", body={"path": "/AIGC/x.mp4"}, session=session)
        self.assertEqual(result["uploadid"], "U")
        self.assertEqual(session.calls[0][0], "POST")
        self.assertNotIn(FAKE_TOKEN, nt.redact_url(session.calls[0][1]))

    def test_permission_error_is_not_retried(self):
        session = FakeSession({"superfile2": [FakeResponse(payload={"errno": 6})]})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(caught.exception.code, "permission_denied")
        self.assertEqual(len(session.calls), 1)

    def test_chunk_size_error_is_not_retried(self):
        session = FakeSession({"superfile2": [FakeResponse(payload={"errno": 31364})]})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(caught.exception.code, "chunk_size_unsupported")

    def test_error_text_never_contains_token(self):
        session = FakeSession({"superfile2": [requests_timeout()] * 4})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertNotIn(FAKE_TOKEN, caught.exception.message)
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))

    def test_md5_mismatch_exhausted_reports_chunk_mismatch(self):
        session = FakeSession({"superfile2": [FakeResponse(payload={"errno": 0, "md5": "BAD"})] * 4})
        with self.assertRaises(nt.UploadError) as caught:
            nt.upload_block_with_retry(session, FAKE_TOKEN, "h", "legacy", "/p", "UID", 0, b"d", "M")
        self.assertEqual(caught.exception.code, "chunk_md5_mismatch")


class LocateUploadTests(unittest.TestCase):
    """14.4 -- host policy and query-path mode."""

    def test_servers_list_server_field(self):
        payload = {"errno": 0, "servers": [{"server": "https://nj02.b.baidu.com"}]}
        session = FakeSession({"locateupload": FakeResponse(payload=payload)})
        host = nt.locate_upload_host(session, FAKE_TOKEN, "/AIGC/a.mp4", "UID")
        self.assertEqual(host, "https://nj02.b.baidu.com")

    def test_bare_host_gets_https(self):
        self.assertEqual(nt._first_https_candidate({"host": "nj.b.baidu.com"}),
                         "https://nj.b.baidu.com")
        self.assertEqual(nt._first_https_candidate({"server": "http://nj.b.baidu.com"}),
                         "https://nj.b.baidu.com")

    def test_auto_falls_back_to_fixed(self):
        session = FakeSession({"locateupload": requests_timeout()})
        warnings = []
        host, mode = nt.resolve_upload_host("auto", session, FAKE_TOKEN, "/p", "UID", warn=warnings.append)
        self.assertEqual(host, "d.pcs.baidu.com")
        self.assertEqual(mode, "legacy")
        self.assertTrue(warnings)

    def test_required_raises(self):
        session = FakeSession({"locateupload": requests_timeout()})
        with self.assertRaises(nt.UploadError):
            nt.resolve_upload_host("required", session, FAKE_TOKEN, "/p", "UID")

    def test_required_raises_when_no_candidate(self):
        session = FakeSession({"locateupload": FakeResponse(payload={"errno": 0})})
        with self.assertRaises(nt.UploadError):
            nt.resolve_upload_host("required", session, FAKE_TOKEN, "/p", "UID")

    def test_fixed_never_calls_locateupload(self):
        session = FakeSession({})   # any locateupload call would raise
        host, mode = nt.resolve_upload_host("fixed", session, FAKE_TOKEN, "/p", "UID")
        self.assertEqual(host, "d.pcs.baidu.com")
        self.assertEqual(mode, "legacy")
        self.assertEqual(session.calls, [])

    def test_query_path_mode_differs_between_fixed_and_locate(self):
        """fixed 用 legacy /apps/BaiduPCS/<uploadid>，locate 成功用真实 remote path。"""
        fixed = FakeSession({"superfile2": FakeResponse(payload={"errno": 0, "md5": "M"})})
        host, mode = nt.resolve_upload_host("fixed", fixed, FAKE_TOKEN, "/AIGC/a.mp4", "UID")
        nt.upload_block_with_retry(fixed, FAKE_TOKEN, host, mode, "/AIGC/a.mp4", "UID", 0, b"d", "M")
        fixed_url = fixed.calls[-1][1]
        self.assertIn("path=%2Fapps%2FBaiduPCS%2FUID", fixed_url)
        # 线上 URL 带 token 是接口约定；真正不能出现在日志里的是 redact 之后的结果。
        self.assertIn(FAKE_TOKEN, fixed_url)
        self.assertNotIn(FAKE_TOKEN, nt.redact_url(fixed_url))

        session = FakeSession({
            "locateupload": FakeResponse(payload={"servers": [{"server": "https://up.b.baidu.com"}]}),
            "superfile2": FakeResponse(payload={"errno": 0, "md5": "M"}),
        })
        host2, mode2 = nt.resolve_upload_host("required", session, FAKE_TOKEN, "/AIGC/a.mp4", "UID")
        nt.upload_block_with_retry(session, FAKE_TOKEN, host2, mode2, "/AIGC/a.mp4", "UID", 0, b"d", "M")
        located_url = [u for m, u in session.calls if m == "POST"][-1]
        self.assertIn("path=%2FAIGC%2Fa.mp4", located_url)
        self.assertTrue(located_url.startswith("https://up.b.baidu.com/"), located_url[:80])
        self.assertNotIn("https://https://", located_url, "不能出现重复 scheme")
        self.assertNotIn(FAKE_TOKEN, nt.redact_url(located_url))
        self.assertEqual(mode, "legacy")
        self.assertEqual(mode2, "remote")


class SettingsTests(unittest.TestCase):
    """14.1 -- profiles and validation."""

    def make(self, **kwargs):
        return mock.Mock(profile=kwargs.pop("profile", None),
                         chunk_size=kwargs.pop("chunk_size", None),
                         concurrency=kwargs.pop("concurrency", None),
                         inter_chunk_delay=kwargs.pop("inter_chunk_delay", None),
                         upload_host_mode=kwargs.pop("upload_host_mode", None),
                         **kwargs)

    def test_default_is_safe_profile(self):
        settings = ns.resolve_settings(self.make())
        self.assertEqual(settings["chunk_size"], 4 * MIB)
        self.assertEqual(settings["concurrency"], 1)
        self.assertEqual(settings["inter_chunk_delay"], 0.15)
        self.assertEqual(settings["upload_host_mode"], "fixed")
        self.assertEqual(settings["profile"], "safe")

    def test_fast_profile(self):
        settings = ns.resolve_settings(self.make(profile="fast"))
        self.assertEqual(settings["chunk_size"], 8 * MIB)
        self.assertEqual(settings["concurrency"], 4)
        self.assertEqual(settings["inter_chunk_delay"], 0.03)
        self.assertEqual(settings["upload_host_mode"], "auto")

    def test_explicit_overrides_profile(self):
        settings = ns.resolve_settings(self.make(profile="fast", chunk_size="4M", concurrency=1,
                                                upload_host_mode="fixed"))
        self.assertEqual(settings["chunk_size"], 4 * MIB)
        self.assertEqual(settings["concurrency"], 1)
        self.assertEqual(settings["upload_host_mode"], "fixed")

    def test_chunk_size_parsing(self):
        for text, expected in (("4M", 4), ("8M", 8), ("16M", 16), ("32M", 32),
                               ("8", 8), ("16mb", 16), (str(8 * MIB), 8)):
            self.assertEqual(ns.parse_chunk_size(text), expected * MIB, text)

    def test_invalid_chunk_size_rejected(self):
        for bad in ("5M", "64M", "abc", "0M"):
            with self.assertRaises(ValueError, msg=bad):
                ns.parse_chunk_size(bad)

    def test_invalid_concurrency_rejected(self):
        for bad in (0, 9, -1, 100):
            with self.assertRaises(ValueError, msg=str(bad)):
                ns.resolve_settings(self.make(concurrency=bad))

    def test_invalid_delay_rejected(self):
        for bad in (-0.1, 5.1, 99):
            with self.assertRaises(ValueError, msg=str(bad)):
                ns.resolve_settings(self.make(inter_chunk_delay=bad))

    def test_invalid_host_mode_rejected(self):
        with self.assertRaises(ValueError):
            ns.resolve_settings(self.make(upload_host_mode="whatever"))


class BlockDigestTests(unittest.TestCase):
    """14.2 -- chunking and precreate block_list handling."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_digest_counts_per_chunk_size(self):
        path = make_tmp_file(self.dir, "f.bin", 10 * MIB)
        for size, expected in ((4 * MIB, 3), (8 * MIB, 2), (16 * MIB, 1), (32 * MIB, 1)):
            self.assertEqual(len(ns.block_digests(path, size)), expected, size)

    def test_last_chunk_is_short_and_digest_matches_manual(self):
        payload = os.urandom(5 * MIB)
        path = os.path.join(self.dir, "f.bin")
        with open(path, "wb") as fh:
            fh.write(payload)
        digests = ns.block_digests(path, 4 * MIB)
        self.assertEqual(len(digests), 2)
        self.assertEqual(digests[0], hashlib.md5(payload[:4 * MIB]).hexdigest())
        self.assertEqual(digests[1], hashlib.md5(payload[4 * MIB:]).hexdigest())

    def test_exact_multiple_has_no_empty_trailing_chunk(self):
        path = make_tmp_file(self.dir, "f.bin", 8 * MIB)
        self.assertEqual(len(ns.block_digests(path, 4 * MIB)), 2)

    def test_select_chunks_honours_valid_block_list(self):
        self.assertEqual(ns.select_chunks([2, 0], 3), ([0, 2], True))

    def test_select_chunks_falls_back_on_bad_input(self):
        for bad in (None, [], "0,1", [0, "x"], [True], [-1], [99], [0, 99]):
            self.assertEqual(ns.select_chunks(bad, 3), ([0, 1, 2], False), repr(bad))

    def test_select_chunks_dedupes_and_sorts(self):
        self.assertEqual(ns.select_chunks([2, 0, 2], 3), ([0, 2], True))

    def test_md5_of_accepts_explicit_chunk_size(self):
        path = make_tmp_file(self.dir, "f.bin", 3 * MIB)
        with open(path, "rb") as fh:
            expected = hashlib.md5(fh.read()).hexdigest()
        self.assertEqual(ns.md5_of(path, chunk_size=1024), expected)


class PlanTests(unittest.TestCase):
    """14.5 -- serial planning, list-once, skip/replace, dry-run."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in ("a.mp4", "b.mp4", "sub/c.mp4"):
            full = os.path.join(self.dir, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(b"x" * 100)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def files(self):
        return ns.collect(self.dir, ["*.DS_Store"], [])

    def test_same_parent_listed_once(self):
        listings = []

        def fake_list_dir(token, directory):
            listings.append(directory)
            return {}

        with mock.patch.object(ns, "list_dir", side_effect=fake_list_dir), \
             mock.patch.object(ns, "call_retry", return_value={"errno": 0, "path": "/AIGC"}):
            ns.build_plan(FAKE_TOKEN, "/AIGC", self.files(), False, {})
        # ensure_dirs walks each parent once; the later plan pass must hit the
        # cache instead of re-listing.
        self.assertLessEqual(len(listings), 3, f"重复列目录：{listings}")

    def test_directories_created_in_depth_order(self):
        created = []

        def fake_call_retry(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/create":
                created.append(body["path"])
                return {"errno": 0, "path": body["path"]}
            return {"errno": 0}

        with mock.patch.object(ns, "list_dir", return_value={}), \
             mock.patch.object(ns, "call_retry", side_effect=fake_call_retry):
            ns.build_plan(FAKE_TOKEN, "/AIGC", self.files(), False, {})
        self.assertEqual(created, ["/AIGC", "/AIGC/sub"])
        self.assertLess(created.index("/AIGC"), created.index("/AIGC/sub"),
                        "父目录必须先于子目录创建")

    def test_same_size_skips(self):
        with mock.patch.object(ns, "list_dir", return_value={"a.mp4": 100}), \
             mock.patch.object(ns, "call_retry", return_value={"errno": 0, "path": "/AIGC"}):
            plan, _ = ns.build_plan(FAKE_TOKEN, "/AIGC", [p for p in self.files()], False, {})
        actions = {item.relative_path: item.action for item in plan}
        self.assertEqual(actions["a.mp4"], "skip")

    def test_different_size_replaces(self):
        with mock.patch.object(ns, "list_dir", return_value={"a.mp4": 42}), \
             mock.patch.object(ns, "call_retry", return_value={"errno": 0, "path": "/AIGC"}):
            plan, _ = ns.build_plan(FAKE_TOKEN, "/AIGC", self.files(), False, {})
        actions = {item.relative_path: item.action for item in plan}
        self.assertEqual(actions["a.mp4"], "replace")
        self.assertEqual(actions["b.mp4"], "upload")

    def test_force_replaces_even_when_same_size(self):
        """--force previously did not delete the remote file first."""
        with mock.patch.object(ns, "list_dir", return_value={"a.mp4": 100}), \
             mock.patch.object(ns, "call_retry", return_value={"errno": 0, "path": "/AIGC"}):
            plan, _ = ns.build_plan(FAKE_TOKEN, "/AIGC", self.files(), True, {})
        actions = {item.relative_path: item.action for item in plan}
        self.assertEqual(actions["a.mp4"], "replace", "--force 必须标记为 replace")

    def test_dry_run_touches_no_write_api(self):
        with mock.patch.object(ns, "call_retry", side_effect=AssertionError("dry-run 不得调用写接口")):
            ns.main([self.dir, "--dest", "/AIGC", "--dry-run"])

    def test_dry_run_creates_no_directory(self):
        with mock.patch.object(ns, "call_retry", side_effect=AssertionError("dry-run 不得调用百度")):
            ns.main([self.dir, "--dest", "/AIGC", "--dry-run", "--profile", "fast"])


class ConcurrencyTests(unittest.TestCase):
    """14.6 -- bounded worker pool, isolation, metrics."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for i in range(12):
            with open(os.path.join(self.dir, f"f{i}.mp4"), "wb") as fh:
                fh.write(b"y" * 10)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_ctx(self, concurrency, delay=0.0):
        settings = {"chunk_size": 4 * MIB, "concurrency": concurrency,
                    "inter_chunk_delay": delay, "upload_host_mode": "fixed", "profile": "safe"}
        transport = mock.Mock()
        transport.session.return_value = FakeSession()
        return ns.BatchContext(FAKE_TOKEN, settings, transport)

    def plan_all(self):
        return [ns.PlanItem(os.path.join(self.dir, f"f{i}.mp4"), f"f{i}.mp4",
                            f"/AIGC/f{i}.mp4", 10, "upload") for i in range(12)]

    def test_active_workers_never_exceed_limit(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_upload(ctx, item):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return ns.FileResult(item.relative_path, "uploaded", "ok", "", item.size)

        for limit in (1, 2, 4, 8):
            active = peak = 0
            ctx = self.make_ctx(limit)
            with mock.patch.object(ns, "upload_one_file", side_effect=fake_upload):
                results = ns.run_batch(ctx, self.plan_all())
            self.assertEqual(len(results), 12)
            self.assertLessEqual(peak, limit, f"并发 {limit} 时峰值 {peak}")

    def test_failure_does_not_stop_others(self):
        def fake_upload(ctx, item):
            if item.relative_path == "f3.mp4":
                return ns.FileResult(item.relative_path, "failed", "boom", ns.ERR_INTERNAL, 10)
            return ns.FileResult(item.relative_path, "uploaded", "ok", "", item.size)

        ctx = self.make_ctx(4)
        with mock.patch.object(ns, "upload_one_file", side_effect=fake_upload):
            results = ns.run_batch(ctx, self.plan_all())
        self.assertEqual(sum(1 for r in results if r.status == "uploaded"), 11)
        self.assertEqual(sum(1 for r in results if r.status == "failed"), 1)

    def test_skipped_items_bypass_the_pool(self):
        plan = [ns.PlanItem(os.path.join(self.dir, "f0.mp4"), "f0.mp4", "/AIGC/f0.mp4", 10, "skip")]
        ctx = self.make_ctx(4)
        with mock.patch.object(ns, "upload_one_file",
                               side_effect=AssertionError("skip 不应进入线程池")):
            results = ns.run_batch(ctx, plan)
        self.assertEqual(results[0].status, "skipped")

    def test_metrics_jsonl_is_parseable_per_line(self):
        ctx = self.make_ctx(2)

        def fake_upload(ctx, item):
            return ns.FileResult(item.relative_path, "uploaded", "ok", "", item.size,
                                 {"relativePath": item.relative_path, "status": "uploaded",
                                  "sizeBytes": item.size, "retryCount": 0})

        metrics_path = os.path.join(self.dir, "m.jsonl")
        with mock.patch.object(ns, "upload_one_file", side_effect=fake_upload):
            results = ns.run_batch(ctx, self.plan_all(), metrics_path)
        ns.write_metrics(metrics_path, {"type": "batch_summary", "uploaded": len(results)})
        with open(metrics_path, encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
        self.assertEqual(len(lines), 13)
        for line in lines:
            json.loads(line)

    def test_metrics_absent_when_not_requested(self):
        ctx = self.make_ctx(1)

        def fake_upload(ctx, item):
            return ns.FileResult(item.relative_path, "uploaded", "ok", "", item.size, {"a": 1})

        before = set(os.listdir(self.dir))
        with mock.patch.object(ns, "upload_one_file", side_effect=fake_upload):
            ns.run_batch(ctx, self.plan_all())
        self.assertEqual(set(os.listdir(self.dir)), before)

    def test_failure_details_sorted_by_path(self):
        results = [ns.FileResult("z.mp4", "failed", "x", "c"),
                   ns.FileResult("a.mp4", "failed", "y", "c"),
                   ns.FileResult("m.mp4", "uploaded", "", "")]
        ordered = sorted(results, key=lambda r: r.relative_path)
        self.assertEqual([r.relative_path for r in ordered], ["a.mp4", "m.mp4", "z.mp4"])


class UploadOneFileTests(unittest.TestCase):
    """Single-file flow: instant upload, size/path verification, metrics shape."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = make_tmp_file(self.dir, "a.mp4", 3 * MIB)
        self.size = os.path.getsize(self.path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_ctx(self, host_mode="fixed", chunk_size=4 * MIB):
        settings = {"chunk_size": chunk_size, "concurrency": 1, "inter_chunk_delay": 0.0,
                    "upload_host_mode": host_mode, "profile": "safe"}
        transport = mock.Mock()
        transport.session.return_value = FakeSession()
        return ns.BatchContext(FAKE_TOKEN, settings, transport)

    def item(self, action="upload"):
        return ns.PlanItem(self.path, "a.mp4", "/AIGC/a.mp4", self.size, action)

    def test_instant_upload_skips_blocks(self):
        calls = []

        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            calls.append(path)
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U", "return_1": 0}
            if path == "/api/create":
                return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}
            return {"errno": 0}

        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item())
        self.assertEqual(result.status, "uploaded")
        self.assertNotIn("superfile2", " ".join(calls))
        self.assertEqual(result.metrics["chunksUploaded"], 0)

    def test_new_style_instant_upload_detected(self):
        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U", "return_type": 2}
            return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}

        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item())
        self.assertEqual(result.metrics["chunksUploaded"], 0)

    def test_create_receives_full_ordered_digest_list(self):
        captured = {}
        digests = ns.block_digests(self.path, 4 * MIB)

        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U"}
            if path == "/api/create":
                captured["block_list"] = body["block_list"]
                return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}
            return {"errno": 0}

        session = FakeSession({"superfile2": FakeResponse(
            payload={"errno": 0, "md5": digests[0]})})
        ctx = self.make_ctx()
        ctx.transport.session.return_value = session
        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            ns.upload_one_file(ctx, self.item())
        self.assertEqual(json.loads(captured["block_list"]), digests)

    def test_size_mismatch_is_detected(self):
        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U", "return_1": 0}
            return {"errno": 0, "path": "/AIGC/a.mp4", "size": 1}

        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, ns.ERR_SIZE_MISMATCH)

    def test_renamed_path_is_detected(self):
        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U", "return_1": 0}
            return {"errno": 0, "path": "/AIGC/a_20260101.mp4", "size": self.size}

        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item())
        self.assertEqual(result.code, ns.ERR_RENAMED)

    def test_replace_deletes_before_precreate(self):
        order = []

        def fake_delete(token, path):
            order.append("delete")
            return True

        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            order.append(path)
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U", "return_1": 0}
            return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}

        with mock.patch.object(ns, "delete_remote", side_effect=fake_delete), \
             mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item("replace"))
        self.assertEqual(result.status, "uploaded")
        self.assertEqual(order[0], "delete")
        self.assertLess(order.index("delete"), order.index("/api/precreate"))

    def test_changed_local_file_is_checked_before_remote_delete(self):
        item = self.item("replace")
        with open(self.path, "ab") as fh:
            fh.write(b"changed")
        with mock.patch.object(ns, "delete_remote") as delete:
            result = ns.upload_one_file(self.make_ctx(), item)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, ns.ERR_LOCAL_IO)
        delete.assert_not_called()

    def test_failed_delete_stops_before_precreate(self):
        with mock.patch.object(ns, "delete_remote", return_value=False), \
             mock.patch.object(ns, "call_retry", side_effect=AssertionError("删除失败后不得 precreate")):
            result = ns.upload_one_file(self.make_ctx(), self.item("replace"))
        self.assertEqual(result.code, ns.ERR_DELETE)

    def test_metrics_never_contain_token(self):
        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U", "return_1": 0}
            return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}

        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item())
        blob = json.dumps(result.metrics, ensure_ascii=False)
        self.assertNotIn(FAKE_TOKEN, blob)
        self.assertNotIn("dlink", blob.lower())

    def test_batch_context_repr_hides_token(self):
        self.assertNotIn(FAKE_TOKEN, repr(self.make_ctx()))

    def test_precreate_token_error_maps_to_code(self):
        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            return {"errno": 111, "errmsg": "expired"}

        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(self.make_ctx(), self.item())
        self.assertEqual(result.code, ns.ERR_TOKEN)


class ChunkFallbackTests(unittest.TestCase):
    """6.1 -- 服务端拒收大分片时，整文件回退到 4 MiB 重来。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = make_tmp_file(self.dir, "a.mp4", 6 * MIB)   # 8 MiB 分片 -> 1 片
        self.size = os.path.getsize(self.path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_ctx(self, chunk_size):
        settings = {"chunk_size": chunk_size, "concurrency": 1, "inter_chunk_delay": 0.0,
                    "upload_host_mode": "fixed", "profile": "fast"}
        transport = mock.Mock()
        transport.session.return_value = FakeSession()
        return ns.BatchContext(FAKE_TOKEN, settings, transport)

    def item(self):
        return ns.PlanItem(self.path, "a.mp4", "/AIGC/a.mp4", self.size, "upload")

    def test_fallback_reprecreates_with_4mib(self):
        """回退必须重新 precreate、换新 uploadid、从第 0 片重来。"""
        precreate_calls = []
        digests_8 = ns.block_digests(self.path, 8 * MIB)
        digests_4 = ns.block_digests(self.path, 4 * MIB)

        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                precreate_calls.append((body["block_list"], body["path"]))
                if len(precreate_calls) == 1:
                    return {"errno": 31364, "errmsg": "block too large"}  # 发生在分片上传时更真实
                return {"errno": 0, "uploadid": "U2"}
            if path == "/api/create":
                return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}
            return {"errno": 0}

        # 第一次 precreate 通过，分片被拒；回退后重新 precreate
        state = {"n": 0}

        def fake_call_seq(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                state["n"] += 1
                precreate_calls.append(json.loads(body["block_list"]))
                return {"errno": 0, "uploadid": f"U{state['n']}"}
            if path == "/api/create":
                return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}
            return {"errno": 0}

        posted = []

        def fake_post(session, host, qmode, remote_path, token, uploadid, partseq, body, boundary, timeout):
            posted.append((uploadid, partseq))
            if partseq == 0 and uploadid == "U1":
                return FakeResponse(payload={"errno": 31364})
            return FakeResponse(payload={"errno": 0, "md5": digests_4[partseq]})

        ctx = self.make_ctx(8 * MIB)
        with mock.patch.object(ns, "call_retry", side_effect=fake_call_seq), \
             mock.patch.object(nt, "_post_block", side_effect=fake_post):
            result = ns.upload_one_file(ctx, self.item())

        self.assertEqual(result.status, "uploaded", result.detail)
        self.assertEqual(len(precreate_calls), 2, "回退必须重新 precreate")
        self.assertEqual(precreate_calls[0], digests_8)
        self.assertEqual(precreate_calls[1], digests_4, "回退后按 4 MiB 重算完整 block list")
        self.assertEqual(posted[0], ("U1", 0))
        self.assertEqual(posted[1], ("U2", 0), "回退必须用新 uploadid 从第 0 片开始")
        self.assertEqual(result.metrics.get("chunkFallbackFrom"), 8 * MIB)
        self.assertEqual(result.metrics.get("chunkFallbackTo"), 4 * MIB)
        self.assertEqual(result.metrics["chunkSizeBytes"], 4 * MIB)

    def test_precreate_chunk_limit_also_falls_back(self):
        precreate_sizes = []
        digests_4 = ns.block_digests(self.path, 4 * MIB)

        def fake_call_seq(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                precreate_sizes.append(len(json.loads(body["block_list"])))
                if len(precreate_sizes) == 1:
                    return {"errno": 31364, "errmsg": "block too large"}
                return {"errno": 0, "uploadid": "U2"}
            if path == "/api/create":
                return {"errno": 0, "path": "/AIGC/a.mp4", "size": self.size}
            return {"errno": 0}

        def fake_post(session, host, qmode, remote_path, token, uploadid, partseq, body, boundary, timeout):
            return FakeResponse(payload={"errno": 0, "md5": digests_4[partseq]})

        ctx = self.make_ctx(8 * MIB)
        with mock.patch.object(ns, "call_retry", side_effect=fake_call_seq), \
             mock.patch.object(nt, "_post_block", side_effect=fake_post):
            result = ns.upload_one_file(ctx, self.item())
        self.assertEqual(result.status, "uploaded", result.detail)
        self.assertEqual(precreate_sizes, [len(ns.block_digests(self.path, 8 * MIB)), len(digests_4)])
        self.assertEqual(result.metrics.get("chunkFallbackTo"), 4 * MIB)

    def test_safe_profile_does_not_fallback(self):
        """4 MiB 被拒时直接报错，不递归回退。"""
        def fake_call_seq(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                return {"errno": 0, "uploadid": "U1"}
            return {"errno": 0}

        ctx = self.make_ctx(4 * MIB)
        with mock.patch.object(ns, "call_retry", side_effect=fake_call_seq), \
             mock.patch.object(nt, "_post_block",
                               return_value=FakeResponse(payload={"errno": 31364})):
            result = ns.upload_one_file(ctx, self.item())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, ns.ERR_CHUNK_SIZE)


class ListErrnoTests(unittest.TestCase):
    """errno=600 只在列举时重试；strict 语义严格；任何失败都不得读成空目录。"""

    def test_600_not_in_global_retryable(self):
        """600 是列举接口的毛病，不能污染 precreate/create/quota 的重试策略。"""
        self.assertNotIn(600, ns.RETRYABLE)
        self.assertEqual(ns.LIST_EXTRA_RETRYABLE, {600})

    def test_600_retried_on_list(self):
        seq = [{"errno": 600}, {"errno": 600}, {"errno": 0, "list": []}]
        with mock.patch.object(ns, "call", side_effect=seq) as call, \
             mock.patch.object(ns, "backoff_sleep"):
            result = ns.call_retry(FAKE_TOKEN, "/api/list", method="GET",
                                   extra_retryable=ns.LIST_EXTRA_RETRYABLE)
        self.assertEqual(result["errno"], 0)
        self.assertEqual(call.call_count, 3, "600 必须退避重试")

    def test_600_not_retried_without_extra(self):
        """不传 extra_retryable 时 600 直接返回，不浪费 4 次往返。"""
        with mock.patch.object(ns, "call", return_value={"errno": 600}) as call:
            result = ns.call_retry(FAKE_TOKEN, "/api/precreate", body={})
        self.assertEqual(result["errno"], 600)
        self.assertEqual(call.call_count, 1)

    def test_list_passes_extra_retryable(self):
        with mock.patch.object(ns, "call_retry", return_value={"errno": 0, "list": []}) as cr:
            ns.list_entries(FAKE_TOKEN, "/AIGC", strict=True)
        self.assertEqual(cr.call_args.kwargs.get("extra_retryable"), ns.LIST_EXTRA_RETRYABLE)

    def test_retry_exhaustion_raises_after_bounded_attempts(self):
        with mock.patch.object(ns, "call", return_value={"errno": 600}) as call, \
             mock.patch.object(ns, "backoff_sleep"):
            with self.assertRaises(ns.NetdiskError):
                ns.call_retry(FAKE_TOKEN, "/api/list", method="GET",
                              extra_retryable=ns.LIST_EXTRA_RETRYABLE)
        self.assertEqual(call.call_count, ns.MAX_ATTEMPTS, "必须是限定次数，不能无限重试")

    def test_strict_true_raises_on_missing_directory(self):
        """strict=True 语义恢复：-9 也必须抛错，调用方要的是"不存在"这个答案。"""
        with mock.patch.object(ns, "call", return_value={"errno": -9}):
            with self.assertRaises(ns.NetdiskError):
                ns.list_entries(FAKE_TOKEN, "/AIGC/不存在", strict=True)

    def test_strict_false_maps_only_minus9_to_empty(self):
        with mock.patch.object(ns, "call", return_value={"errno": -9}):
            self.assertEqual(ns.list_entries(FAKE_TOKEN, "/AIGC/待创建", strict=False), [])

    def test_permission_error_raises_in_both_modes(self):
        for strict in (True, False):
            with mock.patch.object(ns, "call", return_value={"errno": 1, "errmsg": "no permission"}):
                with self.assertRaises(ns.NetdiskError, msg=f"strict={strict}"):
                    ns.list_entries(FAKE_TOKEN, "/AIGC/x", strict=strict)

    def test_600_exhausted_never_reads_as_empty(self):
        """关键回归：一旦当成空目录，已存在文件会被误判为需要上传。"""
        for strict in (True, False):
            with mock.patch.object(ns, "call", return_value={"errno": 600}), \
                 mock.patch.object(ns, "backoff_sleep"):
                with self.assertRaises(ns.NetdiskError, msg=f"strict={strict}"):
                    ns.list_entries(FAKE_TOKEN, "/AIGC/已有文件", strict=strict)

    def test_build_plan_aborts_when_listing_fails(self):
        import tempfile as _tf
        directory = _tf.mkdtemp()
        try:
            with open(os.path.join(directory, "a.mp4"), "wb") as fh:
                fh.write(b"x" * 100)
            with mock.patch.object(ns, "call", return_value={"errno": 600}), \
                 mock.patch.object(ns, "backoff_sleep"):
                with self.assertRaises(ns.NetdiskError):
                    ns.build_plan(FAKE_TOKEN, "/AIGC", ns.collect(directory, ["*.DS_Store"], []), False, {})
        finally:
            import shutil
            shutil.rmtree(directory, ignore_errors=True)


class BackwardCompatTests(unittest.TestCase):
    """14.7 -- read-only commands still parse and behave."""

    def test_readonly_subcommands_present(self):
        for name in ("list", "tree", "publish-catalog", "quota"):
            self.assertIn(name, ns.COMMANDS, name)

    def test_help_runs(self):
        with self.assertRaises(SystemExit) as caught:
            ns.main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_old_invocation_still_parses(self):
        argv = ["/tmp/src", "--dest", "/AIGC/d", "--dry-run", "--limit", "3",
                "--include", "*.mp4", "--exclude", "*.tmp", "--force", "--token", FAKE_TOKEN]
        # Parsing happens inside main(); dry-run short-circuits before any token use.
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            ns.main(argv)

    def test_default_chunk_constant_preserved(self):
        self.assertEqual(ns.DEFAULT_BLOCK_SIZE, 4 * 1024 * 1024)
        self.assertEqual(ns.BLOCK_SIZE, ns.DEFAULT_BLOCK_SIZE)

    def test_list_root_guard_intact(self):
        self.assertEqual(ns.assert_within_root("/AIGC/x"), "/AIGC/x")
        with self.assertRaises(ns.NetdiskError):
            ns.assert_within_root("/Pictures")


class CreateContractTests(unittest.TestCase):
    """回归保护：/api/create 请求体的字段集合、类型与 block_list 语义。

    上一轮只读分析确认 errno=-7 与路径中的 Unicode 符号共变，但客户端契约本身
    未被完整断言。这里把 create 的字段集合、字符串类型、block_list 完整性固定下来，
    任何后续改动导致契约漂移都会立刻失败。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # 10 MiB / 4 MiB 分片 = 3 个 digest，便于验证顺序与完整性
        self.path = make_tmp_file(self.dir, "a.mp4", 10 * MIB)
        self.size = os.path.getsize(self.path)
        self.digests = ns.block_digests(self.path, 4 * MIB)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_ctx(self, host_mode="fixed"):
        settings = {"chunk_size": 4 * MIB, "concurrency": 1, "inter_chunk_delay": 0.0,
                    "upload_host_mode": host_mode, "profile": "safe"}
        transport = mock.Mock()
        transport.session.return_value = FakeSession()
        return ns.BatchContext(FAKE_TOKEN, settings, transport)

    def item(self, remote_path="/AIGC/a.mp4"):
        return ns.PlanItem(self.path, "a.mp4", remote_path, self.size, "upload")

    def run_upload(self, ctx, precreate_payload, captured, remote_path="/AIGC/a.mp4"):
        """跑一次 upload_one_file，捕获 precreate/create 两个 body。"""
        def fake_call(token, path, params=None, body=None, method="POST", session=None):
            if path == "/api/precreate":
                captured["precreate"] = dict(body)
                return precreate_payload
            if path == "/api/create":
                captured["create"] = dict(body)
                return {"errno": 0, "path": remote_path, "size": self.size}
            return {"errno": 0}

        def block_reply(url):
            seq = int(dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["partseq"])
            return FakeResponse(payload={"errno": 0, "md5": self.digests[seq]})

        # FakeSession 的路由是消费型队列：每个分片各放一个可调用响应
        session = FakeSession({"superfile2": [block_reply] * len(self.digests)})
        ctx.transport.session.return_value = session
        with mock.patch.object(ns, "call_retry", side_effect=fake_call):
            result = ns.upload_one_file(ctx, self.item(remote_path))
        return result

    # ---- 字段集合与类型 ----

    def test_create_body_field_set_is_exact(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        self.assertIsNotNone(captured.get("create"), "create 未被调用")
        self.assertEqual(
            set(captured["create"].keys()),
            {"path", "size", "isdir", "uploadid", "block_list"},
            "create 字段集合必须严格保持这 5 个",
        )

    def test_create_body_has_no_extra_or_missing_fields(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        body = captured["create"]
        for required in ("path", "size", "isdir", "uploadid", "block_list"):
            self.assertIn(required, body)
        # 明确排除历史上可能出现的多余字段
        for forbidden in ("autoinit", "ondup", "block_list_index"):
            self.assertNotIn(forbidden, body, f"create 不应包含 {forbidden}")

    def test_create_body_values_are_all_strings(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        for key, value in captured["create"].items():
            self.assertIsInstance(value, str, f"字段 {key} 应为 str，实际 {type(value).__name__}")

    def test_create_isdir_and_size_field_semantics(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        body = captured["create"]
        self.assertEqual(body["isdir"], "0", "isdir 必须是字符串 '0'")
        self.assertEqual(body["size"], str(self.size), "size 必须是字节数的十进制字符串")
        self.assertEqual(body["size"], "10485760")
        self.assertEqual(body["uploadid"], "UP-1", "create 必须沿用 precreate 返回的 uploadid")

    def test_precreate_field_set_is_exact(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        self.assertEqual(
            set(captured["precreate"].keys()),
            {"path", "size", "isdir", "autoinit", "block_list"},
            "precreate 字段集合必须严格保持这 5 个",
        )
        self.assertEqual(captured["precreate"]["autoinit"], "1")
        self.assertNotIn("uploadid", captured["precreate"])

    # ---- block_list 完整性与顺序 ----

    def test_create_block_list_is_full_and_ordered(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        decoded = json.loads(captured["create"]["block_list"])
        self.assertEqual(decoded, self.digests, "create 必须收到完整且有序的 digest 列表")
        self.assertEqual(len(decoded), 3)
        for digest in decoded:
            self.assertRegex(digest, r"^[0-9a-f]{32}$")

    def test_create_keeps_full_block_list_when_precreate_returns_partial(self):
        """precreate 只返回缺失分片时，create 仍必须提交全量有序 digest。"""
        captured = {}
        payload = {"errno": 0, "uploadid": "UP-2", "block_list": [1]}
        result = self.run_upload(self.make_ctx(), payload, captured)
        self.assertEqual(result.status, "uploaded", result.detail)
        self.assertEqual(result.metrics["chunksRequested"], 1, "只应上传缺失的那一片")
        self.assertEqual(result.metrics["chunksUploaded"], 1)
        decoded = json.loads(captured["create"]["block_list"])
        self.assertEqual(decoded, self.digests, "即使只传了一片，create 仍须收到完整列表")
        self.assertEqual(captured["precreate"]["block_list"], json.dumps(self.digests),
                         "precreate 同样提交完整列表")

    def test_create_block_list_unchanged_when_all_blocks_instant(self):
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-3", "return_1": 0}, captured)
        decoded = json.loads(captured["create"]["block_list"])
        self.assertEqual(decoded, self.digests, "秒传也不能丢失 block_list")

    # ---- 编码往返 ----

    def test_create_body_survives_urlencode_roundtrip(self):
        """form-urlencode 编解码后 create 的每个字段值必须与编码前完全一致。"""
        captured = {}
        self.run_upload(self.make_ctx(), {"errno": 0, "uploadid": "UP-1"}, captured)
        body = captured["create"]
        raw = urllib.parse.urlencode(body)
        restored = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        for key, value in body.items():
            self.assertEqual(restored[key], value, f"字段 {key} 经 urlencode 往返后发生变化")

    def test_block_list_survives_nested_urlencode(self):
        """block_list 是嵌在 form body 里的 JSON 字符串，需经二次解码还原。"""
        digests = self.digests
        raw = urllib.parse.urlencode({"block_list": json.dumps(digests)})
        outer = dict(urllib.parse.parse_qsl(raw))
        self.assertEqual(json.loads(outer["block_list"]), digests)


SPECIAL_SEGMENTS = [
    ("ascii", "plain_ascii_name.mp4"),
    ("chinese", "中文文件名.mp4"),
    ("hash", "name#tag#more.mp4"),
    ("fullwidth", "全角标点：测试？。mp4"),
    ("ascii_dot", "name.with.dots.mp4"),
    ("mixed_dot", "片段.0.668s.mp4"),
    ("emoji_simple", "\U0001F9E0emoji.mp4"),
    ("emoji_vs16", "\U0001F3B5VS16.mp4"),
    ("emoji_astral", "\U0001F342fallingleaf.mp4"),
    ("symbol_bmp", "❄️snow.mp4"),
    ("plus_at", "a+b@c.mp4"),
    ("percent_like", "100%real.mp4"),
]


class UnicodePathTests(unittest.TestCase):
    """回归保护：特殊字符路径在 path 构造与 urlencode 后的稳定性。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_collect_matches_exact_unicode_relative_path(self):
        """collect 的 include 匹配必须对含 emoji/全角/#/ASCII 句点的路径精确生效。"""
        for label, name in SPECIAL_SEGMENTS:
            with self.subTest(label):
                target = f"素材/第一批/{name}"
                sub = os.path.join(self.dir, "素材", "第一批")
                os.makedirs(sub, exist_ok=True)
                make_tmp_file(sub, name, 1024)
                files = ns.collect(self.dir, ["*.DS_Store"], [target])
                rels = [rel for _, rel in files]
                self.assertIn(target, rels, f"{label}: include 未精确匹配 {name!r}")
                others = [r for r in rels if r != target]
                self.assertEqual(others, [], f"{label}: 误匹配到其它文件 {others}")

    def test_remote_path_survives_urlencode_roundtrip(self):
        for label, name in SPECIAL_SEGMENTS:
            with self.subTest(label):
                remote = f"/AIGC/1013视频/素材/第一批/{name}"
                raw = urllib.parse.urlencode({"path": remote})
                restored = dict(urllib.parse.parse_qsl(raw))["path"]
                self.assertEqual(restored, remote, f"{label}: path 经 urlencode 往返后被破坏")

    def test_plan_builds_remote_path_with_unicode_preserved(self):
        for label, name in SPECIAL_SEGMENTS:
            with self.subTest(label):
                sub = os.path.join(self.dir, "素材", "第一批")
                os.makedirs(sub, exist_ok=True)
                local = make_tmp_file(sub, name, 512)
                rel = f"素材/第一批/{name}"
                dest = "/AIGC"
                remote_path = dest + "/" + rel.replace(os.sep, "/")
                item = ns.PlanItem(local, rel, remote_path, os.path.getsize(local), "upload")
                self.assertEqual(item.remote_path, remote_path)
                self.assertTrue(item.remote_path.endswith(name),
                                f"{label}: remote_path 末尾未保留原名")
                self.assertIn("/AIGC/素材/第一批/", item.remote_path)

    def test_path_is_not_normalised_or_truncated(self):
        """路径中的连续句点、全角冒号、emoji 都必须原样保留，不做规范化。"""
        remote = "/AIGC/930视频/素材/第三批/x.y.668s.png"
        item = ns.PlanItem("/tmp/x", "x.y.668s.png", remote, 10, "upload")
        self.assertEqual(item.remote_path.count("."), 3, "句点数量被改变")
        self.assertIn("930视频", item.remote_path)

    def test_variation_selector_and_emoji_distinct_but_stable(self):
        for label, name in SPECIAL_SEGMENTS:
            with self.subTest(label):
                remote = f"/AIGC/{name}"
                once = dict(urllib.parse.parse_qsl(urllib.parse.urlencode({"p": remote})))["p"]
                twice = dict(urllib.parse.parse_qsl(urllib.parse.urlencode({"p": once})))["p"]
                self.assertEqual(once, remote, f"{label}: 首次编码即失真")
                self.assertEqual(twice, remote, f"{label}: 二次编码不稳定（幂等性）")

    def test_block_upload_path_mode_unchanged_for_unicode_path(self):
        """fixed/legacy 与 remote 两种分片 path 构造对含 emoji 的路径保持现有行为。"""
        remote = "/AIGC/1013视频/素材/第一批/我决定穿这套.mp4"
        captured = {}

        def capture(url):
            captured["url"] = url
            return FakeResponse(payload={"errno": 0, "md5": "0" * 32})

        session = FakeSession({"superfile2": capture})
        nt.upload_block_with_retry(session, FAKE_TOKEN, "d.pcs.baidu.com", "legacy",
                                  remote, "UPLOADID-1", 0, b"data", "0" * 32)
        legacy_params = dict(p.split("=", 1) for p in captured["url"].split("?", 1)[1].split("&"))
        self.assertEqual(
            urllib.parse.unquote(legacy_params["path"]), "/apps/BaiduPCS/UPLOADID-1",
            "legacy 模式必须使用 /apps/BaiduPCS/<uploadid> 临时路径",
        )
        self.assertEqual(urllib.parse.unquote(legacy_params["uploadid"]), "UPLOADID-1")

        session2 = FakeSession({"superfile2": capture})
        nt.upload_block_with_retry(session2, FAKE_TOKEN, "bjdd.pcs.baidu.com", "remote",
                                   remote, "UPLOADID-2", 0, b"data", "0" * 32)
        remote_params = dict(p.split("=", 1) for p in captured["url"].split("?", 1)[1].split("&"))
        self.assertEqual(
            urllib.parse.unquote(remote_params["path"]), remote,
            "remote 模式必须使用真实远端路径",
        )

    def test_host_scheme_never_duplicated(self):
        for host in ("https://bjdd-ct11.pcs.baidu.com", "d.pcs.baidu.com", "http://x.pcs.baidu.com"):
            with self.subTest(host):
                self.assertNotIn("https://https://", nt._host_only(host))

    def test_special_chars_never_break_boundary_or_content(self):
        """multipart boundary 与二进制分片内容必须保持完整、彼此可区分。"""
        boundary = b"----B0UND"
        payload = b"\x00\x01binary"
        body = nt._build_block_body(boundary.decode(), 3, payload)
        wire_boundary = b"--" + boundary
        self.assertTrue(body.startswith(wire_boundary + b"\r\n"))
        self.assertTrue(body.endswith(b"\r\n" + wire_boundary + b"--\r\n"))
        self.assertIn(payload, body)
        self.assertEqual(body.count(wire_boundary), 2)


class OfflineGuardTests(unittest.TestCase):
    """证明本测试套件不产生任何真实网络连接。"""

    def test_upload_flow_makes_no_socket_connection(self):
        import socket as _socket
        d = tempfile.mkdtemp()
        try:
            path = make_tmp_file(d, "a.mp4", 5 * MIB)
            digests = ns.block_digests(path, 4 * MIB)

            def fake_call(token, api, params=None, body=None, method="POST", session=None):
                if api == "/api/precreate":
                    return {"errno": 0, "uploadid": "U"}
                return {"errno": 0, "path": "/AIGC/a.mp4", "size": os.path.getsize(path)}

            def block_reply(url):
                seq = int(dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["partseq"])
                return FakeResponse(payload={"errno": 0, "md5": digests[seq]})

            block_route = [block_reply] * len(digests)

            settings = {"chunk_size": 4 * MIB, "concurrency": 1, "inter_chunk_delay": 0.0,
                        "upload_host_mode": "fixed", "profile": "safe"}
            transport = mock.Mock()
            transport.session.return_value = FakeSession({"superfile2": block_route})
            ctx = ns.BatchContext(FAKE_TOKEN, settings, transport)
            item = ns.PlanItem(path, "a.mp4", "/AIGC/a.mp4", os.path.getsize(path), "upload")

            def boom(*_a, **_k):
                raise AssertionError("测试期间不允许建立真实网络连接")

            with mock.patch.object(_socket, "create_connection", side_effect=boom), \
                 mock.patch.object(_socket.socket, "connect", side_effect=boom), \
                 mock.patch.object(ns, "call_retry", side_effect=fake_call):
                result = ns.upload_one_file(ctx, item)
            self.assertEqual(result.status, "uploaded")
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
