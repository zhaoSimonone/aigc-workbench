#!/usr/bin/env python3
"""Stream local image/video files into AIGC Shelf."""

import argparse
import getpass
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
        connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.host, self.port, timeout=900)
        request_headers = {"Accept": "application/json", **(headers or {})}
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        connection.request(method, f"{self.base_path}{path}", body=body, headers=request_headers)
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
            raise RuntimeError((payload or {}).get("error", "AIGC Shelf 登录失败"))
        return payload.get("user", {})

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
    parser.add_argument("--source", default="本地导入")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--tags", default="待整理", help="以空格或逗号分隔")
    parser.add_argument("--folder", default="灵感收集", choices=["灵感收集", "我的创作", "角色设定", "项目资料"])
    parser.add_argument("--used", action="store_true")
    parser.add_argument("--character-name", default="")
    parser.add_argument("--character-category", default="")
    parser.add_argument("--parent-asset-id", action="append", dest="parent_asset_ids", default=[])
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API))
    parser.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", str(DEFAULT_COOKIE_FILE)))
    return parser.parse_args()


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
    client = Client(args.api, args.cookie_file)
    user = client.ensure_login()
    print(f"已登录：{user.get('name') or user.get('email') or '当前账号'}")
    tags = [item for item in args.tags.replace(",", " ").replace("，", " ").split() if item]
    failures = 0
    for index, file_path in enumerate(files, 1):
        name = args.name
        if name and len(files) > 1:
            name = f"{name} · {file_path.stem}"
        metadata = {
            "name": name,
            "source": args.source,
            "sourceUrl": args.source_url,
            "tags": tags or ["待整理"],
            "used": args.used,
            "folder": args.folder,
            "characterName": args.character_name,
            "characterCategory": args.character_category,
            "parentAssetIds": args.parent_asset_ids,
        }
        try:
            asset = client.upload(file_path, metadata)
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
