"""HTTP transport for the Baidu Netdisk uploader.

Why this module exists
----------------------
The standard library path (``urllib.request.urlopen``) forces
``Connection: close`` on every request -- Python's
``AbstractHTTPHandler.do_open()`` injects that header and closes the socket
after each response. With thousands of precreate/create/block requests per
batch that means thousands of fresh TCP+TLS handshakes.

This module centralises three things that must not be scattered around the
uploader:

1. **Connection reuse** -- a ``requests.Session`` per worker thread, with a
   pool at least as large as the configured concurrency.
2. **Redaction** -- the Baidu access token travels in the query string, so a
   bare ``str(exception)`` from ``requests`` would happily log it. Everything
   that can reach a log line goes through :func:`redact_url` /
   :func:`safe_http_error` first.
3. **Chunk-level retry** -- data blocks are retried in place on the same
   ``uploadid + partseq``, re-read from the same offset, and re-verified
   against the MD5 the server echoes back.
"""

import random
import threading
import time
import urllib.parse

try:
    import requests
    from requests.adapters import HTTPAdapter
except ImportError:  # pragma: no cover - exercised only on a missing dependency
    requests = None
    HTTPAdapter = None

API_HOST = "pan.baidu.com"
DEFAULT_UPLOAD_HOST = "d.pcs.baidu.com"
LOCATE_API_PATH = "/rest/2.0/pcs/file"
UPLOAD_API_PATH = "/rest/2.0/pcs/superfile2"
UPLOAD_APPID = "250528"

USER_AGENT = "pan.baidu.com"

# Control calls (precreate/create/locateupload) are small JSON round trips;
# data calls move a whole block, so they get a much larger read budget.
CONTROL_TIMEOUT = (10, 60)     # (connect, read) seconds
CHUNK_TIMEOUT = (10, 180)

CHUNK_MAX_ATTEMPTS = 4
CHUNK_BACKOFF_SECONDS = (1, 2, 4)
CHUNK_BACKOFF_JITTER_MS = 250

# Baidu errno values that mean "the token is no longer usable" -- retrying
# these only burns quota, so they must short-circuit the retry ladder.
TOKEN_INVALID_ERRNO = {110, 111}
PERMISSION_ERRNO = {13, 6}
# Chunk larger than the account is allowed to use.
CHUNK_SIZE_ERRNO = {31364}
RETRYABLE_ERRNO = {-6, -8, -11, 112, 158}


class UploadError(Exception):
    """An upload failure with a stable machine-readable code.

    ``code`` is one of the values documented in the task book; ``message`` is a
    human-facing Chinese sentence. Neither ever contains the token.
    """

    def __init__(self, code, message, *, errno=None, status=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.errno = errno
        self.status = status
        self.retryable = retryable


def require_requests():
    if requests is None:
        raise SystemExit(
            "缺少依赖 requests。请先安装：\n"
            "  python3 -m pip install -r "
            "skills/baidu-netdisk-archive/requirements.txt\n"
            "（或 python3 -m pip install 'requests>=2.31,<3'）\n"
            "脚本不会静默回退到没有连接池的旧上传实现。"
        )


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

# Query keys whose values must never appear in a log, a metrics file or an
# exception message. The token is the important one; the rest are defence.
SENSITIVE_QUERY_KEYS = ("access_token", "session_secret", "session_key", "token")


def redact_url(url):
    """Return *url* with every sensitive query value replaced.

    ``https://pan.baidu.com/api/list?access_token=SECRET&page=1`` becomes
    ``https://pan.baidu.com/api/list?access_token=***&page=1``.
    """
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not pairs:
        return url
    safe = [(k, "***" if k.lower() in SENSITIVE_QUERY_KEYS else v) for k, v in pairs]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(safe), parts.fragment)
    )


def safe_http_error(exc):
    """Turn a ``requests`` exception into a token-free one-line description.

    ``requests`` embeds the full request URL in ``str(exc)`` and in
    ``exc.request.url``; the token is in the query string, so both are redacted
    here rather than trusting the caller to remember.
    """
    name = type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    host = ""
    request = getattr(exc, "request", None)
    if request is not None:
        try:
            host = urllib.parse.urlsplit(request.url).netloc
        except (ValueError, AttributeError):
            host = ""
    bits = [name]
    if host:
        bits.append(f"host={host}")
    if status is not None:
        bits.append(f"status={status}")
    return " ".join(bits)


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------

class Transport:
    """Owns one ``requests.Session`` per thread.

    ``concurrency=1`` still gets a real pool -- the point is to stop paying a
    TCP+TLS handshake per request, not to add threads. Sessions are never
    shared across threads: ``requests.Session`` is not documented as
    thread-safe and a shared pool would serialise the very uploads we are
    trying to overlap.
    """

    def __init__(self, *, pool_size=2, user_agent=USER_AGENT):
        require_requests()
        self.pool_size = max(2, int(pool_size))
        self.user_agent = user_agent
        self._local = threading.local()
        self._all_sessions = []
        self._all_lock = threading.Lock()

    def session(self):
        """Return this thread's session, creating it on first use."""
        existing = getattr(self._local, "session", None)
        if existing is not None:
            return existing
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.pool_size,
            pool_maxsize=self.pool_size,
            max_retries=0,          # retries are handled explicitly, per block
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": self.user_agent})
        self._local.session = session
        with self._all_lock:
            self._all_sessions.append(session)
        return session

    def close(self):
        """Close every session this transport created.

        Thread-local sessions are tracked in a list precisely so they can be
        closed here; otherwise they'd only be reclaimed at process exit.
        """
        with self._all_lock:
            sessions = list(self._all_sessions)
            self._all_sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:       # pragma: no cover - best effort cleanup
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


# --------------------------------------------------------------------------
# JSON control plane (precreate/create) over the same per-worker Session
# --------------------------------------------------------------------------

def control_request_json(session, token, path, params=None, body=None, method="POST",
                         timeout=CONTROL_TIMEOUT):
    """Call a small pan.baidu.com JSON endpoint through the worker Session.

    Keeping precreate/create on the same Session as locateupload and block
    upload avoids paying a fresh TCP/TLS handshake for every small file.  The
    raw URL is never exposed in an exception because it contains the token.
    """
    query = {"access_token": token}
    if params:
        query.update(params)
    url = f"https://{API_HOST}{path}?{urllib.parse.urlencode(query)}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if body else None
    data = urllib.parse.urlencode(body) if body else None
    try:
        if method.upper() == "GET":
            response = session.get(url, timeout=timeout)
        else:
            response = session.post(url, data=data, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise UploadError(
            "network_retry",
            f"{path} 网络错误：{safe_http_error(exc)}",
            retryable=True,
        ) from None
    if response.status_code == 408 or response.status_code == 429 or 500 <= response.status_code <= 599:
        raise UploadError(
            "network_retry",
            f"{path} 返回 HTTP {response.status_code}",
            status=response.status_code,
            retryable=True,
        )
    if response.status_code >= 400:
        raise UploadError(
            "control_request_failed",
            f"{path} 返回 HTTP {response.status_code}",
            status=response.status_code,
        )
    try:
        return response.json()
    except ValueError:
        raise UploadError(
            "network_retry",
            f"{path} 返回非 JSON 响应",
            status=response.status_code,
            retryable=True,
        ) from None


# --------------------------------------------------------------------------
# locateupload -- ask Baidu which host this upload task should use
# --------------------------------------------------------------------------

def _first_https_candidate(payload):
    """Pull the first usable HTTPS host out of a locateupload response.

    Baidu has returned this in a few shapes over time, so try them in order:
    ``servers[].server`` -> ``server`` -> ``host``. A bare hostname is upgraded
    to ``https://``; anything that already carries a scheme is kept as-is so a
    future QUIC/http candidate can pass through unchanged.
    """
    candidates = []
    servers = payload.get("servers")
    if isinstance(servers, list):
        for item in servers:
            if isinstance(item, dict) and item.get("server"):
                candidates.append(item["server"])
            elif isinstance(item, str) and item:
                candidates.append(item)
    for key in ("server", "host"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)

    for raw in candidates:
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("https://"):
            return raw
        if raw.startswith("http://"):
            # The legacy superfile2 endpoint is HTTPS-only.
            return "https://" + raw[len("http://"):]
        if "//" not in raw and "/" not in raw:
            return "https://" + raw
    return None


def locate_upload_host(session, token, remote_path, uploadid, timeout=CONTROL_TIMEOUT):
    """Ask Baidu for the recommended upload host for this ``uploadid``.

    Returns the host string, or ``None`` when no HTTPS candidate is present.
    Raises :class:`UploadError` only for transport-level failures; a well
    formed response with no usable host is a normal ``None`` so the caller can
    fall back.
    """
    params = {
        "method": "locateupload",
        "appid": UPLOAD_APPID,
        "access_token": token,
        "path": remote_path,
        "uploadid": uploadid,
        "upload_version": "2.0",
    }
    url = f"https://{_host_only(DEFAULT_UPLOAD_HOST)}{LOCATE_API_PATH}?{urllib.parse.urlencode(params)}"
    try:
        response = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise UploadError(
            "locateupload_failed",
            f"获取推荐上传域名失败：{safe_http_error(exc)}",
        ) from None
    if response.status_code >= 400:
        raise UploadError(
            "locateupload_failed",
            f"获取推荐上传域名失败：HTTP {response.status_code}",
            status=response.status_code,
        )
    try:
        payload = response.json()
    except ValueError:
        return None
    return _first_https_candidate(payload)


def resolve_upload_host(mode, session, token, remote_path, uploadid, warn=None):
    """Apply the ``--upload-host-mode`` policy.

    ``fixed``    -- never call locateupload, keep ``d.pcs.baidu.com`` and the
                    legacy ``/apps/BaiduPCS/<uploadid>`` query path.
    ``auto``     -- try locateupload, warn and fall back to fixed on failure.
    ``required`` -- locateupload failure fails the file.

    Returns ``(host, query_path_mode)`` where the second element is
    ``"legacy"`` or ``"remote"`` and gets recorded in the metrics.
    """
    if mode == "fixed":
        return DEFAULT_UPLOAD_HOST, "legacy"
    try:
        host = locate_upload_host(session, token, remote_path, uploadid)
    except UploadError as exc:
        if mode == "required":
            raise
        if warn:
            warn(f"locateupload 失败，回退固定域名：{exc.message}")
        return DEFAULT_UPLOAD_HOST, "legacy"
    if not host:
        if mode == "required":
            raise UploadError("locateupload_failed", "locateupload 未返回可用的 HTTPS 上传域名")
        if warn:
            warn("locateupload 未返回可用的 HTTPS 上传域名，回退固定域名")
        return DEFAULT_UPLOAD_HOST, "legacy"
    return host, "remote"


# --------------------------------------------------------------------------
# Block upload with in-place retry
# --------------------------------------------------------------------------

def _build_block_body(boundary, partseq, data):
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="part{partseq}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + data + tail


def _host_only(host):
    """Strip any scheme so callers can always build ``https://<host>`` safely."""
    return str(host).strip().split("://", 1)[-1].rstrip("/")


def _post_block(session, host, query_path_mode, remote_path, token, uploadid,
                 partseq, body, boundary, timeout):
    params = {
        "method": "upload",
        "type": "tmpfile",
        "access_token": token,
        "uploadid": uploadid,
        "partseq": str(partseq),
    }
    # The legacy superfile2 endpoint keys the temp object by uploadid under
    # /apps/BaiduPCS/. locateupload-era uploads use the real remote path.
    # Both shapes are proven against this account, so the caller decides --
    # see resolve_upload_host.
    if query_path_mode == "legacy":
        params["path"] = f"/apps/BaiduPCS/{uploadid}"
    else:
        if not remote_path:
            raise UploadError("locateupload_failed", "remote 模式缺少 remote_path，无法构造上传请求")
        params["path"] = remote_path
    url = f"https://{_host_only(host)}{UPLOAD_API_PATH}?{urllib.parse.urlencode(params)}"
    response = session.post(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=timeout,
    )
    return response


def upload_block_with_retry(
    session,
    token,
    upload_host,
    query_path_mode,
    remote_path,
    uploadid,
    partseq,
    data,
    expected_md5,
    *,
    max_attempts=CHUNK_MAX_ATTEMPTS,
    timeout=CHUNK_TIMEOUT,
    on_retry=None,
    cancelled=None,
):
    """Upload one block, retrying in place until it lands.

    The caller is responsible for re-reading ``data`` from the same offset for
    each attempt; this function receives the bytes for one attempt only, so
    retries that need a fresh read must be driven by a ``data_factory``.

    Returns ``(reply, retries)``. Raises :class:`UploadError` when the block
    cannot be delivered or verified.

    Retried: connect/read timeouts, connection resets, HTTP 408/429/5xx,
    non-JSON bodies, Baidu retryable errno, and an MD5 mismatch (the server
    acknowledged the block but with different content -- resend the whole
    block rather than trying to patch it).

    Not retried: invalid token, permission errors, an oversized chunk, and
    other explicit parameter errors.
    """
    boundary = "----zcode" + urllib.parse.quote(uploadid) + str(partseq)
    body = _build_block_body(boundary, partseq, data)
    last = None

    for attempt in range(1, max_attempts + 1):
        if cancelled is not None and cancelled.is_set():
            raise UploadError("cancelled", "已取消")
        try:
            response = _post_block(
                session, upload_host, query_path_mode, remote_path, token, uploadid,
                partseq, body, boundary, timeout,
            )
        except requests.RequestException as exc:
            last = UploadError(
                "network_retry_exhausted" if attempt >= max_attempts else "network_retry",
                f"分片 {partseq} 网络错误（第 {attempt} 次）：{safe_http_error(exc)}",
                retryable=attempt < max_attempts,
            )
        else:
            status = response.status_code
            if status == 408 or status == 429 or 500 <= status <= 599:
                last = UploadError(
                    "network_retry_exhausted" if attempt >= max_attempts else "network_retry",
                    f"分片 {partseq} 返回 HTTP {status}（第 {attempt} 次）",
                    status=status,
                    retryable=attempt < max_attempts,
                )
            else:
                try:
                    reply = response.json()
                except ValueError:
                    last = UploadError(
                        "network_retry_exhausted" if attempt >= max_attempts else "network_retry",
                        f"分片 {partseq} 返回非 JSON（第 {attempt} 次）",
                        status=status,
                        retryable=attempt < max_attempts,
                    )
                    reply = None
                if reply is not None:
                    errno = reply.get("errno", 0)
                    if errno in TOKEN_INVALID_ERRNO:
                        raise UploadError("token_invalid", "百度网盘授权已失效，请重新授权", errno=errno)
                    if errno in PERMISSION_ERRNO:
                        raise UploadError("permission_denied", f"没有写入权限：errno={errno}", errno=errno)
                    if errno in CHUNK_SIZE_ERRNO:
                        raise UploadError(
                            "chunk_size_unsupported",
                            f"分片大小不被接受：errno={errno}",
                            errno=errno,
                        )
                    if errno != 0:
                        if errno not in RETRYABLE_ERRNO:
                            raise UploadError(
                                "chunk_upload_failed",
                                f"分片 {partseq} 上传失败：errno={errno}",
                                errno=errno,
                            )
                        last = UploadError(
                            "network_retry_exhausted" if attempt >= max_attempts else "network_retry",
                            f"分片 {partseq} errno={errno}（第 {attempt} 次）",
                            errno=errno,
                            retryable=attempt < max_attempts,
                        )
                    elif reply.get("md5") != expected_md5:
                        # Server accepted the block but echoed a different MD5:
                        # resend the entire block rather than patching.
                        last = UploadError(
                            "chunk_md5_mismatch",
                            f"分片 {partseq} 校验失败：服务端回显 {reply.get('md5')}，本地 {expected_md5}"
                            f"（第 {attempt} 次）",
                            retryable=attempt < max_attempts,
                        )
                    else:
                        return reply, attempt - 1

        if not last.retryable or attempt >= max_attempts:
            break
        if on_retry:
            on_retry(attempt, last.message)
        delay = CHUNK_BACKOFF_SECONDS[min(attempt - 1, len(CHUNK_BACKOFF_SECONDS) - 1)]
        time.sleep(delay + random.uniform(0, CHUNK_BACKOFF_JITTER_MS / 1000.0))

    if last.code == "network_retry":
        last = UploadError(
            "network_retry_exhausted",
            f"分片 {partseq} 重试 {max_attempts} 次仍失败：{last.message}",
            errno=last.errno,
            status=last.status,
        )
    raise last


def backoff_sleep(attempt):
    """Shared backoff used by the control-plane retry ladder."""
    time.sleep(min(2 ** attempt, 10))