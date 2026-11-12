import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "browser_session", Path(__file__).resolve().parents[1] / "scripts/browser_session.py")
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tfake%3Asession\n"
    ".instagram.com\tTRUE\t/\tTRUE\t0\tds_user_id\t123\n"
    ".notinstagram.com\tTRUE\t/\tTRUE\t0\tds_user_id\t999\n"
)


class BrowserSessionTests(unittest.TestCase):
    def export(self, expected="123"):
        paths = []

        def run(command, **kwargs):
            self.assertEqual(command[command.index("--proxy") + 1], "")
            self.assertEqual(command[-1], "http://127.0.0.1:1/cookie-export")
            path = Path(command[command.index("--cookies") + 1])
            paths.append(path)
            path.write_text(COOKIES)

        try:
            with patch.object(session.subprocess, "run", side_effect=run):
                return session.export_browser_cookies(
                    "chrome:Authorized", "yt-dlp", proxy="https://unused.example",
                    expected_user_id=expected)
        finally:
            for path in paths:
                self.assertFalse(path.exists())

    def test_local_export_filters_domains_and_removes_temporary_cookie_file(self):
        result = session.parse_cookie_jar(self.export(), "instagram.com")
        self.assertEqual(result, {"sessionid": "fake:session", "ds_user_id": "123"})

    def test_wrong_account_is_rejected_and_cookie_file_removed(self):
        with self.assertRaisesRegex(RuntimeError, "不是预期"):
            self.export("456")

    def test_failed_export_does_not_return_a_session(self):
        with patch.object(session.subprocess, "run"):
            with self.assertRaisesRegex(RuntimeError, "没有写出"):
                session.export_browser_cookies("chrome", "yt-dlp")

    def test_missing_session_is_rejected(self):
        with patch.dict(globals(), {"COOKIES": ".instagram.com\tTRUE\t/\tTRUE\t0\tds_user_id\t123\n"}):
            with self.assertRaisesRegex(RuntimeError, "没有 Instagram 会话"):
                self.export()


if __name__ == "__main__":
    unittest.main()
