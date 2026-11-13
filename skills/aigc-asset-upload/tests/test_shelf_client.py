import json
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from shelf_client import Client, APIError, validate_metadata, default_cookie_file, tool_dirs
from upload_assets import Client as UploadClient


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.seen = []
        seen = self.seen

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                seen.append((self.server.server_port, self.path, self.headers.get("Cookie")))
                if self.path == "/redirect":
                    self.send_response(307)
                    self.send_header("Location", self.server.target)
                    self.end_headers()
                    return
                if self.path == "/bytes":
                    body = b"media-data"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.startswith("/api/assets?"):
                    payload = {"assets": [{"id": "two" if "offset=1" in self.path else "one"}],
                               "hasMore": "offset=0" in self.path}
                    status = 200
                elif self.path == "/api/assets/metadata-policy":
                    payload = {"folders": ["测试库"], "folderAliases": {"旧库": "测试库"}}
                    status = 200
                else:
                    status = 503
                    payload = {"error": "Unavailable", "fields": [{"field": "name", "message": "max32"}]}
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                self.server.uploaded = (dict(self.headers), body)
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"asset":{"id":"uploaded"}}')

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                self.server.uploaded = (dict(self.headers), body)
                self.send_response(200)
                self.end_headers()

        self.servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{self.servers[0].server_port}"
        self.servers[0].target = f"http://127.0.0.1:{self.servers[1].server_port}/bytes"
        self.client = Client(self.base, Path(self.temp.name) / "session.json")
        self.client.cookies = {"session": "private"}

    def test_redirect_retains_location_and_download_does_not_leak_cookie(self):
        status, payload = self.client.request("GET", "/redirect")
        self.assertEqual(status, 307)
        self.assertEqual(payload["url"], self.servers[0].target)
        destination = Path(self.temp.name) / "media"
        self.client.download("/redirect", destination)
        self.assertEqual(destination.read_bytes(), b"media-data")
        self.assertEqual(self.seen[-1][2], None)
        self.assertEqual(self.seen[-2][2], "session=private")

    def test_pagination_and_structured_errors(self):
        self.assertEqual([a["id"] for a in self.client.list_assets(folder="灵感收集")], ["one", "two"])
        with self.assertRaises(APIError) as caught:
            self.client.require("GET", "/error")
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("max32", str(caught.exception))
        self.assertTrue(caught.exception.headers)

    def test_streaming_put_sends_exact_length_without_cross_origin_cookie(self):
        from cos_upload import UploadStream
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()), self.client._open(
            "PUT", f"http://127.0.0.1:{self.servers[1].server_port}/upload",
            UploadStream(io.BytesIO(b"test-data"), 9),
            {"Content-Length": "9", "Content-Type": "application/octet-stream"},
        ) as response:
            self.assertEqual(response.status, 200)
        headers, body = self.servers[1].uploaded
        self.assertEqual(body, b"test-data")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Transfer-Encoding", headers)

    def test_server_failure_does_not_prompt_for_password(self):
        with patch("builtins.input", side_effect=AssertionError("unexpected login")):
            with self.assertRaises(APIError):
                self.client.ensure_login()

    def test_corrupt_session_and_origin_binding(self):
        self.client.cookie_file.write_text("{broken")
        with self.assertRaisesRegex(ValueError, "会话文件无法解析"):
            Client(self.base, self.client.cookie_file)
        self.client._save_cookies()
        self.assertEqual(self.client.cookie_file.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, "所属服务"):
            Client(self.servers[0].target, self.client.cookie_file)

    def test_lint_rejects_ambiguous_tags(self):
        for metadata in [{"name": "长" * 33}, {"name": "a.b"}, {"tags": ["a", "b"]},
                         {"tags": ["a", "a", "b"]}, {"tags": ["a b", "c", "d"]}]:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                validate_metadata(metadata)
        validate_metadata({"name": "合规标题", "tags": ["a", "b", "c"]})

    def test_streaming_upload_uses_policy_and_exact_length(self):
        file = Path(self.temp.name) / "视频.mp4"
        file.write_bytes(b"\0\0\0\x18ftypisom" + b"media" * 30000)
        client = UploadClient(self.base, self.client.cookie_file)
        asset = client.upload(file, {"name": "视频标题", "tags": ["a", "b", "c"], "folder": "旧库"})
        headers, body = self.servers[0].uploaded
        self.assertEqual(asset["id"], "uploaded")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(headers["X-Aigc-Metadata-Policy"], "curated-v1")
        self.assertIn("测试库".encode(), body)
        self.assertIn(file.read_bytes(), body)

    def test_portable_runtime_does_not_pick_an_ambiguous_session(self):
        root = Path(self.temp.name)
        with patch.dict("os.environ", {"AIGC_WORKBENCH_STATE_DIR": str(root / "state"),
                                      "MINIMAX_DATA_DIR": str(root / "minimax"),
                                      "XDG_DATA_HOME": str(root / "data")}, clear=True):
            self.assertEqual(default_cookie_file(), root / "state/aigc-shelf-session.json")
            self.assertIn(root / "minimax/tools", tool_dirs())
            self.assertIn(root / "data/aigc-workbench/bin", tool_dirs())
