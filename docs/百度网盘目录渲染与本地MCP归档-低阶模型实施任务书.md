# 低阶模型实施任务书：百度网盘目录渲染与本地 MCP 归档

> **已完成，不要按本文继续改。** 下一期任务书是 `docs/百度网盘目录服务端实时读取-低阶模型实施任务书.md`（服务端实时 list + 删除旧版对象存储归档）。

你正在仓库 `/Users/simon/Desktop/dev/code/aigc-workbench` 中，把“工作文档”从 COS 服务端归档改成：

**本机 MCP/CLI 把文件传到百度网盘，工作台只渲染网盘目录元数据。**

必须先阅读：

- `docs/百度网盘目录渲染与本地MCP归档-方案设计.md`
- `docs/本地目录归档与工作文档备份-方案设计.md`（旧方案，理解差异，不要按它继续把视频传到 COS）
- `skills/baidu-netdisk-archive/SKILL.md`
- `skills/baidu-netdisk-archive/scripts/netdisk_sync.py`
- `src/archive-panel.jsx`
- `src/main.jsx`
- `server/src/index.js` 中 `/api/archive/*` 相关代码
- `server/schema.sql`

不要只改文案。需要一个可运行的最小闭环：本机列出/同步网盘目录 → 工作台能浏览 `/AIGC` 树。不要把视频字节经过 Node 服务器。

## 一、用户场景

用户本地有 `/Users/simon/Desktop/AIGC/1007视频` 这类目录，视频很大。COS 50 GB / 年大约 47 元，不适合当视频备份盘。用户希望：

1. 用已有百度网盘 Skill 在本机上传/下载。
2. 工作台只显示网盘目录，方便浏览哪些成片、素材、文档已经备份。
3. 上传不经过工作台服务器。

默认网盘根目录：`/AIGC`。默认本地根目录：`/Users/simon/Desktop/AIGC`。

## 二、必须实现

1. **扩展 Skill/CLI，保持现有上传行为不变**
   - 现有 `netdisk_sync.py` 的幂等上传、先 list 再建目录、分片 MD5 校验、size 跳过、不删除本地，全部保留。
   - 新增 `list` / `tree` 的 JSON 输出。
   - 新增把 tree 发布到工作台的方式（HTTP catalog 或白名单本地 JSON 文件）。
   - 新增 MCP 可调用说明：在 `SKILL.md` 写清工具名、参数、输出 JSON。若实现 MCP server，必须复用同一 Python 模块。
2. **工作台只存目录元数据**
   - 新增 `netdisk_snapshots`、`netdisk_nodes`。
   - `POST /api/netdisk/catalog` 只接受节点 JSON。
   - `GET /api/netdisk/tree`、`GET /api/netdisk/children`、`GET /api/netdisk/stats`。
   - 路径必须在 `/AIGC` 前缀内。
3. **前端“工作文档”默认改为网盘目录浏览器**
   - 面包屑、进文件夹、文件列表、搜索、类型筛选、统计、最近同步时间。
   - 空态提示本机 MCP/CLI 命令。
   - 不提供“选择本地视频上传到服务器”。
   - 现有 COS 归档 UI 降为折叠的旧版入口，不要删除全部旧代码。
4. **安全**
   - 不把百度 Token 写入服务端环境示例以外的真实值、数据库、前端、日志。
   - `.env.example` 若要提到网盘，只写“Token 仅存在本机 Skill 配置，不进入服务器”。
   - 不保存 dlink。
5. **文档**
   - 更新 README 中“工作文档”小节：默认备份目标是百度网盘，工作台只渲染目录。
   - 保留 COS 归档说明，但标明它不是视频备份主路径。

## 三、不要实现 / 不要做

- 不要给 `archive-jobs.js` / `archive-storage.js` 增加百度后端去上传文件。
- 不要在 `POST /api/archive/batches` 里扫描本地视频再传到网盘。
- 不要让 Node 读 `/Users/simon/Desktop/AIGC` 后上传百度。
- 不要把网盘视频自动写入 `assets` 或 COS。
- 不要在网页里用 `<video>` 播放网盘原片。
- 不要自动删除本地源文件。
- 不要重构音乐库、提示词、人物相册、视频账号。
- 不要把整个 14 GiB `AIGC` 目录在实现过程中强行全量上传。验证用已有网盘目录，或 `--limit` / 单目录 `1007视频`。
- 不要在回复、commit、日志里输出 Token。

## 四、Skill / MCP 接口要求

CLI 至少支持：

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py --help

# 只读列出
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py list /AIGC --json
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py tree /AIGC/1007视频 --json

# 已有上传能力，保持兼容
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频 --dry-run
```

`list` / `tree` 的 JSON 至少包含：`path,name,parentPath,isDir,sizeBytes,kind`。`tree` 可以截断过深层级，但同一层必须翻页拉完。

发布目录到工作台任选一种，优先 HTTP：

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog \
  --root /AIGC \
  --api http://127.0.0.1:18080/api/netdisk/catalog
```

如果 HTTP 登录态不好做，允许写成：

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py tree /AIGC --json \
  > outputs/netdisk-catalog.json
```

然后工作台提供本机导入接口，只读取白名单文件 `outputs/netdisk-catalog.json`。导入的是 JSON 节点，不是媒体文件。

下载可以第二期，但若做了必须流式写盘并用 size 校验。不要用打码 md5 判断成功。

## 五、数据库

可重复执行的 SQL，放进 `server/schema.sql`：

- `netdisk_snapshots`
- `netdisk_nodes`
- `UNIQUE(user_id, path)`
- 需要的索引

不要修改 `assets.type` 约束。不要删除 `archive_*` 表。

## 六、前端要求

路由继续用 `/documents`，“工作文档”入口不变。

主界面必须能：

1. 打开 `/AIGC` 看一级目录（如 `1007视频`、`1008视频`）。
2. 点进子目录看文件。
3. 看到文件大小和类型。
4. 没有快照时明确告诉用户先在本机同步。
5. 提供 dry-run / upload / tree / publish 的可复制命令。

不要把 COS 扫描表单继续当作首页主按钮。

## 七、测试要求

1. `python3 -m py_compile skills/baidu-netdisk-archive/scripts/netdisk_sync.py`
2. `list` / `tree` 在无 Token 时返回明确错误，且输出中无 Token。
3. `tree` JSON 不含 `access_token` / `dlink`。
4. catalog API 拒绝 `/AIGC` 以外的路径。
5. catalog API 拒绝带文件内容的请求体。
6. 前端 `npm run build` 通过。
7. `git diff --check` 通过。
8. 若本机已有 Token 且 `/AIGC` 已有文件，实际跑一次 `tree --json` 并把结果导入工作台，截取/描述能看到的目录名。没有 Token 就跳过真实网盘调用，说明原因，用夹具 JSON 测 API 和 UI。
9. 确认实现过程中没有把 `1007视频` 的 mp4 传到 COS 或经过 `/api/assets`。

## 八、完成时必须汇报

1. 改了哪些文件。
2. MCP/CLI 怎么列出目录、怎么发布 catalog。
3. 工作台如何渲染。
4. 为什么可以证明文件字节没有经过服务器。
5. 跑过的命令和结果。
6. 是否真实访问过百度网盘；若没有，原因是什么。
7. 未做的部分（如下载、sidecar、本地对照）。
8. 不输出 Token、cookie、签名 URL、dlink。

