# AIGC Shelf

一个面向 AIGC 创作者的素材管理工作台。当前版本包含：

- 按文件夹、类型、收藏状态和使用状态筛选素材
- 关键词搜索、名称排序、网格/列表视图
- 真实图片与视频素材预览，点击打开详情抽屉
- 收藏、标签、来源、使用状态、备注和文件信息展示
- 拖拽或选择本地图片/视频上传到腾讯云 COS，并保存素材元数据
- 登录后可通过 `POST /api/assets/import-urls` 批量导入可访问的 OSS/对象存储视频链接
- 视频预览使用服务端同源 Range 流，兼容移动端在线播放
- 音乐库：从全部视频提取标准化音轨，按 SHA-256 精确去重，支持试听和人工标记
- 人物相册可设置固定封面：在角色相册卡片上点击封面按钮，从相册素材中任选一张固定为封面；未设置时默认展示最新素材
- 注册/登录与按账号隔离的数据，可将创作作品关联到多个参考素材
- 桌面端与移动端布局

演示资源来自 `/Users/simon/Desktop/AIGC`，已复制到 `public/media` 作为可运行的样例。生产版本使用 Node.js API、PostgreSQL 和腾讯云 COS；浏览器只保存 HttpOnly 会话 Cookie，不保存密码或 COS 密钥。

## 本地运行

```bash
npm install
npm run dev
```

前端开发服务器为 `http://127.0.0.1:4174/`，`/api` 会代理到本地 `18080`。生产构建：

```bash
npm run build
npm run preview
```

启动 API 前先准备 PostgreSQL，并设置 `DATABASE_URL`、`TENCENT_COS_BUCKET`、`TENCENT_COS_REGION`、`TENCENT_COS_SECRET_ID` 和 `TENCENT_COS_SECRET_KEY`，然后执行：

```bash
cd server
npm ci --omit=dev
node src/index.js
```

服务器部署使用根目录的 `docker-compose.yml`。Compose 只暴露 `127.0.0.1:18080`，容器、网络和数据卷均使用 `aigc_shelf_*` 独立名称；Nginx 配置见 `deploy/nginx-aigc.chatcanvas.online.conf`。

## COS 接入边界

不要把 SecretId 或 SecretKey 放进 React/Vite 的 `VITE_*` 环境变量。建议在同一台服务器上增加服务端上传接口：

1. 服务端读取 `.env` 中的 `TENCENT_COS_*` 配置。
2. 服务端接收 `multipart/form-data`，调用腾讯云 COS SDK 上传并设置正确的 `ContentType`。
3. 返回对象 key、大小、哈希和短期签名 URL；前端用返回值创建素材记录。
4. COS 桶建议关闭匿名写入，仅对应用服务账号授予指定前缀的读写权限。

## 批量导入视频链接 API

接口需要登录会话 Cookie：

```http
POST /api/assets/import-urls
Content-Type: application/json
```

请求体为 `{ "items": [...] }`，单批最多 50 条；也可以只传 `{ "urls": ["..."] }` 快速导入。每项至少包含 `url`，也兼容 `ossUrl`；可同时传入素材元数据：

```json
{
  "items": [
    {
      "url": "https://your-bucket.oss-cn-shanghai.aliyuncs.com/demo/a.mp4?签名参数",
      "name": "门店开场参考",
      "source": "抖音",
      "sourceUrl": "https://v.douyin.com/example/",
      "tags": ["开场", "运镜"],
      "folder": "灵感收集",
      "used": false
    }
  ]
}
```

服务端会下载链接内容、上传到腾讯云 COS、生成视频封面和时长，并将素材写入当前账号的数据库。返回 `results` 数组，每条包含 `ok` 和 `asset` 或 `error`，单条失败不会影响其他条目。链接需要在服务器上可访问，支持 HTTP(S) 视频地址（包括带签名参数的私有 OSS 链接）；原链接会保存到素材的“原视频链接”。

## 素材整理与音乐元数据

仓库内的 `skills/aigc-asset-organize` skill 可按素材 ID 或文件夹读取素材，基于实际画面和音轨提出标题、标签、备注及音乐识别结果。它先输出逐字段 diff，只有用户明确确认后才写回；批量写回使用 `PATCH /api/assets/:id/organize`，请求体可包含 `name`、`tags`、`note`、`folder` 和 `musicTitle`、`musicArtist`、`musicSource`、`musicStatus`、`musicConfidence`、`musicEvidence`。音乐无法可靠识别时应记录待确认状态，不要猜测曲名。

网页侧边栏的“音乐库”用于查看、试听和人工标记。音频整理由本地 skill 完成：本地下载视频并使用 FFmpeg/ffprobe 将第一条音轨转成单声道 44.1kHz PCM WAV 后计算 SHA-256，再把音频和关联关系上传到服务端；服务端不下载视频、不解析音频。同一标准化音频只保存一份 COS 对象，多个视频关联到同一曲目。这是精确内容去重，不是声学歌曲识别，新曲目默认“待标记”，可在音乐库中人工填写库内名称、曲名、艺人和备注。

也可以使用 skill 命令行构建。构建会写入音频对象和数据库关系，必须显式确认：

```bash
python3 skills/aigc-asset-organize/scripts/organize_assets.py music-library
python3 skills/aigc-asset-organize/scripts/organize_assets.py music-library --build --confirm --json
```

`.env.example` 已写入桶名和区域占位符（当前桶实际地域为 `ap-shanghai`）。域名 `aigc.chatcanvas.online` 可在 Nginx 中反向代理到 Vite 构建产物和服务端 API；不要把 `/Users/simon/Desktop/dev/cloud/tencent` 下的凭据目录复制到仓库。

## 工作文档：百度网盘目录 + 本地归档

侧边栏“工作文档”（路由 `/documents`）默认是一个**百度网盘目录浏览器**。创作目录里的视频、图片和工程文件由本机 Skill 直传百度网盘，工作台只渲染网盘目录元数据。

| 内容 | 存在哪里 | 谁负责传输 |
| --- | --- | --- |
| 创作目录的视频、素材、工程文件 | 百度网盘 `/AIGC/...` | 本机 MCP/CLI，不经过工作台服务器 |
| 目录树、文件名、大小、网盘修改时间 | PostgreSQL `netdisk_nodes` | 本机同步清单，服务器只收 JSON |
| 素材库里已在 COS 的小图和封面 | 腾讯云 COS | 现有上传链路，保持不变 |

COS 50 GB / 年的容量包不适合当视频备份盘，视频备份走百度网盘；COS 只保留给需要在网页里直接预览的小文件。

### 同步一个目录到网盘

上传和下载都发生在**本机与百度网盘之间**，文件字节不会经过工作台服务器：

```bash
# ① 先看清单，不实际上传
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频 --dry-run

# ② 确认无误后去掉 --dry-run
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频

# ③ 列出网盘目录
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py tree --root /AIGC --json

# ④ 写出目录快照，让工作台能渲染
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog --root /AIGC
```

`publish-catalog` 默认写到 `outputs/netdisk-catalog.json`，然后在“工作文档”页点“刷新目录”，或让它直接发布：

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog \
  --root /AIGC --api http://127.0.0.1:18080/api/netdisk/catalog --cookie-env AIGC_SESSION_COOKIE
```

不上传文件时，工作台提供一个本机降级导入接口，只读取仓库内固定白名单文件 `outputs/netdisk-catalog.json`：

```bash
curl -X POST http://127.0.0.1:18080/api/netdisk/import-local-catalog \
  -H 'Content-Type: application/json' -b 'aigc_session=<工作台会话>' -d '{}'
```

### 网盘凭据

百度 Token **只存在本机**：`BAIDU_NETDISK_ACCESS_TOKEN` 环境变量或 `~/.config/aigc-workbench/baidu_netdisk_token`。工作台服务器不配置、不转发、不缓存它，数据库里也没有。`list` / `tree` / `publish-catalog` 的输出统一清洗过，不含 `access_token`、`dlink`、`cookie`、`session_secret`；catalog 接口会直接拒绝带这些字段或带文件内容的请求体。

`.env.example` 只说明这一点，不要把真实 Token 写进任何仓库文件。

### 网盘目录接口

```text
GET  /api/netdisk/options              网盘根目录与脚本路径
POST /api/netdisk/catalog              提交目录节点 JSON（mcp/cli）
POST /api/netdisk/import-local-catalog 读取本机 outputs/netdisk-catalog.json
GET  /api/netdisk/tree?path=/AIGC      指定目录的直接子节点
GET  /api/netdisk/children?path=...    同上
GET  /api/netdisk/search?q=&kind=      按文件名搜索
GET  /api/netdisk/stats                快照时间、类型分布、一级目录
GET  /api/netdisk/sync-commands        可复制的本机命令
```

所有路径必须位于 `/AIGC` 前缀内（`NETDISK_ROOT_PATH` 可配置），否则返回 400。`netdisk_nodes` 按 `user_id + path` 唯一，一次发布在事务里完成：写快照 → upsert 节点 → 仅当快照 `complete=true` 且节点清单非空时删除已消失节点。发布工作台登录态请用 `--cookie-env`，不要把 Cookie 写在命令行里。

### 数据库迁移

`netdisk_snapshots` 和 `netdisk_nodes` 写在 `server/schema.sql`，使用 `IF NOT EXISTS`，可重复执行。API 启动时自动执行整份 schema，重启即完成迁移。

### 旧版对象存储归档（已降级）

原“扫描本地目录 → 服务器计算 SHA-256 → 上传 COS/local”的功能保留在“工作文档”页底部的折叠区“旧版对象存储归档”，代码和 `archive_*` 表都还在。它只适合必须放对象存储的小文件；视频备份不要走这条路，它会让大文件流量穿过服务器并持续消耗 COS 容量。详细的环境变量、批次 API 和清理流程见 `docs/本地目录归档与工作文档备份-方案设计.md`。

### 自检脚本

```bash
python3 -m py_compile skills/baidu-netdisk-archive/scripts/netdisk_sync.py
node server/scripts/netdisk-check.js        # 目录解析、白名单、事务写入（内存 SQL 桩）
node server/scripts/netdisk-http-check.js   # 真实 Express 路由 + 真实目录快照
node server/scripts/archive-check.js        # 旧版 COS 归档逻辑
node server/scripts/archive-http-check.js   # 旧版归档路由
```

`netdisk-http-check.js` 会用 `outputs/netdisk-catalog.json`（若存在）真实走一遍导入与树查询，全程不连接真实数据库、不上传任何文件。

## 蕾姆视频妆造 Skill

`skills/rem-video-look-designer` 已配置 hairfree 图像接口、`gpt-image-2`、竖屏尺寸、high 质量和 JSON generations 请求。生成脚本只从环境变量读取 `IMAGE_API_KEY`；其余默认值已内置，也可用 `.env.example` 中的 `IMAGE_API_BASE`、`IMAGE_MODEL`、`IMAGE_SIZE`、`IMAGE_QUALITY` 和 `IMAGE_ENDPOINT_MODE` 覆盖。不要把实际密钥写入仓库。
