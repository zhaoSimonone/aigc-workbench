import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assets import format_uploaded_at, main


ASSETS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "蕾姆正脸",
        "folder": "角色设定",
        "type": "image",
        "contentType": "image/png",
        "size": "1.2 MB",
        "sizeBytes": 1200000,
        "duration": None,
        "date": "2026-11-01T00:00:00.000Z",
        "tags": ["蕾姆", "正脸", "角色设定"],
        "characterName": "蕾姆",
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "蓝色高丸子头",
        "folder": "发型库",
        "type": "image",
        "contentType": "image/png",
        "size": "800 KB",
        "sizeBytes": 800000,
        "duration": None,
        "date": "2026-11-02T00:00:00.000Z",
        "tags": ["蓝色", "丸子头", "发型"],
        "characterName": "蕾姆",
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "蓝色高丸子头-淡紫针织挂脖长裙",
        "folder": "通用片段",
        "type": "video",
        "contentType": "video/mp4",
        "size": "12.3 MB",
        "sizeBytes": 12300000,
        "duration": "00:15",
        "date": "2026-11-03T00:00:00.000Z",
        "tags": ["蓝色高丸子头", "淡紫挂脖长裙", "通用片段"],
        "characterName": "",
    },
]


def matches(asset, query):
    if not query:
        return True
    needle = query.lower()
    haystack = " ".join([
        str(asset.get("name") or ""),
        str(asset.get("characterName") or ""),
        " ".join(asset.get("tags") or []),
    ]).lower()
    return needle in haystack


class AssetCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root / "session.json"
        self.session.write_text(json.dumps({"cookies": {"session": "test-session"}, "origin": None}))
        self.downloads = []
        downloads = self.downloads

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send_json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/api/auth/me":
                    if self.headers.get("Cookie") != "session=test-session":
                        return self.send_json(401, {"error": "Unauthorized"})
                    return self.send_json(200, {"user": {"name": "tester", "email": "t@example.com"}})
                if parsed.path == "/bytes":
                    body = b"media-bytes"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path == "/api/assets":
                    query = parse_qs(parsed.query)
                    filtered = [asset for asset in ASSETS if matches(asset, (query.get("q") or [""])[0])]
                    folder = (query.get("folder") or [""])[0]
                    if folder:
                        filtered = [asset for asset in filtered if asset["folder"] == folder]
                    limit = int((query.get("limit") or ["20"])[0])
                    offset = int((query.get("offset") or ["0"])[0])
                    page = filtered[offset:offset + limit]
                    return self.send_json(200, {
                        "assets": page,
                        "total": len(filtered),
                        "limit": limit,
                        "offset": offset,
                        "hasMore": offset + len(page) < len(filtered),
                    })
                if parsed.path.endswith("/download-url"):
                    asset_id = parsed.path.split("/")[-2]
                    asset = next((item for item in ASSETS if item["id"] == asset_id), None)
                    if not asset:
                        return self.send_json(404, {"error": "素材不存在"})
                    suffix = ".mp4" if asset["type"] == "video" else ".png"
                    return self.send_json(200, {
                        "url": f"http://127.0.0.1:{self.server.server_port}/bytes",
                        "filename": f"{asset['name']}{suffix}",
                    })
                if parsed.path.startswith("/api/assets/"):
                    asset_id = parsed.path.rsplit("/", 1)[-1]
                    asset = next((item for item in ASSETS if item["id"] == asset_id), None)
                    if not asset:
                        return self.send_json(404, {"error": "素材不存在"})
                    return self.send_json(200, {"asset": asset})
                self.send_json(404, {"error": "missing"})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"
        self.session.write_text(json.dumps({
            "cookies": {"session": "test-session"},
            "origin": self.base,
        }))
        self.common = ["--api", self.base, "--cookie-file", str(self.session), "--json"]

    def run_cli(self, command, *extra):
        output = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main([command, *self.common, *extra])
        return code, output.getvalue(), error.getvalue()

    def test_list_paginates_and_searches_by_name(self):
        code, stdout, _stderr = self.run_cli("list", "--limit", "2", "--page", "1")
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(len(payload["assets"]), 2)
        self.assertTrue(payload["hasMore"])
        self.assertNotIn("directUrl", json.dumps(payload))
        self.assertNotIn("thumbUrl", json.dumps(payload))
        self.assertEqual(payload["assets"][0]["uploadedAt"], format_uploaded_at(ASSETS[0]["date"]))

        code, stdout, _stderr = self.run_cli("list", "--limit", "2", "--page", "2")
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual([asset["id"] for asset in payload["assets"]], [ASSETS[2]["id"]])
        self.assertFalse(payload["hasMore"])

        code, stdout, _stderr = self.run_cli("list", "蓝色高丸子头")
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(payload["q"], "蓝色高丸子头")
        self.assertEqual([asset["name"] for asset in payload["assets"]], [
            "蓝色高丸子头",
            "蓝色高丸子头-淡紫针织挂脖长裙",
        ])

        code, stdout, _stderr = self.run_cli("list", "-q", "蕾姆", "--folder", "角色设定")
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual([asset["name"] for asset in payload["assets"]], ["蕾姆正脸"])

    def test_list_text_includes_upload_time(self):
        output = io.StringIO()
        error = io.StringIO()
        args = ["list", "--api", self.base, "--cookie-file", str(self.session), "--limit", "1"]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(args)
        stdout = output.getvalue()
        self.assertEqual(code, 0, error.getvalue())
        self.assertIn("上传时间", stdout)
        self.assertIn(format_uploaded_at(ASSETS[0]["date"]), stdout)

    def test_download_by_id_to_specified_path(self):
        destination = self.root / "out" / "face.png"
        code, stdout, _stderr = self.run_cli(
            "download", ASSETS[0]["id"], "--output", str(destination)
        )
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(payload["path"], str(destination))
        self.assertFalse(payload["cached"])
        self.assertEqual(destination.read_bytes(), b"media-bytes")

        code, stdout, _stderr = self.run_cli(
            "download", ASSETS[0]["id"], "--output", str(destination)
        )
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertTrue(payload["cached"])

    def test_download_by_unique_name_into_directory(self):
        output_dir = self.root / "clips"
        code, stdout, _stderr = self.run_cli(
            "download", "--name", "蕾姆正脸", "--output", str(output_dir)
        )
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(Path(payload["path"]).parent, output_dir)
        self.assertTrue(Path(payload["path"]).exists())

    def test_download_exact_name_wins_over_substring(self):
        output_dir = self.root / "hair"
        code, stdout, _stderr = self.run_cli(
            "download", "蓝色高丸子头", "--output", str(output_dir)
        )
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(payload["assetId"], ASSETS[1]["id"])
        self.assertEqual(Path(payload["path"]).parent, output_dir)

    def test_download_ambiguous_name_prints_ids(self):
        code, _stdout, stderr = self.run_cli("download", "蓝色")
        self.assertEqual(code, 1)
        self.assertIn("命中多条", stderr)
        self.assertIn(ASSETS[1]["id"], stderr)
        self.assertIn(ASSETS[2]["id"], stderr)

    def test_login_check_reuses_session(self):
        code, stdout, stderr = self.run_cli("login", "--check")
        payload = json.loads(stdout)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"]["name"], "tester")
        self.assertEqual(payload["cookieFile"], str(self.session))
        self.assertNotIn("test-session", stdout)

    def test_aigc_entry_dispatches_login(self):
        import aigc
        self.assertIs(aigc.main, main)


if __name__ == "__main__":
    unittest.main()
