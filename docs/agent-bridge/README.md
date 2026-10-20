# Agent Bridge：Codex ↔ Zcode 文件协作桥

本目录把“高阶模型规划、低阶模型执行、再由高阶模型复核”从聊天复制粘贴改成共享文件协作。

**强制交接规则：高阶模型每次回复都必须明确“现在发给 Zcode 的完整提示词”和“Zcode 完成后用户发回 Codex 的固定提示词”。不得只输出分析过程。完整协议见 [`HANDOFF_PROTOCOL.md`](./HANDOFF_PROTOCOL.md)。**

## 两个模型之间的最短流程

### 高阶模型（Codex）

1. 编辑 `current-task.md`，在 fenced JSON 控制块中填写任务。
2. 将 `status` 设为 `ready_for_zcode`，并运行：

   ```bash
   python3 scripts/agent_bridge.py validate-task
   python3 scripts/agent_bridge.py set-state ready_for_zcode --task-id <taskId>
   ```

3. 回复用户时必须提供一个“现在发给 Zcode”的可复制代码块。推荐内容：

   ```text
   请读取并执行 /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-task.md。
   严格按任务文件执行：完成后将结构化结果写入 /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-result.md，不要自行启动下一项任务。
   ```

4. 同一条回复还必须告诉用户：Zcode 完成后只需回到 Codex 发送：

   ```text
   请读取并 Review /Users/simon/Desktop/dev/code/aigc-workbench/docs/agent-bridge/current-result.md。
   ```

### 低阶模型（Zcode）

1. 读取任务文件；确认 `status=ready_for_zcode` 或 `approved`。
2. 开始执行前运行：

   ```bash
   python3 scripts/agent_bridge.py set-state running --task-id <taskId>
   ```

3. 只执行任务文件允许的范围，遇到 stop condition 立即停止。
5. 写 `current-result.md`，运行：

   ```bash
   python3 scripts/agent_bridge.py validate-result
   python3 scripts/agent_bridge.py set-state needs_review --task-id <taskId>
   ```

6. 不得自行把 `needs_review` 改成 `approved`，也不得自行启动下一任务。

### 高阶模型复核

高阶模型读取 `current-result.md`，必要时直接修改代码或生成下一份任务。回复用户时必须先给出 Review 结论，再给出“现在发给 Zcode”和“Zcode 完成后发回 Codex”两个明确动作。任务完成且结果通过校验后可归档：

```bash
python3 scripts/agent_bridge.py archive
```

## 状态机

```text
draft → ready_for_zcode → running → needs_review
                                  ├→ blocked
                                  └→ completed
```

`approved` 只表示高阶模型明确批准当前任务中的危险操作，不是低阶模型自行授予的状态。

## 安全边界

- 任务/结果只写摘要、路径、计数和错误类别，不写 Token、cookie、dlink、签名 URL。
- 默认禁止删除、覆盖、改名、别名上传、修改代码、push、部署。
- `outputs/netdisk-upload-exceptions.json`、`outputs/netdisk-upload-deferred.json` 和 `outputs/local-cleanup-protection.json` 是现有上传异常的事实来源；新任务引用它们，不重复粘贴长路径清单。
- 运行态文件和历史结果应被 Git 忽略；模板和 README 纳入版本控制。

## 初始化

```bash
python3 scripts/agent_bridge.py init
python3 scripts/agent_bridge.py status
```

`init` 不覆盖已有运行态文件；需要重置当前运行态时显式使用 `--force`。历史目录不会被覆盖。
