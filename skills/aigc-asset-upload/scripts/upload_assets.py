#!/usr/bin/env python3
"""Stream local image/video files into AIGC Shelf."""

import argparse
import getpass
import hashlib
import http.client
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
import uuid


DEFAULT_API = "https://aigc.chatcanvas.online"
DEFAULT_COOKIE_FILE = Path.home() / ".codex" / "aigc-shelf-session.json"
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_FOLDERS = [
    "灵感收集",
    "我的创作",
    "角色设定",
    "项目资料",
    "服装库",
    "发型库",
    "通用片段",
]


class MultipartBody:
    def __init__(self, file_path, mime_type, fields):
        self.file = file_path.open("rb")
        self.parts = []
        self.index = 0
        self.offset = 0
        self.boundary = f"----CodexAIGCShelf{uuid.uuid4().hex}"
        prefix = bytearray()
        for name, value in fields.items():
            prefix.extend(f"--{self.boundary}\r\n".encode())
            prefix.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            prefix.extend(str(value).encode())
            prefix.extend(b"\r\n")
        prefix.extend(f"--{self.boundary}\r\n".encode())
        prefix.extend(f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode())
        prefix.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
        self.parts = [bytes(prefix), self.file, f"\r\n--{self.boundary}--\r\n".encode()]
        self.length = len(prefix) + file_path.stat().st_size + len(self.parts[-1])

    @property
    def content_type(self):
        return f"multipart/form-data; boundary={self.boundary}"

    def read(self, size=-1):
        if self.index >= len(self.parts):
            return b""
        chunks = bytearray()
        target = size if size and size > 0 else 1024 * 1024
        while len(chunks) < target and self.index < len(self.parts):
            current = self.parts[self.index]
            if hasattr(current, "read"):
                chunk = current.read(target - len(chunks))
                if not chunk:
                    current.close()
                    self.index += 1
                    continue
            else:
                remaining = len(current) - self.offset
                take = min(remaining, target - len(chunks))
                chunk = current[self.offset:self.offset + take]
                self.offset += take
                if self.offset >= len(current):
                    self.index += 1
                    self.offset = 0
            chunks.extend(chunk)
        return bytes(chunks)


class Client:
    def __init__(self, base_url, cookie_file):
        parsed = urlparse(base_url.rstrip("/"))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("AIGC_SHELF_URL 必须是 HTTP(S) 地址")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port
        self.base_path = parsed.path.rstrip("/")
        self.cookie_file = Path(cookie_file).expanduser()
        self.cookies = self._load_cookies()

    def _load_cookies(self):
        try:
            data = json.loads(self.cookie_file.read_text())
            return {str(k): str(v) for k, v in data.items()}
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
        # Check for proxy configuration
        proxy_url = os.environ.get("HTTPS_PROXY" if self.scheme == "https" else "HTTP_PROXY") or os.environ.get("https_proxy" if self.scheme == "https" else "http_proxy")

        if proxy_url:
            proxy_parsed = urlparse(proxy_url)
            connection_class = http.client.HTTPSConnection if proxy_parsed.scheme == "https" else http.client.HTTPConnection
            connection = connection_class(proxy_parsed.hostname, proxy_parsed.port or 80, timeout=900)

            # Set up proxy authentication if present
            proxy_headers = {}
            if proxy_parsed.username and proxy_parsed.password:
                import base64
                credentials = f"{proxy_parsed.username}:{proxy_parsed.password}"
                encoded = base64.b64encode(credentials.encode()).decode()
                proxy_headers["Proxy-Authorization"] = f"Basic {encoded}"

            # For HTTPS through proxy, use CONNECT tunnel
            full_path = f"{self.scheme}://{self.host}:{self.port or (443 if self.scheme == 'https' else 80)}{self.base_path}{path}"
        else:
            connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
            connection = connection_class(self.host, self.port, timeout=900)
            full_path = f"{self.base_path}{path}"
            proxy_headers = {}

        request_headers = {"Accept": "application/json", **proxy_headers, **(headers or {})}
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        connection.request(method, full_path, body=body, headers=request_headers)
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
        try:
            parsed_payload = json.loads(payload.decode("utf-8")) if payload else None
        except json.JSONDecodeError:
            parsed_payload = {"raw": payload[:500].decode("utf-8", "replace")}
        return response.status, parsed_payload

    def ensure_login(self):
        status, payload = self.request("GET", "/api/auth/me")
        if status == 200:
            return payload.get("user", {})
        email = os.environ.get("AIGC_SHELF_EMAIL") or input("AIGC Shelf 登录邮箱: ").strip()
        password = os.environ.get("AIGC_SHELF_PASSWORD") or getpass.getpass("AIGC Shelf 登录密码: ")
        login_body = json.dumps({"email": email, "password": password}).encode()
        status, payload = self.request(
            "POST",
            "/api/auth/login",
            login_body,
            {"Content-Type": "application/json", "Content-Length": str(len(login_body))},
        )
        if status != 200:
            error_msg = (payload or {}).get("error", "AIGC Shelf 登录失败")
            raise RuntimeError(f"登录失败 (HTTP {status}): {error_msg}, 响应: {payload}")
        return payload.get("user", {})

    def find_existing(self, hashes):
        """Return existing assets keyed by content hash for the signed-in user."""
        hashes = list(dict.fromkeys(hashes))
        if not hashes:
            return {}
        existing = {}
        for offset in range(0, len(hashes), 500):
            body = json.dumps({"sha256s": hashes[offset:offset + 500]}).encode()
            status, payload = self.request(
                "POST",
                "/api/assets/check-hashes",
                body,
                {"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            if status != 200:
                raise RuntimeError((payload or {}).get("error", f"去重检查失败 HTTP {status}"))
            existing.update({
                str(asset.get("sha256", "")).lower(): asset
                for asset in (payload or {}).get("assets", [])
                if asset.get("sha256")
            })
        return existing

    def upload(self, file_path, metadata):
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if not (mime_type.startswith("video/") or mime_type.startswith("image/")):
            raise ValueError(f"不支持的文件类型：{file_path.name}")
        if file_path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"文件超过 2 GB 限制：{file_path.name}")
        fields = {
            "name": metadata.get("name", ""),
            "source": metadata.get("source", "本地导入"),
            "sourceUrl": metadata.get("sourceUrl", ""),
            "tags": json.dumps(metadata.get("tags", ["待整理"]), ensure_ascii=False),
            "used": str(bool(metadata.get("used", False))).lower(),
            "folder": metadata.get("folder", "灵感收集"),
            "characterName": metadata.get("characterName", ""),
            "characterCategory": metadata.get("characterCategory", ""),
        }
        if metadata.get("parentAssetIds"):
            fields["parentAssetIds"] = json.dumps(metadata["parentAssetIds"])
        body = MultipartBody(file_path, mime_type, fields)
        status, payload = self.request(
            "POST",
            "/api/assets",
            body,
            {"Content-Type": body.content_type, "Content-Length": str(body.length)},
        )
        if status != 201:
            raise RuntimeError((payload or {}).get("error", f"上传失败 HTTP {status}"))
        return payload.get("asset", {})


def parse_args():
    parser = argparse.ArgumentParser(description="Upload local AIGC assets to AIGC Shelf")
    parser.add_argument("files", nargs="+", type=Path, help="要上传的视频或图片路径")
    parser.add_argument("--name", default="", help="素材名称；多文件时自动追加原文件名")
    parser.add_argument("--name-map", default="", help="逐文件标题映射文件，每行 `路径|标题`，优先于 --name")
    parser.add_argument("--source", default="本地导入")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--tags", default="待整理", help="以空格或逗号分隔")
    parser.add_argument("--folder", default="灵感收集", choices=ALLOWED_FOLDERS, help="整批默认文件夹，可被 --manifest 逐文件覆盖")
    parser.add_argument("--manifest", default="", help="逐文件元数据 JSON：{items:[{path,folder,name,tags}]}，路径必须是绝对路径")
    parser.add_argument("--used", action="store_true")
    parser.add_argument("--character-name", default="")
    parser.add_argument("--character-category", default="")
    parser.add_argument("--parent-asset-id", action="append", dest="parent_asset_ids", default=[])
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API))
    parser.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", str(DEFAULT_COOKIE_FILE)))
    return parser.parse_args()


def split_tags(text):
    """标签按空格、逗号或中文逗号分隔。"""
    return [item for item in str(text).replace(",", " ").replace("，", " ").split() if item]


def load_manifest(manifest_path, files):
    """读取逐文件 manifest。

    返回 (plan, errors)：plan 是 {绝对路径: 元数据}，errors 是 {绝对路径: 原因}。
    非法的 manifest 条目只会跳过该条，不会让文件静默回落到 --folder。
    """
    path = Path(manifest_path).expanduser()
    if not path.is_file():
        raise SystemExit(f"manifest 文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"manifest 不是合法 JSON：{path}（{error}）")
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit("manifest 需要是 JSON 对象含 items 数组，或直接是数组")
    canonical_files = {os.path.abspath(os.path.expanduser(str(item))) for item in files}
    plan = {}
    errors = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            print(f"忽略：manifest 第 {index} 项不是对象", file=sys.stderr)
            continue
        raw_path = str(item.get("path", "")).strip()
        if not raw_path:
            print(f"忽略：manifest 第 {index} 项缺少 path", file=sys.stderr)
            continue
        if not os.path.isabs(raw_path):
            print(f"忽略：manifest 第 {index} 项 path 必须是绝对路径：{raw_path}", file=sys.stderr)
            continue
        key = os.path.abspath(os.path.expanduser(os.path.normpath(raw_path)))
        if key not in canonical_files:
            print(f"忽略：manifest 第 {index} 项 path 不在本次待上传文件列表：{key}", file=sys.stderr)
            continue
        entry = {}
        if item.get("folder") is not None:
            folder = str(item["folder"]).strip()
            if folder not in ALLOWED_FOLDERS:
                errors[key] = f"manifest 指定的 folder 非法：{folder}（可选：{'、'.join(ALLOWED_FOLDERS)}）"
                print(f"失败：{key}：{errors[key]}", file=sys.stderr)
                continue
            entry["folder"] = folder
        tags = item.get("tags")
        if isinstance(tags, str):
            tags = split_tags(tags)
        elif isinstance(tags, list):
            tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        elif tags is None:
            tags = None
        else:
            errors[key] = "manifest 的 tags 必须是数组或空格分隔的字符串"
            print(f"失败：{key}：{errors[key]}", file=sys.stderr)
            continue
        if tags is not None:
            entry["tags"] = tags
        for key_name in ("name", "source", "sourceUrl", "characterName", "characterCategory"):
            value = str(item.get(key_name, "") or "").strip()
            if value:
                entry[key_name] = value
        if key in plan:
            print(f"提示：manifest 中 {key} 出现多次，以最后一条为准", file=sys.stderr)
        plan[key] = entry
    return plan, errors


def load_name_map(map_path):
    """读取 `路径|标题` 映射文件；路径统一为绝对路径，供逐文件自定义标题。"""
    mapping = {}
    for lineno, line in enumerate(Path(map_path).expanduser().read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            print(f"忽略：名称映射第 {lineno} 行缺少 | 分隔：{line}", file=sys.stderr)
            continue
        path_text, name = line.split("|", 1)
        path_text, name = path_text.strip(), name.strip()
        if not path_text or not name:
            print(f"忽略：名称映射第 {lineno} 行路径或标题为空", file=sys.stderr)
            continue
        mapping[os.path.abspath(os.path.expanduser(path_text))] = name
    return mapping


def resolve_title(file_path, name_map, base_name, total):
    """标题优先级：映射文件 > --name > 原文件名。"""
    map_name = name_map.get(os.path.abspath(os.path.expanduser(str(file_path))))
    if map_name:
        return map_name
    if base_name:
        return f"{base_name} · {file_path.stem}" if total > 1 else base_name
    return file_path.stem


def file_sha256(file_path):
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    args = parse_args()
    files = []
    for path in args.files:
        if not path.is_file():
            print(f"跳过：文件不存在 {path}", file=sys.stderr)
            continue
        files.append(path)
    if not files:
        raise SystemExit("没有可上传的文件")
    name_map = load_name_map(args.name_map) if args.name_map else {}
    if name_map:
        canonical_files = {os.path.abspath(os.path.expanduser(str(p))) for p in files}
        for path_text in sorted(set(name_map) - canonical_files):
            print(f"提示：名称映射中的路径不在待上传列表：{path_text}", file=sys.stderr)
    manifest_plan, manifest_errors = load_manifest(args.manifest, files) if args.manifest else ({}, {})
    client = Client(args.api, args.cookie_file)
    user = client.ensure_login()
    print(f"已登录：{user.get('name') or user.get('email') or '当前账号'}")
    tags = split_tags(args.tags)
    failures = 0
    file_hashes = {}
    hash_failures = {}
    for file_path in files:
        try:
            file_hashes[file_path] = file_sha256(file_path)
        except Exception as error:
            hash_failures[file_path] = error
    try:
        existing_assets = client.find_existing(file_hashes.values())
    except Exception as error:
        print(f"去重检查失败，已停止上传：{error}", file=sys.stderr)
        return 1
    seen_hashes = {}
    for index, file_path in enumerate(files, 1):
        # manifest 的键是绝对路径字符串，file_path 是 Path，必须先转成同样的键再查表
        manifest_key = os.path.abspath(os.path.expanduser(str(file_path)))
        if manifest_key in manifest_errors:
            failures += 1
            print(f"[{index}/{len(files)}] 失败：{file_path.name}：{manifest_errors[manifest_key]}", file=sys.stderr)
            continue
        if file_path in hash_failures:
            failures += 1
            print(f"[{index}/{len(files)}] 失败：{file_path.name}：无法计算文件哈希：{hash_failures[file_path]}", file=sys.stderr)
            continue
        digest = file_hashes[file_path]
        duplicate = existing_assets.get(digest) or seen_hashes.get(digest)
        if duplicate:
            duplicate_name = duplicate.get("name") or "未命名素材"
            duplicate_id = duplicate.get("id") or "未知 ID"
            origin = "已存在素材" if digest in existing_assets else "本批次已上传"
            print(f"[{index}/{len(files)}] 跳过：{file_path.name} -> {origin}「{duplicate_name}」({duplicate_id})")
            continue
        # 逐文件元数据优先级：manifest > --name-map > --name/--tags/--folder
        entry = manifest_plan.get(manifest_key, {})
        # 服务端在 name 为空时回退用 multipart 文件名，而 multer 按 latin1 解码
        # 文件名 header，中文文件名会变乱码；显式传表单字段 name 避免。
        name = entry.get("name") or resolve_title(file_path, name_map, args.name, len(files))
        file_tags = entry.get("tags") or tags or ["待整理"]
        metadata = {
            "name": name,
            "source": entry.get("source") or args.source,
            "sourceUrl": entry.get("sourceUrl") or args.source_url,
            "tags": file_tags,
            "used": args.used,
            "folder": entry.get("folder") or args.folder,
            "characterName": entry.get("characterName") or args.character_name,
            "characterCategory": entry.get("characterCategory") or args.character_category,
            "parentAssetIds": args.parent_asset_ids,
        }
        try:
            asset = client.upload(file_path, metadata)
            seen_hashes[digest] = asset
            print(f"[{index}/{len(files)}] 成功：{file_path.name} -> {asset.get('name')} ({asset.get('id')})")
        except Exception as error:
            failures += 1
            print(f"[{index}/{len(files)}] 失败：{file_path.name}：{error}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("已取消")
