#!/usr/bin/env python3
"""Download social videos, prepare visual review, and import through AIGC Shelf."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, ProxyHandler


HOME = Path.home()
DEFAULT_ROOT = HOME / "Downloads" / "AIGC-Inspiration"
CLIPDOCK_PROFILE = HOME / "Library/Application Support/ClipDock/InstagramChromeProfile"
FORMAT = "bv*+ba/b"
SORT = "res,fps,br"
MAX_BYTES = 2 * 1024**3


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def safe_error(error):
    text = re.sub(r"https?://[^\s\"<>]+", "<url>", str(error))
    text = re.sub(r"(?i)(cookie|authorization|token)[=:][^\n]+", r"\1=<redacted>", text)
    return text[-1800:]


def log(message):
    print(message, file=sys.stderr, flush=True)


def tool(name):
    override = os.environ.get(name.upper().replace("-", "_") + "_PATH")
    candidates = [
        override, shutil.which(name),
        str(HOME / ".codex" / "video-inspiration-venv" / "bin" / name),
        str(HOME / ".codex" / "bin" / name),
        f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(f"Missing {name}; see the skill dependency instructions")


def run(command, timeout=300):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(command[0]).name} timed out after {timeout}s") from None
    if result.returncode:
        raise RuntimeError(safe_error(result.stderr or result.stdout or f"exit {result.returncode}"))
    return result.stdout


def identify(raw, resolve_short=True):
    url = urlparse(raw.strip())
    host = (url.hostname or "").lower()
    if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
        raise ValueError("Use an HTTPS Instagram or TikTok video link")
    if host in ("instagram.com", "www.instagram.com", "m.instagram.com"):
        match = re.fullmatch(r"/(reel|reels|p)/([A-Za-z0-9_-]+)/?", url.path)
        if match:
            kind = "p" if match[1] == "p" else "reel"
            return "instagram", match[2], f"https://www.instagram.com/{kind}/{match[2]}/"
    if host in ("tiktok.com", "www.tiktok.com", "m.tiktok.com"):
        match = re.fullmatch(r"/(@[\w.-]+)/video/(\d+)/?", url.path)
        if match:
            return "tiktok", match[2], f"https://www.tiktok.com/{match[1]}/video/{match[2]}"
    short = host in ("vm.tiktok.com", "vt.tiktok.com") or (
        host in ("www.tiktok.com", "tiktok.com") and url.path.startswith("/t/")
    )
    if short and resolve_short:
        request = Request(raw, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as response:
            return identify(response.url, resolve_short=False)
    raise ValueError("Unsupported link; supply a single Instagram Reel/post or TikTok video")


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This video is already being processed") from None
        yield


def probe(path):
    data = json.loads(run([
        tool("ffprobe"), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], 60))
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("Downloaded file has no video stream")
    video = videos[0]
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0 or not video.get("width") or not video.get("height"):
        raise ValueError("Video has invalid dimensions or duration")
    if not 0 < Path(path).stat().st_size <= MAX_BYTES:
        raise ValueError("Video is empty or exceeds the 2 GiB upload limit")
    return {
        "width": video["width"], "height": video["height"], "duration": duration,
        "videoCodec": video.get("codec_name"),
        "audioCodecs": [s.get("codec_name") for s in data["streams"] if s.get("codec_type") == "audio"],
        "bytes": Path(path).stat().st_size,
    }


def preview(path, media, directory):
    frames = []
    for index, fraction in enumerate((0.02, 0.25, 0.55, 0.85)):
        frame = directory / f"preview-{index + 1}.jpg"
        run([
            tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(media["duration"] * fraction), "-i", str(path),
            "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "3", str(frame),
        ], 60)
        if not frame.is_file():
            raise ValueError("Could not decode video preview")
        frames.append(str(frame))
    sheet = directory / "preview.jpg"
    inputs = [arg for frame in frames for arg in ("-i", frame)]
    run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", "hstack=inputs=4", "-frames:v", "1", str(sheet)], 60)
    return str(sheet)


def download_ytdlp(url, directory, args):
    command = [
        tool("yt-dlp"), "--ignore-config", "--no-playlist", "--no-progress",
        "--no-warnings", "--socket-timeout", "25", "--retries", "1",
        "--fragment-retries", "1", "--abort-on-unavailable-fragments",
        "--max-filesize", str(MAX_BYTES), "-f", FORMAT, "-S", SORT, "--format-sort-force",
        "--ffmpeg-location", str(Path(tool("ffmpeg")).parent),
        "--merge-output-format", "mp4/mkv", "--remux-video", "mp4",
        "-o", str(directory / "media.%(ext)s"),
        "--print", "after_move:%()j",
    ]
    if args.proxy:
        command += ["--proxy", args.proxy]
    if args.cookies_from_browser:
        command += ["--cookies-from-browser", args.cookies_from_browser]
    command += ["--", url]
    output = run(command, args.timeout)
    entries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    if len(entries) != 1 or entries[0].get("_type") == "playlist":
        raise ValueError("Expected exactly one video, not a playlist or carousel")
    info = entries[0]
    path = Path(info["filepath"]).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise ValueError("Downloader did not produce a file in the expected directory")
    # Retain descriptive metadata only, never signed CDN URLs, cookies or request headers.
    return path, {
        "engine": "yt-dlp", "remoteId": str(info.get("id", "")),
        "originalTitle": str(info.get("title", ""))[:1000],
        "creator": str(info.get("uploader", ""))[:200],
        "formatId": info.get("format_id"), "expectedDuration": info.get("duration"),
        "expectedAudio": info.get("acodec") not in (None, "none"),
        "qualityPolicy": "highest available resolution, then fps and bitrate",
    }


def clipdock_request(origin, route, body=None, timeout=15):
    parsed = urlparse(origin)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("ClipDock must use a loopback HTTP origin")
    request = Request(
        origin.rstrip("/") + route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        return build_opener(ProxyHandler({})).open(request, timeout=timeout)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(8192)).get("error", "")
        except (ValueError, AttributeError):
            detail = ""
        raise RuntimeError(f"ClipDock HTTP {error.code}: {safe_error(detail)}") from None


def clipdock_json(origin, route, body=None):
    with clipdock_request(origin, route, body) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "ClipDock request failed"))
    return result


def download_clipdock(platform, url, directory, args):
    session = clipdock_json(args.clipdock, f"/api/{platform}/open", {"url": url})
    log(f"{platform}: ClipDock opened the video; waiting for playback (login if requested).")
    deadline = time.monotonic() + args.capture_timeout
    # Instagram sets ready before it finishes waiting for the separate audio track.
    while not (session.get("ready") and session.get("status") == "captured"):
        if session.get("status") == "failed":
            raise RuntimeError(session.get("message", "Capture failed"))
        if time.monotonic() >= deadline:
            raise RuntimeError("ClipDock capture timed out; complete login/playback then retry")
        time.sleep(2)
        session = clipdock_json(args.clipdock, f"/api/{platform}/capture?{urlencode({'id': session['id']})}")
    path = directory / "media.mp4"
    part = directory / "media.capture.part"
    try:
        with clipdock_request(
            args.clipdock, f"/api/{platform}/download?{urlencode({'id': session['id']})}",
            timeout=args.timeout,
        ) as response, part.open("wb") as stream:
            expected = int(response.headers.get("Content-Length") or 0)
            total = 0
            started = time.monotonic()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES or time.monotonic() - started > args.timeout:
                    raise ValueError("Capture exceeds size or time limit")
                stream.write(chunk)
        if not total or (expected and total != expected):
            raise ValueError("Incomplete ClipDock download")
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
    return path, {
        "engine": "clipdock", "originalTitle": "", "expectedDuration": None,
        "expectedAudio": bool(session.get("media", {}).get("hasAudio")),
        "qualityPolicy": "captured playback stream; maximum platform quality is unverified",
        "transcoded": platform == "instagram",
    }


def prepare_one(raw, args):
    platform, video_id, canonical = identify(raw)
    directory = args.root / platform / video_id
    state_file = directory / "state.json"
    with locked(directory):
        state = read_json(state_file) if state_file.exists() else {}
        cached = Path(state.get("path", directory / "missing"))
        if (not getattr(args, "refresh_download", False)
                and cached.is_file() and state.get("sha256") == digest(cached)):
            log(f"{platform}/{video_id}: reusing verified download")
        else:
            state = {}
            errors = []
            engines = [(args.engine, args)]
            if args.engine == "auto":
                engines = [("yt-dlp", args)]
                if (not args.cookies_from_browser and not args.no_clipdock_cookies
                        and CLIPDOCK_PROFILE.is_dir()):
                    authenticated = argparse.Namespace(**vars(args))
                    authenticated.cookies_from_browser = f"chrome:{CLIPDOCK_PROFILE}"
                    engines.append(("yt-dlp (ClipDock session)", authenticated))
                engines.append(("clipdock", args))
            for engine, engine_args in engines:
                try:
                    log(f"{platform}/{video_id}: downloading with {engine}")
                    # A fresh attempt prevents stale partial files from being accepted as complete.
                    with tempfile.TemporaryDirectory(prefix="attempt-", dir=directory) as temp:
                        attempt = Path(temp)
                        if engine.startswith("yt-dlp"):
                            path, metadata = download_ytdlp(canonical, attempt, engine_args)
                        else:
                            path, metadata = download_clipdock(platform, canonical, attempt, args)
                        media = probe(path)
                        expected = metadata.get("expectedDuration")
                        if expected and abs(media["duration"] - float(expected)) > max(2, float(expected) * 0.05):
                            raise ValueError("Downloaded duration does not match the source")
                        if metadata.get("expectedAudio") and not media["audioCodecs"]:
                            raise ValueError("Source has audio but downloaded file does not")
                        if platform == "tiktok" and metadata.get("remoteId", video_id) != video_id:
                            raise ValueError("Downloaded TikTok ID does not match the requested video")
                        sheet = preview(path, media, attempt)
                        final_path = directory / path.name
                        path.replace(final_path)
                        for frame in attempt.glob("preview*.jpg"):
                            frame.replace(directory / frame.name)
                        state = {
                            "platform": platform, "videoId": video_id, "canonicalUrl": canonical,
                            "sourceUrl": raw, "path": str(final_path),
                            "sha256": digest(final_path), "media": media,
                            "preview": str(directory / Path(sheet).name), **metadata,
                        }
                    write_json(state_file, state)
                    break
                except Exception as error:
                    message = f"{engine}: {safe_error(error)}"
                    errors.append(message)
                    log(message)
            else:
                raise RuntimeError("; ".join(errors))
        return {
            **state, "stateFile": str(state_file), "folder": "灵感收集",
            "name": state.get("asset", {}).get("name", ""), "tags": [],
            "warning": "" if state["media"]["audioCodecs"] else "No audio track; verify the original is silent",
        }


def prepare(args):
    items, errors, seen = [], [], set()
    for raw in args.urls:
        try:
            platform, video_id, _ = identify(raw)
            key = (platform, video_id)
            if key in seen:
                continue
            seen.add(key)
            items.append(prepare_one(raw, args))
        except Exception as error:
            errors.append({"sourceUrl": raw, "error": safe_error(error)})
        write_json(args.report, {"items": items, "errors": errors})
    print(json.dumps({"report": str(args.report), "items": items, "errors": errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


def load_uploader():
    candidates = [
        os.environ.get("AIGC_UPLOAD_SCRIPT"),
        str(Path(__file__).resolve().parents[2] / "aigc-asset-upload/scripts/upload_assets.py"),
        str(HOME / ".codex/skills/aigc-asset-upload/scripts/upload_assets.py"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            spec = importlib.util.spec_from_file_location("aigc_upload", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("Install aigc-asset-upload or set AIGC_UPLOAD_SCRIPT")


def validate_item(item):
    state_file = Path(item["stateFile"]).expanduser().resolve()
    state = read_json(state_file)
    path = Path(item["path"]).expanduser().resolve()
    if path != Path(state["path"]).resolve() or path.parent != state_file.parent:
        raise ValueError("Manifest path differs from the prepared download")
    if digest(path) != state["sha256"] or item.get("sha256") != state["sha256"]:
        raise ValueError("Video changed after preparation; prepare it again")
    name = item.get("name", "").strip()
    tags = item.get("tags", [])
    if not name or len(name) > 32 or not isinstance(tags, list) or not 1 <= len(tags) <= 5:
        raise ValueError("Review the preview and provide a title (1-32 characters) and 1-5 tags")
    if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("Tags must be nonempty strings")
    if item.get("folder", "灵感收集") != "灵感收集":
        raise ValueError("This workflow imports into 灵感收集; use aigc-asset-upload for other libraries")
    metadata = {
        "name": name, "tags": tags, "folder": "灵感收集",
        "source": "Instagram" if state["platform"] == "instagram" else "TikTok",
        "sourceUrl": state["sourceUrl"],
    }
    return state_file, state, path, metadata


def upload_one(item, client):
    with locked(Path(item["stateFile"]).parent):
        state_file, state, path, metadata = validate_item(item)
        # Always ask the current account: local receipts can be stale or belong to another account.
        existing = client.find_existing([state["sha256"]])
        asset = existing.get(state["sha256"])
        status = "existing" if asset else "uploaded"
        if not asset:
            # Do not retry this POST blindly. A later invocation checks hashes first.
            asset = client.upload(path, metadata)
        if not isinstance(asset, dict) or not asset.get("id"):
            raise RuntimeError("Server response did not contain an asset ID; rerun to reconcile by hash")
        receipt = {key: asset[key] for key in ("id", "name", "folder", "sourceUrl") if key in asset}
        state["asset"] = receipt
        write_json(state_file, state)
        return {"status": status, "path": str(path), "asset": receipt, "media": state["media"]}


def upload(args):
    report = read_json(args.manifest)
    items = report.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Manifest has no prepared videos")
    # Validate the complete plan before creating any remote assets.
    for item in items:
        validate_item(item)
    uploader = load_uploader()
    client = uploader.Client(args.api or uploader.DEFAULT_API, args.cookie_file or uploader.DEFAULT_COOKIE_FILE)
    status, _ = client.request("GET", "/api/auth/me")
    if status != 200:
        raise RuntimeError("AIGC Shelf session is unavailable; sign in using aigc-asset-upload, then retry")
    results = []
    for item in items:
        try:
            results.append(upload_one(item, client))
        except Exception as error:
            results.append({"status": "failed", "path": item.get("path"), "error": safe_error(error)})
        write_json(args.results, {"results": results})
    print(json.dumps({"results": results, "resultsFile": str(args.results)}, ensure_ascii=False, indent=2))
    return int(any(result["status"] == "failed" for result in results))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    download = sub.add_parser("prepare", help="Download videos and create a manifest for visual naming")
    download.add_argument("urls", nargs="+")
    download.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    download.add_argument("--report", type=Path, required=True)
    download.add_argument("--engine", choices=("auto", "yt-dlp", "clipdock"), default="auto")
    download.add_argument("--proxy", default="")
    download.add_argument("--cookies-from-browser", default="")
    download.add_argument("--no-clipdock-cookies", action="store_true",
                          help="Do not retry yt-dlp with the existing ClipDock browser profile")
    download.add_argument("--refresh-download", action="store_true",
                          help="Replace the cached file only after a new download passes validation")
    download.add_argument("--clipdock", default="http://127.0.0.1:3457")
    download.add_argument("--timeout", type=int, default=300)
    download.add_argument("--capture-timeout", type=int, default=150)
    send = sub.add_parser("upload", help="Upload a reviewed manifest, checking server hashes first")
    send.add_argument("--manifest", type=Path, required=True)
    send.add_argument("--results", type=Path, required=True)
    send.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", ""))
    send.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", ""))
    args = parser.parse_args()
    for field in ("root", "report", "manifest", "results"):
        if hasattr(args, field):
            setattr(args, field, getattr(args, field).expanduser().resolve())
    return args


def main():
    args = parse_args()
    return prepare(args) if args.action == "prepare" else upload(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        log(safe_error(error) or "Cancelled")
        sys.exit(1)
