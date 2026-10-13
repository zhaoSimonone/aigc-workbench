#!/usr/bin/env python3
"""Sync a local directory tree to Baidu Netdisk over the open-platform HTTP API.

Uses the OAuth access token issued by the official authorization page, so it
never needs the desktop client. Preserves relative paths, verifies every block
by the MD5 the server echoes back, and skips files already present remotely.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netdisk_transport import (  # noqa: E402
    API_HOST,
    CONTROL_TIMEOUT,
    DEFAULT_UPLOAD_HOST,
    Transport,
    UploadError,
    backoff_sleep,
    control_request_json,
    locate_upload_host,
    redact_url,
    require_requests,
    resolve_upload_host,
    safe_http_error,
    upload_block_with_retry,
)

API = "https://pan.baidu.com"
UPLOAD_API = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"

# Kept for backwards compatibility with anything that imported it; the value
# is now a *default* that every upload path receives explicitly. Do not read
# this global from inside the uploader.
DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024
BLOCK_SIZE = DEFAULT_BLOCK_SIZE

TOKEN_FILE = os.path.expanduser("~/.config/aigc-workbench/baidu_netdisk_token")

DEFAULT_SRC = "/Users/simon/Desktop/AIGC"
DEFAULT_DEST = "/AIGC"

# errno values that mean "retry shortly" rather than "give up".
RETRYABLE = {-6, -8, -11, 112, 158}
MAX_ATTEMPTS = 4

# 600 = 列举目录失败，实测是偶发的，只在 /api/list 上出现。
# 它**只在列举这一处**额外重试：塞进全局集合会让 precreate/create/quota 也去
# 重试一个它们根本不返回的 errno；而列举失败若被当成空目录，已存在的文件会被
# 误判为"需要上传"。
LIST_EXTRA_RETRYABLE = {600}

# 远端目录确实不存在（errno -9）才可以当作空目录；其余任何错误都必须中止，
# 绝不能"当成空目录继续"。
DIR_NOT_FOUND_ERRNO = -9

ALLOWED_CHUNK_SIZES = (4, 8, 16, 32)
MIB = 1024 * 1024

PROFILES = {
    # safe keeps the historical behaviour byte-for-byte: 4 MiB blocks, serial,
    # a 0.15 s pause between blocks, and the fixed d.pcs.baidu.com endpoint.
    "safe": {
        "chunk_size": 4 * MIB,
        "concurrency": 1,
        "inter_chunk_delay": 0.15,
        "upload_host_mode": "fixed",
    },
    # fast targets a paying member on a fast link: bigger blocks amortise the
    # per-request overhead, and file-level concurrency hides the per-file
    # precreate/create round trips.
    "fast": {
        "chunk_size": 8 * MIB,
        "concurrency": 4,
        "inter_chunk_delay": 0.03,
        "upload_host_mode": "auto",
    },
}

UPLOAD_HOST_MODES = ("fixed", "auto", "required")

# Stable error codes surfaced in the final failure list.
ERR_NETWORK = "network_retry_exhausted"
ERR_CHUNK_MD5 = "chunk_md5_mismatch"
ERR_CHUNK_SIZE = "chunk_size_unsupported"
ERR_CHUNK_UPLOAD = "chunk_upload_failed"
ERR_TOKEN = "token_invalid"
ERR_PERMISSION = "permission_denied"
ERR_DELETE = "remote_delete_failed"
ERR_PRECREATE = "precreate_failed"
ERR_LOCATE = "locateupload_failed"
ERR_CREATE = "create_failed"
ERR_SIZE_MISMATCH = "remote_size_mismatch"
ERR_RENAMED = "remote_path_renamed"
ERR_CANCELLED = "cancelled"
ERR_LOCAL_IO = "local_io_error"
ERR_INTERNAL = "internal_error"

ERROR_HINTS = {
    ERR_NETWORK: "网络或网盘接口持续失败，可重跑；已上传的文件会跳过",
    ERR_CHUNK_MD5: "分片校验反复不一致，建议重跑该文件",
    ERR_CHUNK_SIZE: "当前账号不支持该分片大小，请改用 --chunk-size 4M 或 --profile safe",
    ERR_CHUNK_UPLOAD: "百度拒绝了该分片，请根据 errno 检查路径或上传参数",
    ERR_TOKEN: "百度网盘授权已失效，请重新授权后重跑",
    ERR_PERMISSION: "该 Token 没有写入权限",
    ERR_DELETE: "远端旧版本删除失败，未覆盖，请检查网盘状态",
    ERR_PRECREATE: "预上传失败",
    ERR_LOCATE: "获取推荐上传域名失败，可加 --upload-host-mode fixed 重试",
    ERR_CREATE: "创建文件失败",
    ERR_SIZE_MISMATCH: "远端落盘大小与本地不一致，请勿删除本地文件",
    ERR_RENAMED: "远端路径被改名，可能存在同名文件",
    ERR_CANCELLED: "已中断",
    ERR_LOCAL_IO: "本地文件读取失败",
    ERR_INTERNAL: "未预期的错误",
}


class NetdiskError(Exception):
    """Kept for the read-only commands; upload errors use UploadError."""


def load_token(explicit=None):
    if explicit:
        return explicit.strip()
    env = os.environ.get("BAIDU_NETDISK_ACCESS_TOKEN")
    if env:
        return env.strip()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            token = fh.read().strip()
        if token:
            return token
    raise NetdiskError(
        "未找到百度网盘 Access Token。请设置环境变量 BAIDU_NETDISK_ACCESS_TOKEN，"
        f"或写入 {TOKEN_FILE}（权限 600）。"
    )


def _request(url, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def call(token, path, params=None, body=None, method="POST"):
    """Call a form-encoded netdisk API and return the decoded JSON body."""
    url = f"{API}{path}?access_token={urllib.parse.quote(token)}"
    if params:
        url += "&" + urllib.parse.urlencode(params)
    data = urllib.parse.urlencode(body).encode() if body else None
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data else {}
    if method == "GET":
        data, headers = None, {}
    raw = _request(url, data, headers)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise NetdiskError(f"{path} 返回了非 JSON 响应：{raw[:200]!r}")


def call_retry(token, path, params=None, body=None, method="POST", session=None, extra_retryable=None):
    """Retry a small JSON control request.

    Upload workers pass their thread-local requests.Session so precreate/create
    share the same keep-alive pool as locateupload and block upload. Read-only
    commands and serial planning keep the legacy urllib path for compatibility.

    ``extra_retryable`` adds errno values that are retryable *for this call
    site only*, so one endpoint's quirk never widens the retry policy of the
    whole client. Exhausted attempts raise: a caller must never be able to read
    a failure as "empty".
    """
    retryable = RETRYABLE | set(extra_retryable or ())
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = (control_request_json(session, token, path, params, body, method)
                      if session is not None else call(token, path, params, body, method))
        except UploadError as exc:
            last = exc
            if not exc.retryable:
                raise
        except (urllib.error.URLError, NetdiskError) as exc:
            last = exc
        else:
            if result.get("errno", 0) == 0:
                return result
            if result.get("errno") not in retryable:
                return result
            last = NetdiskError(f"errno={result.get('errno')} {result.get('errmsg', '')}")
        if attempt < MAX_ATTEMPTS:
            backoff_sleep(attempt)
    if isinstance(last, UploadError):
        raise UploadError(ERR_NETWORK, f"{path} 重试 {MAX_ATTEMPTS} 次仍失败：{last.message}")
    raise NetdiskError(f"{path} 重试 {MAX_ATTEMPTS} 次仍失败：{last}")


def md5_of(path, limit=None, chunk_size=DEFAULT_BLOCK_SIZE):
    """Whole-file MD5 (single part).

    Note: Baidu's per-file md5 is masked and cannot be compared against this,
    so the uploader never uses it for verification -- it only ever compares
    per-block MD5s that the server echoes back.
    """
    digest = hashlib.md5()
    remaining = limit
    with open(path, "rb") as fh:
        while True:
            want = chunk_size if remaining is None else min(chunk_size, remaining)
            if want == 0:
                break
            chunk = fh.read(want)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def block_digests(path, chunk_size=DEFAULT_BLOCK_SIZE):
    """Per-block MD5 list, in file order. chunk_size is always explicit."""
    digests = []
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digests.append(hashlib.md5(chunk).hexdigest())
    return digests


def select_chunks(block_list, total):
    """Which block indices actually need uploading.

    Baidu's precreate may return a ``block_list`` of blocks the server does not
    already have. Two contract shapes are in the wild: the legacy one only
    signals whole-file instant upload via ``return_1``, the newer one returns
    a numeric index list. Anything missing, empty or malformed falls back to
    "upload everything", and out-of-range indices are never trusted.
    """
    if not isinstance(block_list, list) or not block_list:
        return list(range(total)), False
    chosen = []
    for item in block_list:
        if isinstance(item, bool) or not isinstance(item, int):
            return list(range(total)), False
        if item < 0 or item >= total:
            return list(range(total)), False
        chosen.append(item)
    if not chosen:
        return list(range(total)), False
    return sorted(set(chosen)), True


def ensure_dirs(token, remote_dir, cache):
    """Create every segment of remote_dir, skipping ones that already exist.

    Baidu silently renames a directory on conflict and returns the new name, so
    creating blindly would leave a timestamped empty directory behind on every
    upload. Look the segment up in the parent listing first, and when we do have
    to create it, follow the path the server actually reports back.
    """
    current = ""
    for segment in [s for s in remote_dir.strip("/").split("/") if s]:
        parent = current or "/"
        listing = cache.get(parent)
        if listing is None:
            listing = cache[parent] = list_dir(token, parent)

        existing = listing.get(segment)
        if existing is not None:
            current += "/" + segment
            continue

        result = call_retry(token, "/api/create",
                            body={"path": current + "/" + segment, "isdir": "1"})
        if result.get("errno") != 0:
            raise NetdiskError(
                f"创建目录 {current}/{segment} 失败：errno={result.get('errno')} "
                f"{result.get('errmsg')}")
        # Follow the path the server chose; it may differ if the name was taken.
        current = result.get("path") or (current + "/" + segment)
        listing[segment] = 1


def list_dir(token, remote_dir):
    """Return {filename: size} for one remote directory. Empty when absent.

    A directory that genuinely does not exist yet (errno -9) reads as empty so
    the upload path can create it; every other error is raised.
    """
    entries = {}
    for item in list_entries(token, remote_dir, strict=False):
        entries[item["path"].rsplit("/", 1)[-1]] = item.get("size", 0)
    return entries


def list_entries(token, remote_dir, strict=True):
    """Return the raw item dicts of one remote directory, paging to the end.

    Baidu caps a listing at `num` entries, so every page of the same level must
    be pulled or the caller silently loses files.

    ``strict=True`` raises on every errno, including -9: a caller that asked
    whether a directory exists wants "no" as an answer, not silence.
    ``strict=False`` maps only errno -9 to an empty listing, for the upload
    path that legitimately needs to create missing directories. Transient
    listing errno 600 is retried here, and only here, before giving up.
    """
    items = []
    page = 1
    while True:
        result = call_retry(
            token, "/api/list",
            params={"dir": remote_dir, "web": "1", "page": str(page),
                    "num": "1000", "order": "name", "desc": "0"},
            method="GET",
            extra_retryable=LIST_EXTRA_RETRYABLE,
        )
        if result.get("errno") != 0:
            errno = result.get("errno")
            errmsg = result.get("errmsg")
            # 只有「调用方明确说不介意 + 目录确实不存在」才是合法的空目录。
            # strict=True 下 -9 同样要抛错；任何其它 errno（含重试耗尽的 600）
            # 在两种模式下都必须中止，绝不能返回空列表继续。
            if not strict and errno == DIR_NOT_FOUND_ERRNO:
                return items
            raise NetdiskError(
                f"列出 {remote_dir} 失败：errno={errno} {errmsg}")
        batch = result.get("list", [])
        items.extend(batch)
        if len(batch) < 1000:
            return items
        page += 1


def delete_remote(token, remote_path):
    result = call_retry(
        token, "/api/filemanager",
        params={"opera": "delete", "async": "0"},
        body={"filelist": json.dumps([{"path": remote_path}])},
    )
    return result.get("errno") == 0


class BatchContext:
    """Per-batch state handed to workers.

    Deliberately a plain class with an explicit ``__repr__``: a dataclass would
    happily print the token and the live Session in any traceback or log line.
    """

    def __init__(self, token, settings, transport, cancel_event=None, warn=None):
        self.token = token
        self.settings = settings
        self.transport = transport
        self.cancel_event = cancel_event or threading.Event()
        self.warn = warn or (lambda message: None)

    def __repr__(self):
        settings = self.settings
        return (f"BatchContext(chunk={settings['chunk_size']}, "
                f"concurrency={settings['concurrency']}, "
                f"host_mode={settings['upload_host_mode']}, token=<redacted>)")


class PlanItem:
    """One planned file action, produced before any worker starts."""

    __slots__ = ("local_path", "relative_path", "remote_path", "size", "action", "existing_size")

    def __init__(self, local_path, relative_path, remote_path, size, action, existing_size=None):
        self.local_path = local_path
        self.relative_path = relative_path
        self.remote_path = remote_path
        self.size = size
        self.action = action                    # skip | upload | replace
        self.existing_size = existing_size

    def __repr__(self):
        return f"PlanItem({self.remote_path!r}, action={self.action}, size={self.size})"


class FileResult:
    """Structured per-file outcome; workers return these, the main thread aggregates."""

    __slots__ = ("relative_path", "status", "detail", "code", "size", "metrics")

    def __init__(self, relative_path, status, detail="", code="", size=0, metrics=None):
        self.relative_path = relative_path
        self.status = status                    # uploaded | skipped | failed
        self.detail = detail
        self.code = code
        self.size = size
        self.metrics = metrics or {}


def _chunk_count(size, chunk_size):
    if size <= 0:
        return 0
    return (size + chunk_size - 1) // chunk_size


def _perform_upload(ctx, item, chunk_size, metrics):
    """precreate -> 上传分片 -> create；分片大小被拒时整体回退一次到 4 MiB。

    The fallback re-reads the whole file, recomputes every block digest, issues
    a fresh precreate and restarts from block 0 under a new uploadid. Splitting
    one oversized block into two smaller ones under the *old* uploadid would
    desynchronise the server's block_list, so that is never attempted.
    """
    try:
        return _perform_upload_once(ctx, item, chunk_size, metrics)
    except UploadError as exc:
        if exc.code != ERR_CHUNK_SIZE or chunk_size <= DEFAULT_BLOCK_SIZE:
            raise
        metrics["chunkFallbackFrom"] = chunk_size
        metrics["chunkFallbackTo"] = DEFAULT_BLOCK_SIZE
        metrics["chunkSizeBytes"] = DEFAULT_BLOCK_SIZE
        ctx.warn(f"{item.relative_path} 分片 {human(chunk_size)} 被拒收，"
                 f"本文件回退到 {human(DEFAULT_BLOCK_SIZE)} 并从头重传")
        return _perform_upload_once(ctx, item, DEFAULT_BLOCK_SIZE, metrics)


def _perform_upload_once(ctx, item, chunk_size, metrics):
    """One full attempt at a single file using exactly ``chunk_size``."""
    settings = ctx.settings
    delay = settings["inter_chunk_delay"]
    token = ctx.token
    session = ctx.transport.session()
    metrics["chunkSizeBytes"] = chunk_size
    metrics["chunksTotal"] = _chunk_count(item.size, chunk_size)

    mark = time.monotonic()
    digests = block_digests(item.local_path, chunk_size)
    metrics["hashMs"] = int((time.monotonic() - mark) * 1000)

    mark = time.monotonic()
    pre = call_retry(token, "/api/precreate", body={
        "path": item.remote_path,
        "size": str(item.size),
        "isdir": "0",
        "autoinit": "1",
        "block_list": json.dumps(digests),
    }, session=session)
    metrics["precreateMs"] = int((time.monotonic() - mark) * 1000)
    if pre.get("errno") != 0:
        errno = pre.get("errno")
        if errno in (110, 111):
            raise UploadError(ERR_TOKEN, "百度网盘授权已失效，请重新授权", errno=errno)
        if errno == 31364:
            raise UploadError(ERR_CHUNK_SIZE, f"precreate 拒绝当前分片大小：errno={errno}", errno=errno)
        raise UploadError(ERR_PRECREATE,
                          f"precreate 失败：errno={errno} {pre.get('errmsg')}", errno=errno)

    uploadid = pre["uploadid"]
    # Both instant-upload contracts are in the wild: legacy `return_1 == 0` and
    # newer `return_type == 2`. Neither means "nothing to upload" when absent.
    instant = pre.get("return_1") == 0 or pre.get("return_type") == 2
    wanted, honoured_block_list = select_chunks(pre.get("block_list"), len(digests))
    metrics["chunksRequested"] = len(wanted)
    metrics["blockListHonoured"] = honoured_block_list

    if instant:
        metrics["chunksRequested"] = 0
        detail = "秒传命中（内容已存在于网盘）"
    else:
        mark = time.monotonic()
        host, query_mode = _resolve_host(ctx, session, token, item.remote_path, uploadid, metrics)
        metrics["locateMs"] = metrics.pop("_locateMs", 0)
        detail = _upload_blocks(ctx, item, session, host, query_mode, uploadid, digests,
                                wanted, chunk_size, delay, metrics)
        metrics["uploadMs"] = int((time.monotonic() - mark) * 1000)
        if metrics["chunksUploaded"] and metrics["uploadMs"]:
            payload_mb = metrics.get("payloadBytes", 0) / MIB
            metrics["payloadMBps"] = round(payload_mb / (metrics["uploadMs"] / 1000), 3)

    mark = time.monotonic()
    created = call_retry(token, "/api/create", body={
        "path": item.remote_path,
        "size": str(item.size),
        "isdir": "0",
        "uploadid": uploadid,
        # Always the full, ordered digest list -- even when only some blocks
        # were uploaded.
        "block_list": json.dumps(digests),
    }, session=session)
    metrics["createMs"] = int((time.monotonic() - mark) * 1000)
    if created.get("errno") != 0:
        errno = created.get("errno")
        if errno in (110, 111):
            raise UploadError(ERR_TOKEN, "百度网盘授权已失效，请重新授权", errno=errno)
        raise UploadError(ERR_CREATE,
                          f"create 失败：errno={errno} {created.get('errmsg')}", errno=errno)
    try:
        created_size = int(created.get("size"))
    except (TypeError, ValueError):
        created_size = -1
    if created_size != item.size:
        raise UploadError(ERR_SIZE_MISMATCH,
                          f"落盘大小不符：远端 {created.get('size')}，本地 {item.size}")
    final_path = created.get("path", item.remote_path)
    if final_path != item.remote_path:
        raise UploadError(ERR_RENAMED,
                          f"落盘路径被改名：期望 {item.remote_path}，实际 {final_path}")
    return detail


def upload_one_file(ctx, item, chunk_size=None):
    """Upload a single planned file. Returns a :class:`FileResult`.

    Every stage is timed so the metrics file can attribute the batch's speed to
    keep-alive, block size and concurrency rather than leaving us guessing.
    """
    settings = ctx.settings
    chunk_size = chunk_size or settings["chunk_size"]
    token = ctx.token
    metrics = {
        "relativePath": item.relative_path,
        "sizeBytes": item.size,
        "chunkSizeBytes": chunk_size,
        "chunksTotal": _chunk_count(item.size, chunk_size),
        "chunksRequested": 0,
        "chunksUploaded": 0,
        "retryCount": 0,
        "uploadHost": DEFAULT_UPLOAD_HOST,
        "queryPathMode": "legacy",
        "hashMs": 0, "precreateMs": 0, "locateMs": 0, "uploadMs": 0, "createMs": 0,
        "totalMs": 0, "payloadMBps": None, "effectiveMBps": None,
        "status": "failed", "code": "", "detail": "",
    }
    started = time.monotonic()

    try:
        if item.action == "replace":
            # Do not destroy the remote copy when the local source disappeared
            # or changed after planning. This check happens before delete.
            current_size = os.path.getsize(item.local_path)
            if current_size != item.size:
                raise UploadError(ERR_LOCAL_IO,
                                  f"本地文件在规划后发生变化：原 {item.size} 字节，现 {current_size} 字节")
            with open(item.local_path, "rb") as source_check:
                source_check.read(1)
            # --force and size-mismatch both land here, and both must delete the
            # stale remote file *before* precreate or the upload would pile up
            # a renamed duplicate.
            if not delete_remote(token, item.remote_path):
                raise UploadError(ERR_DELETE,
                                  f"远端存在同名文件（{item.existing_size} 字节），删除失败，未覆盖")
        if ctx.cancel_event.is_set():
            raise UploadError(ERR_CANCELLED, "已中断")

        detail = _perform_upload(ctx, item, chunk_size, metrics)

        metrics["status"] = "uploaded"
        metrics["detail"] = detail
        metrics["totalMs"] = int((time.monotonic() - started) * 1000)
        if metrics["totalMs"]:
            metrics["effectiveMBps"] = round(item.size / MIB / (metrics["totalMs"] / 1000), 3)
        metrics.pop("_locateMs", None)
        return FileResult(item.relative_path, "uploaded", detail, "", item.size, metrics)

    except UploadError as exc:
        metrics["status"] = "failed"
        metrics["code"] = exc.code
        metrics["detail"] = exc.message
        metrics["totalMs"] = int((time.monotonic() - started) * 1000)
        metrics.pop("_locateMs", None)
        return FileResult(item.relative_path, "failed", exc.message, exc.code, item.size, metrics)
    except (OSError, IOError) as exc:
        detail = f"本地文件读取失败：{exc.strerror or exc}"
        metrics.update({"status": "failed", "code": ERR_LOCAL_IO, "detail": detail,
                        "totalMs": int((time.monotonic() - started) * 1000)})
        metrics.pop("_locateMs", None)
        return FileResult(item.relative_path, "failed", detail, ERR_LOCAL_IO, item.size, metrics)
    except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
        detail = f"{ERR_INTERNAL}：{type(exc).__name__}"
        metrics.update({"status": "failed", "code": ERR_INTERNAL, "detail": detail,
                        "totalMs": int((time.monotonic() - started) * 1000)})
        metrics.pop("_locateMs", None)
        return FileResult(item.relative_path, "failed", detail, ERR_INTERNAL, item.size, metrics)


def _resolve_host(ctx, session, token, remote_path, uploadid, metrics):
    """Apply the host policy, timing locateupload for the metrics."""
    mode = ctx.settings["upload_host_mode"]
    if mode == "fixed":
        return DEFAULT_UPLOAD_HOST, "legacy"
    mark = time.monotonic()
    try:
        host, query_mode = resolve_upload_host(
            mode, session, token, remote_path, uploadid, warn=ctx.warn
        )
    finally:
        metrics["_locateMs"] = int((time.monotonic() - mark) * 1000)
    metrics["uploadHost"] = host
    metrics["queryPathMode"] = query_mode
    return host, query_mode


def _upload_blocks(ctx, item, session, host, query_mode, uploadid, digests, wanted,
                   chunk_size, delay, metrics):
    """Upload the selected blocks sequentially, retrying each in place.

    Each worker opens its own file handle and seeks per block: two threads must
    never share a file object.
    """
    retried = 0
    uploaded = 0
    payload = 0
    with open(item.local_path, "rb") as fh:
        for index in wanted:
            if ctx.cancel_event.is_set():
                raise UploadError(ERR_CANCELLED, "已中断")
            fh.seek(index * chunk_size)
            data = fh.read(chunk_size)
            expected = digests[index]
            if hashlib.md5(data).hexdigest() != expected:
                raise UploadError(ERR_CHUNK_MD5,
                                  f"分片 {index} 本地读取校验失败，文件可能已被修改")
            try:
                _, retries = upload_block_with_retry(
                    session, ctx.token, host, query_mode, item.remote_path,
                    uploadid, index, data, expected,
                    cancelled=ctx.cancel_event,
                    on_retry=lambda attempt, message: ctx.warn(
                        f"{item.relative_path} 分片 {index} 第 {attempt} 次失败，指数退避重试：{message}"),
                )
            except UploadError as exc:
                if exc.code == ERR_CHUNK_SIZE and chunk_size > DEFAULT_BLOCK_SIZE:
                    raise UploadError(
                        ERR_CHUNK_SIZE,
                        f"{exc.message}；本文件可用 --chunk-size 4M 或 --profile safe 重跑",
                    )
                raise
            retried += retries
            uploaded += 1
            payload += len(data)
            # inter-chunk delay is a per-worker politeness pause, NOT a global
            # QPS control: N workers sleeping does not equal one global rate.
            if delay:
                time.sleep(delay)
    metrics["retryCount"] = retried
    metrics["chunksUploaded"] = uploaded
    metrics["payloadBytes"] = payload
    if uploaded == len(digests):
        return f"上传 {uploaded} 个分片并逐片校验通过"
    return f"上传 {uploaded}/{len(digests)} 个分片并逐片校验通过（网盘已存在其余分片）"


def collect(src, excludes, includes):
    files = []
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(names):
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, src)
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat) for pat in excludes):
                continue
            if includes and not any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat)
                                    for pat in includes):
                continue
            files.append((path, rel))
    return files


def parse_chunk_size(value):
    """Parse "8M" / "16MB" / "4194304" into bytes. Raises ValueError with a Chinese hint."""
    raw = str(value).strip()
    # Accept both "8M"-style shorthand and a plain byte count.
    if raw.isdigit() and int(raw) > 64:
        return _validated_chunk_bytes(int(raw), raw)
    text = raw.upper().replace("IB", "").replace("B", "").replace("M", "")
    try:
        mib = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"无法解析分片大小：{value!r}，请使用 4M/8M/16M/32M")
    return _validated_chunk_bytes(mib * MIB, raw)


def _validated_chunk_bytes(size, original):
    if size not in (n * MIB for n in ALLOWED_CHUNK_SIZES):
        raise ValueError(
            f"分片大小只允许 {'/'.join(str(x) + 'M' for x in ALLOWED_CHUNK_SIZES)}，收到 {original!r}"
        )
    return size


def resolve_settings(args):
    """Merge the profile defaults with explicit CLI overrides, then validate.

    argparse defaults for the new flags are ``None`` so that an explicit flag
    always wins over the profile. Validation happens here, i.e. before any
    remote directory is created or any write API is called.
    """
    profile = getattr(args, "profile", None) or "safe"
    settings = dict(PROFILES[profile])

    chunk = getattr(args, "chunk_size", None)
    if chunk is not None:
        settings["chunk_size"] = parse_chunk_size(chunk)

    concurrency = getattr(args, "concurrency", None)
    if concurrency is not None:
        if not isinstance(concurrency, int) or not (1 <= concurrency <= 8):
            raise ValueError(f"--concurrency 只允许 1-8，收到 {concurrency!r}")
        settings["concurrency"] = concurrency

    delay = getattr(args, "inter_chunk_delay", None)
    if delay is not None:
        if delay < 0 or delay > 5:
            raise ValueError(f"--inter-chunk-delay 只允许 0-5 秒，收到 {delay!r}")
        settings["inter_chunk_delay"] = float(delay)

    host_mode = getattr(args, "upload_host_mode", None)
    if host_mode is not None:
        if host_mode not in UPLOAD_HOST_MODES:
            raise ValueError(f"--upload-host-mode 只允许 {'/'.join(UPLOAD_HOST_MODES)}")
        settings["upload_host_mode"] = host_mode

    settings["profile"] = profile
    return settings


def build_plan(token, dest, files, force, remote_cache, warn=None):
    """Decide, serially, what will happen to every file.

    Doing this before the thread pool starts is what keeps directory creation
    race-free: Baidu silently renames a directory when you create one that
    already exists, so two workers racing on ``ensure_dirs`` would leave a
    timestamped empty directory behind. Here every directory is created once,
    deepest-last, and every remote listing is fetched exactly once per
    directory -- no shared mutable cache to lock.

    Returns ``(plan_items, skipped_results)``.
    """
    warn = warn or (lambda message: None)

    # 1. all remote parent directories, shallow -> deep, then by name
    parents = set()
    for _local, rel in files:
        parts = rel.split(os.sep)[:-1]
        for depth in range(1, len(parts) + 1):
            parents.add(dest + "/" + "/".join(parts[:depth]))
    for directory in sorted(parents, key=lambda p: (p.count("/"), p)):
        ensure_dirs(token, directory, remote_cache)

    # 2. one listing per directory, then decide per file
    plan = []
    skipped = []
    for local, rel in files:
        remote_path = dest + "/" + rel.replace(os.sep, "/")
        remote_dir = remote_path.rsplit("/", 1)[0]
        name = remote_path.rsplit("/", 1)[-1]
        size = os.path.getsize(local)
        if remote_dir not in remote_cache:
            remote_cache[remote_dir] = list_dir(token, remote_dir)
        existing = remote_cache[remote_dir].get(name)

        if existing is None:
            action = "upload"
        elif existing == size and not force:
            action = "skip"
        else:
            action = "replace"
        plan.append(PlanItem(local, rel, remote_path, size, action, existing))
    return plan, skipped


def write_metrics(path, record):
    """Append one JSONL record.

    Always called from the main thread -- never from a worker -- so a batch
    being interrupted mid-write cannot leave two threads interleaving halves of
    a line and producing unparseable JSONL.
    """
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_batch(ctx, plan, metrics_path=None, progress=None):
    """Execute the plan with bounded file-level concurrency.

    At most ``concurrency`` futures exist at once. This matters for a directory
    with thousands of small files: Ctrl-C can stop future submission promptly
    instead of first queueing the whole batch in memory. Blocks inside a file
    remain strictly serial.
    """
    results = []
    concurrency = max(1, int(ctx.settings["concurrency"]))
    total = len(plan)

    def skipped_result(item):
        detail = f"远端已存在且大小一致（{item.size} 字节）"
        return FileResult(item.relative_path, "skipped", detail, "", item.size,
                          {"relativePath": item.relative_path, "sizeBytes": item.size,
                           "status": "skipped", "detail": detail})

    def record(result):
        results.append(result)
        if progress:
            progress(len(results), total,
                     sum(1 for existing in results if existing.status == "uploaded"), result)
        write_metrics(metrics_path, result.metrics)

    if concurrency == 1:
        for item in plan:
            if ctx.cancel_event.is_set():
                break
            record(skipped_result(item) if item.action == "skip" else upload_one_file(ctx, item))
        return results

    pending = []
    for item in plan:
        if item.action == "skip":
            record(skipped_result(item))
        else:
            pending.append(item)
    if not pending:
        return results

    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    pending_iter = iter(pending)
    pool = ThreadPoolExecutor(max_workers=concurrency)
    futures = {}

    def submit_one():
        if ctx.cancel_event.is_set():
            return False
        try:
            item = next(pending_iter)
        except StopIteration:
            return False
        futures[pool.submit(upload_one_file, ctx, item)] = item
        return True

    try:
        for _ in range(concurrency):
            if not submit_one():
                break
        while futures:
            done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in done:
                item = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - defensive boundary
                    result = FileResult(item.relative_path, "failed",
                                        f"{ERR_INTERNAL}：{type(exc).__name__}", ERR_INTERNAL,
                                        item.size,
                                        {"relativePath": item.relative_path, "status": "failed",
                                         "code": ERR_INTERNAL})
                record(result)
                submit_one()
    except KeyboardInterrupt:
        ctx.cancel_event.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return results


def summarise(results, wall_seconds, metrics_path=None):
    """Final batch summary. Keeps the three original counters and adds rates."""
    uploaded = [r for r in results if r.status == "uploaded"]
    skipped = [r for r in results if r.status == "skipped"]
    failed = [r for r in results if r.status == "failed"]
    sent = sum(r.size for r in uploaded)
    retries = sum(int(r.metrics.get("retryCount") or 0) for r in results)

    hosts = {}
    for result in results:
        host = result.metrics.get("uploadHost")
        if host:
            hosts[host] = hosts.get(host, 0) + 1

    print()
    print(f"完成：上传 {len(uploaded)}，跳过 {len(skipped)}，"
          f"失败 {len(failed)}，本次上传 {human(sent)}")
    print(f"总耗时 {wall_seconds:.1f}s，整批有效 {sent / MIB / wall_seconds:.2f} MB/s"
          if wall_seconds > 0 else f"总耗时 {wall_seconds:.1f}s")
    print(f"分片重试 {retries} 次" +
          (f"，上传域名分布 {hosts}" if hosts else ""))

    record = {
        "type": "batch_summary",
        "uploaded": len(uploaded),
        "skipped": len(skipped),
        "failed": len(failed),
        "uploadedBytes": sent,
        "wallSeconds": round(wall_seconds, 2),
        "effectiveMBps": round(sent / MIB / wall_seconds, 3) if wall_seconds > 0 else None,
        "chunkRetryCount": retries,
        "uploadHosts": hosts,
    }
    write_metrics(metrics_path, record)

    if failed:
        print("\n失败明细：")
        # Sorted by relative path so a re-run can be targeted without hunting.
        for result in sorted(failed, key=lambda r: r.relative_path):
            hint = ERROR_HINTS.get(result.code, "")
            suffix = f"（{hint}）" if hint else ""
            print(f"  [{result.code}] {result.relative_path}: {result.detail}{suffix}")


def human(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024


# --------------------------------------------------------------------------
# Read-only directory listing (list / tree / publish-catalog).
# These commands never touch local files and never upload anything.
# --------------------------------------------------------------------------

LIST_ROOT = os.environ.get("NETDISK_LIST_ROOT", "/AIGC").strip() or "/AIGC"
# scripts/ -> baidu-netdisk-archive/ -> skills/ -> 仓库根目录
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
DEFAULT_CATALOG_FILE = os.path.join(REPO_ROOT, "outputs", "netdisk-catalog.json")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tiff", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts"}
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".aiff", ".aif", ".opus"}
DOCUMENT_EXT = {
    ".md", ".markdown", ".txt", ".log", ".json", ".jsonl", ".csv", ".tsv", ".js", ".jsx", ".mjs",
    ".cjs", ".ts", ".tsx", ".py", ".sh", ".css", ".scss", ".html", ".htm", ".xml", ".yaml",
    ".yml", ".toml", ".ini", ".conf", ".sql", ".go", ".rs", ".java", ".c", ".cpp", ".h",
    ".swift", ".rb", ".php", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".rtf",
}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".dmg", ".pkg", ".iso"}

# Never let these reach stdout, a file we write, or a request body.
FORBIDDEN_KEYS = {"access_token", "accesstoken", "dlink", "cookie", "session_secret", "token", "refresh_token"}


def classify_kind(name, is_dir):
    if is_dir:
        return "directory"
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOCUMENT_EXT:
        return "document"
    if ext in ARCHIVE_EXT:
        return "archive"
    return "other"


def assert_within_root(path):
    """Refuse to walk outside the configured netdisk root (default /AIGC)."""
    normalised = "/" + (path or "").strip("/")
    root = "/" + LIST_ROOT.strip("/")
    if normalised != root and not normalised.startswith(root + "/"):
        raise NetdiskError(
            f"{normalised} 不在允许列出的网盘根目录 {root} 内。请只同步 AIGC 目录，避免把网盘其它内容暴露给工作台。")
    return normalised


def scrub(value):
    """Drop any credential-ish key that a future API change might start returning."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k.lower() not in FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def node_from_item(item):
    path = item.get("path") or ""
    name = path.rsplit("/", 1)[-1]
    is_dir = int(item.get("isdir") or 0) == 1
    return {
        "path": path,
        "name": name,
        "parentPath": path.rsplit("/", 1)[0] or "/",
        "isDir": is_dir,
        "sizeBytes": int(item.get("size") or 0),
        "serverMtime": int(item.get("server_mtime") or 0),
        "fsId": str(item.get("fs_id") or 0),
        "category": int(item.get("category") or 0),
        "kind": classify_kind(name, is_dir),
    }


def list_nodes(token, remote_dir):
    remote_dir = assert_within_root(remote_dir)
    nodes = [node_from_item(item) for item in list_entries(token, remote_dir)]
    nodes.sort(key=lambda node: (not node["isDir"], node["name"]))
    return nodes


def walk_tree(token, root, max_depth=None, max_nodes=None):
    """Recursively list a netdisk prefix, paging every level to completion.

    max_depth truncates the walk (reported in the output) but never truncates a
    single level: every directory at a given depth is fully listed.
    """
    root = assert_within_root(root)
    nodes = []
    truncated_depth = False
    truncated_nodes = False
    queue = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        if max_depth is not None and depth > max_depth:
            truncated_depth = True
            continue
        for node in list_nodes(token, current):
            if max_nodes is not None and len(nodes) >= max_nodes:
                truncated_nodes = True
                return nodes, truncated_depth, truncated_nodes
            nodes.append(node)
            if node["isDir"]:
                # 服务端返回的路径理论上都在根下，递归前再确认一次，
                # 避免异常响应把遍历带出 /AIGC
                queue.append((assert_within_root(node["path"]), depth + 1))
    return nodes, truncated_depth, truncated_nodes


def build_catalog(nodes, root, source="cli", complete=False):
    files = [node for node in nodes if not node["isDir"]]
    dirs = [node for node in nodes if node["isDir"]]
    return {
        "root": "/" + root.strip("/"),
        # 只有完整遍历了 /AIGC 才可以声明 complete；工作台据此决定要不要清理消失的节点
        "complete": bool(complete),
        "source": source,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fileCount": len(files),
        "dirCount": len(dirs),
        "totalBytes": sum(node["sizeBytes"] for node in files),
        "nodes": scrub(nodes),
    }


def post_catalog(api_url, catalog, cookie=None, payload_path=None):
    """Send the catalog to the workbench. Never prints the session cookie."""
    url = api_url.rstrip("/")
    if payload_path:
        body = json.dumps({"file": payload_path}).encode("utf-8")
    else:
        body = json.dumps(catalog).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise NetdiskError(f"发布目录到 {api_url} 失败：HTTP {exc.code} {detail}")
    except urllib.error.URLError as exc:
        raise NetdiskError(f"无法连接 {api_url}：{exc.reason}")


def cmd_list(args):
    token = load_token(args.token)
    nodes = list_nodes(token, args.path)
    if args.json:
        print(json.dumps({
            "root": assert_within_root(args.path),
            "fileCount": sum(1 for n in nodes if not n["isDir"]),
            "dirCount": sum(1 for n in nodes if n["isDir"]),
            "totalBytes": sum(n["sizeBytes"] for n in nodes if not n["isDir"]),
            "nodes": nodes,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"{assert_within_root(args.path)} 共 {len(nodes)} 项")
        for node in nodes:
            tag = "[目录]" if node["isDir"] else f"{human(node['sizeBytes'])}"
            print(f"  {tag:>10}  {node['path']}")
    return 0


def cmd_tree(args):
    token = load_token(args.token)
    nodes, truncated_depth, truncated_nodes = walk_tree(token, args.root, args.depth, args.max_nodes)
    is_full_root = assert_within_root(args.root) == assert_within_root(LIST_ROOT)
    catalog = build_catalog(nodes, args.root, "cli", complete=is_full_root and not truncated_depth and not truncated_nodes)
    catalog["truncatedByDepth"] = truncated_depth
    catalog["truncatedByNodeLimit"] = truncated_nodes
    if args.json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
    else:
        print(f"根目录 {catalog['root']}：{catalog['dirCount']} 个目录，"
              f"{catalog['fileCount']} 个文件，合计 {human(catalog['totalBytes'])}")
        if truncated_depth:
            print("  （已达到 --depth 上限，更深层级未列出）")
        if truncated_nodes:
            print("  （已达到 --max-nodes 上限，未列完）")
        for node in nodes:
            tag = "[目录]" if node["isDir"] else human(node["sizeBytes"])
            print(f"  {tag:>10}  {node['path']}")
    return 0


def cmd_publish_catalog(args):
    token = load_token(args.token)
    nodes, truncated_depth, truncated_nodes = walk_tree(token, args.root, args.depth, args.max_nodes)
    is_full_root = assert_within_root(args.root) == assert_within_root(LIST_ROOT)
    catalog = build_catalog(nodes, args.root, args.source,
                            complete=is_full_root and not truncated_depth and not truncated_nodes)
    catalog["truncatedByDepth"] = truncated_depth
    catalog["truncatedByNodeLimit"] = truncated_nodes
    if not catalog["complete"]:
        print("注意：这是截断或子目录快照，工作台只做新增/更新，不会清理未列出的节点。", file=sys.stderr)

    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=2)
    print(f"目录快照已写入 {out}（{catalog['dirCount']} 个目录，{catalog['fileCount']} 个文件，"
          f"{human(catalog['totalBytes'])}）", file=sys.stderr)

    if args.api:
        api = args.api.rstrip("/")
        # 默认把目录 JSON 直接 POST 到 catalog 接口；只有明确指定 import-local-catalog 才走本机文件导入。
        if api.endswith("/api/netdisk/import-local-catalog"):
            endpoint, relative = api, out
        elif api.endswith("/api/netdisk/catalog"):
            endpoint, relative = api, None
        else:
            endpoint, relative = f"{api}/api/netdisk/catalog", None
        if relative is not None and not os.path.exists(out):
            raise NetdiskError("本机导入模式需要先写出快照文件")
        cookie = os.environ.get(args.cookie_env) if args.cookie_env else None
        result = post_catalog(endpoint, catalog, cookie or args.cookie, relative)
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps({
            "written": out,
            "root": catalog["root"],
            "fileCount": catalog["fileCount"],
            "dirCount": catalog["dirCount"],
            "totalBytes": catalog["totalBytes"],
            "hint": "在 AIGC 工作台打开「工作文档」，或用 --api 直接发布到本机 API。",
        }, ensure_ascii=False))
    return 0


def cmd_quota(args):
    """报告网盘容量。

    注意：/api/quota 的 free 字段对会员账号不可靠 —— 带 checkfree=1 会返回普通用户的
    基础免费空间（固定 100 GB 上限），不带则返回 0，都不反映 SVIP 权益。
    实测 total 与 used 是准确的，所以可用空间一律用 total - used 计算。
    """
    token = load_token(args.token)
    result = call_retry(token, "/api/quota", params={"checkfree": "1"}, method="GET")
    if result.get("errno") != 0:
        raise NetdiskError(f"查询容量失败：errno={result.get('errno')} {result.get('errmsg')}")
    total = int(result.get("total") or 0)
    used = int(result.get("used") or 0)
    payload = {
        "totalBytes": total,
        "usedBytes": used,
        "freeBytes": max(0, total - used),
        "freeSource": "total-minus-used",
    }
    api_free = int(result.get("free") or 0)
    if api_free and abs(api_free - payload["freeBytes"]) > 1024 ** 3:
        # 会员账号会看到 100 GB 这种明显偏小的值，明确标注以免误判
        payload["apiReportedFreeBytes"] = api_free
        payload["warning"] = "接口返回的 free 未包含会员权益，可用空间请以 freeBytes 为准"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


COMMANDS = {
    "list": cmd_list,
    "tree": cmd_tree,
    "publish-catalog": cmd_publish_catalog,
    "quota": cmd_quota,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Subcommands are dispatched before the legacy parser runs so that the
    # original `netdisk_sync.py <src> --dest ... --dry-run` form keeps working
    # unchanged, including when a local directory happens to be named `list`.
    if argv and argv[0] in COMMANDS:
        name = argv[0]
        parser = argparse.ArgumentParser(prog=f"netdisk_sync.py {name}")
        parser.add_argument("--token", help="Access Token，默认读环境变量或本地 token 文件")
        if name == "quota":
            parser.add_argument("--json", action="store_true", help="保留（容量固定输出 JSON）")
            args = parser.parse_args(argv[1:])
        elif name == "list":
            parser.add_argument("path", default=LIST_ROOT, help=f"网盘目录，默认 {LIST_ROOT}")
            parser.add_argument("--json", action="store_true", help="输出 JSON")
            args = parser.parse_args(argv[1:])
        else:
            parser.add_argument("--root", default=LIST_ROOT, help=f"网盘根目录，默认 {LIST_ROOT}")
            parser.add_argument("--out", default=DEFAULT_CATALOG_FILE, help="快照 JSON 输出路径")
            parser.add_argument("--api", help="工作台 API，例如 http://127.0.0.1:18080")
            parser.add_argument("--cookie", help="工作台登录 Cookie（不会打印；命令行对本机其它进程可见，优先用 --cookie-env）")
            parser.add_argument("--cookie-env", default="", metavar="VAR",
                                help="从指定环境变量读取工作台 Cookie，优先于 --cookie。推荐 AIGC_SESSION_COOKIE")
            parser.add_argument("--source", default="cli", help="来源标记：cli 或 mcp")
            parser.add_argument("--depth", type=int, help="最大递归深度")
            parser.add_argument("--max-nodes", type=int, help="最多列出多少个节点")
            if name == "tree":
                parser.add_argument("--json", action="store_true", help="输出 JSON")
                args = parser.parse_args(argv[1:])
            else:
                args = parser.parse_args(argv[1:])
        try:
            return COMMANDS[name](args)
        except NetdiskError as exc:
            print(json.dumps({"error": str(exc), "command": name}, ensure_ascii=False), file=sys.stderr)
            return 2

    parser = argparse.ArgumentParser(
        description="把本地目录同步到百度网盘（分片上传 + 逐片校验）",
        epilog=("只读子命令（不触碰本地文件、不上传）：\n"
                "  list <网盘目录> --json              列出一层目录\n"
                "  tree --root /AIGC --json            递归列出目录树\n"
                "  publish-catalog --root /AIGC       写出目录快照，可选发布到工作台\n"
                "  quota --json                       查询网盘容量\n"
                "不带子命令时保持原有行为：把本地目录同步上传到网盘。"),)
    parser.add_argument("src", nargs="?", default=DEFAULT_SRC, help="本地源目录")
    parser.add_argument("--dest", default=DEFAULT_DEST, help="网盘目标目录")
    parser.add_argument("--token", help="Access Token，默认读环境变量或本地 token 文件")
    parser.add_argument("--include", action="append", default=[],
                        help="只同步匹配的文件/目录名，可重复")
    parser.add_argument("--exclude", action="append", default=[],
                        help="排除匹配的文件/目录名，可重复")
    parser.add_argument("--force", action="store_true",
                        help="即使远端已存在（且大小一致）也删除旧文件后重新上传")
    parser.add_argument("--dry-run", action="store_true", help="只列出计划，不实际上传")
    parser.add_argument("--limit", type=int, help="最多处理 N 个文件，用于试跑")

    group = parser.add_argument_group("上传性能（默认 profile=safe，行为与旧版一致）")
    group.add_argument("--profile", choices=("safe", "fast"), default=None,
                       help="safe=保持原行为（4 MiB/串行/固定域名）；fast=面向会员的大批量上传"
                            "（8 MiB/文件并发 4/自动定位上传域名）")
    group.add_argument("--chunk-size", default=None, metavar="4M|8M|16M|32M",
                       help="分片大小，仅允许 4/8/16/32 MiB。普通用户通常只支持 4 MiB，"
                            "会员最高 32 MiB")
    group.add_argument("--concurrency", type=int, default=None,
                       help="文件级并发数 1-8（默认 safe=1，fast=4）。不做嵌套的分片并发")
    group.add_argument("--inter-chunk-delay", type=float, default=None, metavar="SECONDS",
                       help="同一 worker 每个分片之间的停顿秒数 0-5"
                            "（safe=0.15，fast=0.03）。这不是全局限速")
    group.add_argument("--upload-host-mode", choices=UPLOAD_HOST_MODES, default=None,
                       help="fixed=固定 d.pcs.baidu.com（默认）；auto=先问百度推荐域名并回退；"
                            "required=推荐域名失败则该文件失败")
    group.add_argument("--metrics-file", default=None, metavar="PATH",
                       help="把每文件指标以 JSONL 追加写入该文件（主线程写入，不含 Token）")
    args = parser.parse_args(argv)

    src = os.path.abspath(os.path.expanduser(args.src))
    if not os.path.isdir(src):
        print(f"错误：源目录不存在 {src}", file=sys.stderr)
        return 2

    # Validate every upload parameter before touching the network: an invalid
    # value must fail without having created a remote directory or called a
    # write API.
    try:
        settings = resolve_settings(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    excludes = args.exclude or ["*.DS_Store"]
    files = collect(src, excludes, args.include)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("没有匹配到文件。")
        return 0

    dest = "/" + args.dest.strip("/")
    total_bytes = sum(os.path.getsize(p) for p, _ in files)
    print(f"源目录    {src}")
    print(f"目标      {dest}")
    print(f"待处理    {len(files)} 个文件，合计 {human(total_bytes)}")
    if args.dry_run:
        for path, rel in files:
            print(f"  [计划] {rel}  ({human(os.path.getsize(path))})")
        return 0

    try:
        token = load_token(args.token)
    except NetdiskError as exc:
        sys.stdout.flush()
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    transport = Transport(pool_size=settings["concurrency"])
    ctx = BatchContext(token, settings, transport,
                       warn=lambda message: (print(f"    ! {message}", file=sys.stderr), sys.stderr.flush()))
    wall_start = time.monotonic()
    try:
        plan, _skipped = build_plan(token, dest, files, args.force, {}, warn=ctx.warn)
        todo = [item for item in plan if item.action != "skip"]
        skip_count = len(plan) - len(todo)
        print(f"计划：上传/覆盖 {len(todo)}，跳过 {skip_count}，"
              f"分片 {human(settings['chunk_size'])}，文件并发 {settings['concurrency']}，"
              f"上传域名 {settings['upload_host_mode']}")

        def progress(done, total, uploaded_now, result):
            label = {"uploaded": "已上传", "skipped": "跳过", "failed": "失败"}[result.status]
            suffix = f"[{result.code}] " if result.code else ""
            print(f"[完成 {done}/{total} | 上传中 {settings['concurrency']}] "
                  f"{result.relative_path} -> {label} {suffix}{result.detail}")
            sys.stdout.flush()

        results = run_batch(ctx, plan, args.metrics_file, progress)
        summarise(results, time.monotonic() - wall_start, args.metrics_file)
        return 1 if any(r.status == "failed" for r in results) else 0
    except KeyboardInterrupt:
        ctx.cancel_event.set()
        print("\n已中断。已上传的文件保持不变，未开始的不会上传。", file=sys.stderr)
        return 130
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。已上传的文件保持不变，未开始的不会上传。", file=sys.stderr)
        sys.exit(130)