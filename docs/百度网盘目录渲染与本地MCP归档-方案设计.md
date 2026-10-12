# 百度网盘目录渲染与本地 MCP 归档方案

> 本文是对 `docs/本地目录归档与工作文档备份-方案设计.md` 的**存储策略修正**，不是把百度网盘再做成一套服务端上传队列。
>
> 核心结论：大视频不再走腾讯云 COS，也不经过 AIGC 工作台服务器。本地用 `skills/baidu-netdisk-archive`（MCP/CLI）直传百度网盘；工作台只渲染网盘目录和元数据。
>
> 实现时请同时参考：`docs/百度网盘目录渲染与本地MCP归档-低阶模型实施任务书.md`。

## 0. 为什么改方案

现有“工作文档”已经能扫描本机目录、由 Node 后台计算 SHA-256 并上传 COS。这条链路在功能上成立，但成本不合适：

- `/Users/simon/Desktop/AIGC` 约 14 GiB，且会持续增长；视频是大头。
- 腾讯云 COS 标准存储容量包：50 GB / 1 年约 47 元（原价约 70 元）。视频备份如果继续放 COS，容量费、后续扩容和下载流量都会明显高于个人网盘。
- 用户已有可用的百度网盘空间，并且仓库里已经有可工作的本地上传 Skill：`skills/baidu-netdisk-archive/`。

因此存储策略改为：

| 内容 | 存在哪里 | 谁负责传输 |
| --- | --- | --- |
| 创作目录里的视频、图片、工程文件、压缩包、文档 | 百度网盘 `/AIGC/...` | 本机 MCP / Skill，不经过工作台服务器 |
| 工作台里的目录树、文件名、大小、修改时间、类型 | PostgreSQL 元数据目录 | 本机 MCP 同步目录清单；服务器只收 JSON |
| 现有素材库里已经在 COS 的小图、封面、音乐库音频 | 继续留在 COS | 不在本次范围内迁移 |
| 浏览器里的“工作文档”页 | 只渲染网盘目录 | 不上传、不中转、不保存文件字节 |

**明确禁止**：不要给现有 `archive-jobs.js` 增加 `storage_backend=baidu`，让服务器再把本地视频流式上传到百度。那样仍然会打满服务器带宽、需要把网盘 Token 放到服务端，也解决不了 COS 成本问题的本质——大文件不该经过这套 Web API。

## 1. 目标与边界

### 1.1 目标

1. 本机 `/Users/simon/Desktop/AIGC/1007视频` 这类目录，由本地 MCP/CLI 直传百度网盘，保留相对目录结构和原文件名。
2. 工作台“工作文档”页变成**网盘目录浏览器**：面包屑、文件夹、文件列表、搜索、类型筛选、容量统计。
3. 工作台数据库只保存网盘节点元数据，不保存视频/图片二进制，也不把它们复制进 Git、`public/`、`dist/`。
4. 上传、下载、断点续传、分片校验全部发生在用户本机，复用已有 `netdisk_sync.py` 的实测行为。
5. 首次同步仍然**不删除本地文件**。确认网盘侧 size 一致后，由用户人工清理本地，Skill/MCP/服务器都不自动 `unlink`。
6. 远程打开工作台时，仍能看到最近一次同步的目录树；但不能从远程浏览器把本机文件推到网盘。

### 1.2 非目标

- 不把百度网盘做成 COS 的服务端替代后端。
- 不在 `server/src/index.js` 里读取本机视频并上传网盘。
- 不把 `BAIDU_NETDISK_ACCESS_TOKEN` 写入工作台服务器、`.env` 仓库副本、前端或数据库。
- 不在浏览器里直接播放大视频、不把网盘文件流经 Node 再转给浏览器。
- 不把现有素材库、音乐库、提示词、视频账号重做成网盘应用。
- 不在本次实现自动删除本地源文件。
- 不依赖百度网盘桌面客户端。

## 2. 已有能力，必须复用

仓库内 Skill：`/Users/simon/Desktop/dev/code/aigc-workbench/skills/baidu-netdisk-archive/`

已验证、不要凭直觉改掉的行为：

1. 使用开放平台 OAuth Access Token，走 HTTP 接口，不需要桌面客户端。
2. 默认源目录 `/Users/simon/Desktop/AIGC`，默认网盘目标 `/AIGC`。
3. 远端已存在且 **size 一致** 则跳过；size 不一致时先删旧文件再传。上传是幂等的，不会制造 `xxx_20261012_142614.mp4` 时间戳副本。
4. 百度 `ondup=overwrite` 无效；`/api/create?isdir=1` 对已存在目录会静默改名。必须先 `list` 再决定是否创建目录，创建后必须跟随服务端返回的真实路径。
5. 文件级 md5 是打码值，**不能**用来和本地 md5 对比。校验只能用：分片上传时服务端回显的块 MD5 + `create` 后的 size。
6. `xpan/filemanager` 删除对本 Token 不可用，删除走旧接口 `/api/filemanager?opera=delete`。
7. `precreate` 返回 `return_1 == 0` 表示秒传，跳过分片。
8. Token 只从 `--token`、环境变量 `BAIDU_NETDISK_ACCESS_TOKEN` 或 `~/.config/aigc-workbench/baidu_netdisk_token` 读取；权限 600；有效期约 30 天。
9. **脚本没有任何删除本地文件的逻辑。**

当前缺口（本次方案要补）：

- Skill 目前主要是“上传同步”，缺少稳定的递归目录清单 JSON、单文件下载、容量查询、以及把清单写回工作台的目录快照。
- 还不是可被 Codex 直接当 MCP tool 调用的接口，只有 CLI + `SKILL.md`。
- 工作台现有“工作文档”页仍面向 COS/local 服务端归档，需要改成网盘目录渲染。

## 3. 总体架构

```text
本机 AIGC 目录                         百度网盘
/Users/simon/Desktop/AIGC/1007视频  →  /AIGC/1007视频/...
        │  上传/下载/校验                 │
        │  只发生在本机                    │
        ▼                                 │
  MCP / CLI                               │
  skills/baidu-netdisk-archive            │
        │                                 │
        │  仅同步目录元数据 JSON            │
        ▼                                 │
  工作台 API（可选，只收元数据）             │
  netdisk_nodes / netdisk_snapshots       │
        │                                 │
        ▼                                 │
  工作台前端“工作文档”                     │
  渲染目录树、搜索、统计、同步状态
  不经过服务器传输文件字节
```

三条数据通路必须分开：

1. **文件字节通路**：本机磁盘 ↔ 百度网盘。只有 MCP/CLI 走这条路。
2. **目录元数据通路**：MCP 列出网盘 `/AIGC` 后，把节点 JSON 交给工作台保存，供刷新页面后继续渲染。
3. **素材库通路**：现有图片/视频 COS 素材保持不变。网盘归档文件默认**不**自动创建 `assets` 记录，避免把 14 GiB 视频再导入 COS。

### 3.1 运行拓扑

| 场景 | 上传/下载 | 目录渲染 |
| --- | --- | --- |
| 本机 Codex + 本机工作台 | MCP 直接读写网盘 | 前端渲染 MCP 刚同步的目录快照 |
| 只打开远程 `aigc.chatcanvas.online` | 不能上传本机文件 | 只能看最近一次同步的只读目录树 |
| 服务器部署在另一台机器 | 服务器禁止持有网盘 Token，禁止中转上传 | 同样只渲染快照 |

如果以后要在网页里点“下载”，也必须唤起本机 MCP，而不是让 Node 去百度拉视频再转发给浏览器。

## 4. 产品形态：工作台只渲染目录

“工作文档”页不再是“选本地路径 → 服务器扫描 → 服务器上传 COS”。它变成网盘文件管理器的只读壳。

### 4.1 页面结构

1. **顶栏**
   - 当前网盘根：固定 `/AIGC`（可配置显示名，不允许用户在 UI 里改到任意网盘路径，避免把个人其它目录暴露进工作台）。
   - 最近同步时间、文件数、总大小、与本地源目录的对照状态。
   - 按钮：`刷新目录`、`复制同步命令`、`查看 MCP 说明`。
2. **左侧或面包屑目录树**
   - `/AIGC`
   - `/AIGC/1007视频`
   - `/AIGC/1007视频/成片`
   - 只显示目录节点。
3. **右侧文件列表**
   - 列：名称、类型、大小、网盘修改时间、同步状态。
   - 类型沿用 `image/video/audio/document/archive/other`，仅按扩展名分类，用于筛选，不表示文件在 COS。
   - 文件夹可进入；文件默认不能在线播放。
4. **空态**
   - 还没有目录快照时，展示如何在本机让 Codex 执行 Skill：先 `--dry-run`，再上传，再刷新目录。
5. **本地对照（可选）**
   - 若本机工作台与源目录在同一台机器，可显示“本地仍存在 / 本地已不在 / 大小不一致”。
   - 这个对照也只能由本机 MCP 或本机只读扫描提供，不能让远程服务器去读 `/Users/simon/Desktop/AIGC`。

### 4.2 用户操作，不经过服务器文件上传

用户要归档 `1007视频` 时的标准动作：

1. 在工作台复制或让 Codex 执行：

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频 --dry-run
```

2. 确认清单后去掉 `--dry-run` 真正上传。
3. MCP 再执行目录列出，把 `/AIGC/1007视频` 的节点写入工作台快照。
4. 打开工作台“工作文档”，看到该目录和文件。
5. 需要下载回本机时，走 MCP download，而不是网页下载按钮打到 `/api/archive/files/:id/download`。

工作台可以提供“一键复制命令”，也可以提供“请 Codex 执行 Skill”的说明，但**不要**做浏览器 `<input type="file">` 选视频再 POST 到服务器。

### 4.3 现有 COS 归档页怎么处理

现有 `src/archive-panel.jsx` 和 `/api/archive/*` 先保留，不在本次删除。

产品上把 COS/local 归档降为“旧版高级功能”：

- 默认“工作文档”渲染网盘目录。
- COS 扫描上传入口可折叠，文案明确：仅适用于必须放对象存储的小文件；视频备份请走百度网盘。
- 不把已经实现的 COS 批次表立刻删掉，避免破坏未完成的本地改动。后续确认没有 COS 归档数据后再考虑下线。

## 5. 本地 MCP / Skill 设计

把现有 CLI 拆成可被 MCP 调用的工具，底层仍是同一份 Python 模块，避免两套上传逻辑。

### 5.1 工具列表

| 工具名 | 作用 | 是否改网盘 | 是否碰本地源文件 |
| --- | --- | --- | --- |
| `netdisk_quota` | 查询网盘容量 | 否 | 否 |
| `netdisk_list` | 列出一个网盘目录，JSON | 否 | 否 |
| `netdisk_tree` | 递归列出 `/AIGC` 或指定前缀，JSON | 否 | 否 |
| `netdisk_dry_run` | 扫描本地目录，给出将要上传的清单 | 否 | 只读 |
| `netdisk_upload` | 按现有脚本逻辑同步上传 | 是 | 只读，不删除 |
| `netdisk_download` | 把网盘文件下载到本机目标目录 | 否 | 只写用户指定的下载目录 |
| `netdisk_publish_catalog` | 把 tree JSON 发给工作台元数据接口 | 否 | 否 |

所有工具的 stdout 只输出一个 JSON；调试信息走 stderr。失败返回稳定 `error` 字段，不把 Token 打进任何输出。

### 5.2 `netdisk_list` / `netdisk_tree` 输出

每个节点至少：

```json
{
  "path": "/AIGC/1007视频/成片/final.mp4",
  "name": "final.mp4",
  "parentPath": "/AIGC/1007视频/成片",
  "isDir": false,
  "sizeBytes": 12345678,
  "serverMtime": 1728700000,
  "fsId": 0,
  "category": 1,
  "kind": "video"
}
```

约束：

- 只允许列出白名单前缀，默认 `/AIGC`。禁止 `path=/` 把整个网盘个人文件刷进工作台。
- 分页聚合对调用方透明；内部仍按百度 `page/num=1000` 翻页。
- 目录递归要能处理中文名。
- 不返回打码 md5 当真实哈希，也不假装能做内容去重。网盘侧去重继续用“同路径 + size”。
- `fsId` 能拿到就保存，拿不到就存 0，不要因此失败。

### 5.3 `netdisk_upload`

直接调用现有 `upload_file` / `ensure_dirs` / size 跳过逻辑。额外要求：

- 必须先支持 dry-run。
- 继续跳过 `.DS_Store` 和隐藏文件。
- 大目录默认建议 `--limit` 试跑，全量由用户明确确认。
- 返回 `uploaded` / `skipped` / `failed` 三个计数和失败明细。
- 成功后可以自动给一份 tree 摘要，方便下一步 `netdisk_publish_catalog`。

### 5.4 `netdisk_download`

这是现有脚本缺的能力，需要补，但仍是本机执行：

1. 用网盘 list/filemetas 找到目标文件。
2. 走百度官方下载链接把文件写到用户指定的本地目录。
3. 下载后按 size 校验；文件级 md5 仍然不可用。
4. 默认下载目录建议 `/Users/simon/Desktop/AIGC-restore` 或用户传入路径，**不要**写回源目录覆盖原片，除非用户明确传入源路径。
5. 大文件必须流式写入，不能 `read()` 整个视频进内存。
6. 工作台服务器不代理这个下载。

如果当前 Token 对下载接口权限不足，工具必须返回明确错误（例如 `download_api_unsupported`），而不是改去让 Node 中转。

### 5.5 凭据

保持 Skill 现有顺序：

1. 工具参数 `token`（仅本地 MCP 运行时可用，禁止写进工作台请求）
2. `BAIDU_NETDISK_ACCESS_TOKEN`
3. `~/.config/aigc-workbench/baidu_netdisk_token`

工作台 API、前端、文档示例、日志、Git **都不存放 Token**。OAuth client_id 和授权步骤继续只写在 Skill 里，不要复制到工作台仓库的公开 README 示例里。

## 6. 工作台数据模型（只存目录）

不要复用 `archive_objects` 去存网盘文件。那张表的语义是“服务器托管的去重对象”。网盘文件的真实对象在百度，工作台没有 object_key，也没有可校验的真实 SHA-256。

### 6.1 `netdisk_snapshots`

一次目录同步的摘要：

```text
id UUID
user_id UUID
root_path TEXT          -- 固定类似 /AIGC
source TEXT             -- mcp | cli
file_count INTEGER
dir_count INTEGER
total_bytes BIGINT
status TEXT             -- ready | stale | failed
last_error TEXT
created_at TIMESTAMPTZ
```

### 6.2 `netdisk_nodes`

当前可见目录树，按用户 + 网盘路径 upsert：

```text
id UUID
user_id UUID
snapshot_id UUID
fs_id BIGINT
path TEXT               -- /AIGC/1007视频/成片/final.mp4
parent_path TEXT
name TEXT
is_dir BOOLEAN
kind TEXT               -- image/video/audio/document/archive/other/directory
size_bytes BIGINT
server_mtime TIMESTAMPTZ
category INTEGER
local_state TEXT        -- unknown | present | missing | size_mismatch
local_size_bytes BIGINT
extra JSONB             -- 只放非敏感附加信息，不放 token、dlink
updated_at TIMESTAMPTZ
UNIQUE(user_id, path)
```

索引建议：`(user_id, parent_path)`、`(user_id, is_dir)`、`(user_id, kind)`、`(user_id, name)`。

一次 `publish_catalog` 应在事务里：

1. 插入 snapshot。
2. upsert 本次出现的节点。
3. 删除本次快照中根前缀下、但清单里已经消失的节点，避免 UI 残留已删文件。
4. 不要把整个网盘历史版本都堆成新行，工作台不是网盘回收站。

### 6.3 不要做什么

- 不要给 `assets` 自动插入网盘视频。
- 不要写 `object_key` 指向百度路径然后走现有 COS 下载接口。
- 不要保存百度 `dlink`。dlink 含鉴权且会过期，进数据库等于把临时下载地址当资产。
- 不要保存 Token、cookie、授权回调完整 URL。

## 7. 工作台 API（元数据 only）

全部走现有 `requireUser`，按 `user_id` 隔离。请求和响应都不得出现文件 multipart。

```text
GET  /api/netdisk/tree?path=/AIGC
GET  /api/netdisk/children?path=/AIGC/1007视频
GET  /api/netdisk/search?q=蕾姆&kind=video
GET  /api/netdisk/stats
POST /api/netdisk/catalog
GET  /api/netdisk/sync-commands?localPath=/Users/simon/Desktop/AIGC/1007视频
```

### 7.1 `POST /api/netdisk/catalog`

供本机 MCP 调用。请求体是目录节点数组和统计，不是文件。

建议限制：

- 单次节点数量上限（例如 20000），超过要分页多次提交或先缩小 root。
- 每个 `path` 必须位于允许前缀 `/AIGC`。
- 拒绝包含 `access_token`、`dlink`、`cookie` 的字段。
- 不接受 base64 文件内容。

这个接口可以不加网盘 Token。它信任的是“已登录的工作台用户在提交自己的目录快照”。本机 MCP 需要带上工作台登录态（Cookie 或本机一次性 catalog token）。不要把百度 Token 当工作台登录凭证。

如果暂时不想做登录态转发，允许降级为：MCP 把 tree JSON 写到本机 `outputs/netdisk-catalog.json`，由工作台本机 API 读取该文件。读取路径必须白名单，且文件只含元数据。

### 7.2 `GET /api/netdisk/tree`

返回指定 path 的子节点，供前端渲染。没有快照时返回空列表和 `needsSync: true`，不要去服务器边查百度。

### 7.3 `GET /api/netdisk/sync-commands`

只返回可复制的本地命令和 Skill 说明，例如 dry-run / upload / tree / publish。不执行命令，不读本机目录。

## 8. 前端改动范围

文件：`src/archive-panel.jsx`、`src/styles.css`、`src/main.jsx` 中“工作文档”入口。

默认视图改为 `NetdiskBrowser`：

- 面包屑 + 文件夹/文件表格。
- 搜索文件名。
- 按 kind 筛选。
- 显示 snapshot 时间、文件数、总大小。
- 空态和过期态提示“请在本机用 MCP 同步目录”。
- 提供命令复制，不提供 COS 上传进度条作为主流程。
- 点击文件：显示元数据抽屉（路径、大小、类型、网盘时间、本地对照）。不内嵌 `<video src=/api/archive/files/...>`。
- 点击“在本机下载”：展示 MCP download 命令，或在后续版本对接本机 sidecar。第一期不要做假下载按钮。

COS 旧面板移到“旧版对象存储归档”折叠区，避免实现者误把两套主流程混在一个表单里。

## 9. 本机 sidecar（可选，第二期）

如果希望工作台按钮直接触发上传，而不是复制命令给 Codex，可以在本机再开一个只绑定 `127.0.0.1` 的 sidecar，由同一份 Python 模块提供 HTTP。

约束：

- 只监听回环地址。
- 自己读本地 Token，不把 Token 返回给浏览器。
- 工作台前端仅当 `location.hostname` 为 `127.0.0.1` / `localhost` 时才调用 sidecar。
- 远程生产域名禁止探测或代理这个 sidecar。
- 第一期可以不做 sidecar，先 MCP + 目录快照也能闭环。

## 10. 安全

1. 百度 Token 不出本机配置文件/环境变量。
2. 工作台服务器不配置、不转发、不缓存百度 Token。
3. 目录白名单：MCP 列出和工作台保存都限制在 `/AIGC`。
4. 本地源目录白名单仍是 `/Users/simon/Desktop/AIGC`，仅 MCP 扫描本地时使用。
5. 日志只打相对路径、size、errno、uploaded/skipped/failed。禁止完整 Token、完整授权 URL、dlink。
6. 上传是外部状态变更，必须用户明确要求；dry-run 默认先于实际上传。
7. 不自动删除本地；清理本地是用户在网盘目录渲染确认后的手工动作。

## 11. 成本与容量策略

- 视频、成片、素材包、工程目录：百度网盘。
- 工作台现有在线素材库（需要网页预览、Range 播放、多设备在线用的小文件）：可以继续 COS。不要把“网页能播”和“备份要便宜”混成一个存储。
- 封面/缩略图如果以后要在目录页显示，应另做极小图缓存，不能把原视频再上传 COS。
- 50 GB COS 包只适合现有在线素材，不适合把整个 AIGC 创作目录当对象存储用。

## 12. 推荐落地顺序

### Phase 1：目录能看见

- 扩展 Skill：`list --json`、`tree --json`
- `netdisk_nodes` / `netdisk_snapshots`
- `POST /api/netdisk/catalog` 和 `GET /api/netdisk/tree`
- 前端网盘目录浏览器
- 用 `/AIGC` 或已上传过的子目录验证渲染

### Phase 2：本机上传闭环

- MCP tool 包装现有 upload
- 工作台空态给出 dry-run / upload / publish 命令
- 先同步 `1007视频`，确认工作台能看到该目录
- 仍然不删本地

### Phase 3：本机下载

- Skill 增加 download
- 工作台只展示命令或本机 sidecar
- size 校验

### Phase 4：对照与腾空间

- MCP 对比本机目录和网盘 size
- UI 标记“网盘已有，本地仍占用”
- 用户手工删本地；系统不代删

## 13. 验收标准

- 上传 `1007视频` 时，工作台 Node 进程的 CPU/网络不应出现该目录视频的读入或上传。
- 工作台数据库没有视频二进制，也没有 COS object_key 指向这些视频。
- “工作文档”能按文件夹浏览 `/AIGC/...`，显示名称、类型、大小。
- 刷新浏览器后目录还在，数据来自 `netdisk_nodes`。
- Token 不出现在 API 响应、前端包、Git diff、日志。
- 现有素材库、音乐库、提示词页面不回归。
- 现有 COS 归档代码可以保留但不再是默认入口。
- Skill 仍保持：不删本地、幂等上传、先 list 再建目录、分片 MD5 校验、打码 md5 不用于比对。

## 14. 和旧方案的关系

| 旧方案 | 新方案 |
| --- | --- |
| 服务器扫描本地目录 | 仅本机 MCP 扫描 |
| 服务器计算 SHA-256 并上传 COS | 本机分片上传百度网盘，用块 MD5 + size 校验 |
| `archive_files` 表示已托管对象 | `netdisk_nodes` 表示网盘目录索引 |
| 网页可预览/下载原文件 | 网页只看目录；下载走本机 MCP |
| 校验后可清理本地 | 仍然必须人工清理；先看网盘目录确认 |

旧文档保留作为 COS 归档的历史设计；新的默认实现以本文为准。

