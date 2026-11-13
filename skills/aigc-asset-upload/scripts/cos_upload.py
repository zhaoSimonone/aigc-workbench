#!/usr/bin/env python3
"""Upload to COS using a Shelf session, or explicitly supplied local credentials."""

import argparse
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse

from shelf_client import APIError, Client, DEFAULT_API, default_cookie_file

BUCKET = "aigc-1257258774"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="aigc cos upload",
        description="上传文件到 aigc-1257258774 桶并返回签名访问链接；默认目录 file/。",
    )
    parser.add_argument("file", type=Path, help="本地文件路径（支持任意文件类型）")
    parser.add_argument("directory", nargs="?", help="COS 目录，默认 file；自动保留本地文件名")
    parser.add_argument("--path", "--dest", dest="remote_path", help="COS 目录，也可用第二个位置参数")
    parser.add_argument("--region", help="本机密钥模式的桶地域；会话模式由服务端确定")
    parser.add_argument("--env-file", type=Path, help="指定 COS 配置文件并启用本机密钥模式")
    parser.add_argument("--local-credentials", action="store_true", help="使用本机 COS 密钥和 Node.js SDK；默认复用 aigc login 会话")
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API), help="素材库服务地址")
    parser.add_argument("--cookie-file", default=str(default_cookie_file()), help="登录会话文件")
    parser.add_argument("--expires", type=int, default=86400, help="签名链接有效秒数，默认 86400（24 小时）")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的同名对象")
    parser.add_argument("--dry-run", action="store_true", help="只显示目标路径，不连接 COS 或上传")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果，进度写入 stderr")
    return parser


def make_plan(args):
    source = args.file.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"源路径不是文件：{source}")
    if args.directory is not None and args.remote_path is not None:
        raise ValueError("COS 目录只能指定一次：使用第二个位置参数或 --path")
    directory = args.remote_path if args.remote_path is not None else args.directory
    directory = "file" if directory is None else directory
    if "\\" in directory or "://" in directory:
        raise ValueError("请填写桶内目录，例如 file/videos，不要填写 URL 或反斜杠")
    parts = [part for part in directory.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise ValueError("COS 目录不能包含 . 或 ..")
    key = "/".join(parts + [source.name])
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValueError("COS 路径不能包含控制字符")
    if len(key.encode("utf-8")) > 1024:
        raise ValueError("COS 对象路径不能超过 1024 字节")
    if not 1 <= args.expires <= 604800:
        raise ValueError("--expires 必须在 1 到 604800 秒之间")
    return {
        "file": str(source),
        "bucket": BUCKET,
        "key": key,
        "size": source.stat().st_size,
        "contentType": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        "region": args.region,
        "envFile": str(args.env_file.expanduser().resolve()) if args.env_file else None,
        "expires": args.expires,
        "force": args.force,
        "json": args.json,
    }


def request_json(client, endpoint, payload):
    status, result = client.request(
        "POST", endpoint, json.dumps(payload).encode(), {"Content-Type": "application/json"},
    )
    if status == 404 and isinstance(result, dict) and "Cannot POST" in result.get("raw", ""):
        raise RuntimeError("服务端尚未部署 COS CLI 授权接口，请先更新服务端")
    if status != 200:
        raise APIError(status, result)
    return result


class UploadStream:
    def __init__(self, source, size):
        self.source, self.size = source, size
        self.loaded, self.last_progress = 0, 0

    def read(self, size=-1):
        chunk = self.source.read(size)
        self.loaded += len(chunk)
        now = time.monotonic()
        if now - self.last_progress >= 2 or not chunk:
            percent = self.loaded * 100 / self.size if self.size else 100
            print(f"上传进度 {percent:.1f}%", file=sys.stderr)
            self.last_progress = now
        return chunk


def session_upload(args, plan):
    if args.region:
        raise ValueError("会话上传的地域由服务端确定；--region 仅用于 --local-credentials")
    client = Client(args.api, args.cookie_file)
    try:
        client.require("GET", "/api/auth/me")
    except APIError as error:
        if error.status == 401:
            raise RuntimeError("尚未登录或会话已过期，请先执行 aigc login") from None
        raise
    ticket = request_json(client, "/api/cos/upload-ticket", {
        key: plan[key] for key in ("key", "size", "contentType", "expires", "force")
    })
    url = ticket.get("url", "")
    parsed = urlparse(url)
    region = ticket.get("region", "")
    expected_host = f"{BUCKET}.cos.{region}.myqcloud.com"
    if (ticket.get("bucket") != BUCKET or ticket.get("key") != plan["key"]
            or ticket.get("size") != plan["size"] or not ticket.get("uploadToken")
            or parsed.scheme != "https" or parsed.hostname != expected_host
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise RuntimeError("服务端返回的 COS 上传凭证无效")
    headers = ticket.get("headers", {})
    if (headers.get("Content-Length") != str(plan["size"])
            or headers.get("Content-Type") != plan["contentType"]):
        raise RuntimeError("服务端返回的上传文件信息不匹配")
    print(f"上传 {plan['file']}\n目标 cos://{BUCKET}/{plan['key']}", file=sys.stderr)
    # The shared transport sends login cookies only to the original Shelf origin.
    with Path(plan["file"]).open("rb") as source:
        stream = UploadStream(source, plan["size"])
        with client._open("PUT", url, stream, headers) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"COS 上传失败（HTTP {response.status}），可重新执行上传命令")
        if stream.loaded != plan["size"]:
            raise RuntimeError("本地文件大小在上传期间发生变化，请重新上传")
    result = request_json(client, "/api/cos/finalize-upload", {"uploadToken": ticket["uploadToken"]})
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"已上传 {result['size']} 字节，链接有效期 {result['expiresIn']} 秒", file=sys.stderr)
        print(result["url"])
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("upload", "up"):
        argv = argv[1:]
    args = build_parser().parse_args(argv)
    plan = make_plan(args)
    if args.dry_run:
        if args.json:
            print(json.dumps({**plan, "dryRun": True}, ensure_ascii=False, indent=2))
        else:
            print(f"源文件  {plan['file']}")
            print(f"目标    cos://{BUCKET}/{plan['key']}")
            print(f"大小    {plan['size']} 字节")
            print("仅预览，未上传")
        return 0
    if not args.local_credentials and not args.env_file:
        return session_upload(args, plan)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("COS 上传需要 Node.js，请先安装 Node.js 并加入 PATH")
    helper = Path(__file__).with_name("cos_upload.cjs")
    return subprocess.run(
        [node, str(helper)], input=json.dumps(plan), text=True, check=False,
    ).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"失败：{error}", file=sys.stderr)
        sys.exit(1)
