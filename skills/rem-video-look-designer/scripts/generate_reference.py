#!/usr/bin/env python3
"""Generate one reference image with a configurable image backend."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_API_BASE = "https://hairfree.corp.kuaishou.com/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1536"
DEFAULT_QUALITY = "high"
DEFAULT_ENDPOINT_MODE = "generations"


def read_prompt(args: argparse.Namespace) -> tuple[str, Path | None]:
    if bool(args.prompt) == bool(args.prompt_file):
        raise ValueError("provide exactly one of --prompt or --prompt-file")
    if args.prompt_file:
        path = args.prompt_file.resolve()
        if not path.is_file():
            raise ValueError(f"prompt file not found: {path}")
        return path.read_text(encoding="utf-8"), path
    return args.prompt, None


def validate_references(values: list[Path]) -> list[Path]:
    references = []
    for value in values:
        path = value.resolve()
        if not path.is_file():
            raise ValueError(f"reference image not found: {path}")
        references.append(path)
    return references


def multipart_body(fields: dict[str, str], references: list[Path]) -> tuple[bytes, str]:
    boundary = f"----rem-look-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def line(value: str) -> bytes:
        return value.encode("utf-8") + b"\r\n"

    for name, value in fields.items():
        chunks.append(line(f"--{boundary}"))
        chunks.append(line(f'Content-Disposition: form-data; name="{name}"'))
        chunks.append(b"\r\n")
        chunks.append(line(value))
    for path in references:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(line(f"--{boundary}"))
        chunks.append(
            line(
                f'Content-Disposition: form-data; name="image[]"; filename="{path.name}"'
            )
        )
        chunks.append(line(f"Content-Type: {media_type}"))
        chunks.append(b"\r\n")
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(line(f"--{boundary}--"))
    return b"".join(chunks), boundary


def decode_image_response(payload: dict, output: Path, timeout: int) -> None:
    data = payload.get("data") or []
    if not data:
        raise RuntimeError(f"image API returned no image data: {payload}")
    first = data[0]
    if first.get("b64_json"):
        binary = base64.b64decode(first["b64_json"])
    elif first.get("url"):
        with urllib.request.urlopen(first["url"], timeout=timeout) as response:
            binary = response.read()
    else:
        raise RuntimeError("image API response contains neither b64_json nor url")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(binary)


def call_openai(
    prompt: str,
    references: list[Path],
    output: Path,
    model: str,
    api_base: str,
    size: str,
    quality: str,
    endpoint_mode: str,
    timeout: int,
) -> None:
    api_key = os.environ.get("IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("set IMAGE_API_KEY or OPENAI_API_KEY")

    common = {"model": model, "prompt": prompt, "size": size, "quality": quality, "n": 1}
    headers = {"Authorization": f"Bearer {api_key}"}
    if endpoint_mode == "edits":
        if not references:
            raise ValueError("endpoint-mode=edits requires at least one --reference")
        body, boundary = multipart_body(common, references)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        url = f"{api_base.rstrip('/')}/images/edits"
    elif endpoint_mode == "generations":
        if references:
            raise ValueError(
                "endpoint-mode=generations does not accept --reference; "
                "describe the references in the prompt or use endpoint-mode=edits"
            )
        body = json.dumps(common).encode("utf-8")
        headers["Content-Type"] = "application/json"
        url = f"{api_base.rstrip('/')}/images/generations"
    else:
        raise ValueError("endpoint-mode must be generations or edits")

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"image API HTTP {exc.code}: {detail[:2000]}") from exc
    decode_image_response(payload, output, timeout)


def call_command(
    prompt: str,
    prompt_file: Path | None,
    references: list[Path],
    output: Path,
    model: str,
    size: str,
    quality: str,
    timeout: int,
) -> None:
    template = os.environ.get("IMAGE_GENERATOR_COMMAND")
    if not template:
        raise RuntimeError("set IMAGE_GENERATOR_COMMAND when provider=command")

    temporary_path: Path | None = None
    if prompt_file is None:
        handle = tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False)
        with handle:
            handle.write(prompt)
        temporary_path = Path(handle.name)
        prompt_file = temporary_path

    substitutions = {
        "prompt_file": str(prompt_file),
        "out": str(output.resolve()),
        "model": model,
        "size": size,
        "quality": quality,
    }
    try:
        command = shlex.split(template.format(**substitutions))
        child_env = os.environ.copy()
        child_env["IMAGE_REFERENCE_LIST"] = json.dumps(
            [str(path) for path in references], ensure_ascii=False
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(command, check=True, env=child_env, timeout=timeout)
        if not output.is_file():
            raise RuntimeError(f"custom generator succeeded but did not create {output}")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--reference", action="append", type=Path, default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--provider", default=os.environ.get("IMAGE_PROVIDER", "openai"))
    parser.add_argument("--model", default=os.environ.get("IMAGE_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--api-base",
        default=os.environ.get("IMAGE_API_BASE", DEFAULT_API_BASE),
    )
    parser.add_argument("--size", default=os.environ.get("IMAGE_SIZE", DEFAULT_SIZE))
    parser.add_argument("--quality", default=os.environ.get("IMAGE_QUALITY", DEFAULT_QUALITY))
    parser.add_argument(
        "--endpoint-mode",
        choices=("generations", "edits"),
        default=os.environ.get("IMAGE_ENDPOINT_MODE", DEFAULT_ENDPOINT_MODE),
        help="API request shape; generations sends JSON, edits sends multipart references",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        prompt, prompt_file = read_prompt(args)
        if not prompt.strip():
            raise ValueError("prompt is empty")
        references = validate_references(args.reference)
        output = args.out.resolve()

        if args.dry_run:
            summary = {
                "provider": args.provider,
                "model": args.model,
                "api_base": args.api_base if args.provider == "openai" else None,
                "endpoint_mode": args.endpoint_mode,
                "prompt_file": str(prompt_file) if prompt_file else None,
                "prompt": prompt if prompt_file is None else None,
                "references": [str(path) for path in references],
                "output": str(output),
                "size": args.size,
                "quality": args.quality,
                "n": 1,
                "credentials_present": bool(
                    os.environ.get("IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
                ),
            }
            request_path = output.with_suffix(".request.json")
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(request_path)
            return 0

        if args.provider == "openai":
            call_openai(
                prompt,
                references,
                output,
                args.model,
                args.api_base,
                args.size,
                args.quality,
                args.endpoint_mode,
                args.timeout,
            )
        elif args.provider == "command":
            call_command(
                prompt,
                prompt_file,
                references,
                output,
                args.model,
                args.size,
                args.quality,
                args.timeout,
            )
        else:
            raise ValueError("--provider must be openai or command")
        print(output)
        return 0
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
