# Agent Bridge：高阶模型交接协议

这份协议是 Codex（高阶模型）与 Zcode（低阶模型）之间的强制交接格式。

## 高阶模型每次回复必须明确两件事

### 1. 当前需要发给 Zcode 的内容

如果 `state.json` 为 `ready_for_zcode` 或 `approved`，高阶模型必须在回复末尾给出一个可以原样复制的代码块，内容至少包含：

```text
请读取并执行 /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-task.md。
严格按任务文件执行：完成后将结构化结果写入 /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-result.md，不要自行启动下一项任务。
```

如果当前没有可执行任务，也必须明确写：

```text
当前不要给 Zcode 发消息，等待高阶模型先生成或更新 current-task.md。
```

高阶模型不得只描述分析过程而不提供上述结论。

### 2. Zcode 完成后，用户需要回给高阶模型的内容

无论任务成功、失败、暂停还是触发停止条件，用户都只需要在 Codex 中发送：

```text
请读取并 Review /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-result.md。
```

如果结果文件尚未写入或校验失败，高阶模型应先告诉用户让 Zcode 修正结果文件，而不是要求用户复制粘贴长日志。

## 高阶模型回复的固定末尾格式

每次涉及 Agent Bridge 的回复，必须以以下三段之一结尾：

```text
【现在发给 Zcode】
<可原样复制的提示词，或“当前不要发送”>

【Zcode 完成后发回 Codex】
请读取并 Review /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-result.md。

【当前状态】
<ready_for_zcode / running / needs_review / blocked / completed，以及是否允许任何真实写操作>
```

## 危险操作的额外要求

如果任务包含上传、删除、改名、覆盖、代码修改、push 或部署，高阶模型必须在“现在发给 Zcode”的提示词中明确写出：

- 允许的精确范围；
- 不允许触碰的文件；
- 停止条件；
- 是否已经获得本轮明确批准。

未明确批准时，默认只允许只读分析。
