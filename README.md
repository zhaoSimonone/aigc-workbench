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
- 服装库：沉淀可复用的服装参考，按颜色、品类和细节检索，对应 `/outfits`
- 发型库：跨角色复用的发型参考，按发色和款式检索，对应 `/hairstyles`
- 通用片段：可直接用于后续剪辑的视频片段，按发型与服装组合检索，对应 `/clips`
- GIF 素材：用于视频遮挡的 GIF 动画与贴纸，网格和详情里直接播放动画，对应 `/gif`
- 人物相册可设置固定封面：在角色相册卡片上点击封面按钮，从相册素材中任选一张固定为封面；未设置时默认展示最新素材
- 注册/登录与按账号隔离的数据，可将创作作品关联到多个参考素材
- 桌面端与移动端布局

演示资源来自 `/Users/simon/Desktop/AIGC`，已复制到 `public/media` 作为可运行的样例。生产版本使用 Node.js API、PostgreSQL 和腾讯云 COS；浏览器只保存 HttpOnly 会话 Cookie，不保存密码或 COS 密钥。

服装库、发型库、通用片段、 GIF 素材都复用现有素材表，不新建业务表。本机的服装参考图和通用片段目录可以通过 `aigc-asset-upload` 导入：脚本会先判定目标库，再按画面特征生成中文标题和标签，同一批次可以写入不同文件夹（见该 skill 的 `--manifest`）。生产环境不需要依赖任何本机绝对路径。

## GIF 素材（GIF）

「 GIF 素材」库专门存放叠在视频上做遮挡的 GIF 动画和贴纸，对应工作台左侧的「 GIF 素材」页签（`/gif`）。

- GIF 走图片通道入库：`type=image`、`content_type=image/gif`，不占用 `assets.type` 的枚举位，也不需要数据库迁移。
- 服务端**不给 GIF 生成静态首帧缩略图**——抽成 JPG 会让网格里的 GIF 素材变成一张静止画面。前端直接播放原始 GIF 对象。
- GIF 的展示地址用 24 小时窗口的稳定签名 URL，普通图片仍走短期 SDK 签名 URL（见 `docs/方案-缩略图与可缓存URL-实施任务书.md`）。
- `aigc-asset-upload` 兼容 `.gif`：不指定 `--folder` 时 GIF 自动进 `GIF 素材`，其余文件进 `灵感收集`；显式指定的文件夹永远优先。
- 取用：`skills/video-overlay-censor/scripts/fetch_overlay.py` 按素材 ID 把 GIF 拉到本机 `~/.config/aigc-workbench/gif/`，再交给 `apply_overlay.py` 合成到视频上。

```bash
# 列出 GIF 素材库
python3 skills/video-overlay-censor/scripts/fetch_overlay.py --list

# 取一条到本地并直接合成
OVERLAY=$(python3 skills/video-overlay-censor/scripts/fetch_overlay.py --asset-id <素材 ID>)
python3 skills/video-overlay-censor/scripts/apply_overlay.py \
  --input input.mp4 --overlay "$OVERLAY" --output output.mp4 \
  --start 3.45 --width 270 --height 190
```

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

## 工作文档：百度网盘实时目录

侧边栏“工作文档”（路由 `/documents`）实时浏览百度网盘 `/AIGC`。视频、图片和工程文件由本机 Skill 直接上传到百度网盘；工作台服务器只读取目录元数据，不上传、下载、删除或代理文件字节。

| 内容 | 存在哪里 | 谁负责传输 |
| --- | --- | --- |
| 创作目录的视频、素材、工程文件 | 百度网盘 `/AIGC/...` | 本机 Skill/CLI，不经过工作台服务器 |
| 网盘目录 Token | PostgreSQL `netdisk_credentials` 的 AES-256-GCM 密文 | 工作台按用户保存，只用于 `GET /api/list` |
| 目录缓存 | PostgreSQL `netdisk_nodes` | 实时 list 成功后更新；百度不可用时降级显示 |

### 配置实时目录授权

1. 在服务端设置 `NETDISK_TOKEN_ENCRYPTION_KEY`，值为 32 字节 Base64：`openssl rand -base64 32`。
2. 重启 API，登录工作台并打开“工作文档”。
3. 点击授权链接，登录百度后把授权成功的整段地址（或纯 access token）粘贴到页面保存。
4. Token 通常 30 天过期。页面会显示剩余时间；过期或百度返回 errno 110/111 后需要重新授权并覆盖保存。

服务器保存的 Token 只允许读取 `/AIGC` 目录列表。接口、日志和前端响应不会回显明文 Token、`dlink`、缩略图签名或完整授权回调地址。

### 本机上传

```bash
# 先看清单，不实际上传
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频 --dry-run

# 安全档：4 MiB、串行、固定上传域名
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频

# 快速档：8 MiB、文件并发 4、动态上传域名
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1007视频 --dest /AIGC/1007视频 \
  --profile fast --metrics-file outputs/netdisk-upload-metrics.jsonl
```

上传 Token 仍建议只放本机的 `BAIDU_NETDISK_ACCESS_TOKEN` 或 `~/.config/aigc-workbench/baidu_netdisk_token`。上传使用线程内连接池、分片重试和逐片 MD5 校验；脚本不会删除本地文件。

`publish-catalog` 仍保留为可选暖缓存，但不再是网页看到目录的前提：

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog --root /AIGC
```

### 网盘目录接口

```text
GET    /api/netdisk/options              根目录、授权地址、凭据摘要
GET    /api/netdisk/credential           凭据状态（不返回 Token）
PUT    /api/netdisk/credential           加密保存并探活百度 Token
DELETE /api/netdisk/credential           删除凭据，保留目录缓存
GET    /api/netdisk/tree?path=/AIGC      实时读取当前一层
GET    /api/netdisk/children?path=...    实时读取当前一层
GET    /api/netdisk/search?q=&kind=      搜索本地缓存
GET    /api/netdisk/stats                缓存更新时间、类型分布、一级目录
POST   /api/netdisk/catalog              可选的本机暖缓存发布
POST   /api/netdisk/import-local-catalog 导入 outputs/netdisk-catalog.json
GET    /api/netdisk/sync-commands        可复制的本机命令
```

所有目录路径必须位于 `NETDISK_ROOT_PATH`（默认 `/AIGC`）内。实时读取失败时接口返回缓存和 `errorCode`，而不是让整个页面不可用。

### 数据库迁移与旧归档

`netdisk_credentials`、`netdisk_snapshots` 和 `netdisk_nodes` 都在 `server/schema.sql` 中使用 `IF NOT EXISTS` 创建，API 启动时自动执行。

原“扫描本地目录 → 服务器上传 COS/local”的旧工作文档归档链路已经移除：服务端不再暴露 `/api/archive/*`，相关模块和 UI 已删除。历史 `archive_*` 表和 `assets.archive_file_id` 保留用于兼容旧数据；素材库现有的 COS 上传、预览能力不受影响。

## 蕾姆视频妆造 Skill

`skills/rem-video-look-designer` 已配置 hairfree 图像接口、`gpt-image-2`、竖屏尺寸、high 质量和 JSON generations 请求。生成脚本只从环境变量读取 `IMAGE_API_KEY`；其余默认值已内置，也可用 `.env.example` 中的 `IMAGE_API_BASE`、`IMAGE_MODEL`、`IMAGE_SIZE`、`IMAGE_QUALITY` 和 `IMAGE_ENDPOINT_MODE` 覆盖。不要把实际密钥写入仓库。
