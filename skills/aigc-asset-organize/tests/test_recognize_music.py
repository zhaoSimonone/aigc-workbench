"""Sanitized unit tests for recognize_music.py.

These tests never touch the real ACRCloud endpoint and never require real
credentials. They verify:

* the HMAC-SHA1 signature string format against a known vector
* the multipart request shape (fields, URL, file part)
* the response parser for success and no-match payloads
* the documented failure modes (file_not_found, no_audio,
  acrcloud_credentials_missing, ffprobe_not_found, ffmpeg_not_found,
  invalid_media, no_match, acrcloud_request_error)

Run with:

    python3 -m unittest skills.aigc-asset-organize.tests.test_recognize_music

or from the skill directory:

    python3 -m unittest tests.test_recognize_music
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Allow running this test file from either the repo root or the skill dir.
SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import recognize_music  # noqa: E402  module name is intentionally non-PEP8 for CLI


# A minimal ACRCloud success payload modeled on the public documentation.
SAMPLE_SUCCESS_PAYLOAD = {
    "status": {"msg": "Success", "version": "1.0", "code": 0},
    "metadata": {
        "music": [
            {
                "title": "Hello",
                "artists": [{"name": "Adele"}],
                "album": {"name": "Hello"},
                "acrid": "6049f11da7095e8bb8266871d4a70873",
                "score": 100,
                "duration_ms": 295000,
                "release_date": "2015-10-23",
                "genres": [{"name": "Pop"}],
                "external_metadata": {
                    "spotify": {"track": {"id": "4aebBr4JAihzJQR0CiIZJv"}},
                    "youtube": {"vid": "YQHsXMglC9A"},
                    "deezer": {"track": {"id": "110265034"}},
                },
            }
        ]
    },
    "result_type": 0,
}

SAMPLE_NO_MATCH_PAYLOAD = {
    "status": {"msg": "No result", "version": "1.0", "code": 1001},
    "metadata": {},
    "result_type": 0,
}


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)
        # ``recognize_with_acrcloud`` sets this to "utf-8" before calling
        # ``.json()``; tests can assert on it to verify the fix.
        self.encoding = None

    def json(self) -> dict:
        return self._payload


class StringToSignTests(unittest.TestCase):
    def test_string_to_sign_format(self):
        signed = recognize_music.build_string_to_sign("AK", "1700000000")
        self.assertEqual(
            signed,
            "POST\n/v1/identify\nAK\naudio\n1\n1700000000",
        )

    def test_signature_matches_reference_vector(self):
        """Reproduce the ACRCloud Python sample signature with a known vector."""
        access_secret = "test_secret"
        string_to_sign = "POST\n/v1/identify\nAK\naudio\n1\n1700000000"
        expected = base64.b64encode(
            hmac.new(
                access_secret.encode("ascii"),
                string_to_sign.encode("ascii"),
                digestmod=hashlib.sha1,
            ).digest()
        ).decode("ascii")
        actual = recognize_music.sign_request(access_secret, string_to_sign)
        self.assertEqual(actual, expected)
        # Sanity check the vector is deterministic and non-empty.
        self.assertTrue(actual)


class NormalizeResultTests(unittest.TestCase):
    def test_success_payload_is_normalized(self):
        result = recognize_music.normalize_acrcloud_result(
            SAMPLE_SUCCESS_PAYLOAD, start=6.0, duration=10.0, attempts=1
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["success"])
        self.assertEqual(result["title"], "Hello")
        self.assertEqual(result["artist"], "Adele")
        self.assertEqual(result["album"], "Hello")
        self.assertEqual(result["acrcloud_id"], "6049f11da7095e8bb8266871d4a70873")
        self.assertEqual(result["source"], "acrcloud")
        self.assertEqual(result["shazam_id"], "")
        self.assertEqual(result["shazam_url"], "")
        self.assertEqual(result["matches"], 1)
        self.assertEqual(result["segment_start"], 6.0)
        self.assertEqual(result["segment_duration"], 10.0)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["spotify_id"], "4aebBr4JAihzJQR0CiIZJv")
        self.assertEqual(result["youtube_id"], "YQHsXMglC9A")
        self.assertEqual(result["deezer_id"], "110265034")
        self.assertEqual(result["genre"], "Pop")
        self.assertEqual(result["release_date"], "2015-10-23")
        self.assertEqual(result["duration_ms"], 295000)

    def test_no_match_payload_returns_none(self):
        result = recognize_music.normalize_acrcloud_result(
            SAMPLE_NO_MATCH_PAYLOAD, start=0.0, duration=5.0, attempts=1
        )
        self.assertIsNone(result)

    def test_empty_music_list_returns_none(self):
        payload = {"status": {"code": 0}, "metadata": {"music": []}}
        self.assertIsNone(
            recognize_music.normalize_acrcloud_result(payload, 0.0, 5.0, 1)
        )

    def test_missing_title_and_acrid_returns_none(self):
        payload = {
            "status": {"code": 0},
            "metadata": {"music": [{"score": 100}]},
        }
        self.assertIsNone(
            recognize_music.normalize_acrcloud_result(payload, 0.0, 5.0, 1)
        )


class BuildSegmentCandidatesTests(unittest.TestCase):
    def test_short_clip_uses_full_duration(self):
        self.assertEqual(
            recognize_music.build_segment_candidates(5.0),
            [(0.0, 5.0)],
        )

    def test_long_clip_picks_three_distinct_starts(self):
        candidates = recognize_music.build_segment_candidates(120.0)
        self.assertEqual(len(candidates), 3)
        for _, duration in candidates:
            self.assertGreaterEqual(duration, recognize_music.SEGMENT_MIN_SECONDS)
            self.assertLessEqual(duration, recognize_music.SEGMENT_MAX_SECONDS)
        starts = [start for start, _ in candidates]
        self.assertEqual(len(set(round(s, 3) for s in starts)), 3)


class ResolveCredentialsTests(unittest.TestCase):
    def setUp(self):
        # Snapshot and clear env vars so tests are deterministic.
        self._saved = {
            key: os.environ.get(key)
            for key in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_SECRET_KEY")
        }
        for key in self._saved:
            if key in os.environ:
                del os.environ[key]

    def tearDown(self):
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
            elif key in os.environ:
                del os.environ[key]

    def test_missing_credentials_raises(self):
        with self.assertRaises(recognize_music.RecognitionFailure) as ctx:
            recognize_music.resolve_credentials(None, None, None)
        self.assertEqual(ctx.exception.error, "acrcloud_credentials_missing")

    def test_env_vars_are_used(self):
        os.environ["ACRCLOUD_HOST"] = "example.host"
        os.environ["ACRCLOUD_ACCESS_KEY"] = "AK"
        os.environ["ACRCLOUD_SECRET_KEY"] = "SK"
        host, ak, sk = recognize_music.resolve_credentials(None, None, None)
        self.assertEqual(host, "example.host")
        self.assertEqual(ak, "AK")
        self.assertEqual(sk, "SK")

    def test_overrides_take_priority_over_env(self):
        os.environ["ACRCLOUD_HOST"] = "env.host"
        os.environ["ACRCLOUD_ACCESS_KEY"] = "ENV_AK"
        os.environ["ACRCLOUD_SECRET_KEY"] = "ENV_SK"
        host, ak, sk = recognize_music.resolve_credentials("override.host", "OV_AK", "OV_SK")
        self.assertEqual(host, "override.host")
        self.assertEqual(ak, "OV_AK")
        self.assertEqual(sk, "OV_SK")

    def test_default_host_when_unset(self):
        os.environ["ACRCLOUD_ACCESS_KEY"] = "AK"
        os.environ["ACRCLOUD_SECRET_KEY"] = "SK"
        host, _, _ = recognize_music.resolve_credentials(None, None, None)
        self.assertEqual(host, recognize_music.ACRCLOUD_DEFAULT_HOST)


class RecognizeFileFailureTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_SECRET_KEY")
        }
        for key in self._saved:
            if key in os.environ:
                del os.environ[key]

    def tearDown(self):
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
            elif key in os.environ:
                del os.environ[key]

    def test_file_not_found(self):
        result = recognize_music.recognize_file(Path("/tmp/does-not-exist-12345.mp4"))
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "file_not_found")

    def test_credentials_missing_with_real_file(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(b"RIFF\x00\x00\x00\x00WAVEfmt ")
            path = Path(tmp.name)
        try:
            result = recognize_music.recognize_file(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "acrcloud_credentials_missing")

    def test_no_audio(self):
        # Generate a 2s video without audio using ffmpeg.
        try:
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                video = Path(tmp) / "no_audio.mp4"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120",
                        "-c:v", "libx264", "-an", str(video),
                    ],
                    check=True,
                )
                os.environ["ACRCLOUD_ACCESS_KEY"] = "AK"
                os.environ["ACRCLOUD_SECRET_KEY"] = "SK"
                result = recognize_music.recognize_file(video)
        except FileNotFoundError:
            self.skipTest("ffmpeg not installed")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "no_audio")

    def test_invalid_media_text_file(self):
        os.environ["ACRCLOUD_ACCESS_KEY"] = "AK"
        os.environ["ACRCLOUD_SECRET_KEY"] = "SK"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as tmp:
            tmp.write("hello world, this is not media")
            path = Path(tmp.name)
        try:
            result = recognize_music.recognize_file(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_media")

    def test_ffprobe_not_found_with_clean_path(self):
        os.environ["ACRCLOUD_ACCESS_KEY"] = "AK"
        os.environ["ACRCLOUD_SECRET_KEY"] = "SK"
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(b"RIFF\x00\x00\x00\x00WAVEfmt ")
            path = Path(tmp.name)
        try:
            def fake_resolve_binary(name, override=None):
                raise recognize_music.RecognitionFailure(
                    f"{name}_not_found",
                    "FFmpeg or FFprobe is not installed.",
                )
            with mock.patch("recognize_music.resolve_binary", side_effect=fake_resolve_binary):
                result = recognize_music.recognize_file(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "ffprobe_not_found")

    def test_ffmpeg_not_found_when_extraction_needed(self):
        """A long video needs ffmpeg for segment extraction; if ffmpeg is
        missing, the error must be ffmpeg_not_found, not a no_match."""
        os.environ["ACRCLOUD_ACCESS_KEY"] = "AK"
        os.environ["ACRCLOUD_SECRET_KEY"] = "SK"
        try:
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                video = Path(tmp) / "long.mp4"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc=duration=30:size=160x120",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video),
                    ],
                    check=True,
                )
                ffprobe_path = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

                def fake_resolve_binary(name, override=None):
                    if name == "ffmpeg":
                        raise recognize_music.RecognitionFailure(
                            "ffmpeg_not_found",
                            "FFmpeg or FFprobe is not installed.",
                        )
                    if name == "ffprobe":
                        return ffprobe_path
                    return override or shutil.which(name) or f"/opt/homebrew/bin/{name}"
                with mock.patch("recognize_music.resolve_binary", side_effect=fake_resolve_binary):
                    result = recognize_music.recognize_file(video)
        except FileNotFoundError:
            self.skipTest("ffmpeg not installed")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "ffmpeg_not_found")


class ACRCloudRequestTests(unittest.TestCase):
    """Verify the multipart request shape and signature using a mocked POST."""

    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_SECRET_KEY")
        }
        for key in self._saved:
            if key in os.environ:
                del os.environ[key]
        os.environ["ACRCLOUD_HOST"] = "identify-cn-north-1.acrcloud.cn"
        os.environ["ACRCLOUD_ACCESS_KEY"] = "AK_TEST"
        os.environ["ACRCLOUD_SECRET_KEY"] = "SK_TEST"

    def tearDown(self):
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
            elif key in os.environ:
                del os.environ[key]

    def test_request_shape_and_signature(self):
        # Generate a short WAV so recognize_file uses the file directly without
        # invoking ffmpeg for segment extraction.
        try:
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "clip.wav"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le",
                        str(audio),
                    ],
                    check=True,
                )
                captured = {}

                def fake_post(url, data=None, files=None, timeout=None):
                    captured["url"] = url
                    captured["data"] = data
                    captured["files"] = files
                    captured["timeout"] = timeout
                    # The file part holds an open file handle that will be closed
                    # once recognize_with_acrcloud's ``with`` block exits, so
                    # snapshot the bytes now while the handle is still valid.
                    filename, fileobj, content_type = files["sample"]
                    captured["sample_bytes"] = fileobj.read()
                    captured["sample_filename"] = filename
                    captured["sample_content_type"] = content_type
                    response = FakeResponse(SAMPLE_SUCCESS_PAYLOAD)
                    captured["response"] = response
                    return response

                with mock.patch("requests.post", side_effect=fake_post):
                    result = recognize_music.recognize_file(audio, timeout=10.0)
        except FileNotFoundError:
            self.skipTest("ffmpeg not installed")

        self.assertTrue(result["success"], msg=str(result))
        self.assertEqual(result["title"], "Hello")
        self.assertEqual(result["artist"], "Adele")
        self.assertEqual(captured["url"], "https://identify-cn-north-1.acrcloud.cn/v1/identify")
        # Form fields
        self.assertEqual(captured["data"]["access_key"], "AK_TEST")
        self.assertEqual(captured["data"]["data_type"], "audio")
        self.assertEqual(captured["data"]["signature_version"], "1")
        self.assertIn("sample_bytes", captured["data"])
        self.assertIn("timestamp", captured["data"])
        self.assertIn("signature", captured["data"])
        # File part carries the audio bytes
        self.assertIn("sample", captured["files"])
        self.assertTrue(captured["sample_filename"].endswith(".wav"))
        self.assertEqual(captured["sample_content_type"], "application/octet-stream")
        self.assertGreater(len(captured["sample_bytes"]), 44)
        # sample_bytes form field must match the uploaded file size
        self.assertEqual(int(captured["data"]["sample_bytes"]), len(captured["sample_bytes"]))
        # UTF-8 encoding must be forced before .json() to avoid mojibake on
        # non-ASCII metadata returned by ACRCloud.
        self.assertEqual(captured["response"].encoding, "utf-8")
        # Signature must match HMAC-SHA1 of the canonical string with the same timestamp.
        expected_sig = recognize_music.sign_request(
            "SK_TEST",
            recognize_music.build_string_to_sign("AK_TEST", captured["data"]["timestamp"]),
        )
        self.assertEqual(captured["data"]["signature"], expected_sig)

    def test_request_error_is_reported(self):
        try:
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "clip.wav"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le",
                        str(audio),
                    ],
                    check=True,
                )
                import requests as real_requests
                with mock.patch("requests.post", side_effect=real_requests.exceptions.Timeout("boom")):
                    result = recognize_music.recognize_file(audio, timeout=5.0)
        except FileNotFoundError:
            self.skipTest("ffmpeg not installed")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "acrcloud_request_error")

    def test_no_match_returns_no_match(self):
        try:
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "clip.wav"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le",
                        str(audio),
                    ],
                    check=True,
                )
                with mock.patch("requests.post", return_value=FakeResponse(SAMPLE_NO_MATCH_PAYLOAD)):
                    result = recognize_music.recognize_file(audio, timeout=5.0)
        except FileNotFoundError:
            self.skipTest("ffmpeg not installed")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "no_match")

    def test_auth_error_returns_request_error(self):
        """ACRCloud-side auth errors (code != 0/1001) must surface as
        acrcloud_request_error, not no_match, so the user can tell
        credential/code issues apart from a clean no-match."""
        try:
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "clip.wav"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le",
                        str(audio),
                    ],
                    check=True,
                )
                auth_error_payload = {
                    "status": {"msg": "Invalid signature", "version": "1.0", "code": 2003},
                    "metadata": {},
                    "result_type": 0,
                }
                with mock.patch("requests.post", return_value=FakeResponse(auth_error_payload)):
                    result = recognize_music.recognize_file(audio, timeout=5.0)
        except FileNotFoundError:
            self.skipTest("ffmpeg not installed")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "acrcloud_request_error")
        self.assertIn("2003", result.get("message", ""))


if __name__ == "__main__":
    unittest.main()
