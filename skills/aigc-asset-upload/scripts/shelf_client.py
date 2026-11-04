"""Shared standard-library transport and runtime configuration for Shelf skills."""

import getpass
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener


DEFAULT_API = "https://aigc.chatcanvas.online"
LEGACY_FOLDERS = ["灵感收集", "我的创作", "角色设定", "项目资料", "服装库", "发型库", "通用片段", "GIF 素材"]


def state_dir():
    return Path(os.environ.get("AIGC_WORKBENCH_STATE_DIR") or
                str(Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "aigc-workbench")).expanduser()


def runtime_roots():
    configured = [Path(os.environ[key]).expanduser() for key in
                  ("MINIMAX_DATA_DIR", "MINIMAX_HOME", "CODEX_HOME") if os.environ.get(key)]
    return configured or [Path.home() / ".minimax", Path.home() / ".codex"]


def tool_dirs():
    directories = [state_dir() / "venv/bin", state_dir() / "bin"]
    if os.environ.get("AIGC_WORKBENCH_BIN_DIR"):
        directories.insert(0, Path(os.environ["AIGC_WORKBENCH_BIN_DIR"]).expanduser())
    data_dir = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    directories += [data_dir / "aigc-workbench/venv/bin", data_dir / "aigc-workbench/bin"]
    directories += [root / suffix for root in runtime_roots()
                    for suffix in ("tools", "tools/bin", "bin", "video-inspiration-venv/bin")]
    return directories


def default_cookie_file():
    if os.environ.get("AIGC_SHELF_COOKIE_FILE"):
        return Path(os.environ["AIGC_SHELF_COOKIE_FILE"]).expanduser()
    preferred = state_dir() / "aigc-shelf-session.json"
    if preferred.exists() or os.environ.get("AIGC_WORKBENCH_STATE_DIR"):
        return preferred
    legacy = [root / "aigc-shelf-session.json" for root in runtime_roots()]
    existing = [path for path in legacy if path.is_file()]
    if len(existing) > 1:
        return preferred
    return existing[0] if existing else preferred


class APIError(RuntimeError):
    def __init__(self, status, payload, headers=None):
        self.status = status
        self.body = payload
        self.headers = dict(headers or {})
        message = payload.get("error", "API 请求失败") if isinstance(payload, dict) else "API 请求失败"
        fields = payload.get("fields", []) if isinstance(payload, dict) else []
        detail = "; ".join(f"{field.get('field')}: {field.get('message')}" for field in fields)
        super().__init__(f"HTTP {status}: {message}" + (f" ({detail})" if detail else ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, cookie_file=None):
        parsed = urlparse(base_url.rstrip("/"))
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("AIGC_SHELF_URL 必须是 HTTP(S) 地址")
        self.base_url = base_url.rstrip("/")
        self.scheme, self.host, self.port = parsed.scheme, parsed.hostname, parsed.port
        self.base_path = parsed.path.rstrip("/")
        self.origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        self.cookie_file = Path(cookie_file or default_cookie_file()).expanduser()
        self.expires_at = None
        self.cookies = self._load_cookies()
        self.last_response = {}
        # 显式禁用环境代理：本机代理会让 API 与 COS 直连请求时通时断。
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _load_cookies(self):
        try:
            data = json.loads(self.cookie_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise ValueError(f"会话文件无法解析或读取：{self.cookie_file}，请重新登录") from error
        if not isinstance(data, dict):
            raise ValueError("会话文件必须是 JSON 对象")
        if "cookies" in data:
            if data.get("origin") != self.base_url:
                raise ValueError("会话所属服务与当前 API 不同，请指定该服务的会话文件")
            self.expires_at = data.get("expiresAt")
            if self.expires_at is not None:
                if not isinstance(self.expires_at, (int, float)):
                    raise ValueError("会话 expiresAt 必须是 Unix 时间戳")
                if self.expires_at <= time.time():
                    return {}
            data = data["cookies"]
        if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
            raise ValueError("会话 cookies 必须是字符串键值对象")
        return data

    def _save_cookies(self):
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".session-", dir=self.cookie_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"cookies": self.cookies, "origin": self.base_url,
                           "savedAt": time.time(), "expiresAt": self.expires_at}, handle)
            os.replace(temporary, self.cookie_file)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _capture_cookies(self, headers):
        changed = False
        for value in headers.get_all("Set-Cookie", []):
            cookie = SimpleCookie()
            cookie.load(value)
            for name, item in cookie.items():
                changed = True
                if item.value and item["max-age"] != "0":
                    self.cookies[name] = item.value
                else:
                    self.cookies.pop(name, None)
                if item["max-age"]:
                    self.expires_at = time.time() + int(item["max-age"])
                elif item["expires"]:
                    self.expires_at = parsedate_to_datetime(item["expires"]).timestamp()
        if changed:
            self._save_cookies()

    def _open(self, method, url, body=None, headers=None):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise ValueError("下载地址必须是 HTTP(S)")
        origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        request_headers = dict(headers or {})
        if origin == self.origin and self.cookies:
            request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        try:
            response = self.opener.open(Request(url, data=body, headers=request_headers, method=method), timeout=900)
        except HTTPError as error:
            response = error
        if origin == self.origin:
            self._capture_cookies(response.headers)
        return response

    def request(self, method, path, body=None, headers=None):
        url = self.base_url + "/" + path.lstrip("/")
        with self._open(method, url, body, {"Accept": "application/json", **(headers or {})}) as response:
            raw = response.read()
            status = response.status
            safe_headers = {key: value for key, value in response.headers.items() if key.lower() != "set-cookie"}
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeError):
                payload = {"raw": raw.decode("utf-8", "replace")}
            if status in (301, 302, 303, 307, 308):
                payload = {"url": urljoin(url, response.headers.get("Location", "")),
                           "redirect": True, "status": status}
            self.last_response = {"status": status, "body": raw, "headers": safe_headers}
            return status, payload

    def require(self, method, path, body=None, headers=None, expected=200):
        status, payload = self.request(method, path, body, headers)
        if status != expected:
            raise APIError(status, payload, self.last_response.get("headers"))
        return payload

    def ensure_login(self):
        status, payload = self.request("GET", "/api/auth/me")
        if status == 200:
            return payload.get("user", {})
        if status != 401:
            raise APIError(status, payload, self.last_response.get("headers"))
        email = os.environ.get("AIGC_SHELF_EMAIL") or input("AIGC Shelf 登录邮箱: ").strip()
        password = os.environ.get("AIGC_SHELF_PASSWORD") or getpass.getpass("AIGC Shelf 登录密码: ")
        body = json.dumps({"email": email, "password": password}).encode()
        return self.require("POST", "/api/auth/login", body, {"Content-Type": "application/json"}).get("user", {})

    def list_assets(self, **filters):
        assets, offset = [], 0
        while True:
            payload = self.require("GET", "/api/assets?" + urlencode({**filters, "limit": 200, "offset": offset}))
            page = payload.get("assets", [])
            assets.extend(page)
            if not payload.get("hasMore"):
                return assets
            if not page:
                raise RuntimeError("分页响应为空但 hasMore=true")
            offset += len(page)

    def metadata_policy(self):
        if not hasattr(self, "_policy"):
            status, payload = self.request("GET", "/api/assets/metadata-policy")
            if status == 200:
                self._policy = payload
            elif status == 404 and "Cannot GET" in payload.get("raw", ""):
                self._policy = {"folders": LEGACY_FOLDERS, "folderAliases": {"成片": "我的创作"}}
            else:
                raise APIError(status, payload, self.last_response.get("headers"))
        return self._policy

    def get_asset(self, asset_id):
        status, payload = self.request("GET", f"/api/assets/{asset_id}")
        if status == 200:
            return payload["asset"]
        # Only old deployments without the route get a list fallback.
        if status == 404 and isinstance(payload, dict) and "Cannot GET" in payload.get("raw", ""):
            for asset in self.list_assets(ids=asset_id):
                if asset.get("id") == asset_id:
                    return asset
        raise APIError(status, payload, self.last_response.get("headers"))

    def download(self, url, destination):
        url = urljoin(self.base_url + "/", url)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(6):
            with self._open("GET", url) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("下载重定向缺少 Location")
                    target = urljoin(url, location)
                    if urlparse(url).scheme == "https" and urlparse(target).scheme != "https":
                        raise RuntimeError("拒绝 HTTPS 降级重定向")
                    url = target
                    continue
                if response.status != 200:
                    raise APIError(response.status, {"error": "下载失败"})
                fd, temporary = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
                try:
                    count = 0
                    with os.fdopen(fd, "wb") as handle:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            count += len(chunk)
                    expected = response.headers.get("Content-Length")
                    if not count or (expected is not None and count != int(expected)):
                        raise RuntimeError("下载不完整")
                    os.replace(temporary, destination)
                    return count
                finally:
                    Path(temporary).unlink(missing_ok=True)
        raise RuntimeError("下载重定向次数超过上限")


def validate_metadata(metadata):
    if "name" in metadata:
        name = metadata["name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 32 or any(c in name for c in "·."):
            raise ValueError("标题应为 1–32 字，不能含 · 或 .")
    if "tags" in metadata:
        tags = metadata["tags"]
        if (not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip()
                or any(c.isspace() or c in ",，" for c in t) for t in tags)
                or not 3 <= len(set(tags)) <= 5):
            raise ValueError("标签须有 3–5 个不同的非空字符串")
