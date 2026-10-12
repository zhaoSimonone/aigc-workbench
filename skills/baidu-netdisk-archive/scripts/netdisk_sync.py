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
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://pan.baidu.com"
UPLOAD_API = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"
BLOCK_SIZE = 4 * 1024 * 1024
TOKEN_FILE = os.path.expanduser("~/.config/aigc-workbench/baidu_netdisk_token")

DEFAULT_SRC = "/Users/simon/Desktop/AIGC"
DEFAULT_DEST = "/AIGC"

# errno values that mean "retry shortly" rather than "give up".
RETRYABLE = {-6, -8, -11, 112, 158}
MAX_ATTEMPTS = 4


class NetdiskError(Exception):
    pass


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
    data = urllib.parse.urlencode(body).encode() if body else not None
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data else {}
    if method == "GET":
        data, headers = None, {}
    raw = _request(url, data, headers)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise NetdiskError(f"{path} 返回了非 JSON 响应：{raw[:200]!r}")


def call_retry(token, path, params=None, body=None, method="POST"):
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = call(token, path, params, body, method)
        except (urllib.error.URLError, NetdiskError) as exc:
            last = exc
        else:
            if result.get("errno", 0) == 0:
                return result
            if result.get("errno") not in RETRYABLE:
                return result
            last = NetdiskError(f"errno={result.get('errno')} {result.get('errmsg', '')}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2 ** attempt, 10))
    raise NetdiskError(f"{path} 重试 {MAX_ATTEMPTS} 次仍失败：{last}")


def md5_of(path, limit=None):
    digest = hashlib.md5()
    remaining = limit
    with open(path, "rb") as fh:
        while True:
            want = BLOCK_SIZE if remaining is None else min(BLOCK_SIZE, remaining)
            if want == 0:
                break
            chunk = fh.read(want)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def block_digests(path):
    digests = []
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(BLOCK_SIZE)
            if not chunk:
                break
            digests.append(hashlib.md5(chunk).hexdigest())
    return digests


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

    Stay tolerant on purpose: the upload path calls this for target directories
    that may not exist yet, and used to treat a failed listing as "empty".
    """
    entries = {}
    for item in list_entries(token, remote_dir, strict=False):
        entries[item["path"].rsplit("/", 1)[-1]] = item.get("size", 0)
    return entries


def list_entries(token, remote_dir, strict=True):
    """Return the raw item dicts of one remote directory, paging to the end.

    Baidu caps a listing at `num` entries, so every page of the same level must
    be pulled or the caller silently loses files. Listing commands want a hard
    error here; the upload path keeps the historical lenient behaviour.
    """
    items = []
    page = 1
    while True:
        result = call_retry(
            token, "/api/list",
            params={"dir": remote_dir, "web": "1", "page": str(page),
                    "num": "1000", "order": "name", "desc": "0"},
            method="GET",
        )
        if result.get("errno") != 0:
            if strict:
                raise NetdiskError(
                    f"列出 {remote_dir} 失败：errno={result.get('errno')} {result.get('errmsg')}")
            return items
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


def put_block(token, uploadid, partseq, data):
    boundary = "----zcode" + urllib.parse.quote(uploadid) + str(partseq)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="part{partseq}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    url = (
        f"{UPLOAD_API}?method=upload&type=tmpfile"
        f"&access_token={urllib.parse.quote(token)}"
        f"&path=/apps/BaiduPCS/{uploadid}&uploadid={uploadid}&partseq={partseq}"
    )
    raw = _request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise NetdiskError(f"分块 {partseq} 返回非 JSON：{raw[:200]!r}")


def upload_file(token, local_path, remote_path, force, remote_cache):
    """Upload one file. Returns ("uploaded"|"skipped", detail)."""
    size = os.path.getsize(local_path)
    remote_dir = remote_path.rsplit("/", 1)[0]
    name = remote_path.rsplit("/", 1)[-1]

    if remote_dir not in remote_cache:
        remote_cache[remote_dir] = list_dir(token, remote_dir)
    existing = remote_cache[remote_dir].get(name)

    if existing is not None and not force:
        if existing == size:
            return "skipped", f"远端已存在且大小一致（{size} 字节）"
        if not delete_remote(token, remote_path):
            return "failed", f"远端存在同名文件但大小不同（{existing} vs {size}），且删除失败"
        print(f"    已删除远端旧版本（{existing} 字节），重新上传")

    ensure_dirs(token, remote_dir, remote_cache)
    # ensure_dirs may have just created remote_dir, so the listing taken before
    # it existed is stale or absent.
    if remote_dir not in remote_cache:
        remote_cache[remote_dir] = list_dir(token, remote_dir)
    digests = block_digests(local_path)
    pre = call_retry(token, "/api/precreate", body={
        "path": remote_path,
        "size": str(size),
        "isdir": "0",
        "autoinit": "1",
        "block_list": json.dumps(digests),
    })
    if pre.get("errno") != 0:
        raise NetdiskError(f"precreate 失败：errno={pre.get('errno')} {pre.get('errmsg')}")

    uploadid = pre["uploadid"]
    if pre.get("return_1") == 0:
        detail = "秒传命中（内容已存在于网盘）"
    else:
        with open(local_path, "rb") as fh:
            for index in range(len(digests)):
                chunk = fh.read(BLOCK_SIZE)
                reply = put_block(token, uploadid, index, chunk)
                if reply.get("md5") != digests[index]:
                    raise NetdiskError(
                        f"分块 {index} 校验失败：服务端回显 {reply.get('md5')}，"
                        f"本地计算 {digests[index]}"
                    )
                time.sleep(0.15)
        detail = f"上传 {len(digests)} 个分片并逐片校验通过"

    created = call_retry(token, "/api/create", body={
        "path": remote_path,
        "size": str(size),
        "isdir": "0",
        "uploadid": uploadid,
        "block_list": json.dumps(digests),
    })
    if created.get("errno") != 0:
        raise NetdiskError(f"create 失败：errno={created.get('errno')} {created.get('errmsg')}")

    if created.get("size") != size:
        raise NetdiskError(f"落盘大小不符：远端 {created.get('size')}，本地 {size}")
    final_path = created.get("path", remote_path)
    if final_path != remote_path:
        raise NetdiskError(f"落盘路径被改名：期望 {remote_path}，实际 {final_path}")

    remote_cache[remote_dir][name] = size
    return "uploaded", detail


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
    token = load_token(args.token)
    result = call_retry(token, "/api/quota", params={"checkfree": "1"}, method="GET")
    if result.get("errno") != 0:
        raise NetdiskError(f"查询容量失败：errno={result.get('errno')} {result.get('errmsg')}")
    print(json.dumps({
        "totalBytes": int(result.get("total") or 0),
        "usedBytes": int(result.get("used") or 0),
        "freeBytes": int(result.get("free") or 0),
    }, ensure_ascii=False, indent=2))
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
    parser.add_argument("--force", action="store_true", help="即使远端已存在也重新上传")
    parser.add_argument("--dry-run", action="store_true", help="只列出计划，不实际上传")
    parser.add_argument("--limit", type=int, help="最多处理 N 个文件，用于试跑")
    args = parser.parse_args(argv)

    src = os.path.abspath(os.path.expanduser(args.src))
    if not os.path.isdir(src):
        print(f"错误：源目录不存在 {src}", file=sys.stderr)
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

    ensure_dirs(token, dest, {})
    remote_cache = {}
    results = {"uploaded": [], "skipped": [], "failed": []}
    sent = 0

    for index, (path, rel) in enumerate(files, 1):
        remote_path = dest + "/" + rel
        size = os.path.getsize(path)
        print(f"[{index}/{len(files)}] {rel}  ({human(size)})")
        try:
            status, detail = upload_file(token, path, remote_path, args.force, remote_cache)
        except Exception as exc:                      # keep going on per-file errors
            status, detail = "failed", str(exc)
        results[status].append((rel, detail))
        if status == "uploaded":
            sent += size
            print(f"    -> 已上传（{detail}）")
        elif status == "skipped":
            print(f"    -> 跳过（{detail}）")
        else:
            print(f"    -> 失败：{detail}", file=sys.stderr)
        sys.stdout.flush()

    print()
    print(f"完成：上传 {len(results['uploaded'])}，跳过 {len(results['skipped'])}，"
          f"失败 {len(results['failed'])}，本次上传 {human(sent)}")
    if results["failed"]:
        print("\n失败明细：")
        for rel, detail in results["failed"]:
            print(f"  {rel}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。已上传的文件保持不变，未开始的不会上传。", file=sys.stderr)
        sys.exit(130)