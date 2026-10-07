#!/usr/bin/env python3
"""Composite a user-provided PNG/GIF over a video with fixed geometry and optional position keyframes."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def probe(ffprobe: str, path: Path) -> dict:
    result = run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,sample_rate,channels",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def finite(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def edge_connected_near_white_alpha(rgba: np.ndarray) -> np.ndarray:
    """Make only near-white pixels connected to the image edge transparent."""
    rgb = rgba[..., :3]
    near_white = np.all(rgb >= 245, axis=2).astype(np.uint8) * 255
    flood = near_white.copy()
    flood_mask = np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), dtype=np.uint8)
    height, width = flood.shape
    for x in range(width):
        if flood[0, x] == 255:
            cv2.floodFill(flood, flood_mask, (x, 0), 128)
        if flood[height - 1, x] == 255:
            cv2.floodFill(flood, flood_mask, (x, height - 1), 128)
    for y in range(height):
        if flood[y, 0] == 255:
            cv2.floodFill(flood, flood_mask, (0, y), 128)
        if flood[y, width - 1] == 255:
            cv2.floodFill(flood, flood_mask, (width - 1, y), 128)
    result = rgba.copy()
    result[..., 3][flood == 128] = 0
    return result


def load_overlay(path: Path, remove_edge_white: bool) -> tuple[list[Image.Image], list[float]]:
    image = Image.open(path)
    frames: list[Image.Image] = []
    durations: list[float] = []
    count = getattr(image, "n_frames", 1)
    for index in range(count):
        image.seek(index)
        rgba = np.array(image.convert("RGBA"))
        if remove_edge_white:
            rgba = edge_connected_near_white_alpha(rgba)
        frames.append(Image.fromarray(rgba))
        duration_ms = image.info.get("duration", 100)
        try:
            duration = max(0.001, float(duration_ms) / 1000.0)
        except (TypeError, ValueError):
            duration = 0.1
        durations.append(duration)
    return frames, durations


def parse_keyframes(path: Path | None, x: float | None, y: float | None, start: float, end: float) -> list[tuple[float, float, float]]:
    if path is None:
        if x is None or y is None:
            raise ValueError("provide --x and --y when --keyframes is omitted")
        return [(start, finite(x, "x"), finite(y, "y")), (end, finite(x, "x"), finite(y, "y"))]
    data = json.loads(path.read_text())
    rows = data.get("keyframes")
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("keyframes JSON must contain at least two keyframes")
    parsed = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"keyframe {index} must be an object")
        parsed.append((finite(row.get("time"), f"keyframes[{index}].time"),
                       finite(row.get("x"), f"keyframes[{index}].x"),
                       finite(row.get("y"), f"keyframes[{index}].y")))
    if any(parsed[i][0] >= parsed[i + 1][0] for i in range(len(parsed) - 1)):
        raise ValueError("keyframe times must be strictly increasing")
    if parsed[0][0] > start or parsed[-1][0] < end:
        raise ValueError("keyframes must cover the complete overlay interval")
    return parsed


def interpolated_position(keyframes: list[tuple[float, float, float]], time: float) -> tuple[float, float]:
    times = np.array([row[0] for row in keyframes], dtype=np.float64)
    xs = np.array([row[1] for row in keyframes], dtype=np.float64)
    ys = np.array([row[2] for row in keyframes], dtype=np.float64)
    return float(np.interp(time, times, xs)), float(np.interp(time, times, ys))


def make_overlay_frames(
    directory: Path,
    video_fps: float,
    frame_count: int,
    video_size: tuple[int, int],
    gif_frames: list[Image.Image],
    gif_durations: list[float],
    start: float,
    end: float,
    width: int,
    height: int,
    keyframes: list[tuple[float, float, float]],
    smoothing_sigma: float,
) -> None:
    canvas_width, canvas_height = video_size
    frame_periods = np.cumsum([0.0] + gif_durations)
    gif_duration = frame_periods[-1]
    if gif_duration <= 0:
        raise ValueError("overlay animation has no positive duration")

    output_times = np.arange(frame_count, dtype=np.float64) / video_fps
    positions = np.array([interpolated_position(keyframes, float(t)) for t in output_times])
    if smoothing_sigma > 0 and len(positions) > 3:
        positions[:, 0] = cv2.GaussianBlur(positions[:, 0].reshape(-1, 1), (0, 0), smoothing_sigma).ravel()
        positions[:, 1] = cv2.GaussianBlur(positions[:, 1].reshape(-1, 1), (0, 0), smoothing_sigma).ravel()

    for frame_index, time in enumerate(output_times):
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        if start <= time <= end:
            phase = (time - start) % gif_duration
            gif_index = int(np.searchsorted(frame_periods, phase, side="right") - 1)
            gif_index = max(0, min(len(gif_frames) - 1, gif_index))
            sprite = gif_frames[gif_index].resize((width, height), Image.Resampling.NEAREST)
            x = round(positions[frame_index, 0] - width / 2)
            y = round(positions[frame_index, 1] - height / 2)
            canvas.alpha_composite(sprite, (x, y))
        canvas.save(directory / f"{frame_index + 1:06d}.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int)
    parser.add_argument("--x", type=float)
    parser.add_argument("--y", type=float)
    parser.add_argument("--keyframes", type=Path)
    parser.add_argument("--remove-edge-white", action="store_true")
    parser.add_argument("--smoothing-sigma", type=float, default=1.0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path, label in ((args.input, "input"), (args.overlay, "overlay")):
        if not path.is_file():
            raise ValueError(f"{label} file not found: {path}")
    if args.width <= 0 or args.width > 10000:
        raise ValueError("width must be between 1 and 10000")
    if args.height is not None and (args.height <= 0 or args.height > 10000):
        raise ValueError("height must be between 1 and 10000")
    if args.smoothing_sigma < 0 or not math.isfinite(args.smoothing_sigma):
        raise ValueError("smoothing-sigma must be finite and non-negative")

    media = probe(args.ffprobe, args.input)
    video_stream = next((stream for stream in media.get("streams", []) if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError("input has no video stream")
    video_width = int(video_stream["width"])
    video_height = int(video_stream["height"])
    duration = finite(media.get("format", {}).get("duration"), "video duration")
    fps_text = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    numerator, denominator = (fps_text or "0/1").split("/", 1)
    video_fps = float(numerator) / float(denominator)
    if not math.isfinite(video_fps) or video_fps <= 0:
        raise ValueError("could not determine a positive video frame rate")
    frame_count = max(1, round(duration * video_fps))

    start = finite(args.start, "start")
    end = duration if args.end is None else finite(args.end, "end")
    if start < 0 or end <= start or end > duration + 1.0 / video_fps:
        raise ValueError("overlay interval must be within the video duration")
    end = min(duration, end)
    if args.height is None:
        overlay_image = Image.open(args.overlay)
        aspect = overlay_image.width / overlay_image.height
        height = max(1, round(args.width / aspect))
    else:
        height = args.height

    keyframes = parse_keyframes(args.keyframes, args.x, args.y, start, end)
    gif_frames, gif_durations = load_overlay(args.overlay, args.remove_edge_white)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="video-overlay-censor-") as temp_name:
        frame_dir = Path(temp_name) / "overlay"
        frame_dir.mkdir()
        make_overlay_frames(
            frame_dir, video_fps, frame_count, (video_width, video_height), gif_frames, gif_durations,
            start, end, args.width, height, keyframes, args.smoothing_sigma,
        )
        command = [
            args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(args.input), "-framerate", f"{video_fps:.12g}",
            "-i", str(frame_dir / "%06d.png"),
            "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]",
            "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
            str(args.output),
        ]
        try:
            run(command)
        except subprocess.CalledProcessError as exc:
            # Audio stream-copy may be incompatible with a particular output container.
            fallback = command.copy()
            audio_index = fallback.index("-c:a")
            fallback[audio_index + 1] = "aac"
            run(fallback)

    print(json.dumps({
        "output": str(args.output),
        "duration": duration,
        "videoFPS": video_fps,
        "videoSize": [video_width, video_height],
        "overlaySize": [args.width, height],
        "start": start,
        "end": end,
        "positionMode": "keyframes" if args.keyframes else "fixed",
        "removedEdgeWhite": bool(args.remove_edge_white),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
