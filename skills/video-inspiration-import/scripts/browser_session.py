"""Read a locally authorized browser session without requesting platform data."""

from pathlib import Path
import subprocess
import tempfile
from urllib.parse import unquote


def parse_cookie_jar(text, domain_suffix=""):
    jar = {}
    wanted = domain_suffix.lstrip(".").lower()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, _path, _secure, _expires, name, value = parts[:7]
        bare = domain.lstrip(".").lower()
        if wanted and bare != wanted and not bare.endswith("." + wanted):
            continue
        jar[name] = unquote(value)
    return jar


def export_browser_cookies(cookies_from_browser, ytdlp, proxy=None, timeout=120,
                           expected_user_id=""):
    if not cookies_from_browser:
        raise ValueError("没有可用的浏览器登录态")
    with tempfile.TemporaryDirectory(prefix="ig-session-") as directory:
        path = Path(directory) / "cookies.txt"
        # yt-dlp writes the local cookie jar even though this loopback URL fails.
        # Ignore proxy environment/config so this never contacts an external server.
        command = [str(ytdlp), "--ignore-config", "--no-warnings", "--no-playlist",
                   "--skip-download", "--retries", "0", "--extractor-retries", "0",
                   "--proxy", "", "--cookies-from-browser", str(cookies_from_browser),
                   "--cookies", str(path), "--", "http://127.0.0.1:1/cookie-export"]
        try:
            subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("读取浏览器登录态超时") from error
        if not path.is_file():
            raise RuntimeError("读取浏览器登录态失败：yt-dlp 没有写出临时会话")
        text = path.read_text(encoding="utf-8", errors="replace")
    jar = parse_cookie_jar(text, "instagram.com")
    if not jar.get("sessionid"):
        raise RuntimeError("浏览器里没有 Instagram 会话，请先在所选 Chrome 档案登录")
    expected = str(expected_user_id or "").strip()
    if expected and jar.get("ds_user_id") != expected:
        raise RuntimeError("浏览器登录态不是预期的 Instagram 账号；请切换到已授权的账号")
    return text
