# AIGC Shelf

一个面向 AIGC 创作者的素材管理工作台。当前版本包含：

- 按文件夹、类型、收藏状态和使用状态筛选素材
- 关键词搜索、名称排序、网格/列表视图
- 真实图片与视频素材预览，点击打开详情抽屉
- 收藏、标签、来源、使用状态、备注和文件信息展示
- 拖拽或选择本地图片/视频上传到腾讯云 COS，并保存素材元数据
- 视频预览使用服务端同源 Range 流，兼容移动端在线播放
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

`.env.example` 已写入桶名和区域占位符（当前桶实际地域为 `ap-shanghai`）。域名 `aigc.chatcanvas.online` 可在 Nginx 中反向代理到 Vite 构建产物和服务端 API；不要把 `/Users/simon/Desktop/dev/cloud/tencent` 下的凭据目录复制到仓库。
