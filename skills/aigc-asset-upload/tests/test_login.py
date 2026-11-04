import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from login import main


class LoginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name) / "session.json"
        self.logins = []
        logins = self.logins

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, cookie=False):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if cookie:
                    self.send_header("Set-Cookie", "session=test-session; Max-Age=3600; HttpOnly")
                self.end_headers()
                self.wfile.write(b'{"user":{"id":"test"}}' if status == 200 else b'{"error":"Unauthorized"}')

            def do_GET(self):
                assert self.path == "/api/auth/me"
                self.reply(200 if self.headers.get("Cookie") == "session=test-session" else 401)

            def do_POST(self):
                assert self.path == "/api/auth/login"
                credentials = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                logins.append(credentials)
                valid = credentials == {"email": "test@example.com", "password": "test-password"}
                self.reply(200 if valid else 401, cookie=valid)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.args = ["--api", f"http://127.0.0.1:{server.server_port}",
                     "--cookie-file", str(self.session)]

    def run_login(self, *extra, password="test-password"):
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), \
                patch("builtins.input", return_value="test@example.com") as email, \
                patch("getpass.getpass", return_value=password) as secret, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = main(self.args + list(extra))
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn("test-session", output.getvalue())
        return result, email, secret

    def test_login_persists_private_session_and_reuses_without_prompt(self):
        result, email, secret = self.run_login()
        self.assertEqual(result, 0)
        email.assert_called_once()
        secret.assert_called_once()
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("test-password", self.session.read_text())
        result, email, secret = self.run_login()
        self.assertEqual(result, 0)
        email.assert_not_called()
        secret.assert_not_called()
        self.assertEqual(len(self.logins), 1)
        self.assertEqual(self.run_login("--check")[0], 0)

    def test_check_missing_session_never_prompts(self):
        result, email, secret = self.run_login("--check")
        self.assertEqual(result, 1)
        email.assert_not_called()
        secret.assert_not_called()
        self.assertFalse(self.session.exists())

    def test_invalid_password_does_not_report_success(self):
        self.assertEqual(self.run_login(password="wrong-password")[0], 1)
        self.assertFalse(self.session.exists())


if __name__ == "__main__":
    unittest.main()
