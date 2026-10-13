#!/usr/bin/env python3
"""File-based coordination bridge for a high-tier planner and a low-tier executor.

The bridge deliberately uses only the standard library. Human-readable Markdown
files carry a fenced JSON control block so both an agent and a small validator
can read the same task/result without relying on a chat transcript.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = REPO_ROOT / "docs" / "agent-bridge"
TASK_FILE = BRIDGE_ROOT / "current-task.md"
RESULT_FILE = BRIDGE_ROOT / "current-result.md"
STATE_FILE = BRIDGE_ROOT / "state.json"
HISTORY_DIR = BRIDGE_ROOT / "history"

TASK_TEMPLATE = BRIDGE_ROOT / "TASK_TEMPLATE.md"
RESULT_TEMPLATE = BRIDGE_ROOT / "RESULT_TEMPLATE.md"

VALID_STATES = {"draft", "ready_for_zcode", "running", "needs_review", "approved", "blocked", "completed"}
RESULT_STATES = {"needs_review", "completed", "completed_with_deferred", "blocked", "failed"}
DANGEROUS_OPS = {
    "delete_local", "delete_remote", "overwrite_remote", "rename_local", "rename_remote",
    "upload_alias", "force_upload", "modify_code", "git_push", "deploy",
}

# These patterns detect leaked credentials while allowing prose such as
# "do not print access_token". Values must be suspicious, not merely key names.
SECRET_PATTERNS = [
    re.compile(r"(?i)(?:access[_-]?token|accessToken|session_secret|session_key|dlink)\s*[=:]\s*[^\s`'\"]{12,}"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\b123\.[A-Za-z0-9._-]{20,}\b"),
    re.compile(r"(?i)(?:cookie|refresh_token)\s*[=:]\s*[^\s`'\"]{12,}"),
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_block(text: str, label: str) -> dict[str, Any]:
    pattern = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
    blocks = pattern.findall(text)
    if not blocks:
        raise ValueError(f"{label} 缺少 fenced JSON 控制块（```json ... ```）")
    for raw in blocks:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"{label} 的 fenced JSON 控制块不是有效对象")


def read_json_block(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{label} 不存在：{path}")
    return json_block(path.read_text(encoding="utf-8"), label)


def secret_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            # Report only the key/pattern family, never the matched value.
            fragment = match.group(0).split("=", 1)[0].split(":", 1)[0].strip()
            hits.append(fragment[:40])
    return sorted(set(hits))


def require_string(obj: dict[str, Any], key: str, errors: list[str]) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"缺少非空字段：{key}")
        return ""
    return value.strip()


def string_list(obj: dict[str, Any], key: str, errors: list[str], required: bool = True) -> list[str]:
    value = obj.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        errors.append(f"字段 {key} 必须是非空字符串数组")
        return []
    return [item.strip() for item in value]


def validate_task(path: Path = TASK_FILE) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        task = read_json_block(path, "任务文件")
    except ValueError as exc:
        return {}, [str(exc)]
    text = path.read_text(encoding="utf-8")
    if secret_hits(text):
        errors.append("任务文件疑似包含凭据值，请删除 Token/cookie/dlink 后重试")

    task_id = require_string(task, "taskId", errors)
    require_string(task, "objective", errors)
    status = require_string(task, "status", errors)
    if status not in {"draft", "ready_for_zcode", "approved"}:
        errors.append("任务 status 必须是 draft、ready_for_zcode 或 approved")
    require_string(task, "resultFile", errors)
    allowed = string_list(task, "allowed", errors)
    forbidden = string_list(task, "forbidden", errors)
    stops = string_list(task, "stopConditions", errors)
    success = string_list(task, "successCriteria", errors)
    if not stops:
        errors.append("必须至少声明一个 stopConditions")
    if not success:
        errors.append("必须至少声明一个 successCriteria")

    missing_safety = sorted(DANGEROUS_OPS - set(forbidden))
    approval = task.get("approval")
    if missing_safety and not (isinstance(approval, dict) and approval.get("granted") is True):
        errors.append("危险操作未列入 forbidden：" + ", ".join(missing_safety))
    if not task_id:
        errors.append("taskId 不能为空")
    return task, errors


def validate_result(path: Path = RESULT_FILE) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        result = read_json_block(path, "结果文件")
    except ValueError as exc:
        return {}, [str(exc)]
    text = path.read_text(encoding="utf-8")
    if secret_hits(text):
        errors.append("结果文件疑似包含凭据值，请删除 Token/cookie/dlink 后重试")
    task_id = require_string(result, "taskId", errors)
    status = require_string(result, "status", errors)
    if status not in RESULT_STATES:
        errors.append("结果 status 必须是 needs_review、completed、completed_with_deferred、blocked 或 failed")
    require_string(result, "summary", errors)
    require_string(result, "nextAction", errors)

    counts = result.get("counts")
    if counts is not None:
        if not isinstance(counts, dict):
            errors.append("counts 必须是对象")
        else:
            for key in ("uploaded", "skipped", "failed", "missing", "extra", "sizeMismatch", "renamed"):
                if key in counts and (not isinstance(counts[key], int) or counts[key] < 0):
                    errors.append(f"counts.{key} 必须是非负整数")
            eligible = counts.get("eligibleFiles")
            accounted = [counts.get(key, 0) for key in ("uploaded", "skipped", "failed")]
            if isinstance(eligible, int) and sum(accounted) > eligible:
                errors.append("counts 不自洽：uploaded + skipped + failed 大于 eligibleFiles")
            if status == "completed" and any(counts.get(key, 0) != 0 for key in ("failed", "missing", "extra", "sizeMismatch", "renamed")):
                errors.append("status=completed 时 failed/missing/extra/sizeMismatch/renamed 必须全部为 0")
    return result, errors


def state_read() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"status": "draft", "updatedAt": now_iso(), "taskFile": str(TASK_FILE), "resultFile": str(RESULT_FILE)}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def state_write(status: str, task_id: str | None = None, **extra: Any) -> None:
    if status not in VALID_STATES:
        raise ValueError(f"未知状态：{status}")
    state = state_read()
    state.update({"status": status, "updatedAt": now_iso()})
    if task_id:
        state["taskId"] = task_id
    state.update(extra)
    BRIDGE_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init_bridge(force: bool = False) -> None:
    BRIDGE_ROOT.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    if force or not TASK_FILE.exists():
        TASK_FILE.write_text(TASK_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    if force or not RESULT_FILE.exists():
        RESULT_FILE.write_text(RESULT_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    if force or not STATE_FILE.exists():
        state_write("draft")


def archive_current() -> Path:
    task, task_errors = validate_task()
    result, result_errors = validate_result()
    if task_errors or result_errors:
        raise ValueError("不能归档未通过校验的任务/结果：" + "; ".join(task_errors + result_errors))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    task_id = task.get("taskId", "task")
    target = HISTORY_DIR / f"{stamp}-{task_id}"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(TASK_FILE, target / "task.md")
    shutil.copy2(RESULT_FILE, target / "result.md")
    shutil.copy2(STATE_FILE, target / "state.json")
    state_write("completed", task_id, archivedTo=str(target))
    return target


def cmd_status() -> int:
    state = state_read()
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(kind: str) -> int:
    if kind == "task":
        value, errors = validate_task()
    else:
        value, errors = validate_result()
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"valid": True, "taskId": value.get("taskId"), "status": value.get("status")}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIGC Workbench 文件型 Agent Bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="创建桥接目录、模板和运行态文件")
    init.add_argument("--force", action="store_true", help="覆盖当前运行态模板（不会覆盖 history）")
    sub.add_parser("status", help="查看当前桥接状态")
    sub.add_parser("validate-task", help="校验 current-task.md")
    sub.add_parser("validate-result", help="校验 current-result.md")
    sub.add_parser("archive", help="归档已通过校验的任务和结果")
    state = sub.add_parser("set-state", help="更新桥接状态")
    state.add_argument("status", choices=sorted(VALID_STATES))
    state.add_argument("--task-id")
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            init_bridge(args.force)
            print(f"bridge initialized: {BRIDGE_ROOT}")
            return 0
        if args.command == "status":
            return cmd_status()
        if args.command == "validate-task":
            return cmd_validate("task")
        if args.command == "validate-result":
            return cmd_validate("result")
        if args.command == "archive":
            print(f"archived: {archive_current()}")
            return 0
        if args.command == "set-state":
            state_write(args.status, args.task_id)
            return cmd_status()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"agent_bridge error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
