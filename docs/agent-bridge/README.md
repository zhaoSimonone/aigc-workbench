# Agent Bridge：Codex ↔ Zcode 文件协作桥

本目录把“高阶模型规划、低阶模型执行、再由高阶模型复核”从聊天复制粘贴改成共享文件协作。

## 两个模型之间的最短流程

### 高阶模型（Codex）

1. 编辑 `current-task.md`，在 fenced JSON 控制块中填写任务。
2. 将 `status` 设为 `ready_for_zcode`，并运行：

   ```bash
   python3 scripts/agent_bridge.py validate-task
   python3 scripts/agent_bridge.py set-state ready_for_zcode --task-id <taskId>
   ```

3. 只需让 Zcode 执行一句：

   > 读取并执行 `docs/agent-bridge/current-task.md`；完成后把结构化结果写入 `docs/agent-bridge/current-result.md`，不要自行启动下一项任务。

### 低阶模型（Zcode）

1. 读取任务文件；确认 `status=ready_for_zcode` 或 `approved`。
2. 开始执行前运行：

   ```bash
   python3 scripts/agent_bridge.py set-state running --task-id <taskId>
   ```

3. 只执行任务文件允许的范围，遇到 stop condition 立即停止。
4. 写 `current-result.md`，运行：

   ```bash
   python3 scripts/agent_bridge.py validate-result
   python3 scripts/agent_bridge.py set-state needs_review --task-id <taskId>
   ```

5. 不得自行把 `needs_review` 改成 `approved`，也不得自行启动下一任务。

### 高阶模型复核

高阶模型读取 `current-result.md`，必要时直接修改代码或生成下一份任务。任务完成且结果通过校验后可归档：

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
