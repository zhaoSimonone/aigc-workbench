"""平台社交计数采集。

Instagram 走移动端 REST `media/{pk}/info/`：必须用两套 app-id 合并才有完整数据
（`567310203415052` 才带收藏/分享/播放，`936619743392459` 才带转发），
Web 端 GraphQL 根本不返回收藏数。TikTok 直接复用 yt-dlp 已经取到的计数字段，
不额外发请求。

计数字段固定为 like/comment/repost/share/save/play；
值为 null 表示平台没有下发该指标（例如 TikTok 不区分转发与分享），不等于 0。
"""

import contextlib
import json
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

METRIC_KEYS = ("like", "comment", "repost", "share", "save", "play")

# 每个计数字段对应接口里的字段名。不同 app-id / host 返回的字段不一样，
# 所以按来源顺序合并，缺的那项由下一个来源补上。
METRIC_FIELDS = (
    ("like", "like_count"),
    ("comment", "comment_count"),
    ("repost", "media_repost_count"),
    ("share", "reshare_count"),
    ("save", "save_count"),
    ("play", "play_count"),
)


class AccountChallengeError(RuntimeError):
    """Instagram 要求账号完成验证。

    账号被要求 challenge 时，继续请求只会把风控打得更死，调用方必须整批停下来，
    先在浏览器里把验证做完，而不是把它当成一条普通失败继续跑下一条。
    """

IG_APP_ID_FULL = "567310203415052"
IG_APP_ID_REPOST = "936619743392459"
IG_UA_MOBILE = ("Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; "
                "Google/google; Pixel 7; panther; panther; en_US; 458229257)")
IG_UA_WEB = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
IG_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

SOURCE_INSTAGRAM = "instagram-mobile-info"
SOURCE_TIKTOK = "tiktok-ytdlp"


def shortcode_to_pk(shortcode):
    """Instagram 短代码转数字 media id（与 yt-dlp 的 Instagram 提取器同一套编码）。"""
    code = str(shortcode or "").strip()
    if len(code) > 28:
        code = code[:-28]
    if not code:
        raise ValueError("Instagram 短代码为空")
    result = 0
    for char in code:
        index = IG_BASE64_ALPHABET.find(char)
        if index < 0:
            raise ValueError(f"无法解析 Instagram 短代码：{shortcode}")
        result = result * 64 + index
    return result


def parse_cookie_jar(text, domain_suffix=""):
    """解析 Netscape cookie 文件，只保留指定域名的 cookie，值按 URL 编码还原。

    必须按域名过滤：把别的站点的 Cookie 一起发给 Instagram 会被直接拒绝。
    """
    jar = {}
    wanted = domain_suffix.lstrip(".").lower()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if not line.startswith("#HttpOnly_"):
                continue
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, _path, _secure, _expires, name, value = parts[:7]
        if wanted:
            bare = domain.lstrip(".").lower()
            if bare != wanted and not bare.endswith("." + wanted):
                continue
        jar[name] = urllib.parse.unquote(value)
    return jar


def export_browser_cookies(cookies_from_browser, ytdlp, proxy=None, timeout=120,
                           expected_user_id=""):
    """用 yt-dlp 把浏览器登录态导出成 Netscape cookie 文本。

    指向一个必然失败的地址即可：yt-dlp 在退出时仍会把浏览器 cookie 落盘，
    因此这一步不需要任何外网请求，也不会把 cookie 内容打印出来。
    返回文本而不是字典，因为调用方要按域名取用。

    expected_user_id 给定时会校验 Instagram 账号（ds_user_id）。浏览器里可能
    同时存着多个 Instagram 账号的登录态，一旦在同一个 profile 里切了账号，
    不校验就会拿主账号去采集，等于用主账号的频率去请求平台。
    """
    if not cookies_from_browser:
        raise ValueError("没有可用的浏览器登录态")
    with _temp_cookie_file() as path:
        command = [str(ytdlp), "--ignore-config", "--no-warnings", "--no-playlist",
                   "--skip-download", "--cookies-from-browser", str(cookies_from_browser),
                   "--cookies", str(path)]
        if proxy:
            command += ["--proxy", str(proxy)]
        command += ["http://127.0.0.1:1/cookie-export"]
        try:
            subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("导出浏览器登录态超时") from error
        if not path.is_file():
            raise RuntimeError("导出浏览器登录态失败：yt-dlp 没有写出 cookie 文件")
        text = path.read_text(encoding="utf-8", errors="replace")
    jar = parse_cookie_jar(text, "instagram.com")
    if "sessionid" not in jar:
        raise RuntimeError("浏览器登录态里没有 Instagram 会话 Cookie，请先在该浏览器登录 Instagram")
    expected = str(expected_user_id or "").strip()
    if expected:
        actual = str(jar.get("ds_user_id", "") or "")
        if actual != expected:
            raise RuntimeError(
                f"浏览器登录态不是预期账号（ds_user_id={actual or '缺失'}，期望 {expected}）；"
                "请先在浏览器里切换到正确的 Instagram 账号再采集")
    return text


@contextlib.contextmanager
def _temp_cookie_file():
    """临时 cookie 文件上下文；退出时清理，不留在磁盘上。"""
    with tempfile.TemporaryDirectory(prefix="ig-cookies-") as directory:
        yield Path(directory) / "cookies.txt"


def _http_get_json(url, headers, proxy=None, timeout=40):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body)


def _instagram_info(pk, app_id, user_agent, cookies, referer, host="i.instagram.com", proxy=None, timeout=40):
    headers = {
        "User-Agent": user_agent,
        "accept": "*/*",
        "x-ig-app-id": app_id,
        "x-csrftoken": cookies.get("csrftoken", ""),
        "referer": referer,
        "Cookie": "; ".join(f"{name}={value}" for name, value in cookies.items()),
    }
    payload = _http_get_json(f"https://{host}/api/v1/media/{pk}/info/", headers, proxy, timeout)
    items = payload.get("items")
    return items[0] if isinstance(items, list) and items else payload


def metrics_from_sources(sources):
    """从多个响应来源里取计数字段，第一个非空的值优先。

    REST 通道和浏览器内同源请求返回的字段名一致，所以两边共用这一份合并逻辑。
    """
    def pick(key):
        for source in sources:
            value = source.get(key)
            if value is not None:
                return int(value)
        return None

    return {name: pick(field) for name, field in METRIC_FIELDS}


def collect_instagram(shortcode, cookies, proxy=None, timeout=40):
    """采集 Instagram 帖子的点赞/评论/转发/分享/收藏/播放。"""
    pk = shortcode_to_pk(shortcode)
    referer = f"https://www.instagram.com/p/{shortcode}/"
    sources = []
    for app_id, user_agent, host in (
        (IG_APP_ID_FULL, IG_UA_MOBILE, "i.instagram.com"),
        (IG_APP_ID_REPOST, IG_UA_MOBILE, "i.instagram.com"),
        (IG_APP_ID_REPOST, IG_UA_WEB, "www.instagram.com"),
    ):
        try:
            sources.append(_instagram_info(pk, app_id, user_agent, cookies, referer, host, proxy, timeout))
        except urllib.error.HTTPError as error:
            # HTTPError 是 URLError 的子类，必须先接住，否则会被下面的兜底吞掉。
            body = ""
            try:
                body = error.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 读不出响应体不影响判断
                body = ""
            # challenge_required：账号被要求验证；checkpoint_required：会话被要求过检查点。
            # 两者都不是单条视频的问题，继续请求只会加重风控，必须整批停下。
            for marker in ("challenge_required", "checkpoint_required"):
                if marker in body:
                    raise AccountChallengeError(
                        f"Instagram 返回 {marker}，账号或会话被风控；"
                        "请立刻停止采集，先在浏览器里正常使用该账号并处理验证，再低频重试") from error
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
    if not sources:
        raise RuntimeError(f"Instagram 计数接口没有返回数据：{shortcode}")

    return metrics_from_sources(sources)


def collect_tiktok(info):
    """从 yt-dlp 的 TikTok info 字典取计数，不额外发请求。

    yt-dlp 把 TikTok 的 shareCount 命名为 repost_count、collectCount 命名为 save_count、
    playCount 命名为 view_count。TikTok 不区分转发与分享，因此 repost 记为 null。
    """
    info = info or {}

    def pick(*keys):
        for key in keys:
            value = info.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return None

    return {
        "like": pick("like_count"),
        "comment": pick("comment_count"),
        "repost": None,
        "share": pick("repost_count"),
        "save": pick("save_count"),
        "play": pick("view_count", "play_count"),
    }


def build_snapshot(platform, counts, source, captured_at=None):
    """把采集结果整理成写库的社交计数快照。"""
    snapshot = {
        "platform": str(platform or "").strip()[:40],
        "capturedAt": (captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))[:40],
        "source": str(source or "").strip()[:120],
    }
    for key in METRIC_KEYS:
        value = (counts or {}).get(key)
        snapshot[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return snapshot


def has_metrics(snapshot):
    """判断快照里是否至少有一个真实计数。"""
    return any(isinstance((snapshot or {}).get(key), int) for key in METRIC_KEYS)
