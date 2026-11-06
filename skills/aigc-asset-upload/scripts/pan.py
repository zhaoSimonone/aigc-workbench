#!/usr/bin/env python3
"""Short Baidu Netdisk upload command: aigc pan upload <src> <dest>."""

import argparse
import sys
from pathlib import Path


def _netdisk_sync():
    scripts = Path(__file__).resolve().parents[2] / "baidu-netdisk-archive" / "scripts"
    scripts = str(scripts)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import netdisk_sync
    return netdisk_sync


def build_parser(prog="aigc pan"):
    parser = argparse.ArgumentParser(
        prog=prog,
        description="把本地文件或目录上传到百度网盘。aigc upload 仍是素材库，不是网盘。",
    )
    # 位置参数：本地路径 + 网盘目录。单文件时 dest 也可以写成完整远端文件路径。
    parser.add_argument("src", help="本地文件或目录")
    parser.add_argument("dest", help="网盘目录，例如 /AIGC/1106视频/成片")
    parser.add_argument("--dry-run", action="store_true", help="只列出计划，不实际上传")
    parser.add_argument("--force", action="store_true", help="远端已存在且大小一致也先删后传")
    parser.add_argument("--profile", choices=("safe", "fast"),
                        help="safe=稳妥默认；fast=会员大批量")
    parser.add_argument("--limit", type=int, help="最多处理 N 个文件，用于试跑")
    parser.add_argument("--include", action="append", default=[],
                        help="只同步匹配的文件名，可重复")
    parser.add_argument("--exclude", action="append", default=[],
                        help="排除匹配的文件名，可重复")
    parser.add_argument("--chunk-size", default=None, metavar="4M|8M|16M|32M",
                        help="分片大小，仅允许 4/8/16/32 MiB")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="文件级并发数 1-8")
    parser.add_argument("--metrics-file", default=None, metavar="PATH",
                        help="把每文件指标以 JSONL 追加写入该文件")
    parser.add_argument("--token", default=None,
                        help="Access Token，默认读环境变量或本地 token 文件")
    return parser


def to_netdisk_argv(args):
    forwarded = [args.src, "--dest", args.dest]
    if args.dry_run:
        forwarded.append("--dry-run")
    if args.force:
        forwarded.append("--force")
    if args.profile:
        forwarded.extend(["--profile", args.profile])
    if args.limit is not None:
        forwarded.extend(["--limit", str(args.limit)])
    for pattern in args.include or []:
        forwarded.extend(["--include", pattern])
    for pattern in args.exclude or []:
        forwarded.extend(["--exclude", pattern])
    if args.chunk_size:
        forwarded.extend(["--chunk-size", args.chunk_size])
    if args.concurrency is not None:
        forwarded.extend(["--concurrency", str(args.concurrency)])
    if args.metrics_file:
        forwarded.extend(["--metrics-file", args.metrics_file])
    if args.token:
        forwarded.extend(["--token", args.token])
    return forwarded


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `aigc pan upload ...` 和 `aigc pan ...` 都支持；upload/up 只是动词，可省略。
    if argv and argv[0] in ("upload", "up"):
        argv = argv[1:]
        prog = "aigc pan upload"
    else:
        prog = "aigc pan"
    args = build_parser(prog).parse_args(argv)
    return _netdisk_sync().main(to_netdisk_argv(args))


if __name__ == "__main__":
    sys.exit(main())
