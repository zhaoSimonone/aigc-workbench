#!/usr/bin/env python3
"""Inspect and safely write content-based metadata for AIGC Shelf assets."""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urljoin, urlparse
import http.client


DEFAULT_API = "https://aigc.chatcanvas.online"
DEFAULT_COOKIE_FILE = Path.home() / ".codex" / "aigc-shelf-session.json"
FOLDERS = ("灵感收集", "我的创作", "角色设定", "项目资料")
TAB_ALIASES = {
    "灵感收集整理": "灵感收集",
    "我的创作整理": "我的创作",
    "我的创造整理": "我的创作",
    "角色整理": "角色设定",
    "角色设定整理": "角色设定",
    "项目资料整理": "项目资料",
}
MUSIC_FIELDS = ("musicTitle", "musicArtist", "musicSource", "musicStatus", "musicConfidence", "musicEvidence")
ASSET_FIELDS = (
    "name", "tags", "note", "folder", "source", "sourceUrl", "characterName", "characterCategory",
    *MUSIC_FIELDS,
)
PLACEHOLDER_TAGS = {"待整理", "未分类", "占位"}
PLACEHOLDER_NAMES = {"", "未命名素材", "新上传素材", "无标题", "待整理"}
GENERATED_NAME_PATTERNS = (
    re.compile(r"^(?:tiktok|douyin|抖音|video|image|img|mov|mp4|screenshot|screen|asset)[-_ ]?(?:video|image)?[-_ ]?\d{8,14}$", re.I),
    re.compile(r"^(?:IMG|VID|MOV|DSC|SVID)[-_ ]?\d{4,}(?:[-_ ]\d{2,})*$", re.I),
)


def parse_json(payload):
    try:
        return json.loads(payload.decode("utf-8")) if payload else None
    except json.JSONDecodeError:
        return {"raw": payload[:500].decode("utf-8", "replace")}


class Client:
    def __init__(self, base_url, cookie_file):
        parsed = urlparse(base_url.rstrip("/"))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("AIGC_SHELF_URL 必须是 HTTP(S) 地址")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port
        self.base_path = parsed.path.rstrip("/")
        self.base_url = base_url.rstrip("/")
        self.cookie_file = Path(cookie_file).expanduser()
        self.cookies = self._load_cookies()

    def _load_cookies(self):
        try:
            data = json.loads(self.cookie_file.read_text())
            return {str(key): str(value) for key, value in data.items()}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_cookies(self):
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_file.write_text(json.dumps(self.cookies))
        try:
            self.cookie_file.chmod(0o600)
        except OSError:
            pass

    def request(self, method, path, body=None, headers=None):
        connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.host, self.port, timeout=900)
        request_headers = {"Accept": "application/json", **(headers or {})}
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        request_path = path if path.startswith("/") else f"/{path}"
        connection.request(method, f"{self.base_path}{request_path}", body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        set_cookie = response.headers.get("Set-Cookie")
        if set_cookie:
            pair = set_cookie.split(";", 1)[0]
            if "=" in pair:
                name, value = pair.split("=", 1)
                if value:
                    self.cookies[name] = value
                else:
                    self.cookies.pop(name, None)
            self._save_cookies()
        connection.close()
        return response.status, parse_json(payload)

    def ensure_login(self):
        status, payload = self.request("GET", "/api/auth/me")
        if status == 200:
            return (payload or {}).get("user", {})
        email = os.environ.get("AIGC_SHELF_EMAIL") or input("AIGC Shelf 登录邮箱: ").strip()
        password = os.environ.get("AIGC_SHELF_PASSWORD") or getpass.getpass("AIGC Shelf 登录密码: ")
        body = json.dumps({"email": email, "password": password}).encode()
        status, payload = self.request("POST", "/api/auth/login", body, {
            "Content-Type": "application/json", "Content-Length": str(len(body)),
        })
        if status != 200:
            raise RuntimeError((payload or {}).get("error", "AIGC Shelf 登录失败"))
        return (payload or {}).get("user", {})

    def list_assets(self):
        status, payload = self.request("GET", "/api/assets")
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"读取素材失败 HTTP {status}"))
        return (payload or {}).get("assets", [])

    def list_music_tracks(self):
        status, payload = self.request("GET", "/api/music-tracks")
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"读取音乐库失败 HTTP {status}"))
        return (payload or {}).get("tracks", [])

    def build_music_library(self):
        status, payload = self.request("POST", "/api/music-tracks/build", b"{}", {
            "Content-Type": "application/json", "Content-Length": "2",
        })
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"构建音乐库失败 HTTP {status}"))
        return payload or {}

    def upload_music_track(self, audio_path, sha256, asset_ids, probe):
        boundary = f"----AIGCShelfMusic{uuid.uuid4().hex}"
        fields = {
            "sha256": sha256,
            "assetIds": json.dumps(asset_ids, ensure_ascii=False),
            "durationSeconds": str(probe.get("durationSeconds") or ""),
            "sampleRate": str(probe.get("sampleRate") or ""),
            "channels": str(probe.get("channels") or ""),
        }
        parts = []
        for key, value in fields.items():
            parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").encode("utf-8"))
        filename = Path(audio_path).name
        file_header = f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n".encode("utf-8")
        ending = f"\r\n--{boundary}--\r\n".encode("ascii")
        file_size = Path(audio_path).stat().st_size
        content_length = sum(len(part) for part in parts) + len(file_header) + file_size + len(ending)
        connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.host, self.port, timeout=900)
        request_path = f"{self.base_path}/api/music-tracks" if self.base_path else "/api/music-tracks"
        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(content_length),
        }
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        try:
            connection.putrequest("POST", request_path)
            for key, value in headers.items():
                connection.putheader(key, value)
            connection.endheaders()
            for part in parts:
                connection.send(part)
            connection.send(file_header)
            with Path(audio_path).open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    connection.send(chunk)
            connection.send(ending)
            response = connection.getresponse()
            payload = response.read()
            set_cookie = response.headers.get("Set-Cookie")
            if set_cookie:
                pair = set_cookie.split(";", 1)[0]
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    if value:
                        self.cookies[name] = value
                    else:
                        self.cookies.pop(name, None)
                self._save_cookies()
            if response.status != 201:
                parsed = parse_json(payload) or {}
                raise RuntimeError(parsed.get("error", f"上传音乐失败 HTTP {response.status}"))
            return parse_json(payload) or {}
        finally:
            connection.close()

    def consolidate_music_tracks(self, groups):
        body = json.dumps({"groups": groups}, ensure_ascii=False).encode()
        status, payload = self.request("POST", "/api/music-tracks/consolidate", body, {
            "Content-Type": "application/json", "Content-Length": str(len(body)),
        })
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"合并音乐失败 HTTP {status}"))
        return payload or {}

    def organize(self, asset_id, fields):
        body = json.dumps(fields, ensure_ascii=False).encode()
        status, payload = self.request("PATCH", f"/api/assets/{asset_id}/organize", body, {
            "Content-Type": "application/json", "Content-Length": str(len(body)),
        })
        if status == 404:
            return self.legacy_organize(asset_id, fields)
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"写回失败 HTTP {status}"))
        return (payload or {}).get("asset", {}), []

    def legacy_organize(self, asset_id, fields):
        """Fallback for production servers that predate the atomic organize route."""
        supported = {key: value for key, value in fields.items() if key in {
            "name", "note", "folder", "source", "sourceUrl", "characterName", "characterCategory",
        }}
        warnings = []
        unsupported = sorted(set(fields) - set(supported) - {"tags"})
        if unsupported:
            warnings.append(f"旧版服务端未保存字段：{', '.join(unsupported)}")
        if supported:
            body = json.dumps(supported, ensure_ascii=False).encode()
            status, payload = self.request("PATCH", f"/api/assets/{asset_id}", body, {
                "Content-Type": "application/json", "Content-Length": str(len(body)),
            })
            if status != 200:
                raise RuntimeError((payload or {}).get("error", f"旧接口写回失败 HTTP {status}"))
        if "tags" in fields:
            tag_body = json.dumps({"tags": fields["tags"]}, ensure_ascii=False).encode()
            status, payload = self.request("POST", f"/api/assets/{asset_id}/tags", tag_body, {
                "Content-Type": "application/json", "Content-Length": str(len(tag_body)),
            })
            if status != 204:
                raise RuntimeError((payload or {}).get("error", f"旧接口标签写回失败 HTTP {status}"))
        return {}, warnings

    def download(self, url, destination):
        parsed = urlparse(urljoin(self.base_url + "/", url))
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=900)
        headers = {}
        if self.cookies and parsed.hostname == self.host:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status >= 400:
            connection.close()
            raise RuntimeError(f"下载素材失败 HTTP {response.status}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
        connection.close()


def explicit_name(name):
    value = str(name or "").strip()
    if value in PLACEHOLDER_NAMES:
        return False
    return not any(pattern.fullmatch(value) for pattern in GENERATED_NAME_PATTERNS)


def clear_tags(tags):
    values = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    return bool(values) and not any(tag in PLACEHOLDER_TAGS for tag in values)


def needs_music(asset):
    if asset.get("type") != "video":
        return False
    status = str(asset.get("musicStatus") or "").strip()
    return not status


def asset_value(asset, key):
    """Treat music fields as empty while older API deployments omit them."""
    if key in MUSIC_FIELDS:
        return asset.get(key) or ""
    return asset.get(key)


def candidate(asset, force=False, music=False):
    needs_name = not explicit_name(asset.get("name"))
    needs_tags = not clear_tags(asset.get("tags"))
    needs_note = not str(asset.get("note") or "").strip() or str(asset.get("note", "")).strip() == "新上传素材，等待补充备注。"
    return {
        "needsName": needs_name,
        "needsTags": needs_tags,
        "needsNote": needs_note,
        "needsMusic": music and needs_music(asset),
        "eligible": force or needs_name or needs_tags or needs_note or (music and needs_music(asset)),
    }


def select_assets(assets, args):
    by_id = {str(asset.get("id")): asset for asset in assets}
    if args.asset_id:
        missing = [asset_id for asset_id in args.asset_id if asset_id not in by_id]
        if missing:
            raise RuntimeError(f"素材不存在或不属于当前账号：{', '.join(missing)}")
        selected = [by_id[asset_id] for asset_id in dict.fromkeys(args.asset_id)]
    else:
        folder = TAB_ALIASES.get(args.tab, args.tab) if args.tab else "灵感收集"
        selected = [asset for asset in assets if asset.get("folder") == folder or (folder == "我的创作" and asset.get("folder") == "成片")]
    output = []
    for asset in selected:
        status = candidate(asset, args.force, args.music)
        if args.asset_id or status["eligible"]:
            output.append({"asset": asset, "classification": status})
    return output


def print_inspection(items, as_json=False):
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for item in items:
        asset = item["asset"]
        status = item["classification"]
        print(f"[{asset.get('id')}] {asset.get('name') or '未命名素材'} | {asset.get('folder')} | {asset.get('type')}")
        print(f"  标签：{'、'.join(asset.get('tags') or []) or '无'}")
        print(f"  需要整理：标题={status['needsName']} 标签={status['needsTags']} 备注={status['needsNote']} 音乐={status['needsMusic']}")
        print(f"  媒体地址：{asset.get('src') or asset.get('thumb') or '无'}")


def normalise_plan(plan):
    if isinstance(plan, dict):
        plan = plan.get("items")
    if not isinstance(plan, list):
        raise ValueError("plan 必须是数组或包含 items 数组的对象")
    result = []
    for index, item in enumerate(plan, 1):
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError(f"plan 第 {index} 项缺少 id")
        after = item.get("after")
        if not isinstance(after, dict) or not after:
            raise ValueError(f"plan 第 {index} 项 after 必须是非空对象")
        before = item.get("before")
        if not isinstance(before, dict):
            raise ValueError(f"plan 第 {index} 项必须包含 before 对象")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"plan 第 {index} 项必须包含基于素材证据的 reason")
        missing_before = set(after) - set(before)
        if missing_before:
            raise ValueError(f"plan 第 {index} 项 before 缺少字段：{', '.join(sorted(missing_before))}")
        unknown = set(after) - set(ASSET_FIELDS)
        if unknown:
            raise ValueError(f"plan 第 {index} 项包含不支持的字段：{', '.join(sorted(unknown))}")
        if "tags" in after and not isinstance(after["tags"], list):
            raise ValueError(f"plan 第 {index} 项 tags 必须是数组")
        if "tags" in after and (len(after["tags"]) > 5 or any(not isinstance(tag, str) or not tag.strip() for tag in after["tags"])):
            raise ValueError(f"plan 第 {index} 项 tags 最多 5 个且不能包含空标签")
        if "folder" in after and after["folder"] not in FOLDERS:
            raise ValueError(f"plan 第 {index} 项 folder 必须是：{'、'.join(FOLDERS)}")
        if "name" in after and not str(after["name"] or "").strip():
            raise ValueError(f"plan 第 {index} 项 name 不能为空")
        result.append(item)
    return result


def changed_fields(before, after):
    return {key: value for key, value in after.items() if asset_value(before, key) != value}


def print_diff(items, current_by_id):
    changes = []
    for item in items:
        current = current_by_id.get(str(item["id"]))
        if not current:
            print(f"[{item['id']}] 拒绝：素材不存在或不属于当前账号")
            continue
        before = item.get("before") or {}
        drift = {key: asset_value(current, key) for key in set(before) if asset_value(current, key) != asset_value(before, key)}
        after = changed_fields(current, item["after"])
        if drift:
            print(f"[{item['id']}] 拒绝：预览后素材已变化：{json.dumps(drift, ensure_ascii=False)}")
            continue
        if not after:
            print(f"[{item['id']}] 跳过：没有实际变化")
            continue
        changes.append((item, current, after))
        print(f"[{item['id']}] {current.get('name') or '未命名素材'}")
        for key, value in after.items():
            print(f"  {key}: {json.dumps(asset_value(current, key), ensure_ascii=False)} -> {json.dumps(value, ensure_ascii=False)}")
        if item.get("reason"):
            print(f"  原因：{item['reason']}")
    if not changes:
        print("没有可写回的变化。")
    return changes


def inspect_command(client, args):
    user = client.ensure_login()
    items = select_assets(client.list_assets(), args)
    tab = TAB_ALIASES.get(args.tab, args.tab) if args.tab else None
    payload = {
        "user": {"id": user.get("id"), "name": user.get("name") or user.get("email")},
        "scope": {"assetIds": args.asset_id, "tab": tab or (None if args.asset_id else "灵感收集"), "force": args.force, "music": args.music},
        "items": items,
    }
    if args.download_dir:
        target = Path(args.download_dir).expanduser()
        for item in items:
            asset = item["asset"]
            url = asset.get("src") or asset.get("thumb")
            if not url:
                item["downloadError"] = "没有可下载地址"
                continue
            destination = target / f"{asset.get('id')}{'.mp4' if asset.get('type') == 'video' else '.jpg'}"
            try:
                client.download(url, destination)
                item["localPath"] = str(destination)
            except Exception as error:
                item["downloadError"] = str(error)
    print_inspection(payload if args.json else items, args.json)


def apply_command(client, args):
    try:
        plan = normalise_plan(json.loads(Path(args.plan).read_text() if args.plan != "-" else sys.stdin.read()))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"无法读取整理提案：{error}") from error
    client.ensure_login()
    current_by_id = {str(asset.get("id")): asset for asset in client.list_assets()}
    changes = print_diff(plan, current_by_id)
    if not args.confirm:
        print("预览模式：未写回。获得用户明确确认后，再加 --confirm 重试。")
        return 0
    failures = 0
    for item, current, after in changes:
        try:
            _asset, warnings = client.organize(item["id"], after)
            print(f"[{item['id']}] 已更新")
            for warning in warnings:
                print(f"[{item['id']}] 提醒：{warning}", file=sys.stderr)
        except Exception as error:
            failures += 1
            print(f"[{item['id']}] 失败：{error}", file=sys.stderr)
    return 1 if failures else 0


def resolve_binary(name, override=None):
    candidates = [override, os.environ.get(f"AIGC_{name.upper()}_PATH"), shutil.which(name)]
    repo_root = Path(__file__).resolve().parents[3]
    package_root = repo_root / "server" / "node_modules"
    if name == "ffmpeg":
        candidates.extend(str(path) for path in package_root.glob("@ffmpeg-installer/*/ffmpeg"))
    if name == "ffprobe":
        candidates.extend(str(path) for path in package_root.glob("@ffprobe-installer/*/ffprobe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(f"本地找不到 {name}，请安装 FFmpeg/ffprobe，或设置 AIGC_{name.upper()}_PATH")


def local_audio_probe(ffprobe_path, audio_path):
    completed = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "stream=sample_rate,channels:format=duration", "-of", "json", str(audio_path)],
        check=True, capture_output=True, text=True, timeout=120,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    stream = (payload.get("streams") or [{}])[0]
    try:
        duration = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {
        "durationSeconds": duration if duration and duration > 0 else None,
        "sampleRate": int(stream.get("sample_rate") or 44100),
        "channels": int(stream.get("channels") or 1),
    }


def local_file_sha256(audio_path):
    import hashlib
    digest = hashlib.sha256()
    with Path(audio_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_audio_fingerprint(audio_path):
    """Return a short-time spectral fingerprint for matching shifted music excerpts."""
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("本地音乐去重需要 numpy，请先安装 numpy") from error
    import wave
    with wave.open(str(audio_path), "rb") as stream:
        if stream.getsampwidth() != 2:
            raise RuntimeError("音乐库音频必须是 16-bit PCM WAV")
        sample_rate = stream.getframerate()
        channels = stream.getnchannels()
        samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if sample_rate != 44100:
        raise RuntimeError(f"音乐库音频采样率应为 44100Hz，实际为 {sample_rate}Hz")
    frame_size = 2048
    hop_size = 1024
    window = np.hanning(frame_size)
    frequencies = np.fft.rfftfreq(frame_size, 1 / sample_rate)
    band_edges = np.geomspace(80, 16000, 33)
    band_masks = [(frequencies >= low) & (frequencies < high) for low, high in zip(band_edges[:-1], band_edges[1:])]
    frames = []
    for offset in range(0, max(1, len(samples) - frame_size + 1), hop_size):
        frame = samples[offset:offset + frame_size]
        if len(frame) < frame_size:
            frame = np.pad(frame, (0, frame_size - len(frame)))
        spectrum = np.abs(np.fft.rfft(frame * window))
        bands = np.array([np.log1p(spectrum[mask].mean()) for mask in band_masks], dtype=np.float32)
        bands = (bands - bands.mean()) / (bands.std() + 1e-6)
        frames.append(bands)
    if not frames:
        raise RuntimeError("音频内容为空")
    fingerprint = np.stack(frames)
    # One point is roughly 93 ms. Averaging lowers encoding noise while retaining musical timing.
    group_size = 4
    grouped = [fingerprint[index:index + group_size].mean(axis=0) for index in range(0, len(fingerprint), group_size)]
    fingerprint = np.stack(grouped)
    norms = np.linalg.norm(fingerprint, axis=1, keepdims=True)
    return fingerprint / np.maximum(norms, 1e-6)


def fingerprint_similarity(left, right):
    """Align two fingerprints and score their best sustained matching excerpt."""
    import numpy as np
    dimension = left.shape[1]
    correlation = np.zeros(len(left) + len(right) - 1, dtype=np.float64)
    for index in range(dimension):
        correlation += np.correlate(left[:, index], right[:, index], mode="full")
    lags = np.arange(-(len(right) - 1), len(left))
    left_start = np.maximum(lags, 0)
    right_start = np.maximum(-lags, 0)
    overlap = np.minimum(len(left) - left_start, len(right) - right_start)
    minimum_overlap = max(24, int(np.ceil(min(len(left), len(right)) * 0.60)))
    valid = overlap >= minimum_overlap
    if not valid.any():
        return {"score": 0.0, "p25": 0.0, "overlapSeconds": 0.0}
    scores = correlation / np.maximum(overlap, 1)
    candidate_indexes = np.flatnonzero(valid)
    best_index = candidate_indexes[np.argmax(scores[candidate_indexes])]
    count = int(overlap[best_index])
    start_left = int(left_start[best_index])
    start_right = int(right_start[best_index])
    per_frame = np.sum(left[start_left:start_left + count] * right[start_right:start_right + count], axis=1)
    return {
        "score": float(scores[best_index]),
        "p25": float(np.quantile(per_frame, 0.25)),
        "overlapSeconds": round(count * 4 * 1024 / 44100, 2),
    }


def is_high_confidence_music_match(similarity):
    """Require a strong, sustained match before records are auto-merged."""
    return (
        similarity.get("score", 0.0) >= 0.97
        and similarity.get("p25", 0.0) >= 0.95
        and similarity.get("overlapSeconds", 0.0) >= 5.0
    )


def is_review_music_match(similarity):
    """Flag near matches that may be the same song with a different mix."""
    return (
        similarity.get("score", 0.0) >= 0.94
        and similarity.get("p25", 0.0) >= 0.90
        and similarity.get("overlapSeconds", 0.0) >= 5.0
        and not is_high_confidence_music_match(similarity)
    )


def track_duration_seconds(track):
    try:
        return float(track.get("durationSeconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def choose_music_survivor(tracks):
    status_rank = {"已确认": 3, "已标记": 2, "待标记": 1, "不使用": 0}
    return sorted(
        tracks,
        key=lambda track: (
            track_duration_seconds(track),
            status_rank.get(track.get("status"), 0),
            bool(track.get("title") or track.get("artist") or track.get("note")),
            int(track.get("linkedAssetCount") or 0),
            str(track.get("createdAt") or ""),
        ),
        reverse=True,
    )[0]


def local_content_dedupe(client):
    tracks = client.list_music_tracks()
    result = {"tracksScanned": len(tracks), "groups": [], "reviewCandidates": [], "errors": []}
    if len(tracks) < 2:
        return result
    with tempfile.TemporaryDirectory(prefix="aigc-music-dedupe-") as temp_dir:
        fingerprints = {}
        for index, track in enumerate(tracks):
            audio_path = Path(temp_dir) / f"{track.get('id')}.wav"
            try:
                client.download(track.get("streamUrl") or "", audio_path)
                fingerprints[track["id"]] = local_audio_fingerprint(audio_path)
            except Exception as error:
                result["errors"].append({"trackId": track.get("id"), "error": str(error)[:500]})
        matches = []
        review_matches = []
        ids = list(fingerprints)
        pair_matches = {}
        for left_index, left_id in enumerate(ids):
            for right_id in ids[left_index + 1:]:
                similarity = fingerprint_similarity(fingerprints[left_id], fingerprints[right_id])
                pair_matches[frozenset((left_id, right_id))] = similarity
                if is_high_confidence_music_match(similarity):
                    matches.append({"leftTrackId": left_id, "rightTrackId": right_id, **similarity})
                elif is_review_music_match(similarity):
                    review_matches.append({"leftTrackId": left_id, "rightTrackId": right_id, **similarity})

        # Complete-link clustering prevents a weak bridge from merging unrelated
        # songs just because each one resembles a different member of a cluster.
        clusters = []
        for track_id in ids:
            placed = False
            for cluster in clusters:
                if all(
                    is_high_confidence_music_match(pair_matches[frozenset((track_id, member_id))])
                    for member_id in cluster
                ):
                    cluster.append(track_id)
                    placed = True
                    break
            if not placed:
                clusters.append([track_id])
        by_id = {track["id"]: track for track in tracks}
        result["reviewCandidates"] = [
            {
                **match,
                "leftSources": [asset.get("name") for asset in by_id[match["leftTrackId"]].get("linkedAssets") or []],
                "rightSources": [asset.get("name") for asset in by_id[match["rightTrackId"]].get("linkedAssets") or []],
            }
            for match in sorted(review_matches, key=lambda item: (-item["score"], -item["overlapSeconds"]))
        ]
        for track_ids in clusters:
            if len(track_ids) < 2:
                continue
            members = [by_id[track_id] for track_id in track_ids]
            survivor = choose_music_survivor(members)
            duplicates = [track for track in members if track["id"] != survivor["id"]]
            result["groups"].append({
                "keepTrackId": survivor["id"],
                "duplicateTrackIds": [track["id"] for track in duplicates],
                "sourceVideos": sum(int(track.get("linkedAssetCount") or 0) for track in members),
                "members": [{"id": track["id"], "duration": track.get("duration"), "sources": [asset.get("name") for asset in track.get("linkedAssets") or []]} for track in members],
                "matches": [match for match in matches if match["leftTrackId"] in track_ids and match["rightTrackId"] in track_ids],
            })
    result["groups"].sort(key=lambda group: (-group["sourceVideos"], group["keepTrackId"]))
    result["duplicateTracks"] = sum(len(group["duplicateTrackIds"]) for group in result["groups"])
    return result


def build_local_music_library(client, args):
    ffmpeg_path = resolve_binary("ffmpeg", args.ffmpeg)
    ffprobe_path = resolve_binary("ffprobe", args.ffprobe)
    assets = client.list_assets()
    tracks = client.list_music_tracks()
    linked_asset_ids = {
        str(asset.get("id"))
        for track in tracks
        for asset in (track.get("linkedAssets") or [])
        if asset.get("id")
    }
    videos = [asset for asset in assets if asset.get("type") == "video"]
    result = {"totalVideos": len(videos), "processed": 0, "reused": 0, "noAudio": 0, "failed": 0, "tracks": [], "errors": []}
    with tempfile.TemporaryDirectory(prefix="aigc-music-") as temp_dir:
        groups = {}
        for index, asset in enumerate(videos, 1):
            asset_id = str(asset.get("id"))
            if asset_id in linked_asset_ids:
                result["reused"] += 1
                continue
            source_path = Path(temp_dir) / f"source-{index}.media"
            audio_path = Path(temp_dir) / f"audio-{index}.wav"
            try:
                client.download(asset.get("src") or "", source_path)
                completed = subprocess.run(
                    [ffmpeg_path, "-y", "-i", str(source_path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", "-f", "wav", str(audio_path)],
                    check=True, capture_output=True, text=True, timeout=10 * 60,
                )
                probe = local_audio_probe(ffprobe_path, audio_path)
                sha256 = local_file_sha256(audio_path)
                group = groups.setdefault(sha256, {"audioPath": audio_path, "probe": probe, "assetIds": []})
                group["assetIds"].append(asset_id)
            except subprocess.CalledProcessError as error:
                message = (error.stderr or error.stdout or "音频提取失败").strip()
                if re.search(r"matches no streams|does not contain any stream|stream map.*no streams|no audio", message, re.I):
                    result["noAudio"] += 1
                else:
                    result["failed"] += 1
                    result["errors"].append({"assetId": asset_id, "name": asset.get("name"), "error": message[-500:]})
            except Exception as error:
                result["failed"] += 1
                result["errors"].append({"assetId": asset_id, "name": asset.get("name"), "error": str(error)[:500]})
        for sha256, group in groups.items():
            try:
                client.upload_music_track(group["audioPath"], sha256, group["assetIds"], group["probe"])
                result["processed"] += len(group["assetIds"])
            except Exception as error:
                result["failed"] += len(group["assetIds"])
                result["errors"].append({"assetIds": group["assetIds"], "sha256": sha256, "error": str(error)[:500]})
    result["tracks"] = client.list_music_tracks()
    return result


def music_library_command(client, args):
    client.ensure_login()
    if args.dedupe_content:
        result = local_content_dedupe(client)
        if not args.confirm:
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else (
                f"本地内容去重预览：扫描 {result['tracksScanned']} 条音乐，发现 {len(result['groups'])} 组可合并内容，"
                f"共 {result.get('duplicateTracks', 0)} 条重复记录。确认后加 --confirm 执行合并。"
            ))
            return 0
        groups = [{"keepTrackId": group["keepTrackId"], "duplicateTrackIds": group["duplicateTrackIds"]} for group in result["groups"]]
        if groups:
            result["mergeResult"] = client.consolidate_music_tracks(groups)
        result["tracks"] = client.list_music_tracks()
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else (
            f"本地内容去重已完成：合并 {len(groups)} 组，删除 {result.get('duplicateTracks', 0)} 条重复记录，"
            f"音乐库剩余 {len(result['tracks'])} 条。"
        ))
        return 0
    if not args.build:
        tracks = client.list_music_tracks()
        if args.json:
            print(json.dumps({"tracks": tracks}, ensure_ascii=False, indent=2))
        else:
            for track in tracks:
                linked = track.get("linkedAssetCount", 0)
                label = track.get("title") or track.get("name") or "待标记音乐"
                print(f"[{track.get('status', '待标记')}] {label} | {track.get('artist') or '艺人待标记'} | {linked} 个来源视频")
            if not tracks:
                print("音乐库为空。使用 music-library --build --confirm 提取全部视频音轨。")
        return 0
    if not args.confirm:
        print("构建音乐库会下载视频、写入音频对象和数据库关系。预览模式未执行；确认后加 --confirm。")
        return 0
    result = build_local_music_library(client, args)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else (
        f"音乐库已更新：视频 {result.get('totalVideos', 0)} 个，处理 {result.get('processed', 0)} 个，"
        f"复用 {result.get('reused', 0)} 个，无音轨 {result.get('noAudio', 0)} 个，失败 {result.get('failed', 0)} 个。"
    ))
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect and safely organize AIGC Shelf assets")
    parser.add_argument("command", choices=("inspect", "apply", "music-library"))
    parser.add_argument("--asset-id", action="append", default=[])
    parser.add_argument("--tab", choices=(*FOLDERS, *TAB_ALIASES))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--music", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--download-dir")
    parser.add_argument("--plan")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--build", action="store_true", help="构建或重新扫描音乐库")
    parser.add_argument("--dedupe-content", action="store_true", help="按本地感知音频指纹合并相同音乐的不同片段")
    parser.add_argument("--ffmpeg", help="本地 ffmpeg 可执行文件路径")
    parser.add_argument("--ffprobe", help="本地 ffprobe 可执行文件路径")
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API))
    parser.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", str(DEFAULT_COOKIE_FILE)))
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "apply" and not args.plan:
        raise SystemExit("apply 必须指定 --plan PATH")
    client = Client(args.api, args.cookie_file)
    if args.command == "inspect":
        return inspect_command(client, args)
    if args.command == "apply":
        return apply_command(client, args)
    return music_library_command(client, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
