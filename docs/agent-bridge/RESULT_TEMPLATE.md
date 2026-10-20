# Agent Bridge Result

> 低阶模型写入，高阶模型 Review。不得写入 Token、cookie、dlink、签名 URL 或 Authorization 值。

```json
{
  "taskId": "replace-me",
  "status": "needs_review",
  "startedAt": "",
  "finishedAt": "",
  "summary": "填写执行摘要",
  "counts": {
    "localFiles": 0,
    "eligibleFiles": 0,
    "uploaded": 0,
    "skipped": 0,
    "failed": 0,
    "missing": 0,
    "extra": 0,
    "sizeMismatch": 0,
    "renamed": 0
  },
  "exceptions": [],
  "deferred": [],
  "commands": [],
  "nextAction": "high_model_review"
}
```

## Evidence

记录实际执行的只读/写入动作、对账结果、日志文件位置和无法确认的事项。

## Exceptions

每个异常包含相对路径、错误类别、阶段和处理状态；不要包含任何凭据。

## Recommendation

说明下一步建议。低阶模型不得自行批准下一项任务。

## Handoff

低阶模型不要填写下一项任务的执行提示词；由高阶模型 Review 后按 `HANDOFF_PROTOCOL.md` 生成。高阶模型必须在回复用户时明确：

- 现在发给 Zcode 的完整提示词，或明确说明当前不要发送；
- Zcode 完成后用户发回 Codex 的固定提示词：

```text
请读取并 Review /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-result.md。
```
