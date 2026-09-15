#!/usr/bin/env python3
"""toapis.cn 异步生图适配器:提交任务 -> 轮询 -> 下载转存。

配合 skills/rem-video-look-designer 的 command adapter 使用:

    IMAGE_PROVIDER=command
    IMAGE_GENERATOR_COMMAND='python3 scripts/toapis_generate.py --prompt-file {prompt_file} --out {out} --model {model} --size {size} --quality {quality}'

接口形态(2026-09 服务端约定):
- POST /v1/images/generations 创建任务,响应 {"id":"tsk_img_...","status":"pending",...}
- GET  /v1/images/generations/{id} 轮询,pending -> in_progress -> completed
- 完成后 result.data[0].url(约 24h 过期,需立即转存)或 b64_json
- size 为比例字符串(如 "4:5"、"9:16");quality 字段仅 gpt-image-2.5-flare 支持
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://toapis.cn/v1"
TERMINAL_FAILED = {"failed", "cancelled", "canceled", "error"}


def api_call(url: str, api_key: str, payload: dict | None = None, timeout: int = 60) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"toapis HTTP {exc.code}: {detail[:2000]}") from exc


def extract_images(payload: dict) -> list[dict]:
    for holder in ("result", ""):
        node = payload if holder == "" else payload.get(holder)
        if isinstance(node, dict):
            data = node.get("data")
            if isinstance(data, list) and data:
                return data
    raise RuntimeError(f"toapis completed response has no result.data: {json.dumps(payload)[:800]}")


def download(url: str, output: Path, timeout: int) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        binary = response.read()
    if not binary:
        raise RuntimeError(f"empty download from {url}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(binary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=os.environ.get("TOAPIS_MODEL", "gpt-image-2.5-flare"))
    parser.add_argument("--size", default=os.environ.get("TOAPIS_SIZE", "9:16"))
    parser.add_argument("--quality", default=os.environ.get("TOAPIS_QUALITY", "high"))
    parser.add_argument("--resolution", default=os.environ.get("TOAPIS_RESOLUTION", "1K"))
    parser.add_argument("--orientation", default=os.environ.get("TOAPIS_ORIENTATION", "portrait"))
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    api_key = os.environ.get("TOAPIS_API_KEY")
    if not api_key:
        print("error: set TOAPIS_API_KEY", file=sys.stderr)
        return 2
    prompt = args.prompt_file.resolve().read_text(encoding="utf-8")
    if not prompt.strip():
        print("error: prompt file is empty", file=sys.stderr)
        return 2

    payload: dict = {
        "model": args.model,
        "prompt": prompt,
        "size": args.size,
        "n": 1,
        "response_format": "b64_json",
        "metadata": {"resolution": args.resolution, "orientation": args.orientation},
    }
    # 服务端约定:gpt-image-2 不接受 quality 字段
    if args.quality and args.model != "gpt-image-2":
        payload["quality"] = args.quality

    base = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE).rstrip("/")
    output = args.out.resolve()
    started = time.time()

    task = api_call(f"{base}/images/generations", api_key, payload)
    task_id = task.get("id")
    if not task_id:
        print(f"error: no task id in response: {json.dumps(task)[:800]}", file=sys.stderr)
        return 2
    print(f"task {task_id} status={task.get('status')} progress={task.get('progress')}", flush=True)

    while True:
        status_payload = api_call(f"{base}/images/generations/{task_id}", api_key)
        status = status_payload.get("status")
        print(
            f"  [{time.time() - started:6.1f}s] status={status} progress={status_payload.get('progress')}",
            flush=True,
        )
        if status == "completed":
            break
        if status in TERMINAL_FAILED:
            print(f"error: task {task_id} ended with status={status}: {json.dumps(status_payload)[:1200]}", file=sys.stderr)
            return 2
        if time.time() - started > args.timeout:
            print(f"error: polling timed out after {args.timeout}s (task {task_id} still {status})", file=sys.stderr)
            return 2
        time.sleep(args.poll_interval)

    images = extract_images(status_payload)
    first = images[0]
    if first.get("b64_json"):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(first["b64_json"]))
    elif first.get("url"):
        download(first["url"], output, timeout=120)
    else:
        print(f"error: no b64_json/url in result.data[0]: {json.dumps(first)[:400]}", file=sys.stderr)
        return 2

    size_bytes = output.stat().st_size
    print(output)
    print(f"saved {output} ({size_bytes} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
