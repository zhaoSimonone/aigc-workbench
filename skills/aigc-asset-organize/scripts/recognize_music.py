#!/usr/bin/env python3
"""Recognize music in a local video or audio file using ACRCloud.

The command deliberately keeps stdout machine-readable: exactly one JSON
object is emitted, while optional diagnostics go to stderr.

ACRCloud credentials are read from environment variables
(``ACRCLOUD_HOST``, ``ACRCLOUD_ACCESS_KEY``, ``ACRCLOUD_SECRET_KEY``) and
are never written to disk, logs, or stdout.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}
DEFAULT_TIMEOUT = 45.0

ACRCLOUD_HTTP_METHOD = "POST"
ACRCLOUD_HTTP_URI = "/v1/identify"
ACRCLOUD_DATA_TYPE = "audio"
ACRCLOUD_SIGNATURE_VERSION = "1"
ACRCLOUD_DEFAULT_HOST = "identify-cn-north-1.acrcloud.cn"
# ACRCloud accepts up to ~10 seconds of audio per request; keep segments
# within 8-10 seconds so each attempt fits comfortably under the limit.
SHORT_CLIP_THRESHOLD = 15.0
SEGMENT_MIN_SECONDS = 8.0
SEGMENT_MAX_SECONDS = 10.0


class RecognitionFailure(Exception):
    """An expected, user-actionable recognition failure."""

    def __init__(self, error: str, message: str, attempts: int = 0, **extra: Any):
        super().__init__(message)
        self.error = error
        self.message = message
        self.attempts = attempts
        self.extra = extra


def failure_json(error: str, message: str, attempts: int = 0, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "success": False,
        "error": error,
        "message": message,
    }
    if attempts:
        result["attempts"] = attempts
    result.update(extra)
    return result


def resolve_binary(name: str, override: Optional[str] = None) -> str:
    """Resolve an executable without assuming a particular operating system."""
    candidates = [
        override,
        os.environ.get(f"AIGC_{name.upper()}_PATH"),
        shutil.which(name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    if name in {"ffmpeg", "ffprobe"}:
        raise RecognitionFailure(
            f"{name}_not_found",
            "FFmpeg or FFprobe is not installed.",
        )
    raise RecognitionFailure(f"{name}_not_found", f"{name} is not installed.")


def run_command(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RecognitionFailure("ffmpeg_not_found", "FFmpeg or FFprobe is not installed.") from error
    except subprocess.TimeoutExpired as error:
        raise RecognitionFailure(
            "invalid_media",
            "FFmpeg/FFprobe timed out while processing the input file.",
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "media processing failed").strip()
        raise RecognitionFailure("invalid_media", detail[-500:]) from error


def _positive_float(*values: Any) -> Optional[float]:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def probe_media(ffprobe_path: str, input_path: Path) -> Dict[str, Any]:
    """Read stream types and duration through ffprobe, not OpenCV."""
    try:
        completed = run_command(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(input_path),
            ],
            timeout=120,
        )
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as error:
        raise RecognitionFailure("invalid_media", "FFprobe returned invalid media metadata.") from error

    streams = payload.get("streams") or []
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if not streams:
        raise RecognitionFailure("invalid_media", "The input file has no readable media streams.")
    if not has_audio:
        raise RecognitionFailure("no_audio", "The input file does not contain an audio stream.")
    format_info = payload.get("format") or {}
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    duration = _positive_float(format_info.get("duration"), audio_stream.get("duration"))
    if duration is None:
        raise RecognitionFailure("invalid_media", "Could not determine the input duration.")
    return {
        "media_type": "video" if has_video else "audio",
        "duration": duration,
        "has_audio": has_audio,
    }


def detect_media_type(path: Path, probe: Optional[Dict[str, Any]] = None) -> str:
    """Return video/audio using stream metadata, with an extension fallback."""
    if probe and probe.get("media_type") in {"video", "audio"}:
        return str(probe["media_type"])
    return "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "audio"


def extract_audio_segment(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    start: float,
    duration: float,
    normalize: bool = False,
) -> Path:
    """Extract a small, ACRCloud-friendly mono PCM WAV segment."""
    command = [
        ffmpeg_path,
        "-v",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{max(0.1, duration):.3f}",
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
    ]
    if normalize:
        command.extend(["-af", "volume=1.5"])
    command.append(str(output_path))
    run_command(command, timeout=10 * 60)
    if not output_path.is_file() or output_path.stat().st_size <= 44:
        raise RecognitionFailure("invalid_media", "FFmpeg did not produce a usable audio segment.")
    return output_path


def build_segment_candidates(duration: float) -> List[Tuple[float, float]]:
    """Choose a full short clip or three non-identical samples from a long file."""
    if duration <= SHORT_CLIP_THRESHOLD:
        return [(0.0, max(0.1, duration))]
    segment_duration = min(SEGMENT_MAX_SECONDS, max(SEGMENT_MIN_SECONDS, duration - 0.01))
    latest_start = max(0.0, duration - segment_duration)
    starts = [min(latest_start, max(0.0, duration * fraction)) for fraction in (0.10, 0.45, 0.72)]
    # Very short files cannot fit three windows at those fractions; use three
    # distinct offsets instead of silently reducing retry coverage.
    if len({round(start, 3) for start in starts}) < 3 and latest_start > 0:
        starts = [starts[0], (starts[0] + latest_start) / 2.0, latest_start]
    candidates: List[Tuple[float, float]] = []
    for start in starts:
        if not any(abs(start - previous_start) < 0.01 for previous_start, _ in candidates):
            candidates.append((start, segment_duration))
    return candidates or [(0.0, segment_duration)]


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def build_string_to_sign(access_key: str, timestamp: str) -> str:
    """Build the canonical string used as the HMAC-SHA1 message.

    Format defined by ACRCloud Identify Protocol V1:
        http_method + "\\n" + http_uri + "\\n" + access_key + "\\n" +
        data_type + "\\n" + signature_version + "\\n" + timestamp
    """
    return "\n".join(
        [
            ACRCLOUD_HTTP_METHOD,
            ACRCLOUD_HTTP_URI,
            access_key,
            ACRCLOUD_DATA_TYPE,
            ACRCLOUD_SIGNATURE_VERSION,
            timestamp,
        ]
    )


def sign_request(access_secret: str, string_to_sign: str) -> str:
    """Return the base64-encoded HMAC-SHA1 signature ACRCloud expects."""
    digest = hmac.new(
        access_secret.encode("ascii"),
        string_to_sign.encode("ascii"),
        digestmod=hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def resolve_credentials(
    host_override: Optional[str],
    access_key_override: Optional[str],
    secret_key_override: Optional[str],
) -> Tuple[str, str, str]:
    """Read ACRCloud credentials from overrides or environment variables."""
    host = _text(host_override) or _text(os.environ.get("ACRCLOUD_HOST")) or ACRCLOUD_DEFAULT_HOST
    access_key = _text(access_key_override) or _text(os.environ.get("ACRCLOUD_ACCESS_KEY"))
    secret_key = _text(secret_key_override) or _text(os.environ.get("ACRCLOUD_SECRET_KEY"))
    if not access_key or not secret_key:
        raise RecognitionFailure(
            "acrcloud_credentials_missing",
            "ACRCloud credentials are not configured. Set ACRCLOUD_ACCESS_KEY and ACRCLOUD_SECRET_KEY environment variables.",
        )
    return host, access_key, secret_key


class MusicRecognitionProvider:
    """Provider interface kept for future swappable backends."""

    def recognize(self, audio_path: Path, timeout: float) -> Dict[str, Any]:
        raise NotImplementedError


class ACRCloudProvider(MusicRecognitionProvider):
    """ACRCloud Identify Protocol V1 client."""

    def __init__(self, host: str, access_key: str, secret_key: str) -> None:
        self.host = host
        self.access_key = access_key
        self.secret_key = secret_key

    def recognize(self, audio_path: Path, timeout: float) -> Dict[str, Any]:
        return recognize_with_acrcloud(
            audio_path,
            host=self.host,
            access_key=self.access_key,
            secret_key=self.secret_key,
            timeout=timeout,
        )


def recognize_with_acrcloud(
    audio_path: Path,
    *,
    host: str,
    access_key: str,
    secret_key: str,
    timeout: float,
) -> Dict[str, Any]:
    """Send audio to ACRCloud and return the parsed JSON response."""
    try:
        import requests  # imported lazily so credential errors surface first
    except ImportError as error:  # pragma: no cover - exercised via tests with mocked import
        raise RecognitionFailure(
            "acrcloud_request_error",
            "The requests package is not installed. Run: python3 -m pip install -r requirements.txt",
        ) from error

    timestamp = str(int(time.time()))
    string_to_sign = build_string_to_sign(access_key, timestamp)
    signature = sign_request(secret_key, string_to_sign)
    sample_bytes = audio_path.stat().st_size
    url = f"https://{host}{ACRCLOUD_HTTP_URI}"
    data = {
        "access_key": access_key,
        "sample_bytes": str(sample_bytes),
        "timestamp": timestamp,
        "signature": signature,
        "data_type": ACRCLOUD_DATA_TYPE,
        "signature_version": ACRCLOUD_SIGNATURE_VERSION,
    }
    try:
        with open(audio_path, "rb") as sample_file:
            files = {"sample": (audio_path.name, sample_file, "application/octet-stream")}
            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=max(5.0, timeout),
            )
    except Exception as error:
        cls = error.__class__.__name__
        message = str(error).strip() or cls
        raise RecognitionFailure("acrcloud_request_error", f"ACRCloud request failed: {cls}: {message[:400]}") from error

    # ACRCloud returns JSON encoded as UTF-8 but does not always set the
    # charset in the Content-Type header, so ``requests`` may fall back to
    # ISO-8859-1 and mangle non-ASCII metadata. Force UTF-8 before decoding.
    response.encoding = "utf-8"
    try:
        payload = response.json()
    except ValueError as error:
        snippet = (response.text or "")[:200]
        raise RecognitionFailure(
            "acrcloud_request_error",
            f"ACRCloud returned non-JSON response (HTTP {response.status_code}): {snippet}",
        ) from error

    if not isinstance(payload, dict):
        raise RecognitionFailure(
            "acrcloud_request_error",
            f"ACRCloud returned an unexpected JSON shape (HTTP {response.status_code}).",
        )
    return payload


def normalize_acrcloud_result(
    raw: Dict[str, Any],
    start: float,
    duration: float,
    attempts: int,
) -> Optional[Dict[str, Any]]:
    """Convert the ACRCloud metadata payload into the standardized JSON shape.

    Returns ``None`` when the response is well-formed but does not contain a
    confident music match (so the caller can try the next segment), and raises
    ``RecognitionFailure("acrcloud_request_error", ...)`` when ACRCloud
    reports an authentication/server error that retrying will not fix.
    """
    if not isinstance(raw, dict):
        return None
    status = raw.get("status") or {}
    if not isinstance(status, dict):
        status = {}
    code = status.get("code")
    msg = _text(status.get("msg")) or ""
    # ACRCloud status codes: 0 = Success, 1001 = No result. Other non-zero
    # codes (e.g. 2003 invalid signature, 2004 invalid access key,
    # 3013 invalid data type) indicate request/credential problems that
    # retrying will not fix; surface them as request errors so the caller can
    # distinguish credential/network issues from a clean no-match.
    if code not in (0, 1001, None):
        raise RecognitionFailure(
            "acrcloud_request_error",
            f"ACRCloud returned status code {code}: {msg or 'unspecified error'}",
        )
    if code == 1001:
        return None
    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    music_list = metadata.get("music") or []
    if not isinstance(music_list, list) or not music_list:
        return None
    track = music_list[0] if isinstance(music_list[0], dict) else {}
    title = _text(track.get("title"))
    artists = track.get("artists") or []
    artist = ""
    if isinstance(artists, list) and artists:
        first_artist = artists[0] if isinstance(artists[0], dict) else {}
        artist = _text(first_artist.get("name")) or ""
    album = ""
    album_info = track.get("album") or {}
    if isinstance(album_info, dict):
        album = _text(album_info.get("name")) or ""
    acrcloud_id = _text(track.get("acrid")) or ""
    if not title and not acrcloud_id:
        return None
    result: Dict[str, Any] = {
        "success": True,
        "title": title or "",
        "artist": artist or "",
        "album": album,
        "shazam_id": "",
        "shazam_url": "",
        "acrcloud_id": acrcloud_id,
        "matches": len(music_list),
        "source": "acrcloud",
        "segment_start": round(start, 3),
        "segment_duration": round(duration, 3),
    }
    score = track.get("score")
    if isinstance(score, (int, float)):
        result["score"] = score
    external = track.get("external_metadata") or {}
    if isinstance(external, dict):
        spotify = external.get("spotify") or {}
        if isinstance(spotify, dict):
            spotify_track = spotify.get("track") or {}
            if isinstance(spotify_track, dict):
                spotify_id = _text(spotify_track.get("id"))
                if spotify_id:
                    result["spotify_id"] = spotify_id
        youtube = external.get("youtube") or {}
        if isinstance(youtube, dict) and _text(youtube.get("vid")):
            result["youtube_id"] = _text(youtube.get("vid"))
        deezer = external.get("deezer") or {}
        if isinstance(deezer, dict):
            deezer_track = deezer.get("track") or {}
            if isinstance(deezer_track, dict):
                deezer_id = _text(deezer_track.get("id"))
                if deezer_id:
                    result["deezer_id"] = deezer_id
    genres = track.get("genres") or []
    if isinstance(genres, list) and genres:
        first_genre = genres[0] if isinstance(genres[0], dict) else {}
        genre_name = _text(first_genre.get("name"))
        if genre_name:
            result["genre"] = genre_name
    release_date = _text(track.get("release_date"))
    if release_date:
        result["release_date"] = release_date
    duration_ms = track.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        result["duration_ms"] = int(duration_ms)
    result["attempts"] = attempts
    return result


def recognize_file(
    input_path: Path,
    *,
    ffmpeg_override: Optional[str] = None,
    ffprobe_override: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
    host_override: Optional[str] = None,
    access_key_override: Optional[str] = None,
    secret_key_override: Optional[str] = None,
    provider: Optional[MusicRecognitionProvider] = None,
) -> Dict[str, Any]:
    if not input_path.is_file():
        return failure_json("file_not_found", "Input file does not exist.")

    if provider is None:
        try:
            host, access_key, secret_key = resolve_credentials(
                host_override, access_key_override, secret_key_override
            )
        except RecognitionFailure as error:
            return failure_json(error.error, error.message)
        provider = ACRCloudProvider(host, access_key, secret_key)

    try:
        ffprobe_path = resolve_binary("ffprobe", ffprobe_override)
    except RecognitionFailure as error:
        return failure_json(error.error, error.message)
    try:
        probe = probe_media(ffprobe_path, input_path)
    except RecognitionFailure as error:
        return failure_json(error.error, error.message)

    media_type = detect_media_type(input_path, probe)
    candidates = build_segment_candidates(float(probe["duration"]))

    attempts = 0
    errors: List[str] = []
    no_match_attempts = 0
    had_request_error = False

    with tempfile.TemporaryDirectory(prefix="aigc-music-recognition-") as temp_dir:
        temp_root = Path(temp_dir)
        last_candidate: Optional[Tuple[float, float, Path]] = None
        for index, (start, duration) in enumerate(candidates, 1):
            attempts += 1
            if media_type == "audio" and len(candidates) == 1:
                audio_path = input_path
            else:
                try:
                    ffmpeg_path = resolve_binary("ffmpeg", ffmpeg_override)
                except RecognitionFailure as error:
                    return failure_json(error.error, error.message, attempts)
                audio_path = temp_root / f"segment-{index}.wav"
                try:
                    extract_audio_segment(ffmpeg_path, input_path, audio_path, start, duration)
                except RecognitionFailure as error:
                    return failure_json(error.error, error.message, attempts, errors=errors)
            last_candidate = (start, duration, audio_path)
            try:
                raw = provider.recognize(audio_path, timeout)
                normalized = normalize_acrcloud_result(raw, start, duration, attempts)
            except RecognitionFailure as error:
                if error.error == "acrcloud_request_error":
                    errors.append(f"attempt {attempts}: {error.message}")
                    had_request_error = True
                    if verbose:
                        print(f"music recognition attempt {attempts} failed: {error.message}", file=sys.stderr)
                    continue
                return failure_json(error.error, error.message, attempts, errors=errors)
            if normalized:
                return normalized
            errors.append(f"attempt {attempts}: no match")
            no_match_attempts += 1
            if verbose:
                print(f"music recognition attempt {attempts}: no match", file=sys.stderr)

        # A deliberately different final attempt can recover quiet or heavily
        # compressed clips without endlessly repeating the same request.
        if last_candidate:
            start, duration, _ = last_candidate
            attempts += 1
            try:
                ffmpeg_path = resolve_binary("ffmpeg", ffmpeg_override)
                normalized_path = temp_root / "segment-normalized.wav"
                extract_audio_segment(
                    ffmpeg_path,
                    input_path,
                    normalized_path,
                    start,
                    duration,
                    normalize=True,
                )
                raw = provider.recognize(normalized_path, timeout)
                normalized = normalize_acrcloud_result(raw, start, duration, attempts)
            except RecognitionFailure as error:
                if error.error == "acrcloud_request_error":
                    errors.append(f"attempt {attempts}: {error.message}")
                    had_request_error = True
                else:
                    return failure_json(error.error, error.message, attempts, errors=errors)
            else:
                if normalized:
                    return normalized
                errors.append(f"attempt {attempts}: no match after volume normalization")
                no_match_attempts += 1

    if had_request_error:
        last_error = errors[-1] if errors else "ACRCloud recognition failed."
        return failure_json(
            "acrcloud_request_error",
            last_error,
            attempts,
            errors=errors,
        )
    return failure_json("no_match", "No music match was found.", attempts, errors=errors)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="local video or audio file")
    parser.add_argument("--pretty", action="store_true", help="indent JSON for human review")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-request timeout in seconds")
    parser.add_argument("--ffmpeg", help="path to the local ffmpeg executable")
    parser.add_argument("--ffprobe", help="path to the local ffprobe executable")
    parser.add_argument("--verbose", action="store_true", help="write retry diagnostics to stderr")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = recognize_file(
            args.input.expanduser(),
            ffmpeg_override=args.ffmpeg,
            ffprobe_override=args.ffprobe,
            timeout=max(1.0, args.timeout),
            verbose=args.verbose,
        )
    except RecognitionFailure as error:
        result = failure_json(error.error, error.message, error.attempts, **error.extra)
    except Exception as error:  # Keep stdout JSON even for unexpected local failures.
        if args.verbose:
            print(f"unexpected recognition error: {error}", file=sys.stderr)
        result = failure_json("acrcloud_request_error", str(error)[:500])
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
