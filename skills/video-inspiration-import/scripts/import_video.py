#!/usr/bin/env python3
"""Download social videos, prepare visual review, and import through AIGC Shelf."""

import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_session


HOME = Path.home()
DEFAULT_ROOT = HOME / "Downloads" / "AIGC-Inspiration"
FORMAT = "bv*+ba/b"
SORT = "res,fps,br"
MAX_BYTES = 2 * 1024**3
DELIVERY_VERSION = 1
CHANNELS_AGENT_DEFAULT = "http://127.0.0.1:2022"
# The shelf keeps the platform name as the source label, so every platform needs one.
SOURCE_LABELS = {"instagram": "Instagram", "tiktok": "TikTok", "weixin": "视频号"}


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
    runtime = [Path(os.environ[key]).expanduser() for key in
               ("MINIMAX_DATA_DIR", "MINIMAX_HOME", "CODEX_HOME") if os.environ.get(key)]
    runtime += [HOME / ".minimax", HOME / ".codex"]
    state = Path(os.environ.get("AIGC_WORKBENCH_STATE_DIR") or
                 str(Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "aigc-workbench")).expanduser()
    directories = [state / "venv/bin", state / "bin"]
    data_dir = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "aigc-workbench"
    directories += [data_dir / "venv/bin", data_dir / "bin"]
    if os.environ.get("AIGC_WORKBENCH_BIN_DIR"):
        directories.insert(0, Path(os.environ["AIGC_WORKBENCH_BIN_DIR"]).expanduser())
    directories += [root / suffix for root in runtime for suffix in
                    ("tools", "tools/bin", "bin", "video-inspiration-venv/bin")]
    candidates = [
        override, shutil.which(name),
        *(str(directory / name) for directory in directories),
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
        raise ValueError("Use an HTTPS Instagram, TikTok or WeChat Channels video link")
    if host in ("instagram.com", "www.instagram.com", "m.instagram.com"):
        match = re.fullmatch(r"/(reel|reels|p)/([A-Za-z0-9_-]+)/?", url.path)
        if match:
            # /reel/ and /reels/ are recommendation feeds: the server may answer
            # with a different video than the one the shortcode names.  /p/ is
            # the permalink of that exact post, so every shortcode is fetched
            # through it; the original link is kept in sourceUrl.
            return "instagram", match[2], f"https://www.instagram.com/p/{match[2]}/"
    if host in ("tiktok.com", "www.tiktok.com", "m.tiktok.com"):
        match = re.fullmatch(r"/(@[\w.-]+)/video/(\d+)/?", url.path)
        if match:
            return "tiktok", match[2], f"https://www.tiktok.com/{match[1]}/video/{match[2]}"
    if host in ("weixin.qq.com", "www.weixin.qq.com"):
        match = re.fullmatch(r"/sph/([A-Za-z0-9]+)/?", url.path)
        if match:
            return "weixin", match[1], f"https://weixin.qq.com/sph/{match[1]}"
    short = host in ("vm.tiktok.com", "vt.tiktok.com") or (
        host in ("www.tiktok.com", "tiktok.com") and url.path.startswith("/t/")
    )
    if short and resolve_short:
        request = Request(raw, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as response:
            return identify(response.url, resolve_short=False)
    raise ValueError("Unsupported link; supply a single Instagram Reel/post, TikTok video or WeChat Channels share link")


@contextmanager
def locked(directory, message="This video is already being processed"):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(message) from None
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
        "pixelFormat": video.get("pix_fmt"),
        "colorTransfer": video.get("color_transfer"),
        "frameRate": video.get("avg_frame_rate"),
        "videoBitRate": video.get("bit_rate"),
        "audioCodecs": [s.get("codec_name") for s in data["streams"] if s.get("codec_type") == "audio"],
        "audioStreams": [{key: s.get(key) for key in
                          ("index", "codec_name", "sample_rate", "channels", "channel_layout", "bit_rate")}
                         for s in data["streams"] if s.get("codec_type") == "audio"],
        "bytes": Path(path).stat().st_size,
    }


def compatible(media):
    return (media["videoCodec"] == "h264" and media.get("pixelFormat") == "yuv420p"
            and all(codec == "aac" for codec in media["audioCodecs"]))


def full_decode(path):
    run([
        tool("ffmpeg"), "-hide_banner", "-v", "error", "-xerror",
        "-err_detect", "explode", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-",
    ], 1800)


def verify_media(path):
    """Technical checks cannot establish perceptual quality or lip sync."""
    report = {"path": str(path), "errors": [], "warnings": [], "audio": []}
    try:
        report["media"] = media = probe(path)
        full_decode(path)
        report["decode"] = {"passed": True, "errorCount": 0}
        if not media["audioStreams"]:
            report["warnings"].append("No audio track; compare with the original")
        for stream in media["audioStreams"]:
            result = subprocess.run([
                tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
                "-map", f"0:{stream['index']}", "-vn",
                "-af", "volumedetect,silencedetect=noise=-40dB:d=0.5", "-f", "null", "-",
            ], capture_output=True, text=True, timeout=1800)
            if result.returncode:
                raise RuntimeError(safe_error(result.stderr))
            def volume(label):
                match = re.search(rf"{label}_volume:\s*([-\w.]+) dB", result.stderr)
                return match[1] if match else None
            starts = [float(value) for value in re.findall(r"silence_start:\s*([\d.]+)", result.stderr)]
            ends = [float(value) for value in re.findall(r"silence_end:\s*([\d.]+)", result.stderr)]
            segments = [{"start": start, "end": ends[i] if i < len(ends) else media["duration"]}
                        for i, start in enumerate(starts)]
            report["audio"].append({**stream, "meanVolumeDb": volume("mean"),
                                    "maxVolumeDb": volume("max"), "silenceSegments": segments,
                                    "silenceSegmentCount": len(segments)})
            if starts:
                report["warnings"].append(f"Audio stream {stream['index']} contains silence; listen to verify")
    except Exception as error:
        report.setdefault("decode", {"passed": False, "errorCount": None})
        report["errors"].append(safe_error(error))
    report["passed"] = not report["errors"]
    return report


def cleanup_previews(directory):
    # Only remove exact filenames owned by this workflow, never arbitrary frames.
    target = directory / ".identify"
    if target.is_symlink():
        raise ValueError("Refusing to clean a symlinked preview directory")
    for name in ["preview.jpg", *(f"preview-{i}.jpg" for i in range(1, 5))]:
        (target / name).unlink(missing_ok=True)
    if target.is_dir() and not any(target.iterdir()):
        target.rmdir()


def delivery_copy(path, media, output):
    """Keep the best downloaded stream; derive a separate SDR playback copy."""
    full_decode(path)
    if compatible(media):
        return path, media
    if media.get("colorTransfer") in ("smpte2084", "arib-std-b67"):
        raise ValueError("HDR source needs an explicit tone-mapping policy; refusing an SDR conversion")
    if media["width"] % 2 or media["height"] % 2:
        raise ValueError("Odd video dimensions need explicit handling for H.264 yuv420p")
    video_copy = media["videoCodec"] == "h264" and media.get("pixelFormat") == "yuv420p"
    command = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-n",
        "-xerror", "-err_detect", "explode", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a?",
    ]
    command += (["-c:v", "copy"] if video_copy else [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-fps_mode", "passthrough",
    ])
    command += (["-c:a", "copy"] if all(c == "aac" for c in media["audioCodecs"])
                else ["-c:a", "aac", "-b:a", "192k"])
    command += ["-movflags", "+faststart", str(output)]
    run(command, 1800)
    converted = probe(output)
    if (not compatible(converted)
            or (converted["width"], converted["height"]) != (media["width"], media["height"])
            or len(converted["audioCodecs"]) != len(media["audioCodecs"])
            or abs(converted["duration"] - media["duration"]) > 0.25):
        raise ValueError("Playback conversion changed dimensions, audio tracks or duration")
    full_decode(output)
    return output, converted


def publish_delivery(state, directory, staging):
    source = Path(state["path"])
    media = probe(source)
    output, delivered = delivery_copy(source, media, staging / "compatible.mp4")
    state = dict(state)
    if output != source:
        # A new name preserves both the original and any previously referenced file.
        checksum = digest(output)
        destination = directory / f"media-compatible-{checksum[:12]}.mp4"
        output.replace(destination)
        state.update(sourcePath=str(source), sourceSha256=digest(source), sourceMedia=media,
                     path=str(destination), sha256=checksum)
    state.update(media=delivered, deliveryVersion=DELIVERY_VERSION,
                 deliveryPolicy="H.264 yuv420p/AAC; original retained when conversion is needed",
                 fullDecodeVerified=True)
    return state


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


def build_ytdlp_command(url, directory, args):
    """Download only; do not expand comments, playlists or retry extraction."""
    command = [
        tool("yt-dlp"), "--ignore-config", "--no-playlist", "--no-progress",
        "--no-warnings", "--socket-timeout", "25", "--retries", "0",
        "--extractor-retries", "0", "--fragment-retries", "0",
        "--no-write-comments", "--concurrent-fragments", "1",
        "--abort-on-unavailable-fragments",
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
    # 限速选项：不传就不改变原有行为；用于把请求摊开，降低被判定为爬虫的概率。
    if getattr(args, "ytdlp_sleep_requests", 0):
        command += ["--sleep-requests", str(args.ytdlp_sleep_requests)]
    if getattr(args, "ytdlp_sleep_interval", 0):
        command += ["--sleep-interval", str(args.ytdlp_sleep_interval)]
    if getattr(args, "ytdlp_limit_rate", ""):
        command += ["--limit-rate", str(args.ytdlp_limit_rate)]
    command += ["--", url]
    return command


def download_ytdlp(url, directory, args):
    command = build_ytdlp_command(url, directory, args)
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
        "creatorId": str(info.get("uploader_id", ""))[:200],
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


def agent_request(origin, route, body=None, method="GET", timeout=30):
    """Call the WeChat Channels desktop agent over loopback HTTP."""
    parsed = urlparse(origin)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("The WeChat Channels agent must use a loopback HTTP origin")
    request = Request(
        origin.rstrip("/") + route, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(8192)).get("msg", "")
        except (ValueError, AttributeError):
            detail = ""
        raise RuntimeError(f"Channels agent HTTP {error.code}: {safe_error(detail)}") from None


def agent_wait(origin, route, deadline, ready, failed, description):
    """Poll an agent endpoint until it reports a terminal state."""
    while True:
        payload = agent_request(origin, route)
        state = payload.get("data") or {}
        if ready(state):
            return state
        if failed(state):
            raise RuntimeError(state.get("message") or state.get("error") or f"{description} failed")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{description} timed out")
        time.sleep(2)


def channels_page_ready(agent):
    """Report whether the agent holds a live WeChat Channels page."""
    payload = agent_request(agent, "/api/scraper/platform/status")
    states = (payload.get("data") or {}).get("statuses") or []
    page = next((state for state in states if state.get("key") == "wxchannels:page"), None)
    return bool(page and page.get("available")), page


def pick_best_spec(raw):
    """Choose the largest WeChat Channels variant; WeChat grades its own list by account level."""
    media = (((raw or {}).get("data") or {}).get("object") or {}).get("objectDesc", {}).get("media") or []
    specs = media[0].get("spec") if media else None
    best = None
    for spec in specs or []:
        area = int(spec.get("width") or 0) * int(spec.get("height") or 0)
        rank = (area, int(spec.get("videoBitrate") or 0))
        if best is None or rank > best[0]:
            best = (rank, spec.get("fileFormat") or "")
    return best[1] if best else ""


def download_weixin(url, directory, args):
    """Resolve a WeChat Channels share link through the local desktop agent."""
    agent = args.wx_agent
    ready, page = channels_page_ready(agent)
    if not ready:
        reason = (page or {}).get("reason") or "等待视频号页面连接"
        raise RuntimeError(
            f"视频号页面未连接（{reason}）：在微信里打开该视频号链接后重试")
    created = agent_request(agent, "/api/scraper/fetch", {"url": url, "force_refresh": True},
                            method="POST")
    job = (created.get("data") or {}).get("id")
    if not job:
        raise RuntimeError("Channels agent did not return a scrape job id")
    deadline = time.monotonic() + args.timeout
    state = agent_wait(agent, f"/api/scraper/job?{urlencode({'id': job})}", deadline,
                       lambda item: item.get("status") == "completed",
                       lambda item: item.get("status") == "failed", "视频号解析")
    raw = ((state.get("output") or {}).get("result")) or {}
    content = state.get("content") or {}
    if not raw:
        raise RuntimeError("Channels agent returned no feed payload for this share link")
    spec = pick_best_spec(raw)
    task = agent_request(agent, "/api/v1/download_task/create", {"objects": [{
        "platform": "wxchannels", "content": raw, "build_from_fetch": True,
        "filename": "media.mp4", "config": ({"spec": spec} if spec else {}), "auto_start": True,
    }]}, method="POST")
    tasks = ((task.get("data") or {}).get("tasks")) or []
    task_id = ((tasks[0].get("data") or {}).get("id")) if tasks else None
    if not task_id:
        raise RuntimeError("Channels agent did not return a download task id")
    detail = agent_wait(agent, f"/api/v1/download_task/detail?{urlencode({'id': task_id})}",
                        deadline,
                        lambda item: item.get("status") == 5,
                        lambda item: item.get("status") in (6, 7), "视频号下载")
    files = [item for item in (detail.get("files") or []) if item.get("file_path")]
    if not files:
        raise RuntimeError("Channels agent finished without producing a file")
    source = Path(files[0]["file_path"])
    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError("Channels agent download produced no usable file")
    path = directory / "media.mp4"
    shutil.copyfile(source, path)
    author = ((state.get("account") or {}).get("nickname")) or ""
    media = (((raw.get("data") or {}).get("object") or {}).get("objectDesc", {}).get("media") or [{}])[0]
    duration = media.get("videoPlayLen")
    return path, {
        "engine": "weixin-agent", "remoteId": content.get("external_id") or "",
        "originalTitle": str(content.get("description") or content.get("title") or "")[:1000],
        "creator": str(author)[:200],
        "formatId": spec or "agent-default",
        "expectedDuration": float(duration) if duration else None,
        "expectedAudio": True,
        "qualityPolicy": "largest WeChat Channels variant, then bitrate",
        "coverUrl": content.get("cover_url") or "",
    }


def close_weixin_channels(app="WeChat"):
    """Close the WeChat Channels tab so the page stops playing.

    Clicking a Channels link opens the video in a WeChat side panel tab that
    keeps autoplaying with sound long after the download finished.  Any
    failure is reported by the caller but never fails the download.
    """
    script = Path(__file__).with_name("weixin_close_channels.py")
    spec = importlib.util.spec_from_file_location("weixin_close_channels", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.close_channels(app)


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
            if state.get("deliveryVersion") != DELIVERY_VERSION:
                with tempfile.TemporaryDirectory(prefix="compatibility-", dir=directory) as temp:
                    state = publish_delivery(state, directory, Path(temp))
                write_json(state_file, state)
        else:
            state = {}
            errors = []
            engines = [(args.engine, args)]
            if platform == "weixin":
                # WeChat Channels has no public download API; the desktop agent owns the session.
                engines = [("weixin-agent", args)]
            elif args.engine == "auto":
                # Legacy alias: never silently change accounts or download engines.
                engines = [("yt-dlp", args)]
            for engine, engine_args in engines:
                try:
                    log(f"{platform}/{video_id}: downloading with {engine}")
                    # A fresh attempt prevents stale partial files from being accepted as complete.
                    with tempfile.TemporaryDirectory(prefix="attempt-", dir=directory) as temp:
                        attempt = Path(temp)
                        if engine.startswith("yt-dlp"):
                            engine_args.platform = platform
                            path, metadata = download_ytdlp(canonical, attempt, engine_args)
                        elif engine == "weixin-agent":
                            path, metadata = download_weixin(canonical, attempt, engine_args)
                        else:
                            path, metadata = download_clipdock(platform, canonical, attempt, args)
                        media = probe(path)
                        expected = metadata.get("expectedDuration")
                        if expected and abs(media["duration"] - float(expected)) > max(2, float(expected) * 0.05):
                            raise ValueError("Downloaded duration does not match the source")
                        if metadata.get("expectedAudio") and not media["audioCodecs"]:
                            raise ValueError("Source has audio but downloaded file does not")
                        if engine.startswith("yt-dlp") and platform in ("instagram", "tiktok"):
                            if metadata.get("remoteId") != video_id:
                                raise ValueError("Downloaded video ID does not match the requested video")
                        delivered, delivery_media = delivery_copy(path, media, attempt / "compatible.mp4")
                        sheet = preview(delivered, delivery_media, attempt)
                        checksum = digest(delivered)
                        final_path = directory / f"media-{checksum[:12]}.mp4"
                        source_info = {}
                        if delivered != path:
                            source_hash = digest(path)
                            source_path = directory / f"source-{source_hash[:12]}.mp4"
                            path.replace(source_path)
                            source_info = {"sourcePath": str(source_path), "sourceSha256": source_hash,
                                           "sourceMedia": media}
                        delivered.replace(final_path)
                        identify_dir = directory / ".identify"
                        identify_dir.mkdir(exist_ok=True)
                        for frame in attempt.glob("preview*.jpg"):
                            frame.replace(identify_dir / frame.name)
                        state = {
                            "platform": platform, "videoId": video_id, "canonicalUrl": canonical,
                            "sourceUrl": raw, "path": str(final_path),
                            "sha256": checksum, "media": delivery_media,
                            "preview": str(identify_dir / Path(sheet).name), **metadata, **source_info,
                            "deliveryVersion": DELIVERY_VERSION, "fullDecodeVerified": True,
                            "deliveryPolicy": "H.264 yuv420p/AAC; original retained when conversion is needed",
                        }
                    write_json(state_file, state)
                    break
                except Exception as error:
                    message = f"{engine}: {safe_error(error)}"
                    errors.append(message)
                    log(message)
            else:
                raise RuntimeError("; ".join(errors))
        if platform == "weixin" and not getattr(args, "keep_channels_page", False):
            # The Channels tab keeps autoplaying with sound; close it unless the
            # caller wants the page to stay open for the next link.
            try:
                closed = close_weixin_channels()
                log(f"{platform}/{video_id}: channels page closed={closed.get('closed')} "
                    f"({closed.get('method') or closed.get('reason')})")
            except Exception as error:
                log(f"{platform}/{video_id}: could not close the channels page: {safe_error(error)}")
        if not Path(state.get("preview", directory / "missing")).is_file():
            identify_dir = directory / ".identify"
            identify_dir.mkdir(exist_ok=True)
            state["preview"] = preview(Path(state["path"]), state["media"], identify_dir)
        if state.get("verification", {}).get("sha256") != state["sha256"]:
            verification = verify_media(Path(state["path"]))
            if not verification["passed"]:
                raise ValueError("; ".join(verification["errors"]))
            state["verification"] = {**verification, "sha256": state["sha256"]}
        write_json(state_file, state)
        return {
            **state, "stateFile": str(state_file), "folder": "灵感收集",
            "name": state.get("asset", {}).get("name", ""), "tags": [],
            "account": derive_account(state.get("creator"), state.get("creatorId"), state.get("originalTitle")),
            "warning": "" if state["media"]["audioCodecs"] else "No audio track; verify the original is silent",
        }


def pace_pause(delay, jitter):
    """Spread requests out; random delays do not guarantee rate-limit immunity."""
    delay, jitter = float(delay), float(jitter)
    if not math.isfinite(delay) or not math.isfinite(jitter) or delay < 30 or jitter < 0:
        raise ValueError("Download delay must be >= 30 seconds and jitter must be finite and >= 0")
    return delay + random.uniform(0, jitter)


def verify_browser_account(args):
    """用浏览器登录态前先确认它是预期账号。

    同一个浏览器 profile 里可能同时留着多个 Instagram 账号的登录态，换号登录
    后旧 cookie 还在。不校验就会拿主账号去采集，等于用主账号的频率请求平台，
    可能触发风控。这里在下载开始前拦一次；导出本身只读本地 cookie 库，不联网。
    """
    expected = str(getattr(args, "expected_ig_user_id", "") or "").strip()
    source = str(getattr(args, "cookies_from_browser", "") or "").strip()
    if not expected or not source:
        return
    browser_session.export_browser_cookies(
        source, tool("yt-dlp"), proxy=getattr(args, "proxy", "") or None,
        timeout=getattr(args, "timeout", 300), expected_user_id=expected)


def prepare(args):
    state_root = Path(os.environ.get("AIGC_WORKBENCH_STATE_DIR") or
                      str(Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() /
                          "aigc-workbench")).expanduser()
    # One shared lock/cooldown also covers separate processes and different --root values.
    with locked(state_root / "video-download-pacing", "Another video download batch is running"):
        return prepare_serial(args, state_root / "video-download-pacing" / "cooldown.json")


def prepare_serial(args, cooldown_file):
    delay, jitter = getattr(args, "delay", 30), getattr(args, "delay_jitter", 30)
    pace_pause(delay, jitter)
    verify_browser_account(args)
    items, errors, seen = [], [], set()
    not_attempted = []
    for index, raw in enumerate(args.urls):
        try:
            # URL resolution can itself make a request (e.g. TikTok short links).
            if cooldown_file.exists():
                wait_until = float(read_json(cooldown_file)["nextAllowedAt"])
                pause = wait_until - time.time()
                if pause > 0:
                    log(f"pacing: waiting {pause:.1f}s before the next download")
                    time.sleep(pause)
            platform, video_id, _ = identify(raw)
            key = (platform, video_id)
            if key in seen:
                continue
            seen.add(key)
            items.append(prepare_one(raw, args))
        except Exception as error:
            errors.append({"sourceUrl": raw, "status": "stopped", "error": safe_error(error)})
            not_attempted = args.urls[index + 1:]
            log("Download batch stopped; remaining URLs were not attempted")
        finally:
            write_json(cooldown_file, {"nextAllowedAt": time.time() + pace_pause(delay, jitter)})
            write_json(args.report, {"items": items, "errors": errors, "notAttempted": not_attempted})
        if errors:
            break
    print(json.dumps({"report": str(args.report), "items": items, "errors": errors,
                      "notAttempted": not_attempted}, ensure_ascii=False, indent=2))
    return int(bool(errors))


def load_uploader():
    runtime = [Path(os.environ[key]).expanduser() for key in
               ("MINIMAX_DATA_DIR", "MINIMAX_HOME", "CODEX_HOME") if os.environ.get(key)]
    runtime += [HOME / ".minimax", HOME / ".codex"]
    candidates = [
        os.environ.get("AIGC_UPLOAD_SCRIPT"),
        str(Path(__file__).resolve().parents[2] / "aigc-asset-upload/scripts/upload_assets.py"),
        *(str(root / "skills/aigc-asset-upload/scripts/upload_assets.py") for root in runtime),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            spec = importlib.util.spec_from_file_location("aigc_upload", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("Install aigc-asset-upload or set AIGC_UPLOAD_SCRIPT")


DECORATION_PATTERN = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F\u200D]+"
)
ACCOUNT_PATTERN = re.compile(r"[0-9a-z\u4e00-\u9fff]+")
# 中文昵称常写成“主名就是别名”的自述句式，整串塞进标题会挤掉 32 字预算。
# 只认方向一致的主名连接词：“主名就是别名”切前半段；
# “昵称真名XXX”这类主名在后、方向相反的写法不做猜测。
CHINESE_NAME_SEPARATORS = ("就是", "正是")
# 切分后主名两侧可能粘着标点，去掉才不会让令牌过不了 ACCOUNT_PATTERN。
NAME_TRIM_CHARS = " \t，。！？、·.,!?:：;；~～-—_"


def chinese_display_name(token):
    """把“悠哈酱就是悠悠大王”这样的中文昵称收成主名“悠哈酱”。

    视频号、小红书这类平台的中文昵称常用“主名就是别名”的句式，账号前缀只取
    主名，作品名和角色名才有预算写清楚。切不出合法主名就原样返回。
    """
    for separator in CHINESE_NAME_SEPARATORS:
        head, found, _ = token.partition(separator)
        if not found:
            continue
        head = head.strip(NAME_TRIM_CHARS)
        if (len(head) >= 2
                and any("\u4e00" <= ch <= "\u9fff" for ch in head)
                and ACCOUNT_PATTERN.fullmatch(head)):
            return head
    return token


def derive_account(creator="", creator_id="", original_title=""):
    """从发布者信息里取出用于标题前缀的账号令牌。

    平台给的显示名带 emoji、竖线或是拉开字距的 "S A N D R A"，原帖标题里则常见
    "Video by <handle>"。这里只保留一个稳定的短令牌：去掉装饰符号、合并被拆开的
    拉丁字母、取第一个词并转小写，中文自述昵称再收成主名；显示名取不到时退回
    账号 ID 的第一段。
    """
    text = DECORATION_PATTERN.sub(" ", str(creator or ""))
    text = re.sub(r"[｜|·]", " ", text)
    words = text.split()
    if len(words) > 1 and all(len(word) == 1 and word.isalpha() for word in words):
        words = ["".join(words)]
    token = words[0] if words else ""
    if token:
        token = chinese_display_name(token)
    if not token:
        handle = str(creator_id or "").strip()
        if not handle:
            match = re.search(r"Video by ([A-Za-z0-9._-]+)", str(original_title or ""))
            handle = match.group(1) if match else ""
        token = handle.split("_")[0].split(".")[0]
    token = token.strip().lower()
    return token if ACCOUNT_PATTERN.fullmatch(token) else ""


def apply_account_prefix(name, account):
    """清单里写了 account 就给标题加账号前缀；已经带前缀的不重复添加。"""
    token = str(account or "").strip()
    name = str(name or "").strip()
    if not token or not name:
        return name
    if name == token or name.startswith(token + "-"):
        return name
    return f"{token}-{name}"


def validate_item(item):
    state_file = Path(item["stateFile"]).expanduser().resolve()
    state = read_json(state_file)
    path = Path(item["path"]).expanduser().resolve()
    if path != Path(state["path"]).resolve() or path.parent != state_file.parent:
        raise ValueError("Manifest path differs from the prepared download")
    if digest(path) != state["sha256"] or item.get("sha256") != state["sha256"]:
        raise ValueError("Video changed after preparation; prepare it again")
    if state.get("deliveryVersion") != DELIVERY_VERSION or not compatible(probe(path)):
        raise ValueError("Prepare this video again to validate playback compatibility before upload")
    verification = state.get("verification", {})
    if verification.get("sha256") != state["sha256"] or not verification.get("passed"):
        raise ValueError("Prepare this video again to complete media and audio verification before upload")
    name = apply_account_prefix(item.get("name", "").strip(), item.get("account"))
    tags = item.get("tags", [])
    if len(name) > 32:
        raise ValueError(f"标题超过 32 字（{len(name)} 字）：加上账号前缀后要压缩换装两端描述")
    load_uploader().validate_metadata({"name": name, "tags": tags})
    if item.get("folder", "灵感收集") != "灵感收集":
        raise ValueError("This workflow imports into 灵感收集; use aigc-asset-upload for other libraries")
    metadata = {
        "name": name, "tags": tags, "folder": "灵感收集",
        "source": SOURCE_LABELS.get(state["platform"], state["platform"]),
        "sourceUrl": state["sourceUrl"],
        **{key: item[key] for key in ("characterName", "characterCategory", "characterCandidates") if key in item},
    }
    metrics = item.get("socialMetrics") or state.get("socialMetrics") or {}
    if metrics:
        metadata["socialMetrics"] = metrics
    return state_file, state, path, metadata


def upload_one(item, client):
    with locked(Path(item["stateFile"]).parent):
        state_file, state, path, metadata = validate_item(item)
        # Always ask the current account: local receipts can be stale or belong to another account.
        hashes = [state["sha256"]]
        if state.get("sourceSha256"):
            hashes.append(state["sourceSha256"])
        existing = client.find_existing(hashes)
        asset = next((existing[h] for h in hashes if existing.get(h)), None)
        status = "existing" if asset else "uploaded"
        if not asset:
            # Do not retry this POST blindly. A later invocation checks hashes first.
            asset = client.upload(path, metadata)
        if not isinstance(asset, dict) or not asset.get("id"):
            raise RuntimeError("Server response did not contain an asset ID; rerun to reconcile by hash")
        asset = client.get_asset(asset["id"])
        warnings = []
        if status == "uploaded":
            for key, value in metadata.items():
                if key == "socialMetrics" and not asset.get("socialMetrics"):
                    # 服务端还没上线 social_metrics 列：保留本地快照，不阻断上传，
                    # 保留历史/用户提供的数据，但不向来源平台请求计数。
                    warnings.append("服务端尚未支持 socialMetrics 字段，计数只保留在本地")
                    continue
                equal = set(asset.get(key, [])) == set(value) if key == "tags" else asset.get(key) == value
                if not equal:
                    raise RuntimeError(f"Uploaded metadata differs on readback: {key}; rerun to reconcile")
        receipt = {key: asset[key] for key in
                   ("id", "name", "tags", "folder", "sourceUrl", "characterName", "characterCategory",
                    "characterCandidates", "socialMetrics") if key in asset}
        state["asset"] = receipt
        write_json(state_file, state)
        cleanup_previews(state_file.parent)
        return {"status": status, "path": str(path), "asset": receipt, "media": state["media"],
                "warning": "; ".join(warnings)}


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


def refresh(args):
    uploader = load_uploader()
    client = uploader.Client(args.api or uploader.DEFAULT_API, args.cookie_file or uploader.DEFAULT_COOKIE_FILE)
    client.require("GET", "/api/auth/me")
    errors = []
    with locked(args.manifest.parent):
        report = read_json(args.manifest)
        if not isinstance(report.get("items"), list) or not report["items"]:
            raise ValueError("Manifest has no items")
        for item in report["items"]:
            try:
                state_file = Path(item["stateFile"]).expanduser().resolve()
                with (nullcontext() if state_file.parent == args.manifest.parent else locked(state_file.parent)):
                    state = read_json(state_file)
                    asset_id = state.get("asset", {}).get("id")
                    if not asset_id:
                        raise ValueError("No uploaded asset ID; upload/reconcile first")
                    asset = client.get_asset(asset_id)
                    receipt = {key: asset[key] for key in
                               ("id", "name", "tags", "folder", "sourceUrl", "characterName", "characterCategory", "characterCandidates") if key in asset}
                    state["asset"] = receipt
                    write_json(state_file, state)
                    item.update({key: value for key, value in receipt.items() if key != "id"})
                    item["asset"] = receipt
            except Exception as error:
                errors.append({"stateFile": item.get("stateFile"), "error": safe_error(error)})
        write_json(args.manifest, report)
    print(json.dumps({"manifest": str(args.manifest), "errors": errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    download = sub.add_parser("prepare", help="Download videos and create a manifest for visual naming")
    download.add_argument("urls", nargs="+")
    download.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    download.add_argument("--report", type=Path, required=True)
    download.add_argument("--engine", choices=("auto", "yt-dlp", "clipdock", "weixin"), default="yt-dlp",
                          help="auto is a legacy alias for yt-dlp; no automatic fallback")
    download.add_argument("--proxy", default="")
    download.add_argument("--cookies-from-browser", default="")
    download.add_argument("--no-clipdock-cookies", action="store_true",
                          help=argparse.SUPPRESS)
    download.add_argument("--refresh-download", action="store_true",
                          help="Replace the cached file only after a new download passes validation")
    download.add_argument("--clipdock", default="http://127.0.0.1:3457")
    download.add_argument("--wx-agent", default=CHANNELS_AGENT_DEFAULT,
                          help="WeChat Channels desktop agent API origin (loopback only)")
    download.add_argument("--keep-channels-page", action="store_true",
                          help="Leave the WeChat Channels page open after the download "
                               "(use it to grab several links per activation, close it at the end)")
    download.add_argument("--no-social-metrics", action="store_true",
                          help=argparse.SUPPRESS)
    download.add_argument("--delay", type=float, default=30,
                          help="Minimum pause after each video, >= 30 seconds (default: 30)")
    download.add_argument("--delay-jitter", type=float, default=30,
                          help="Extra random wait from 0 to this many seconds (default: 30)")
    download.add_argument("--ytdlp-sleep-requests", type=int, default=0,
                          help="Additional sleep between extraction requests within yt-dlp")
    download.add_argument("--ytdlp-sleep-interval", type=float, default=0,
                          help="Additional sleep before each media download within yt-dlp")
    download.add_argument("--ytdlp-limit-rate", default="",
                          help="Bandwidth cap for yt-dlp, for example 800K")
    download.add_argument("--timeout", type=int, default=300)
    download.add_argument("--capture-timeout", type=int, default=150)
    send = sub.add_parser("upload", help="Upload a reviewed manifest, checking server hashes first")
    send.add_argument("--manifest", type=Path, required=True)
    send.add_argument("--results", type=Path, required=True)
    send.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", ""))
    send.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", ""))
    repair = sub.add_parser("repair", help="Create a compatible local MP4, preserving the input")
    repair.add_argument("--input", type=Path, required=True)
    repair.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("verify", help="Probe, fully decode and measure each audio stream")
    check.add_argument("--input", type=Path, required=True)
    check.add_argument("--report", type=Path)
    sync = sub.add_parser("refresh", help="Refresh local receipts from the current account")
    sync.add_argument("--manifest", type=Path, required=True)
    sync.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", ""))
    sync.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", ""))
    download.add_argument(
        "--expected-ig-user-id", default=os.environ.get("AIGC_IG_USER_ID", ""),
        help="Check the locally selected browser's Instagram ds_user_id before downloading")
    args = parser.parse_args()
    if args.action == "prepare":
        try:
            pace_pause(args.delay, args.delay_jitter)
        except ValueError as error:
            parser.error(str(error))
    for field in ("root", "report", "manifest", "results"):
        if getattr(args, field, None) is not None:
            setattr(args, field, getattr(args, field).expanduser().resolve())
    return args


def main():
    args = parse_args()
    if args.action == "prepare":
        return prepare(args)
    if args.action == "upload":
        return upload(args)
    if args.action == "refresh":
        return refresh(args)
    if args.action == "verify":
        report = verify_media(args.input.expanduser().resolve())
        if args.report:
            write_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(not report["passed"])
    source, output = args.input.expanduser().resolve(), args.output.expanduser().resolve()
    if output.exists() or output == source or output.suffix.lower() != ".mp4":
        raise ValueError("Choose a new .mp4 output path; existing files are never overwritten")
    media = probe(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repair-", dir=output.parent) as temp:
        path, delivered = delivery_copy(source, media, Path(temp) / "compatible.mp4")
        # Exclusive creation also protects against an output created while encoding.
        with path.open("rb") as reader, output.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    print(json.dumps({"path": str(output), "sourcePath": str(source),
                      "sourceMedia": media, "media": delivered,
                      "fullDecodeVerified": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        log(safe_error(error) or "Cancelled")
        sys.exit(1)
