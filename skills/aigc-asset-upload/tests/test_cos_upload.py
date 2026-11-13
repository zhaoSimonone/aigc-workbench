import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cos_upload
from assets import main
from shelf_client import APIError


class CosUploadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / "video space #1.mp4"
        self.file.write_bytes(b"test")

    def plan(self, *args):
        return cos_upload.make_plan(
            cos_upload.build_parser().parse_args([str(self.file), *args]))

    def test_default_and_custom_directories(self):
        self.assertEqual(self.plan()["key"], "file/" + self.file.name)
        for args in [("/videos//day/",), ("--path", "videos/day"), ("--dest", "videos/day")]:
            self.assertEqual(self.plan(*args)["key"], "videos/day/" + self.file.name)
        self.assertEqual(self.plan("/")["key"], self.file.name)

    def test_invalid_inputs(self):
        for args in [
            ("../file",), ("a/../b",), ("https://host/file",),
            ("--expires", "0"), ("--expires", "604801"),
            ("file", "--path", "video"), ("bad\npath",),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.plan(*args)
        with self.assertRaises(ValueError):
            cos_upload.make_plan(cos_upload.build_parser().parse_args([str(self.file.parent)]))

    def test_dry_run_routes_through_aigc_without_node_or_network(self):
        with patch("cos_upload.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(["cos", "upload", str(self.file), "--dry-run", "--json"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["bucket"], "aigc-1257258774")
        self.assertEqual(json.loads(out.getvalue())["key"], "file/" + self.file.name)

    def test_process_receives_structured_input_and_propagates_exit_code(self):
        with patch("cos_upload.shutil.which", return_value="/usr/bin/node"), \
                patch("cos_upload.subprocess.run") as run:
            run.return_value.returncode = 1
            self.assertEqual(main(["cos", "upload", str(self.file), "custom", "--force",
                                   "--local-credentials"]), 1)
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "/usr/bin/node")
        self.assertEqual(json.loads(kwargs["input"])["key"], "custom/" + self.file.name)
        self.assertTrue(json.loads(kwargs["input"])["force"])
        self.assertNotIn("shell", kwargs)

    def test_help_needs_no_node(self):
        with patch("cos_upload.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["cos", "upload", "--help"]), 0)
        run.assert_not_called()

    def session_client(self, *, changes=None, status=200):
        plan = self.plan()
        ticket = {
            "bucket": cos_upload.BUCKET, "region": "ap-shanghai", "key": plan["key"],
            "size": 4, "uploadToken": "private-ticket",
            "url": f"https://{cos_upload.BUCKET}.cos.ap-shanghai.myqcloud.com/file/upload",
            "headers": {"Content-Type": "video/mp4", "Content-Length": "4"},
        }
        ticket.update(changes or {})
        client = MagicMock()
        client.request.side_effect = [
            (200, ticket),
            (200, {"size": 4, "url": "https://example.test/download", "expiresIn": 86400}),
        ]
        uploaded = bytearray()

        def send(method, url, body, headers):
            self.assertEqual(method, "PUT")
            while chunk := body.read(2):
                uploaded.extend(chunk)
            return contextlib.nullcontext(SimpleNamespace(status=status))

        client._open.side_effect = send
        return client, uploaded

    def test_default_mode_uses_saved_login_and_never_reads_or_writes_cos_keys(self):
        client, uploaded = self.session_client()
        out, err = io.StringIO(), io.StringIO()
        with patch("cos_upload.Client", return_value=client), \
                patch("cos_upload.subprocess.run") as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["cos", "upload", str(self.file), "--json"]), 0)
        run.assert_not_called()
        client.require.assert_called_once_with("GET", "/api/auth/me")
        self.assertEqual(uploaded, b"test")
        self.assertEqual(json.loads(out.getvalue())["url"], "https://example.test/download")
        self.assertNotIn("private-ticket", out.getvalue() + err.getvalue())
        self.assertEqual(list(self.file.parent.iterdir()), [self.file])
        self.assertEqual(client.request.call_args_list[0].args[1], "/api/cos/upload-ticket")
        self.assertEqual(client.request.call_args_list[1].args[1], "/api/cos/finalize-upload")

    def test_failed_login_prompts_for_login_without_uploading(self):
        client, _ = self.session_client()
        client.require.side_effect = APIError(401, {"error": "expired"})
        with patch("cos_upload.Client", return_value=client), \
                contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(main(["cos", "upload", str(self.file)]), 1)
        self.assertIn("aigc login", out.getvalue())
        client.request.assert_not_called()
        client._open.assert_not_called()

    def test_old_server_explains_required_deployment(self):
        client, _ = self.session_client()
        client.request.side_effect = [(404, {"raw": "Cannot POST /api/cos/upload-ticket"})]
        with patch("cos_upload.Client", return_value=client), \
                contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(main(["cos", "upload", str(self.file)]), 1)
        self.assertIn("尚未部署", out.getvalue())
        client._open.assert_not_called()

    def test_invalid_ticket_cannot_send_file_to_other_host(self):
        for changes in [
            {"url": "https://other.example/upload"}, {"bucket": "another"},
            {"url": f"http://{cos_upload.BUCKET}.cos.ap-shanghai.myqcloud.com/file/upload"},
            {"key": "other/file"}, {"size": 5},
            {"headers": {"Content-Length": "5", "Content-Type": "video/mp4"}},
        ]:
            client, _ = self.session_client(changes=changes)
            with patch("cos_upload.Client", return_value=client), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["cos", "upload", str(self.file)]), 1)
            client._open.assert_not_called()

    def test_failed_put_does_not_finalize_or_print_download_url(self):
        client, _ = self.session_client(status=403)
        with patch("cos_upload.Client", return_value=client), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["cos", "upload", str(self.file)]), 1)
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
