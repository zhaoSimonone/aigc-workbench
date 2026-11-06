#!/usr/bin/env python3
"""List, download and upload AIGC Shelf assets; aigc pan uploads to Baidu Netdisk."""

import argparse
import shlex
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shelf_client import APIError, Client, DEFAULT_API, default_cookie_file

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DEFAULT_LIMIT = 20


def is_uuid(value):
    return bool(UUID_RE.match(str(value or "").strip()))


def connect(args):
    client = Client(args.api, args.cookie_file)
    return client, client.ensure_login()


def asset_filters(args):
    filters = {}
    query = (getattr(args, "q", None) or getattr(args, "search", None) or "").strip()
    if query:
        filters["q"] = query
    folder = (getattr(args, "folder", None) or "").strip()
    if folder:
        filters["folder"] = folder
    return filters


def format_uploaded_at(value):
    raw = str(value or "").strip()
    if not raw:
        return "-"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def public_asset(asset):
    created = asset.get("createdAt") or asset.get("date")
    return {
        "id": asset.get("id"),
        "name": asset.get("name"),
        "folder": asset.get("folder"),
        "type": asset.get("type"),
        "contentType": asset.get("contentType"),
        "size": asset.get("size"),
        "sizeBytes": asset.get("sizeBytes"),
        "duration": asset.get("duration"),
        "durationSeconds": asset.get("durationSeconds"),
        "date": created,
        "createdAt": created,
        "uploadedAt": format_uploaded_at(created),
        "tags": asset.get("tags") or [],
        "characterName": asset.get("characterName") or "",
    }


def format_row(asset):
    duration = asset.get("duration") or "-"
    size = asset.get("size") or "-"
    folder = asset.get("folder") or "-"
    kind = asset.get("type") or "-"
    name = asset.get("name") or ""
    uploaded = format_uploaded_at(asset.get("createdAt") or asset.get("date"))
    return f"{asset.get('id')}  {uploaded}  {folder}  {kind}  {size}  {duration}  {name}"


def cmd_login(args):
    try:
        client = Client(args.api, args.cookie_file)
        if not args.check:
            client.ensure_login()
        user = Client(args.api, client.cookie_file).require("GET", "/api/auth/me").get("user", {})
    except APIError as error:
        if error.status == 401:
            message = ("会话已失效或尚未登录，请去掉 --check 重新运行。"
                       if args.check else "登录失败，请检查邮箱和密码后重试。")
        else:
            message = f"认证失败：服务返回 HTTP {error.status}。"
        raise SystemExit(message)
    except (URLError, OSError):
        raise SystemExit("认证失败：请检查网络、证书和会话文件读写权限。")
    except ValueError as error:
        raise SystemExit(f"认证配置错误：{error}")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("登录已取消。请在终端中运行脚本并输入邮箱和密码。")
    if args.json:
        print(json.dumps({
            "ok": True,
            "user": user,
            "cookieFile": str(client.cookie_file),
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"API 认证有效，会话文件：{client.cookie_file}")
    return 0


def cmd_list(args):
    if args.page < 1:
        raise SystemExit("--page 必须从 1 开始")
    if args.limit < 1 or args.limit > 200:
        raise SystemExit("--limit 必须是 1-200")
    client, user = connect(args)
    filters = asset_filters(args)
    payload = client.list_assets_page(limit=args.limit, offset=(args.page - 1) * args.limit, **filters)
    assets = payload.get("assets") or []
    total = int(payload.get("total", len(assets)))
    has_more = bool(payload.get("hasMore"))
    pages = max(1, (total + args.limit - 1) // args.limit)
    result = {
        "user": user.get("name") or user.get("email"),
        "total": total,
        "page": args.page,
        "pages": pages,
        "limit": args.limit,
        "offset": (args.page - 1) * args.limit,
        "hasMore": has_more,
        "q": filters.get("q", ""),
        "folder": filters.get("folder", ""),
        "assets": [public_asset(asset) for asset in assets],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    who = result["user"] or "当前账号"
    bits = [f"第 {args.page}/{pages} 页", f"共 {total} 条", f"每页 {args.limit} 条", f"账号 {who}"]
    if result["folder"]:
        bits.append(f"文件夹 {result['folder']}")
    if result["q"]:
        bits.append(f"搜索「{result['q']}」")
    print("  ".join(bits))
    if not assets:
        print("没有素材")
        return 0
    print("ID                                    上传时间             文件夹    类型   大小      时长   名称")
    for asset in assets:
        print(format_row(asset))
    if has_more:
        next_cmd = f"--page {args.page + 1}"
        if result["q"]:
            next_cmd += f" -q {shlex.quote(result['q'])}"
        if result["folder"]:
            next_cmd += f" --folder {shlex.quote(result['folder'])}"
        if args.limit != DEFAULT_LIMIT:
            next_cmd += f" --limit {args.limit}"
        print(f"还有更多。下一页：aigc list {next_cmd}")
    return 0


FILE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
}


def resolve_destination(output, filename, multiple):
    raw = "." if output in (None, "") else output
    destination = Path(raw).expanduser()
    as_directory = (
        multiple
        or str(raw).endswith(("/", os.sep))
        or destination.exists() and destination.is_dir()
        or (not destination.exists() and destination.suffix.lower() not in FILE_SUFFIXES)
    )
    if multiple and not as_directory:
        raise SystemExit("下载多条时 --output 必须是目录")
    if as_directory:
        destination.mkdir(parents=True, exist_ok=True)
        return destination / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def resolve_target(client, target, folder=None):
    target = str(target).strip()
    if not target:
        raise SystemExit("素材 ID 或名称不能为空")
    if is_uuid(target):
        return client.get_asset(target)
    filters = {"q": target}
    if folder:
        filters["folder"] = folder
    payload = client.list_assets_page(limit=50, offset=0, **filters)
    assets = payload.get("assets") or []
    exact = [asset for asset in assets if asset.get("name") == target]
    if len(exact) == 1:
        return exact[0]
    matches = exact or assets
    if not matches:
        raise SystemExit(f"没有名称包含「{target}」的素材")
    print(f"「{target}」命中多条，请改用素材 ID：", file=sys.stderr)
    for asset in matches:
        print(f"  {asset.get('id')}  {asset.get('folder')}  {asset.get('name')}", file=sys.stderr)
    if payload.get("hasMore") and not exact:
        print("  ...还有更多结果", file=sys.stderr)
    raise SystemExit(1)


def cmd_upload(args):
    # 其余参数原样交给 upload_assets，这样 --name/--tags/--folder 不会被 aigc 吃掉。
    from upload_assets import main as upload_main
    result = upload_main(list(args.upload_args or []))
    return 0 if result is None else result


def cmd_pan(args):
    # 其余参数原样交给 pan.py，再转给百度网盘 netdisk_sync。
    from pan import main as pan_main
    result = pan_main(list(args.pan_args or []))
    return 0 if result is None else result


def cmd_download(args):
    targets = [item for item in list(args.targets or []) + list(args.id or []) if item]
    if args.name:
        targets.append(args.name)
    if not targets:
        raise SystemExit("请提供素材 ID 或名称")
    client, _user = connect(args)
    assets = [resolve_target(client, target, args.folder) for target in targets]
    results = []
    for asset in assets:
        url, filename = client.download_url(asset["id"])
        destination = resolve_destination(args.output, filename, multiple=len(assets) > 1)
        if destination.exists() and not args.force:
            results.append({
                "path": str(destination),
                "size": destination.stat().st_size,
                "cached": True,
                "assetId": asset.get("id"),
                "name": asset.get("name"),
            })
            continue
        size = client.download(url, destination)
        results.append({
            "path": str(destination),
            "size": size,
            "cached": False,
            "assetId": asset.get("id"),
            "name": asset.get("name"),
        })
    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2))
        return 0
    for item in results:
        state = "已存在" if item["cached"] else "已下载"
        print(f"{state}  {item['name']}")
        print(f"  {item['path']}  ({item['size']} 字节)")
    return 0


def add_common_args(parser):
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API), help="素材库服务地址")
    parser.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", str(default_cookie_file())),
                        help="会话文件路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="aigc",
        description="查看、下载和上传 AIGC Shelf 素材，也可把本地文件传到百度网盘。只输出文字列表，不预览图片。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="登录素材库并保存会话")
    add_common_args(login)
    login.add_argument("--check", action="store_true", help="只检查会话，不提示输入或重新登录")
    login.set_defaults(func=cmd_login)

    listing = sub.add_parser("list", aliases=["ls"], help="分页列出素材")
    add_common_args(listing)
    listing.add_argument("search", nargs="?", default="", help="名称关键词，也可用 -q")
    listing.add_argument("-q", "--query", "--search", dest="q", default="", help="按名称、标签或角色名搜索")
    listing.add_argument("--folder", default="", help="按素材库筛选，例如 服装库")
    listing.add_argument("--page", type=int, default=1, help="页码，从 1 开始")
    listing.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="每页条数，最大 200")
    listing.set_defaults(func=cmd_list)

    download = sub.add_parser("download", aliases=["get", "dl"], help="下载素材到本地")
    add_common_args(download)
    download.add_argument("targets", nargs="*", help="素材 ID 或名称")
    download.add_argument("--id", action="append", default=[], help="素材 ID，可重复")
    download.add_argument("--name", default="", help="按名称匹配；命中多条时请改用 ID")
    download.add_argument("-o", "--output", default=".", help="保存路径：目录或完整文件名")
    download.add_argument("--folder", default="", help="按名称下载时限定素材库")
    download.add_argument("--force", action="store_true", help="覆盖已存在的本地文件")
    download.set_defaults(func=cmd_download)

    # 仅用于 `aigc -h` 列出子命令。真正的参数在 main() 里原样转给 upload_assets。
    upload = sub.add_parser("upload", aliases=["up"], add_help=False, help="上传本地图片或视频到素材库")
    upload.add_argument("upload_args", nargs=argparse.REMAINDER)
    upload.set_defaults(func=cmd_upload)

    # 同样只占位；`aigc pan --help` / `--dest` 由 pan.py 自己解析。
    pan = sub.add_parser("pan", aliases=["netdisk"], add_help=False, help="上传本地文件或目录到百度网盘")
    pan.add_argument("pan_args", nargs=argparse.REMAINDER)
    pan.set_defaults(func=cmd_pan)
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        # 先截走 upload / pan，避免 argparse 把 --help / --folder / --dest 当成 aigc 自己的参数。
        if argv and argv[0] in ("upload", "up"):
            return cmd_upload(argparse.Namespace(upload_args=argv[1:]))
        if argv and argv[0] in ("pan", "netdisk"):
            return cmd_pan(argparse.Namespace(pan_args=argv[1:]))
        parser = build_parser()
        args = parser.parse_args(argv)
        return args.func(args)
    except SystemExit as error:
        if isinstance(error.code, str) and error.code:
            print(error.code, file=sys.stderr)
            return 1
        return 0 if error.code in (0, None) else int(error.code)
    except APIError as error:
        if error.status == 401:
            print("会话已失效，请重新登录。", file=sys.stderr)
        else:
            print(f"请求失败：{error}", file=sys.stderr)
        return 1
    except (URLError, OSError, ValueError, RuntimeError) as error:
        print(f"失败：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
