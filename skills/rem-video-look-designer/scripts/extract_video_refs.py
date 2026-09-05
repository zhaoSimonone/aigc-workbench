#!/usr/bin/env python3
"""Extract metadata and representative opening-segment frames from a video."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_time(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        parts = value.split(":")
        if len(parts) not in (2, 3):
            raise argparse.ArgumentTypeError(
                "time must be seconds or MM:SS[.sss] / HH:MM:SS[.sss]"
            )
        try:
            nums = [float(part) for part in parts]
        except ValueError as exc:
            raise argparse.ArgumentTypeError("invalid time value") from exc
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        return nums[0] * 3600 + nums[1] * 60 + nums[2]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def parse_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    return float(value)


def probe(video: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(video),
        ]
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video_stream is None:
        raise RuntimeError("input has no video stream")
    duration_candidates = [
        payload.get("format", {}).get("duration"),
        video_stream.get("duration"),
    ]
    duration = next((float(v) for v in duration_candidates if v not in (None, "N/A")), None)
    if duration is None or duration <= 0:
        raise RuntimeError("could not determine video duration")
    return {
        "duration_seconds": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec": video_stream.get("codec_name"),
        "pixel_format": video_stream.get("pix_fmt"),
        "fps": parse_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


def make_contact_sheet(frames: list[Path], output: Path) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    images = [Image.open(path).convert("RGB") for path in frames]
    if not images:
        return False
    thumb_width = 360
    prepared = []
    for path, source in zip(frames, images):
        ratio = thumb_width / source.width
        thumb = source.resize((thumb_width, max(1, int(source.height * ratio))))
        prepared.append((path, thumb))
    label_height = 34
    cell_height = max(image.height for _, image in prepared) + label_height
    columns = min(3, len(prepared))
    rows = (len(prepared) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * thumb_width, rows * cell_height), (20, 20, 24))
    draw = ImageDraw.Draw(canvas)
    for index, (path, thumb) in enumerate(prepared):
        x = (index % columns) * thumb_width
        y = (index // columns) * cell_height
        canvas.paste(thumb, (x, y))
        draw.text((x + 8, y + thumb.height + 8), path.stem, fill=(240, 240, 244))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    for image in images:
        image.close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    boundary = parser.add_mutually_exclusive_group()
    boundary.add_argument(
        "--front-end",
        type=parse_time,
        help="opening segment end in seconds or timecode; defaults to half the video",
    )
    boundary.add_argument(
        "--full-video",
        action="store_true",
        help="sample across the full video to locate the transformation boundary",
    )
    parser.add_argument(
        "--fractions",
        default="0.06,0.24,0.46,0.70,0.92",
        help="comma-separated relative positions within the opening segment",
    )
    args = parser.parse_args()

    if not args.video.is_file():
        parser.error(f"video not found: {args.video}")
    for executable in ("ffprobe", "ffmpeg"):
        if shutil.which(executable) is None:
            parser.error(f"required executable not found: {executable}")

    try:
        fractions = [float(value) for value in args.fractions.split(",") if value.strip()]
    except ValueError:
        parser.error("--fractions must contain numbers")
    if not fractions or any(value < 0 or value > 1 for value in fractions):
        parser.error("--fractions values must be between 0 and 1")

    metadata = probe(args.video.resolve())
    duration = metadata["duration_seconds"]
    if args.full_video:
        front_end = duration
        boundary_source = "full-video-scan"
    elif args.front_end is not None:
        front_end = args.front_end
        boundary_source = "user"
    else:
        front_end = duration * 0.5
        boundary_source = "provisional-half"
    if front_end <= 0 or front_end > duration:
        parser.error(f"--front-end must be greater than 0 and at most {duration:.3f}")

    frames_dir = args.out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    for index, fraction in enumerate(fractions, start=1):
        timestamp = min(front_end * fraction, max(0.0, duration - 0.001))
        name = f"frame-{index:02d}-{timestamp:08.3f}s.png"
        output = frames_dir / name
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{timestamp:.6f}",
                "-i",
                str(args.video.resolve()),
                "-frames:v",
                "1",
                "-y",
                str(output),
            ]
        )
        extracted.append({"timestamp_seconds": round(timestamp, 6), "path": str(output.resolve())})

    contact_sheet = args.out_dir / "contact-sheet.png"
    made_sheet = make_contact_sheet([Path(item["path"]) for item in extracted], contact_sheet)
    manifest = {
        "source_video": str(args.video.resolve()),
        **metadata,
        "front_segment_end_seconds": round(front_end, 6),
        "front_segment_source": boundary_source,
        "frames": extracted,
        "contact_sheet": str(contact_sheet.resolve()) if made_sheet else None,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
