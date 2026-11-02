#!/usr/bin/env python3
"""Fetch an overlay GIF from the AIGC Shelf GIF 素材 library.

The workbench stores overlay assets (GIF / 贴纸) in its own COS bucket. This
helper resolves an asset id to a local file so `apply_overlay.py` can consume
it directly, without the user re-downloading it from the browser.

Examples:
    # list everything in GIF 素材
    python3 fetch_overlay.py --list

    # download one asset and print the local path
    python3 fetch_overlay.py --asset-id 0f2c...  -> /Users/x/.config/aigc-workbench/gif/心形贴纸-胸口位置.gif
"""

import argparse
import getpass
import http.client
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

upload_script = Path(os.environ.get("AIGC_UPLOAD_SCRIPT") or
                     Path(__file__).resolve().parents[2] / "aigc-asset-upload/scripts/upload_assets.py").expanduser()
sys.path.insert(0, str(upload_script.parent))
from shelf_client import Client as ShelfClient, default_cookie_file, state_dir

DEFAULT_API = "https://aigc.chatcanvas.online"
DEFAULT_COOKIE_FILE = default_cookie_file()
DEFAULT_CACHE_DIR = state_dir() / "gif"
OVERLAY_FOLDER = "GIF 素材"


def parse_json(payload):
    try:
        return json.loads(payload.decode("utf-8")) if payload else None
    except json.JSONDecodeError:
        return {"raw": payload[:500].decode("utf-8", "replace")}


class Client(ShelfClient):
    def download_url(self, asset_id):
        status, payload = self.request("GET", f"/api/assets/{asset_id}/download-url")
        if status != 200:
            raise RuntimeError((payload or {}).get("error", f"获取下载地址失败 HTTP {status}"))
        url = (payload or {}).get("url")
        if not url:
            raise RuntimeError("服务端没有返回下载地址")
        return url, (payload or {}).get("filename") or f"{asset_id}.gif"



def safe_filename(name, fallback):
    cleaned = "".join("_" if character in '\\/:*?"<>|' else character for character in str(name or "")).strip()
    return (cleaned or fallback)[:120]


def find_asset(assets, asset_id):
    for asset in assets:
        if str(asset.get("id", "")).lower() == str(asset_id).lower():
            return asset
    return None


def parse_args():
    parser = argparse.ArgumentParser(description="从 AIGC Shelf 取 GIF 素材（GIF）到本地")
    parser.add_argument("--asset-id", default="", help="素材 ID；省略时与 --name 一起按名称匹配")
    parser.add_argument("--name", default="", help="按名称包含匹配素材；命中多条时报错")
    parser.add_argument("--list", action="store_true", help="列出 GIF 素材库里的全部素材")
    parser.add_argument("--output-dir", default=str(DEFAULT_CACHE_DIR), help="本地缓存目录")
    parser.add_argument("--force", action="store_true", help="已存在同名文件时重新下载")
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API))
    parser.add_argument("--cookie-file", default=os.environ.get("AIGC_SHELF_COOKIE_FILE", str(DEFAULT_COOKIE_FILE)))
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.list and not args.asset_id and not args.name:
        raise SystemExit("需要 --list、--asset-id 或 --name 之一")
    client = Client(args.api, args.cookie_file)
    user = client.ensure_login()
    assets = [client.get_asset(args.asset_id)] if args.asset_id else client.list_assets(folder=OVERLAY_FOLDER)

    if args.list:
        overlays = [asset for asset in assets if asset.get("folder") == OVERLAY_FOLDER]
        if args.json:
            print(json.dumps([
                {
                    "id": asset.get("id"),
                    "name": asset.get("name"),
                    "contentType": asset.get("contentType"),
                    "size": asset.get("size"),
                    "tags": asset.get("tags"),
                }
                for asset in overlays
            ], ensure_ascii=False, indent=2))
        else:
            if not overlays:
                print(f"GIF 素材库还是空的（当前账号：{user.get('name') or user.get('email') or '未知'}）")
            for asset in overlays:
                marker = "GIF" if asset.get("contentType") == "image/gif" else asset.get("contentType") or "?"
                print(f"{asset.get('id')}  {asset.get('name')}  [{marker}]  {asset.get('size')}")
        return 0

    if args.asset_id:
        asset = find_asset(assets, args.asset_id)
        if not asset:
            raise SystemExit(f"找不到素材：{args.asset_id}")
    else:
        keyword = args.name.strip()
        matches = [asset for asset in assets if keyword in str(asset.get("name") or "")]
        if not matches:
            raise SystemExit(f"没有名称包含「{keyword}」的素材")
        if len(matches) > 1:
            print("命中多条素材，请用 --asset-id 指定：", file=sys.stderr)
            for asset in matches:
                print(f"  {asset.get('id')}  {asset.get('name')}", file=sys.stderr)
            raise SystemExit(1)
        asset = matches[0]

    if asset.get("contentType") != "image/gif":
        print(f"提示：该素材类型是 {asset.get('contentType') or '未知'}，不是 GIF", file=sys.stderr)

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(asset.get("name"), asset.get("id"))
    if not filename.lower().endswith(".gif"):
        filename = f"{filename}.gif"
    destination = output_dir / filename
    if destination.exists() and not args.force:
        size = destination.stat().st_size
        print(json.dumps({"path": str(destination), "size": size, "cached": True}, ensure_ascii=False) if args.json
              else str(destination))
        return 0

    url, _ = client.download_url(asset.get("id"))
    size = client.download(url, destination)
    if args.json:
        print(json.dumps({
            "path": str(destination),
            "size": size,
            "assetId": asset.get("id"),
            "name": asset.get("name"),
            "cached": False,
        }, ensure_ascii=False))
    else:
        print(str(destination))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("已取消")
