#!/usr/bin/env python3
"""Fetch an overlay GIF from the AIGC Shelf GIF 素材 library.

The workbench stores overlay assets (GIF / 贴纸) in its own COS bucket. This
helper resolves an asset id to a local file so `apply_overlay.py` can consume
it directly, without the user re-downloading it from the browser.

Examples:
    # list everything in GIF 素材
    python3 fetch_overlay.py --list

    # download one asset and print the local path
    python3 fetch_overlay.py --asset-id 0f2c...  -> /Users/x/.config/aigc-workbench/gif/心形贴纸-胸口位置.gif
"""

import argparse
import getpass
import http.client
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_API = "https://aigc.chatcanvas.online"
DEFAULT_COOKIE_FILE = Path.home() / ".codex" / "aigc-shelf-session.json"
DEFAULT_CACHE_DIR = Path.home() / ".config" / "aigc-workbench" / "gif"
OVERLAY_FOLDER = "GIF 素材"


def parse_json(payload):
    try:
        return json.loads(payload.decode("utf-8")) if payload else None
    except json.JSONDecodeError:
        return {"raw": payload[:500].decode("utf-8", "replace")}


class Client:
    """Same session handling as the other AIGC Shelf skills: cookie file + login fallback."""

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

    def _connect(self):
        """返回 (连接, 代理请求头, 是否用绝对 URL)。

        走代理时，连接类和请求路径都按「代理的协议」决定：代理是 http:// 时要用
        明文连接和它握手，再把绝对 URL 交给它转发；按目标站点的协议选连接类会
        变成对着明文代理做 TLS 握手，直接 WRONG_VERSION_NUMBER。
        """
        proxy_url = os.environ.get("HTTPS_PROXY" if self.scheme == "https" else "HTTP_PROXY") or os.environ.get(
            "https_proxy" if self.scheme == "https" else "http_proxy"
        )
        if not proxy_url:
            connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
            default_port = 443 if self.scheme == "https" else 80
            return connection_class(self.host, self.port or default_port, timeout=900), None, False
        proxy = urlparse(proxy_url)
        connection_class = http.client.HTTPSConnection if proxy.scheme == "https" else http.client.HTTPConnection
        default_port = 443 if proxy.scheme == "https" else 80
        connection = connection_class(proxy.hostname, proxy.port or default_port, timeout=900)
        headers = {}
        if proxy.username and proxy.password:
            import base64
            credentials = f"{proxy.username}:{proxy.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Proxy-Authorization"] = f"Basic {encoded}"
        return connection, headers, True

    def request(self, method, path, body=None, headers=None):
        connection, proxy_headers, absolute = self._connect()
        request_headers = {"Accept": "application/json", **proxy_headers, **(headers or {})}
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        request_path = path if path.startswith("/") else f"/{path}"
        if absolute:
            target = f"{self.scheme}://{self.host}:{self.port or (443 if self.scheme == 'https' else 80)}{self.base_path}{request_path}"
        else:
            target = f"{self.base_path}{request_path}"
        connection.request(method, target, body=body, headers=request_headers)
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
            raise RuntimeError(f"登录失败 (HTTP {status}): {(payload or {}).get('error', '未知错误')}")
        return (payload or {}).get("user", {})

    def list_assets(self):
        status, payload = self.request("GET", "/api/assets")
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"读取素材失败 HTTP {status}"))
        return (payload or {}).get("assets", [])

    def download_url(self, asset_id):
        status, payload = self.request("GET", f"/api/assets/{asset_id}/download-url")
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"获取下载地址失败 HTTP {status}"))
        url = (payload or {}).get("url")
        if not url:
            raise RuntimeError("服务端没有返回下载地址")
        return url, (payload or {}).get("filename") or f"{asset_id}.gif"

    def download(self, url, destination):
        # 用 urllib 而不是裸 http.client：它会自动读 HTTPS_PROXY/HTTP_PROXY
        # 环境变量，下载 COS 直链时和请求工作台 API 走同一条网络路径。
        import urllib.request
        request = urllib.request.Request(url, headers={"Accept": "*/*", "User-Agent": "aigc-shelf-fetch-overlay/1.0"})
        with urllib.request.urlopen(request, timeout=900) as response:
            if response.status >= 400:
                raise RuntimeError(f"下载失败 HTTP {response.status}")
            with destination.open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
        return destination.stat().st_size


def safe_filename(name, fallback):
    cleaned = "".join("_" if character in '\\/:*?"<>|' else character for character in str(name or "")).strip()
    return (cleaned or fallback)[:120]


def find_asset(assets, asset_id):
    for asset in assets:
        if str(asset.get("id", "")).lower() == str(asset_id).lower():
            return asset
    return None


def parse_args():
    parser = argparse.ArgumentParser(description="从 AIGC Shelf 取 GIF 素材（GIF）到本地")
    parser.add_argument("--asset-id", default="", help="素材 ID；省略时与 --name 一起按名称匹配")
    parser.add_argument("--name", default="", help="按名称包含匹配素材；命中多条时报错")
    parser.add_argument("--list", action="store_true", help="列出 GIF 素材库里的全部素材")
    parser.add_argument("--output-dir", default=str(DEFAULT_CACHE_DIR), help="本地缓存目录")
    parser.add_argument("--force", action="store_true", help="已存在同名文件时重新下载")
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API))
    parser.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", str(DEFAULT_COOKIE_FILE)))
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.list and not args.asset_id and not args.name:
        raise SystemExit("需要 --list、--asset-id 或 --name 之一")
    client = Client(args.api, args.cookie_file)
    user = client.ensure_login()
    assets = client.list_assets()

    if args.list:
        overlays = [asset for asset in assets if asset.get("folder") == OVERLAY_FOLDER]
        if args.json:
            print(json.dumps([
                {
                    "id": asset.get("id"),
                    "name": asset.get("name"),
                    "contentType": asset.get("contentType"),
                    "size": asset.get("size"),
                    "tags": asset.get("tags"),
                }
                for asset in overlays
            ], ensure_ascii=False, indent=2))
        else:
            if not overlays:
                print(f"GIF 素材库还是空的（当前账号：{user.get('name') or user.get('email') or '未知'}）")
            for asset in overlays:
                marker = "GIF" if asset.get("contentType") == "image/gif" else asset.get("contentType") or "?"
                print(f"{asset.get('id')}  {asset.get('name')}  [{marker}]  {asset.get('size')}")
        return 0

    if args.asset_id:
        asset = find_asset(assets, args.asset_id)
        if not asset:
            raise SystemExit(f"找不到素材：{args.asset_id}")
    else:
        keyword = args.name.strip()
        matches = [asset for asset in assets if keyword in str(asset.get("name") or "")]
        if not matches:
            raise SystemExit(f"没有名称包含「{keyword}」的素材")
        if len(matches) > 1:
            print("命中多条素材，请用 --asset-id 指定：", file=sys.stderr)
            for asset in matches:
                print(f"  {asset.get('id')}  {asset.get('name')}", file=sys.stderr)
            raise SystemExit(1)
        asset = matches[0]

    if asset.get("contentType") != "image/gif":
        print(f"提示：该素材类型是 {asset.get('contentType') or '未知'}，不是 GIF", file=sys.stderr)

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(asset.get("name"), asset.get("id"))
    if not filename.lower().endswith(".gif"):
        filename = f"{filename}.gif"
    destination = output_dir / filename
    if destination.exists() and not args.force:
        size = destination.stat().st_size
        print(json.dumps({"path": str(destination), "size": size, "cached": True}, ensure_ascii=False) if args.json
              else str(destination))
        return 0

    url, _ = client.download_url(asset.get("id"))
    size = client.download(url, destination)
    if args.json:
        print(json.dumps({
            "path": str(destination),
            "size": size,
            "assetId": asset.get("id"),
            "name": asset.get("name"),
            "cached": False,
        }, ensure_ascii=False))
    else:
        print(str(destination))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("已取消")
