# 代码 Review 报告：百度网盘目录渲染与本地 MCP 归档

> 面向高阶模型的审查材料。目标读者需要能独立判断：功能是否满足任务书、架构是否真的满足「文件字节不经过工作台服务器」、以及哪些结论只是"看起来对"而没有实测。
>
> 仓库：`/Users/simon/Desktop/dev/code/aigc-workbench`　分支：`main`　审查日期：2026-10-12
> 对应任务书：`docs/百度网盘目录渲染与本地MCP归档-低阶模型实施任务书.md`
> 对应设计：`docs/百度网盘目录渲染与本地MCP归档-方案设计.md`

---

## 0. 请重点审查的 8 个问题

以下是我自己认为**风险最高、最可能出问题**的地方，请优先看：

| # | 问题 | 位置 | 我的判断 |
|---|---|---|---|
| Q1 | **没有在真实 PostgreSQL 上验证过任何 SQL**。全部靠内存桩测试，桩不校验 SQL 语法，SQL 字符串写错不会被发现 | `server/src/netdisk-catalog.js` `applyCatalog` | 高风险，建议在真实库上跑一遍 |
| Q2 | `ON CONFLICT ... DO UPDATE SET` + `RETURNING *` 在真实 Postgres 下的行为（14 个占位符、批量 500 行） | 同上 | 中风险，桩无法覆盖 |
| Q3 | `DELETE ... AND NOT (path = ANY($4::text[]))` 传 2 万个元素的数组，是否会撞上参数上限或性能塌陷 | 同上 | 中风险 |
| Q4 | `LIKE d.path \|\| '/%'` 在文件名含 `%` / `_` 时会过度匹配（stats 接口的顶层目录汇总） | `server/src/index.js` `topDirs` 查询 | 已知小缺陷，未修 |
| Q5 | **前端 UI 从未在浏览器里渲染过**。应用需要登录 + 数据库才能进入 `/documents`，本机两者都没有 | `src/netdisk-browser.jsx` | 高风险，只有 build 通过 |
| Q6 | 两个 catalog 并发发布没有互斥，可能交叉覆盖 | `applyCatalog` | 低风险（元数据层面） |
| Q7 | `express.json` 双注册（catalog 8mb + 全局 1mb）依赖 body-parser 的 `req._body` 短路行为 | `server/src/index.js` | 低风险，但值得确认 |
| Q8 | 工作台 Cookie 通过 `--cookie` 传参会出现在 `ps` 输出中。已加 `--cookie-env` 规避，但默认路径仍可用 | `netdisk_sync.py` `post_catalog` | 已缓解，建议文档强调用 env |

---

## 1. 这次做了什么（一句话）

把"工作文档"从 **Node 服务器扫描本机目录 → 上传 COS** 改成 **本机 CLI/MCP 直传百度网盘 → 工作台只渲染网盘目录元数据**；旧的 COS 归档链路完整保留，降级为折叠的"旧版对象存储归档"。

---

## 2. 改动文件清单

### 2.1 新增

| 文件 | 行数 | 作用 |
|---|---|---|
| `src/netdisk-browser.jsx` | 364 | 网盘目录浏览器（默认视图） |
| `server/src/netdisk-catalog.js` | 254 | 目录白名单、请求体安全校验、快照事务写入 |
| `server/scripts/netdisk-check.js` | 280 | 目录层单测（内存 SQL 桩） |
| `server/scripts/netdisk-http-check.js` | 273 | HTTP 冒烟（真实路由 + 真实快照） |
| `server/schema.sql` 内 | +44 | `netdisk_snapshots`、`netdisk_nodes` |
| `server/src/index.js` 内 | +186 | `/api/netdisk/*` 七个路由 |

### 2.2 修改

| 文件 | 说明 |
|---|---|
| `skills/baidu-netdisk-archive/scripts/netdisk_sync.py` | 新增 4 个只读子命令 + 白名单 + 凭据清洗 |
| `skills/baidu-netdisk-archive/SKILL.md` | 新增「只读工具」章节、JSON 契约、白名单与凭据约束 |
| `skills/baidu-netdisk-archive/agents/openai.yaml` | 更新工具描述 |
| `src/archive-panel.jsx` | 内部 COS 面板改名并折叠；新增 `DocumentsPanel` 作为默认导出 |
| `src/styles.css` | +约 400 行网盘浏览器样式 |
| `src/main.jsx` | 仅图标 import（`Cloud`、`ChevronDown`），路由不变 |
| `README.md` | 「工作文档」小节重写 |
| `.env.example` | 新增 `NETDISK_ROOT_PATH`；说明 Token 只在本机 |

### 2.3 **未**修改（刻意）

- `server/src/archive-jobs.js`、`archive-storage.js`、`archive-paths.js` — 时间戳停留在上一轮（11:5x），本轮零改动
- `assets` 表与所有素材相关代码
- 音乐库、提示词、人物相册、视频账号

---

## 3. 架构与数据流

```
本机磁盘                     百度网盘                工作台
/Users/simon/Desktop/AIGC  ──►  /AIGC/...   ──►  PostgreSQL
     │                          ▲                  netdisk_nodes
     │  netdisk_sync.py         │                  （只有路径/大小/时间）
     │  （唯一传字节的通路）──────┘                         │
     └──────────────── 目录元数据 JSON ──────────────────►│
                                                       前端只渲染目录
```

**三条通路严格分离**，这是本方案的核心约束：

1. **字节通路**：`netdisk_sync.py` ↔ 百度网盘。上传、下载、分片校验全在这里。
2. **元数据通路**：`tree --json` → catalog API → `netdisk_nodes`。
3. **素材通路**：现有 COS 小图链路，未变。

### 3.1 如何证明字节没经过服务器

| 证明方式 | 结果 |
|---|---|
| `grep -n "assets\|putObject\|object_key" server/src/netdisk-catalog.js` | 零命中 |
| 在 `/api/netdisk/*` 路由体内 grep `putObject\|assets\|getObject\|multipart` | 零命中 |
| `grep -n "<video\|/api/archive/files\|/api/assets" src/netdisk-browser.jsx` | 零命中 |
| catalog 请求体校验 | 拒绝 `access_token`/`dlink`/`cookie`/base64/Buffer |
| 网盘节点响应字段 | 只有 path/name/size/mtime/kind/fsId，无任何下载地址 |

**残留风险**：旧的 COS 归档链路（`/api/archive/*`）仍然存在且可用，理论上用户仍可把视频传到 COS。这是刻意的（任务书要求保留），但意味着"字节不经过服务器"是**新默认路径**的性质，不是系统的强制约束。

---

## 4. 数据模型

```sql
netdisk_snapshots(id, user_id, root_path, source, file_count, dir_count,
                  total_bytes, status, last_error, created_at)

netdisk_nodes(id, user_id, snapshot_id, fs_id BIGINT, path, parent_path, name,
              is_dir, kind, size_bytes, server_mtime, category,
              local_state, local_size_bytes, extra JSONB, updated_at,
              UNIQUE(user_id, path))
```

索引：`(user_id, parent_path)`、`(user_id, is_dir)`、`(user_id, kind)`、`(user_id, name)`、`(user_id, size_bytes DESC)`，外加 `UNIQUE(user_id, path)` 自带索引。

全部 `CREATE TABLE IF NOT EXISTS`，可重复执行；API 启动时执行整份 schema。

**请审查**：`kind` 的 CHECK 约束包含 `directory`，而设计文档 §6.2 列的是 `image/video/audio/document/archive/other/directory`，一致；但 `archive_files.kind`（旧表）不含 `directory`，两表枚举不同，是否会造成后续 join 时的困扰。

---

## 5. 关键实现决策与理由

### 5.1 `complete` 标记防止误删（自查发现并修复）

设计文档 §6.2 要求"删除本次快照中根前缀下、但清单里已经消失的节点"。但**没有任何机制保证快照覆盖了整个根目录**——如果用户跑了 `tree --root /AIGC --depth 2` 再发布，删除逻辑会把更深层的节点全部清掉，界面看起来"文件消失了"。

修复：CLI 计算 `complete = (root == /AIGC) and not truncatedByDepth and not truncatedByNodeLimit`；服务端只在 `complete === true` 时执行删除。

顺带发现：发布**子目录**快照（如 `--root /AIGC/1007视频`）会被服务端以"只接受 /AIGC 的目录快照"拒绝（400），不会误删，但也意味着这个用法目前不可用。见 Q9。

### 5.2 `fs_id` 用字符串而非 Number（自查发现并修复）

百度 `fs_id` 是 64 位。`Number(497116331455040)` 尚可，但更大的值会丢精度。已改为字符串透传给 BIGINT 列，并加了超过 2^53 的测试。**注意 `publicNode` 返回的也是字符串**，前端若直接展示需注意。

### 5.3 `list_dir` 重构时保留宽容行为（差点引入回归）

把 `list_dir` 抽成 `list_entries` 时，原本"errno != 0 返回 `{}`"的宽容语义会让上传路径在目标目录不存在时直接抛错。已加 `strict=False` 分支保留旧行为，`list`/`tree` 才用 `strict=True` 抛明确错误。

### 5.4 Python 递归前复检路径（自查发现并修复）

`walk_tree` 直接把服务端返回的子目录路径放进队列。虽然百度不会返回 `/AIGC` 之外的路径，但递归前已加 `assert_within_root` 复检。

### 5.5 catalog 接口的 body 体积单独放宽

全局 `express.json({limit:'1mb'})` 拦不住 2 万节点的快照。在其**之前**注册 `app.use('/api/netdisk/catalog', express.json({limit:'8mb'}))`，依赖 body-parser 设置 `req._body` 后跳过二次解析。见 Q7。

---

## 6. 凭据安全

| 面 | 措施 |
|---|---|
| Token 存放 | 仅本机：`BAIDU_NETDISK_ACCESS_TOKEN` 或 `~/.config/aigc-workbench/baidu_netdisk_token`（权限 600） |
| 服务端 | 不配置、不转发、不缓存百度 Token；`.env.example` 只有一句说明 |
| Python 输出 | `scrub()` 统一剥离 `access_token`/`dlink`/`cookie`/`session_secret`/`token`/`refresh_token` 等键 |
| 请求体 | `assertNoFileContent()` 拒绝凭据字段、超长字符串、base64、data URI、Buffer/ArrayBuffer |
| 数据库 | 无 token / dlink / 签名 URL 列 |
| 前端 | 无凭据字段；`sync-commands` 返回的命令里 cookie 是占位符 |
| 日志 | 只记录路径、size、errno、计数 |

验证方式：`grep -rIl "$TOKEN" --exclude-dir=node_modules --exclude-dir=.git .` → **零命中**（仓库内不存在真实 token 值）。

**注意**：`--cookie` 会让工作台会话出现在 `ps` 输出中。已提供 `--cookie-env VAR` 规避，但默认值仍可用。**建议在 SKILL.md 强调只用 env**。

---

## 7. 测试覆盖与证据

### 7.1 已执行并通过

| 命令 | 结果 |
|---|---|
| `python3 -m py_compile netdisk_sync.py` | 通过 |
| `netdisk_sync.py --help` | 原参数 + 4 子命令 |
| `netdisk_sync.py 1007视频 --dest ... --dry-run` | 287 文件 / 589.7 MB，旧行为未变 |
| 4 个子命令无 Token | 均为 `{"error","command"}`，用真实 token 值比对零泄露 |
| 真实 `tree --root /AIGC --json` | 26 文件 / 6 目录 / 70,818,529 B；一级目录 `902视频` |
| `node server/scripts/netdisk-check.js` | **24 项通过** |
| `node server/scripts/netdisk-http-check.js` | **16 项通过** |
| `node server/scripts/archive-check.js` | 39 项通过（旧链路无回归） |
| `node server/scripts/archive-http-check.js` | 17 项通过 |
| `npm run build` | 通过 |
| `git diff --check` | 通过 |

### 7.2 覆盖的断言（节选）

- 路径白名单：接受 `/AIGC` 及子路径；拒绝 `/`、`/Pictures`、`/aigc/x`、`/AIGC2/x`、`/AIGC/../secret`
- 请求体：拒绝 token 字段、dlink、base64、data URI、Buffer；正常元数据不误判
- 事务：完整快照删除消失节点、**截断快照不删除**、写入失败回滚、用户隔离
- HTTP：401 / 403 / 404 / 400 状态码，**错误响应不含请求中的凭据**
- 非法 `localPath`（`; rm -rf /`）不会污染生成的命令

### 7.3 **未覆盖**（重要）

1. **真实 PostgreSQL**：无。所有 SQL 未经真实引擎校验。**这是最大的缺口。**
2. **浏览器渲染**：无。`/documents` 需要登录 + 数据库才能到达，UI 只经过 vite build 的语法/打包检查。
3. **真实 COS 上传**：本轮完全未涉及（旧链路）。
4. **网盘上传**：只跑了 dry-run 和只读命令，未实际上传任何文件到网盘。
5. **并发**：未测试并发 catalog 发布、并发树遍历。
6. **大数据量**：未测试 2 万节点上限、超长路径、特殊字符文件名的真实行为。
7. **Python 单元测试**：只有手工执行的命令验证，没有 pytest 等可回归的测试文件。

---

## 8. 已知缺陷与未做项

### 8.1 缺陷

| # | 描述 | 影响 | 状态 |
|---|---|---|---|
| D1 | stats 的 `LIKE d.path \|\| '/%'` 在目录名含 `_`/`%` 时过度匹配 | 顶层目录容量统计可能偏大 | **未修**，见 Q4 |
| D2 | 子目录快照（`--root /AIGC/1007视频`）被 400 拒绝 | 用户只能发布完整 `/AIGC` 树 | 刻意但需确认是否符合预期 |
| D3 | `netdisk_snapshots` 无清理策略，长期累积 | 行数缓慢增长 | 未处理 |
| D4 | `/api/netdisk/tree` 与 `/children` 功能重复 | 冗余 | 未处理 |
| D5 | 搜索只匹配文件名，不匹配路径 | `/1007/成片/x.mp4` 搜 "1007" 找不到 | 产品取舍 |
| D6 | `local_state` 字段恒为 `unknown` | 预留未用 | 见 8.2 |

### 8.2 未实现（方案允许）

- **下载**：无 `netdisk_download`。前端不做假按钮。方案 §5.4 属 Phase 3。
- **本地对照**：`local_state`/`local_size_bytes` 已预留，Skill 未上报。
- **sidecar**：未做（方案 §9，Phase 1 可不做）。
- **独立 MCP server**：未做，按任务书允许的方式在 SKILL.md 写清契约。

---

## 9. 需要 Reviewer 特别验证的代码路径

按优先级：

1. **`applyCatalog` 的批量 INSERT**（`server/src/netdisk-catalog.js:164-190`）
   - 14 个占位符的 `$N` 编号计算（`start = values.length - 13`）
   - 分片 500 行时跨片累加的编号是否连续
   - `ON CONFLICT (user_id, path) DO UPDATE` 是否需要显式唯一索引名

2. **删除语句**（`server/src/netdisk-catalog.js:194-197`）
   - `path LIKE '/AIGC/%'` 对含 `%`/`_` 路径的行为
   - `NOT (path = ANY($4::text[]))` 空数组与非空数组

3. **中间件顺序**（`server/src/index.js:58-59`）
   - 8mb parser 先于 1mb parser；body-parser 短路是否生效

4. **`publicNode` 的 fs_id 字符串**是否影响前端任何计算

5. **`netdisk_sync.py` 子命令分发**（`main()` 的 `netdisk_sync.py:574`）
   - 旧调用 `netdisk_sync.py <本地目录名恰好叫 list>` 会被误判为子命令
   - 这是我已知的边界情况，见 D 类问题之外的一个未记录项

---

## 10. 安全评估自查

| 威胁 | 缓解 | 残余风险 |
|---|---|---|
| 读取网盘任意目录 | 前后端双重 `/AIGC` 前缀校验 + `..` 拒绝 | 无 |
| 凭据泄漏进数据库 | 请求体拒绝 + 输出 scrub + 无相关列 | 无 |
| 大文件绕过白名单进数据库 | 拒绝 base64/Buffer/长字符串 | 大数组 DoS（2 万节点上限 + 8mb） |
| CSRF | 沿用现有 `requireUser` cookie 模式 | 与现有 API 一致，未额外加固 |
| 本地文件被误删 | 本轮无任何删除本地文件的代码 | 无 |
| 网盘文件被误删 | 本轮无任何网盘写操作 | 无 |
| 敏感文件误上传 | Skill 跳过 `.` 开头文件 | `.env` 等非隐藏敏感文件仍会上传（沿用旧行为） |

---

## 11. 复现命令

```bash
cd /Users/simon/Desktop/dev/code/aigc-workbench

# 只读查看网盘现状
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py list /AIGC --json
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py tree --root /AIGC --json

# 生成目录快照
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog --root /AIGC

# 测试
node server/scripts/netdisk-check.js
node server/scripts/netdisk-http-check.js
npm run build
git diff --check
```

`netdisk-http-check.js` 会用 `outputs/netdisk-catalog.json` 真实走一遍导入与树查询，不连接真实数据库、不上传文件。

---

## 12. 给 Reviewer 的结论性提问

1. Q1–Q8 中，哪些必须在合入前修复？
2. 「文件字节不经过服务器」在保留旧 COS 链路的前提下，是否需要更强的技术强制手段（如环境开关）？
3. `complete` 标记方案是否足够，还是应该在服务端强制只接受 `root === /AIGC` 的快照？
4. 目录元数据的保质期如何定义？当前只有"最近同步时间"，没有过期提示逻辑。
5. 是否应该补 pytest 形式的 Python 测试？当前 CLI 的回归保障较弱。