# 2026-11-02 素材采集发布验收

## 发布结果

正式服务 `https://aigc.chatcanvas.online` 已更新；前端保持现有版本，通过真实网页兼容性检查。独立测试库验收通过后，生产直接使用同一镜像，未在切换时重新构建。

| 项目 | 值 |
| --- | --- |
| 镜像标签 | `aigc-shelf-api:verified-20261102` |
| 镜像 ID | `sha256:0484b03876448bedf95ff04d83057fdcfd6f3ef7972153ddae774b955584dabb` |
| 业务功能提交 | `c6755dbffc46d754da14551d557229985cf04038` |
| `server/src/index.js` SHA-256 | `bb0bf000c91982d186e72058dc58812648797cc08127d8de8885289f5ed67501` |
| `server/schema.sql` SHA-256 | `70fcb55c4c62cd1eab7fe78749cb64b77a073da235a21d88bc94f19c66bca611` |
| `server/package-lock.json` SHA-256 | `08f07ea7ceb70f458bd2ee0dab1ee476bf06f91900352f243ca5ed58f762c19c` |

锁文件另包含 Express 4.22.3、Multer 2.5.0 及兼容依赖更新。生产容器执行公共 npm registry 的 `npm audit --omit=dev`，结果为 0 个已知漏洞。运行镜像中的源码和锁文件已与本地逐项核对。

## 验收范围

| 层级 | 结果 |
| --- | --- |
| 本地回归 | 上传/导入/整理 72 项 Python 测试；服务端路由测试；预览 11 项及 GIF 7 项通过 |
| 独立 PostgreSQL + COS | 最终镜像 42 项接口及 13 项真实视频工作流检查通过 |
| 正式 HTTPS + COS | 42 项接口及 13 项真实视频工作流检查通过 |
| 桌面 Chrome | 1440×1000，登录、搜索、两条真实视频播放/拖动通过，无未捕获 JS 错误 |
| 移动尺寸 WebKit | 390×844，同上；已人工核对截图 |
| HTTP Range | 两条真实视频均返回 206，Content-Range 与首段字节一致 |
| 原有数据 | 发布前 1,389 条素材的已记录字段逐条核对未变 |
| 清理 | 正式验收 2 个账号、13 个 COS 对象已删除；临时服务和数据库已删除 |

真实示例为 Instagram `DactUk9Ayv3` 与 TikTok `7671816463304461599`，本次均重新下载：

| 来源 | 站点原流 | 兼容交付 | 音频与完整性 |
| --- | --- | --- | --- |
| Instagram | VP9，1080×1920，2,975,365 字节 | H.264/yuv420p，1080×1920，30 fps，14.233 秒，10,176,146 字节 | AAC 包哈希与原流一致；完整解码成功；非静音 |
| TikTok | HEVC，1080×1920，2,579,715 字节 | H.264/yuv420p，1080×1920，30 fps，11.771 秒，12,271,493 字节 | AAC 包哈希与原流一致；完整解码成功；非静音 |

兼容转码采用 CRF 18，体积增大不表示画质提升，也不表示无损。保留站点原件。此次是不同示例视频的重新下载，不能用这些体积替代此前约 5.05 MB 文件的结论。

Instagram 匿名下载失败后复用已有 ClipDock 浏览器登录态成功，未使用捕获回退；TikTok 匿名下载成功。角色标题基于实际预览和用户已有纠正：Instagram 标记海贼王/汉库克，TikTok 未确认具体角色，只记录可见服装和内容。服务端保存角色依据，不承担自动视觉识别。

音频包一致和非静音检查不能证明人工听感或音画同步；WebKit 移动尺寸验收不等于实机 iPhone/QuickTime 测试。另一台 MiniMax 的本地安装与登录态不在本次可远程验证范围内。

## 复验与回滚

可重复运行的接口、视频和浏览器验收入口及限定账号清理命令见 [API 文档](api.md#真实环境验收)。本机截图和私有回执保存在 gitignored 的 `outputs/deploy-20261102/`；其中回执可能包含会话或签名直链，不作为公开附件。

服务器保留发布前备份：

- `/opt/aigc-shelf/releases/c6755db-20261102/database-before.dump`：已用 `pg_restore --list` 校验。
- 同目录 `server-before.tar.gz`：发布前源码。
- `aigc-shelf-api:rollback-c6755db-20261102`：旧 API 镜像。

如需回退 API，先将旧镜像重新标记为 `aigc-shelf-api:latest`，再在 `/opt/aigc-shelf` 执行 `docker compose up -d --no-deps --no-build api` 并检查 `/api/health`。本次迁移是新增字段，旧 API 可继续运行；不要为回退 API 直接恢复数据库备份，以免覆盖发布后的业务数据。回滚镜像已保留，本次没有在生产执行回滚演练。
