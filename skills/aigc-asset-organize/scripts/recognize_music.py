#!/usr/bin/env python3
"""Recognize music in a local video or audio file with ShazamIO.

The command deliberately keeps stdout machine-readable: exactly one JSON
object is emitted, while optional diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}
DEFAULT_TIMEOUT = 45.0


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
    raise RecognitionFailure(
        f"{name}_not_found",
        "FFmpeg or FFprobe is not installed." if name in {"ffmpeg", "ffprobe"} else f"{name} is not installed.",
    )


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
        raise RecognitionFailure("media_processing_error", "FFmpeg/FFprobe timed out while processing the input file.") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "media processing failed").strip()
        raise RecognitionFailure("media_processing_error", detail[-500:]) from error


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
    """Extract a small, Shazam-friendly mono PCM WAV segment."""
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
        raise RecognitionFailure("audio_extract_error", "FFmpeg did not produce a usable audio segment.")
    return output_path


def build_segment_candidates(duration: float) -> List[Tuple[float, float]]:
    """Choose a full short clip or three non-identical samples from a long file."""
    if duration <= 15.0:
        return [(0.0, max(0.1, duration))]
    segment_duration = min(12.0, max(8.0, duration - 0.01))
    latest_start = max(0.0, duration - segment_duration)
    starts = [min(latest_start, max(0.0, duration * fraction)) for fraction in (0.10, 0.45, 0.72)]
    # Very short files cannot fit three 12-second windows at those fractions;
    # use three distinct offsets instead of silently reducing retry coverage.
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


def _track_url(track: Dict[str, Any]) -> Optional[str]:
    if _text(track.get("url")):
        return _text(track.get("url"))
    hub = track.get("hub") or {}
    if not isinstance(hub, dict):
        return None
    for action in hub.get("actions") or []:
        if isinstance(action, dict) and _text(action.get("uri")) and action.get("type") in {"uri", "applemusic"}:
            return _text(action.get("uri"))
    return None


def normalize_shazam_result(raw: Dict[str, Any], start: float, duration: float, attempts: int) -> Optional[Dict[str, Any]]:
    """Keep only stable, useful fields from ShazamIO's response."""
    if not isinstance(raw, dict):
        return None
    track = raw.get("track") or {}
    if not isinstance(track, dict):
        track = {}
    title = _text(track.get("title"))
    artist = _text(track.get("subtitle") or track.get("artist"))
    matches = raw.get("matches") or []
    if not title and not matches:
        return None
    result: Dict[str, Any] = {
        "success": True,
        "title": title or "",
        "artist": artist or "",
        "shazam_id": _text(track.get("key") or track.get("id")) or "",
        "shazam_url": _track_url(track) or "",
        "matches": len(matches) if isinstance(matches, list) else 1,
        "source": "shazamio",
        "segment_start": round(start, 3),
        "segment_duration": round(duration, 3),
    }
    images = track.get("images") or {}
    if not isinstance(images, dict):
        images = {}
    cover_url = _text(images.get("coverart") or images.get("background"))
    if cover_url:
        result["cover_url"] = cover_url
    album = _text(track.get("album"))
    if album:
        result["album"] = album
    genre = _text(track.get("genres", {}).get("primary")) if isinstance(track.get("genres"), dict) else None
    if genre:
        result["genre"] = genre
    result["attempts"] = attempts
    return result


class MusicRecognitionProvider:
    async def recognize(self, audio_path: Path, timeout: float) -> Dict[str, Any]:
        raise NotImplementedError


class ShazamProvider(MusicRecognitionProvider):
    def __init__(self) -> None:
        try:
            from shazamio import Shazam
        except ImportError as error:
            raise RecognitionFailure(
                "shazamio_not_installed",
                "ShazamIO is not installed. Run: python3 -m pip install -r requirements.txt",
            ) from error
        self._client = Shazam()

    async def recognize(self, audio_path: Path, timeout: float) -> Dict[str, Any]:
        try:
            return await asyncio.wait_for(self._client.recognize(str(audio_path)), timeout=timeout)
        except asyncio.TimeoutError as error:
            raise RecognitionFailure("recognition_error", "Shazam recognition timed out.") from error
        except RecognitionFailure:
            raise
        except Exception as error:
            message = str(error).strip() or error.__class__.__name__
            raise RecognitionFailure("recognition_error", message[-500:]) from error


async def recognize_with_shazam(
    audio_path: Path,
    timeout: float = DEFAULT_TIMEOUT,
    provider: Optional[MusicRecognitionProvider] = None,
) -> Dict[str, Any]:
    """Call the current asynchronous ShazamIO API."""
    provider = provider or ShazamProvider()
    return await provider.recognize(audio_path, timeout)


def recognize_file(
    input_path: Path,
    ffmpeg_override: Optional[str] = None,
    ffprobe_override: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> Dict[str, Any]:
    if not input_path.is_file():
        return failure_json("file_not_found", "Input file does not exist.")

    ffprobe_path = resolve_binary("ffprobe", ffprobe_override)
    probe = probe_media(ffprobe_path, input_path)
    media_type = detect_media_type(input_path, probe)
    candidates = build_segment_candidates(float(probe["duration"]))

    # Import once before downloading/extracting so missing dependencies fail clearly.
    provider = ShazamProvider()
    attempts = 0
    errors: List[str] = []
    no_match_attempts = 0
    had_recognition_error = False
    with tempfile.TemporaryDirectory(prefix="aigc-music-recognition-") as temp_dir:
        temp_root = Path(temp_dir)
        last_candidate: Optional[Tuple[float, float, Path]] = None
        for index, (start, duration) in enumerate(candidates, 1):
            attempts += 1
            if media_type == "audio" and len(candidates) == 1:
                audio_path = input_path
            else:
                ffmpeg_path = resolve_binary("ffmpeg", ffmpeg_override)
                audio_path = temp_root / f"segment-{index}.wav"
                extract_audio_segment(ffmpeg_path, input_path, audio_path, start, duration)
            last_candidate = (start, duration, audio_path)
            try:
                raw = asyncio.run(recognize_with_shazam(audio_path, timeout, provider))
                normalized = normalize_shazam_result(raw, start, duration, attempts)
                if normalized:
                    return normalized
                errors.append(f"attempt {attempts}: no match")
                no_match_attempts += 1
            except RecognitionFailure as error:
                if error.error == "recognition_error":
                    errors.append(f"attempt {attempts}: {error.message}")
                    had_recognition_error = True
                else:
                    raise RecognitionFailure(error.error, error.message, attempts, errors=errors) from error
            if verbose:
                print(f"music recognition attempt {attempts} failed", file=sys.stderr)

        # A deliberately different final attempt can recover quiet or heavily
        # compressed clips without endlessly repeating the same request.
        if last_candidate:
            start, duration, _ = last_candidate
            attempts += 1
            ffmpeg_path = resolve_binary("ffmpeg", ffmpeg_override)
            normalized_path = temp_root / "segment-normalized.wav"
            extract_audio_segment(ffmpeg_path, input_path, normalized_path, start, duration, normalize=True)
            try:
                raw = asyncio.run(recognize_with_shazam(normalized_path, timeout, provider))
                normalized = normalize_shazam_result(raw, start, duration, attempts)
                if normalized:
                    return normalized
                errors.append(f"attempt {attempts}: no match after volume normalization")
                no_match_attempts += 1
            except RecognitionFailure as error:
                if error.error == "recognition_error":
                    errors.append(f"attempt {attempts}: {error.message}")
                    had_recognition_error = True
                else:
                    raise RecognitionFailure(error.error, error.message, attempts, errors=errors) from error

    if had_recognition_error and no_match_attempts == 0:
        return failure_json("recognition_error", "Shazam recognition failed.", attempts, errors=errors)
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
        result = failure_json("recognition_error", str(error)[:500])
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
