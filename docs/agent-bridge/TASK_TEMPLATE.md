# Agent Bridge Task

> 高阶模型写入，低阶模型只执行 `status=ready_for_zcode` 或 `status=approved` 的任务。
> 任务完成后，低阶模型将结构化结果写入 `current-result.md`，不要把凭据写入任务或结果。

```json
{
  "taskId": "replace-me",
  "status": "draft",
  "objective": "填写单一、可验收的任务目标",
  "createdAt": "",
  "resultFile": "docs/agent-bridge/current-result.md",
  "allowed": [
    "read_local_metadata",
    "list_remote",
    "write_metrics"
  ],
  "forbidden": [
    "delete_local",
    "delete_remote",
    "overwrite_remote",
    "rename_local",
    "rename_remote",
    "upload_alias",
    "force_upload",
    "modify_code",
    "git_push",
    "deploy"
  ],
  "stopConditions": [
    "填写任何必须立即停止的情况"
  ],
  "successCriteria": [
    "填写可客观验证的成功条件"
  ],
  "approval": {
    "granted": false,
    "note": "默认不允许危险操作"
  }
}
```

## Context

填写必要背景，但不要粘贴整段聊天记录。

## Execution

填写允许执行的命令、路径和顺序。所有路径使用绝对路径；涉及网盘时明确区分本地路径和远端路径。

## Safety

填写不可违反的安全约束。已有异常和延期清单优先引用文件：
`outputs/netdisk-upload-exceptions.json`、`outputs/netdisk-upload-deferred.json`、`outputs/local-cleanup-protection.json`。

## Reporting

结果写入 `docs/agent-bridge/current-result.md`，同时运行：

```bash
python3 scripts/agent_bridge.py validate-result
```
