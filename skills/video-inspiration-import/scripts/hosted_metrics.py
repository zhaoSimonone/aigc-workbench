#!/usr/bin/env python3
"""托管采集通道：把取数动作交给第三方 Instagram 数据 API。

为什么需要这条通道
------------------
本机自建的两条通道（`social_metrics.py` 的移动端 REST、`browser_metrics.py` 的
浏览器内同源请求）都必须携带**某个账号的登录态**，因此：

- 请求特征和账号信誉绑在一起，密集使用会伤账号；
- 创作者主页带年龄门槛时，未登录侧四条匿名路线同时失效，本地拿不到任何内容。

托管 API 把「账号池、住宅代理、challenge 处理、重试」放在对方基础设施上，
本机只发一次普通 HTTPS 请求，不携带任何 Instagram Cookie。代价是要备案 key，
并且对方返回的字段名与移动端 REST 不完全一致，需要归一化。

字段归一化
----------
本模块只产出 `social_metrics.METRIC_KEYS` 那六个键（like/comment/repost/share/save/play），
方便直接喂给 `metrics_from_sources()`，不用改上传链路。

注意 `play` 的口径：Instagram 网页端把播放量四舍五入成「21.8万」，托管 API 一般给
原始整数，所以同一批视频用 API 排序比用网页读数更准。

失败语义
--------
- 401/403：key 无效或额度耗尽 → `HostedAuthError`，不要重试。
- 429：被限流 → `HostedRateLimitError`，**不自动重试**。连续重试正是把限流打死的做法。
- 其余 4xx/5xx：`HostedRequestError`。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

METRIC_KEYS = ("like", "comment", "repost", "share", "save", "play")

# 托管 API 各家的字段名不一致，且同一家内部不同端点也不一致，所以每个指标给一组候选。
# 点号表示嵌套路径，例如 edge_media_to_comment.count。
METRIC_ALIASES = {
    "like": ("like_count", "likes", "like_count_exact", "edge_liked_by.count",
             "edge_media_preview_like.count"),
    "comment": ("comment_count", "comments", "edge_media_to_comment.count",
                "edge_media_to_parent_comment.count"),
    "repost": ("media_repost_count", "repost_count", "reshare_count_repost"),
    "share": ("reshare_count", "share_count", "shares"),
    "save": ("save_count", "saved_count", "saves"),
    "play": ("play_count", "view_count", "video_view_count", "videoViewCount",
             "ig_play_count", "plays"),
}

DEFAULT_API = "https://api.hikerapi.com"
DEFAULT_KEY_ENV = "HIKERAPI_KEY"
SOURCE_HOSTED = "hosted-api"

# 端点按官方 OpenAPI（https://api.hikerapi.com/openapi.json）核对过：
# - /v2/user/by/username 取账号资料，返回 User 对象（含 pk）
# - /v2/user/clips 取 reels，**只接受 user_id + page_id，不接受 username**
# 返回体是 {"response": {"items": [...], "paging_info": {...}}, "next_page_id": "..."}，
# 所以解析要往 response.items 里钻一层。
DEFAULT_SCAVIO_API = "https://api.scavio.dev"
PATH_PROFILE = "/v2/user/by/username"
PATH_CLIPS = "/v2/user/clips"

# 分页响应的外层可能是 {"response": {...}}、{"items": [...]} 或直接一个数组。
_REELS_CONTAINER_KEYS = ("items", "response_items", "clips", "reels", "media", "data")


class HostedError(RuntimeError):
    """托管通道的基类异常。"""


class HostedAuthError(HostedError):
    """key 无效、额度耗尽或权限不足。不要重试。"""


class HostedRateLimitError(HostedError):
    """被对方限流。不要自动重试，交给调用方决定。"""


class HostedInsufficientFundsError(HostedError):
    """账户余额为 0（HTTP 402）。key 本身可能是对的，只是没有可用额度。

    HikerAPI 的免费额度不会自动到账：需要在官网 /billing 页按横幅提示
    确认 Telegram ID 才解锁。遇到 402 不要换 key 重试，先去确认额度状态。
    """


class HostedRequestError(HostedError):
    """其它非预期响应。"""


def _deep_get(payload, path):
    """按点号路径取值；任一层缺失或不是字典就返回 None。"""
    current = payload
    for part in str(path).split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def normalize_counts(item):
    """把一条媒体记录归一化成六个计数字段。

    值统一成 int；取不到就是 None（平台没下发，不等于 0）。
    """
    if not isinstance(item, dict):
        return {key: None for key in METRIC_KEYS}

    counts = {}
    for key in METRIC_KEYS:
        value = None
        for alias in METRIC_ALIASES[key]:
            raw = _deep_get(item, alias)
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            break
        counts[key] = value
    return counts


def extract_video_urls(item):
    """从一条媒体记录里收集可下载的视频直链。

    Instagram 的 CDN 直链（scontent 域）不校验账号，所以拿到 URL 后本机可以直接下载，
    不需要再走 www.instagram.com。
    """
    if not isinstance(item, dict):
        return []

    urls = []
    for key in ("video_url", "videoUrl", "video_dash_manifest_playlist"):
        value = item.get(key)
        # dash manifest 是播放列表而不是文件，不能当直链下载，跳过。
        if isinstance(value, str) and value.startswith("http") and "manifest" not in key:
            urls.append(value)

    versions = item.get("video_versions") or item.get("videoVersions")
    if isinstance(versions, list):
        for entry in versions:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url") or entry.get("src")
            if isinstance(url, str) and url.startswith("http"):
                urls.append(url)

    # 去重但保留顺序
    return list(dict.fromkeys(urls))


def shortcode_of(item):
    """取 shortcode / code / pk，用于和本地已有清单对齐。"""
    for key in ("code", "shortcode", "short_code"):
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, str) and value:
            return value
    pk = item.get("pk") or item.get("id") if isinstance(item, dict) else None
    return str(pk) if pk else ""


def media_items(payload):
    """从各种外层结构里取出媒体数组。

    官方 `/v2/user/clips` 的实际形状是
    `{"response": {"items": [...], "paging_info": {...}}, "next_page_id": "..."}`，
    所以先往 `response` 钻一层，再按容器键找；直接放在顶层的写法也一并兼容。
    """
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if not isinstance(payload, dict):
        return []

    candidates = [payload]
    # HikerAPI: {"response": {...}}；Scavio: {"data": {"data": {"items": [...]}}}
    for wrapper in ("response", "data"):
        nested = payload.get(wrapper)
        if isinstance(nested, dict):
            candidates.insert(0, nested)
            deeper = nested.get("data")
            if isinstance(deeper, dict):
                candidates.insert(0, deeper)

    for container in candidates:
        for key in _REELS_CONTAINER_KEYS:
            value = container.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
    return []


def next_page_id(payload):
    """取翻页游标：`next_page_id`，或 response 里的 `paging_info.max_id`。"""
    if not isinstance(payload, dict):
        return None
    cursor = payload.get("next_page_id")
    if isinstance(cursor, str) and cursor:
        return cursor
    nested = payload.get("response")
    if isinstance(nested, dict):
        paging = nested.get("paging_info")
        if isinstance(paging, dict):
            for key in ("max_id", "next_max_id"):
                value = paging.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def build_snapshot(counts, source=SOURCE_HOSTED, captured_at=None):
    """与 `social_metrics.build_snapshot` 同形的快照，方便两条通道互换。"""
    captured = captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "platform": "instagram",
        "capturedAt": captured,
        "source": str(source or "").strip()[:120],
    }
    snapshot.update(counts)
    return snapshot


def _default_transport(url, headers, timeout, method="GET", body=None):
    """最朴素的单次请求。不做任何 TLS 伪装——伪装特征在风控里更刺眼。"""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class HostedClient:
    """托管 API 的薄客户端。传输层可注入，便于离线单测。"""

    def __init__(self, api_key=None, key_env=DEFAULT_KEY_ENV, api=DEFAULT_API,
                 timeout=40, transport=None):
        self.key_env = key_env
        self._api_key = api_key if api_key is not None else os.environ.get(key_env, "")
        self.api = (api or DEFAULT_API).rstrip("/")
        self.timeout = timeout
        self._transport = transport or _default_transport

    @property
    def has_key(self):
        return bool(str(self._api_key or "").strip())

    def _headers(self):
        if not self.has_key:
            raise HostedAuthError(
                f"没有找到托管 API 的 key：请把 {self.key_env}=... 写进私密环境文件"
                "（例如 ~/.minimax/aigc-shelf.env），不要贴在对话里。")
        return {
            "x-access-key": self._api_key,
            "accept": "application/json",
            "user-agent": "aigc-workbench/hosted-metrics",
        }

    def _get(self, path, params=None):
        url = f"{self.api}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        status, body = self._transport(url, self._headers(), self.timeout)
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)

        if status in (401, 403):
            # 把服务端原话带上：它通常会指明缺的是哪种授权方式，
            # 比我们自己猜「key 无效」有用得多。
            raise HostedAuthError(
                f"托管 API 拒绝授权（HTTP {status}）：key 无效、额度耗尽，或授权方式不对。"
                f"服务端原话：{text[:200]}。不要重试，先确认账户余额与 key 的传递方式。")
        if status == 402:
            raise HostedInsufficientFundsError(
                f"托管 API 返回 402：账户没有可用额度（key 认证本身可能没问题）。"
                f"服务端原话：{text[:200]}。"
                "先去 https://hikerapi.com/billing 确认额度；免费额度通常要按"
                "页面横幅提示确认 Telegram ID 才会解锁。不要换 key 反复重试。")
        if status == 429:
            raise HostedRateLimitError(
                "托管 API 返回 429 限流。不要自动重试——连续重试会把限流打死；"
                "等额度周期恢复或降低并发。")
        if status >= 400:
            raise HostedRequestError(f"托管 API 返回 HTTP {status}：{text[:200]}")

        try:
            return json.loads(text)
        except ValueError as error:
            raise HostedRequestError(f"托管 API 返回的不是 JSON：{text[:200]}") from error

    def profile(self, username):
        """账号资料。第一步用它确认能否读到该账号（含年龄门槛账号）。"""
        return self._get(PATH_PROFILE, {"username": username})

    def user_id(self, username):
        payload = self.profile(username)
        for key in ("pk", "id", "user_id", "pkid"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if value:
                return str(value)
        nested = payload.get("user") if isinstance(payload, dict) else None
        if isinstance(nested, dict):
            for key in ("pk", "id", "user_id"):
                if nested.get(key):
                    return str(nested[key])
        raise HostedRequestError(f"账号资料里没有 id 字段：{str(payload)[:200]}")

    def reels(self, username=None, user_id=None, page_id=None):
        """账号的 reels/clips 列表。带 play_count / like_count / video_url。

        该端点只认 `user_id`（官方 OpenAPI 里没有 username 参数），
        所以给 username 时先解析出 pk，多花一次请求。
        """
        if not user_id and username:
            user_id = self.user_id(username)
        if not user_id:
            raise ValueError("reels 需要 username 或 user_id")
        params = {"user_id": str(user_id)}
        if page_id:
            params["page_id"] = str(page_id)
        return self._get(PATH_CLIPS, params)


class ScavioClient:
    """Scavio（api.scavio.dev）客户端。

    与 HikerAPI 的差别：
    - 用 `POST` + JSON body，认证是 `Authorization: Bearer <key>`；
    - 响应把数据包在 `data` 里，并附 `credits_used` / `credits_remaining`；
      实测过四个端点在无 key 时返回 401 `Missing Authorization header`，
      说明路径与认证方式都对。
    - 计费按 credits：profile 1、reels 每页 1、posts 每页 2、单条 post 2。
      所以「列表一次拿全」比「逐条查」省一个数量级。
    """

    def __init__(self, api_key=None, key_env="SCAVIO_API_KEY",
                 api="https://api.scavio.dev", timeout=40, transport=None):
        self.key_env = key_env
        self._api_key = api_key if api_key is not None else os.environ.get(key_env, "")
        self.api = (api or "https://api.scavio.dev").rstrip("/")
        self.timeout = timeout
        self._transport = transport or _default_transport

    @property
    def has_key(self):
        return bool(str(self._api_key or "").strip())

    def _headers(self):
        if not self.has_key:
            raise HostedAuthError(
                f"没有找到 Scavio 的 key：把 {self.key_env}=... 写进私密环境文件"
                "（例如 ~/.minimax/aigc-shelf.env）。注册免费送 50 credits，不需要信用卡。")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
            "user-agent": "aigc-workbench/hosted-metrics",
        }

    def _post(self, path, body):
        url = f"{self.api}{path}"
        status, payload = self._transport(url, self._headers(), self.timeout,
                                          method="POST", body=json.dumps(body or {}).encode())
        text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)

        if status in (401, 403):
            raise HostedAuthError(
                f"Scavio 拒绝授权（HTTP {status}）：key 无效或 credits 用尽。"
                f"服务端原话：{text[:200]}")
        if status == 402:
            raise HostedInsufficientFundsError(
                f"Scavio 返回 402：credits 不足。服务端原话：{text[:200]}。"
                "免费额度 50 credits，用尽后需在 dashboard 补。")
        if status == 429:
            raise HostedRateLimitError(
                "Scavio 返回 429 限流。不要自动重试——连续重试会把限流打死。")
        if status >= 400:
            raise HostedRequestError(f"Scavio 返回 HTTP {status}：{text[:200]}")

        try:
            return json.loads(text)
        except ValueError as error:
            raise HostedRequestError(f"Scavio 返回的不是 JSON：{text[:200]}") from error

    def profile(self, username):
        return self._post("/api/v1/instagram/profile", {"username": username})

    def _unwrap(self, payload):
        """Scavio 把数据包在 `data` 里；拿内层，保持与 HikerAPI 一致的用法。"""
        if isinstance(payload, dict):
            inner = payload.get("data")
            if isinstance(inner, dict):
                return inner
        return payload

    def user_id(self, username):
        inner = self._unwrap(self.profile(username))
        for key in ("pk", "id", "user_id"):
            value = inner.get(key) if isinstance(inner, dict) else None
            if value:
                return str(value)
        raise HostedRequestError(f"Scavio 账号资料里没有 id 字段：{str(inner)[:200]}")

    def reels(self, username=None, user_id=None, page_id=None):
        """Scavio 的 reels 端点直接吃 username，不需要先解析 pk（与 HikerAPI 不同）。"""
        if not username and not user_id:
            raise ValueError("reels 需要 username 或 user_id")
        body = {"username": username} if username else {"user_id": str(user_id)}
        if page_id:
            body["cursor"] = str(page_id)
        return self._post("/api/v1/instagram/user/reels", body)


def summarize(payload):
    """把一次 reels 响应整理成便于人读与排序的记录列表。"""
    records = []
    for item in media_items(payload):
        records.append({
            "shortcode": shortcode_of(item),
            "counts": normalize_counts(item),
            "videoUrls": extract_video_urls(item),
        })
    return records


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="hosted_metrics",
        description="经托管第三方 API 采集 Instagram 计数与视频直链（不携带本机 Instagram 登录态）。")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("profile", "读取账号资料，确认托管侧能否访问该账号"),
        ("reels", "读取账号 reels 列表及播放/点赞等计数"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("username")
        cmd.add_argument("--provider", choices=("hiker", "scavio"), default="hiker",
                         help="hiker=api.hikerapi.com（GET+x-access-key）；"
                              "scavio=api.scavio.dev（POST+Bearer，免费 50 credits）")
        cmd.add_argument("--api", default=None,
                         help="覆盖 API 基址；默认按 provider 选择")
        cmd.add_argument("--key-env", default=None,
                         help="存放 key 的环境变量名；默认 hiker→HIKERAPI_KEY，scavio→SCAVIO_API_KEY")
        cmd.add_argument("--timeout", type=int, default=40)
        cmd.add_argument("--out", default="",
                         help="把原始 JSON 写到该文件，便于核对字段名")
        cmd.add_argument("--records", action="store_true",
                         help="reels：输出归一化后的记录（shortcode/计数/直链）")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.provider == "scavio":
        client = ScavioClient(api=args.api or DEFAULT_SCAVIO_API,
                              key_env=args.key_env or "SCAVIO_API_KEY",
                              timeout=args.timeout)
    else:
        client = HostedClient(api=args.api or DEFAULT_API,
                              key_env=args.key_env or DEFAULT_KEY_ENV,
                              timeout=args.timeout)

    try:
        if args.command == "profile":
            payload = client.profile(args.username)
        else:
            payload = client.reels(username=args.username)
    except HostedAuthError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except HostedInsufficientFundsError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 4
    except HostedRateLimitError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 3
    except (HostedRequestError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        print(f"原始响应已写入 {args.out}")

    if args.command == "reels" and args.records:
        records = summarize(payload)
        print(json.dumps(records, ensure_ascii=False, indent=1))
        print(f"# 共 {len(records)} 条", file=sys.stderr)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
