#!/usr/bin/env python3
"""在真实 Chrome 页面里采集 Instagram 社交计数。

为什么需要这条通道：
脚本用 urllib 直连 i.instagram.com 的移动端接口时，请求特征和浏览器不一样（TLS/HTTP2 指纹、
header 组合、调用节奏），平台会把它判成自动化流量，返回 challenge_required / checkpoint_required。
在真实 Chrome 的 Instagram 页面上发**同源**请求就不一样了：请求自带浏览器自己的指纹、完整
Cookie 和 CSRF token，referer 也是同源页面，平台看到的就是网页自己的正常请求。

同源的意义：页面本身就在 https://www.instagram.com，fetch 到 /api/v1/... 不走 CORS 校验，
可以直接读响应体；跨域到 i.instagram.com 则会被 CORS 挡住，只能拿到 opaque 响应。

前置条件（一次性，Chrome 菜单「显示 → 开发者 → 允许来自 Apple 事件的 JavaScript」）：
  1. 打开这个开关，否则 osascript 会被 Chrome 拒绝（-1723）。
  2. 采集时让 Chrome 前台窗口的活动标签页停在 Instagram 页面上。

注意：这个通道同样受账号风控约束。账号被停用或在申诉中时，页面会跳到
/accounts/suspended/，此时整批停止，不要继续重试。
"""

import json
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import social_metrics

IG_WEB_HOST = "www.instagram.com"
IG_WEB_ORIGIN = f"https://{IG_WEB_HOST}"

# 网页端 app-id 在前：它和 www.instagram.com 同源，最接近网页自己的调用。
# 移动端 app-id 作为补充：有的计数字段只有它会返回。
DEFAULT_APP_IDS = (social_metrics.IG_APP_ID_REPOST, social_metrics.IG_APP_ID_FULL)

COLLECT_KEY = "__aigcCollect"
SUSPENDED_PATH = "/accounts/suspended"


class BrowserUnavailableError(RuntimeError):
    """Chrome 通道不可用：Chrome 没开、Apple 事件脚本被关、活动页不是 Instagram 等。"""


class AccountMismatchError(RuntimeError):
    """浏览器登录态不是预期账号。

    和 challenge 一样要整批停下：继续跑只会把另一个账号也拖进风控。
    """


# --- Chrome 脚本通道 -------------------------------------------------------

_ACTIVE_TAB_SCRIPT = (
    "on run argv\n"
    '  tell application "Google Chrome" to execute javascript (item 1 of argv) '
    "in active tab of front window\n"
    "end run"
)

_TAB_SCRIPT = (
    "on run argv\n"
    '  tell application "Google Chrome" to execute javascript (item 1 of argv) '
    "in tab (item 2 of argv) of window (item 3 of argv)\n"
    "end run"
)

# 探针：读页面基本信息，顺便把 ds_user_id / csrftoken 位置确认下来。
_PROBE_JS = r"""(function(){
  var cookie = document.cookie || "";
  var user = cookie.match(/(?:^|;\s*)ds_user_id=([^;]+)/);
  var csrf = cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return JSON.stringify({
    href: location.href,
    host: location.hostname,
    path: location.pathname,
    userId: user ? decodeURIComponent(user[1]) : "",
    hasCsrf: !!csrf,
    title: document.title
  });
})()"""

# 采集脚本：同源 fetch，字段名保持和接口返回一致，方便和 REST 通道合并。
_COLLECT_JS = r"""(function(){
  var key = "__aigcCollect";
  window[key] = null;
  (async function(){
    var out = {code: "%%CODE%%", status: 0, like_count: null, comment_count: null,
               media_repost_count: null, reshare_count: null, save_count: null,
               play_count: null, error: "", challenge: "", userId: ""};
    try {
      var cookie = document.cookie || "";
      var user = cookie.match(/(?:^|;\s*)ds_user_id=([^;]+)/);
      if (user) out.userId = decodeURIComponent(user[1]);
      var headers = {"x-ig-app-id": "%%APPID%%"};
      var csrf = cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
      if (csrf) headers["x-csrftoken"] = decodeURIComponent(csrf[1]);
      var response = await fetch("/api/v1/media/%%PK%%/info/", {headers: headers, credentials: "include"});
      var text = await response.text();
      out.status = response.status;
      if (text.indexOf("challenge_required") >= 0) out.challenge = "challenge_required";
      else if (text.indexOf("checkpoint_required") >= 0) out.challenge = "checkpoint_required";
      var payload = null;
      try { payload = JSON.parse(text); } catch (error) { payload = null; }
      if (payload) {
        var item = (payload.items && payload.items[0]) || payload;
        out.like_count = item.like_count === undefined ? null : item.like_count;
        out.comment_count = item.comment_count === undefined ? null : item.comment_count;
        out.media_repost_count = item.media_repost_count === undefined ? null : item.media_repost_count;
        out.reshare_count = item.reshare_count === undefined ? null : item.reshare_count;
        out.save_count = item.save_count === undefined ? null : item.save_count;
        out.play_count = item.play_count === undefined ? null : item.play_count;
      } else if (!out.challenge) {
        out.error = "响应不是 JSON（可能被登录墙或风控页替换）";
      }
    } catch (error) {
      out.error = String(error);
    }
    window[key] = out;
  })();
  return "started";
})()"""

_POLL_JS = r"""(function(){
  var value = window["__aigcCollect"];
  return value ? JSON.stringify(value) : "";
})()"""


def _describe_osascript_error(message):
    """把 AppleScript 的报错翻译成能直接照做的中文提示。"""
    text = (message or "").strip()
    if "不允许访问" in text or "-1723" in text:
        return ("Chrome 没允许 Apple 事件执行 JavaScript：请在 Chrome 菜单"
                "「显示 → 开发者 → 允许来自 Apple 事件的 JavaScript」打开开关")
    if "-600" in text or "无法连接到" in text:
        return "AppleScript 连不上 Chrome，请确认 Chrome 正在运行"
    return f"Chrome 脚本执行失败：{text[:200]}"


def chrome_running():
    """Chrome 是否在运行（查询不会把 Chrome 拉起来）。"""
    result = subprocess.run(["osascript", "-e", 'application "Google Chrome" is running'],
                            capture_output=True, text=True)
    return result.stdout.strip().lower() == "true"


def _osascript(js, tab=None, window=None, timeout=45):
    """在 Chrome 标签页里执行 JS 并拿回返回值。"""
    if tab and window:
        command = ["osascript", "-e", _TAB_SCRIPT, js, str(tab), str(window)]
    else:
        command = ["osascript", "-e", _ACTIVE_TAB_SCRIPT, js]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BrowserUnavailableError(f"Chrome 脚本执行超时（{timeout}s）") from None
    if result.returncode:
        raise BrowserUnavailableError(_describe_osascript_error(result.stderr or result.stdout))
    return result.stdout.strip()


def browser_probe(tab=None, window=None):
    """读当前页面的基本信息，顺带确认通道本身通不通。"""
    if not chrome_running():
        raise BrowserUnavailableError("Chrome 没有运行")
    raw = _osascript(_PROBE_JS, tab=tab, window=window)
    try:
        return json.loads(raw)
    except ValueError:
        raise BrowserUnavailableError(f"Chrome 没有返回可解析的页面信息：{raw[:200]}") from None


def _collect_one(shortcode, pk, app_id, tab=None, window=None, timeout=30):
    """在页面里跑一次同源 fetch，轮询取回结果。"""
    js = (_COLLECT_JS
          .replace("%%CODE%%", shortcode)
          .replace("%%PK%%", str(pk))
          .replace("%%APPID%%", str(app_id)))
    _osascript(js, tab=tab, window=window)
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = _osascript(_POLL_JS, tab=tab, window=window)
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if data:
                return data
        time.sleep(0.6)
    raise BrowserUnavailableError(f"Chrome 页面没有在 {timeout}s 内返回结果：{shortcode}")


def collect_instagram_in_browser(shortcodes, delay=3.0, jitter=1.0, timeout=30,
                                 expected_user_id="", app_ids=DEFAULT_APP_IDS,
                                 tab=None, window=None, log=None):
    """在真实 Chrome 里逐条采集 Instagram 的六项计数。

    返回 {shortcode: {"like":…, "comment":…, "repost":…, "share":…, "save":…, "play":…}}。

    账号被停用、要求验证或登录态不是预期账号时抛异常并停下整批：
    继续跑只会把风控打得更死，剩下的条目留着下次再来。
    """
    codes = [str(code).strip() for code in shortcodes if str(code or "").strip()]
    if not codes:
        return {}

    probe = browser_probe(tab=tab, window=window)
    host = str(probe.get("host", ""))
    path = str(probe.get("path", ""))
    if host not in ("instagram.com", IG_WEB_HOST):
        raise BrowserUnavailableError(
            f"Chrome 活动标签页不是 Instagram 页面（当前是 {probe.get('href', host) or '未知'}）；"
            "请把 Instagram 页面切到 Chrome 前台窗口的活动标签页")
    if path.startswith(SUSPENDED_PATH):
        raise social_metrics.AccountChallengeError(
            "Instagram 账号当前处于停用/申诉状态，页面在 /accounts/suspended/；"
            "请先等申诉结果出来，再采集计数")
    page_user = str(probe.get("userId", "") or "")
    if expected_user_id and page_user != str(expected_user_id):
        raise AccountMismatchError(
            f"浏览器登录态不是预期账号（页面 ds_user_id={page_user or '读不到'}，"
            f"期望 {expected_user_id}）；请切换到正确的 Instagram 账号再采集")

    results = {}
    for index, shortcode in enumerate(codes):
        if index:
            pause = float(delay or 0) + random.uniform(0, float(jitter or 0))
            if pause:
                if log:
                    log(f"pacing: waiting {pause:.0f}s before the next count")
                time.sleep(pause)
        pk = social_metrics.shortcode_to_pk(shortcode)
        sources = []
        last_error = ""
        for app_id in app_ids:
            data = _collect_one(shortcode, pk, app_id, tab=tab, window=window, timeout=timeout)
            if data.get("challenge"):
                raise social_metrics.AccountChallengeError(
                    f"Instagram 返回 {data['challenge']}，整批停止采集；"
                    "先在浏览器里正常使用该账号并处理验证，再低频重试")
            if data.get("error"):
                last_error = str(data["error"])
            sources.append(data)
            counts = social_metrics.metrics_from_sources(sources)
            if all(counts.get(key) is not None for key in social_metrics.METRIC_KEYS):
                break
            status = int(data.get("status") or 0)
            if status >= 400:
                # 端点不认这个 app-id，换下一个也只会白跑一趟，先停在这里。
                break
        counts = social_metrics.metrics_from_sources(sources)
        if not any(counts.get(key) is not None for key in social_metrics.METRIC_KEYS):
            raise BrowserUnavailableError(
                f"页面采集没有拿到任何计数：{shortcode}"
                + (f"（{last_error}）" if last_error else ""))
        results[shortcode] = counts
        if log:
            log(f"{shortcode}: {counts}")
    return results
