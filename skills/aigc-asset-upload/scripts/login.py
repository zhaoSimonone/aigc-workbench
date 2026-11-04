#!/usr/bin/env python3
"""Authenticate the shared Shelf client without uploading any assets."""

import argparse
import os
import sys
from urllib.error import URLError

from shelf_client import APIError, Client, DEFAULT_API


def main(argv=None):
    parser = argparse.ArgumentParser(description="登录素材库 API 并保存会话，供上传和整理技能复用。")
    parser.add_argument("--api", default=os.environ.get("AIGC_SHELF_URL", DEFAULT_API),
                        help="素材库服务地址")
    parser.add_argument("--cookie-file", help="会话文件路径，默认与上传技能一致")
    parser.add_argument("--check", action="store_true", help="只检查会话，不提示输入或重新登录")
    args = parser.parse_args(argv)
    try:
        client = Client(args.api, args.cookie_file)
        if not args.check:
            client.ensure_login()
        # Verify the persisted session using a fresh client, as the next upload will.
        Client(args.api, client.cookie_file).require("GET", "/api/auth/me")
    except APIError as error:
        if error.status == 401:
            message = ("会话已失效或尚未登录，请去掉 --check 重新运行。"
                       if args.check else "登录失败，请检查邮箱和密码后重试。")
        else:
            message = f"认证失败：服务返回 HTTP {error.status}。"
        print(message, file=sys.stderr)
        return 1
    except (URLError, OSError):
        print("认证失败：请检查网络、证书和会话文件读写权限。", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"认证配置错误：{error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n登录已取消。请在终端中运行脚本并输入邮箱和密码。", file=sys.stderr)
        return 1
    print(f"API 认证有效，会话文件：{client.cookie_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
