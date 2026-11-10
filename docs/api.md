# 素材库 API

本文件描述素材采集、读取、整理、下载和评论端点；音乐库、网盘等独立模块不在此范围。字段唯一契约见 [素材 API 共享字段表](../skills/aigc-asset-upload/references/asset-api.md)，上传与整理技能也引用该表。

## 鉴权和错误

默认基址 `https://aigc.chatcanvas.online`，可通过 `AIGC_SHELF_URL` 更换。先 `POST /api/auth/login`，JSON 为 `{email,password}`，成功返回 200 `{user}` 并设置会话 Cookie；`GET /api/auth/me` 返回 200 `{user}` 或 401 `{error}`。所有下列端点需登录且按账号隔离。不要把凭据或签名直链提交到 Git。

JSON 请求使用 `Content-Type: application/json`。成功响应形状见各表；常见失败为 400 字段错误、401 未登录、404 不存在或无权访问、409 预览后变化、413 超出上传大小、415 媒体类型不支持、500 服务错误。错误体为 `{error:string,fields?:[{field,rule,message}]}`。批量接口须检查每项结果，HTTP 200 不代表全部成功。

技能发送 `X-AIGC-Metadata-Policy: curated-v1`，执行标题/标签/folder 机械校验。网页草稿不发送该头时保留旧行为。作品归属、角色、换装两端准确性由 Agent 结合真实画面判断。

## 读取和筛选

| 请求 | 响应 |
| --- | --- |
| `GET /api/assets/metadata-policy` | 200 `{policy:"curated-v1",folders:string[],folderAliases:object,nameMaxLength:32,forbiddenNameCharacters:["·","."],tagsMin:3,tagsMax:5}` |
| `GET /api/assets/:id` | 200 `{asset:Asset}`；不存在为 404 `{"error":"素材不存在"}`，无效 UUID 为 400 |
| `GET /api/assets` | 200 `{assets:Asset[],total:number,limit:number|null,offset:number,hasMore:boolean}` |
| `POST /api/assets/check-hashes` | JSON `{sha256s:string[]}`；200 `{assets:[{id,name,type,folder,sha256,createdAt}]}`，注意键是 **assets**，不是 results |

`check-hashes` 过滤无效哈希并最多处理 2000 个不同 SHA-256，共享客户端按 500 个分批；未命中的不返回。哈希只代表字节相同；不同编码的相同画面需另外比对。

列表支持以下查询参数，可组合，除相似候选内的 OR 外均为 AND：

| 参数 | 含义 |
| --- | --- |
| `id` / `ids` | 单 UUID / 逗号分隔 UUID；有 ids 时优先 |
| `q` / `search` | 名称、来源、顶层角色名、标签 ILIKE 模糊查找；有 q 时优先；`%`/`_` 保留 SQL 模式语义 |
| `folder`, `tag` | 文件夹、单标签精确匹配；folder 接受别名 |
| `sourceUrl` | 原链接字符串精确匹配，不自动消除分享参数 |
| `characterName` | 单独使用时匹配顶层角色名；与下两项组合时匹配同一候选的 name |
| `characterWork` | 角色候选的作品名精确匹配 |
| `confirmedCharacter` | `"true"` 或 `"false"`，要求至少一个候选符合该状态，不表示“没有任何确认角色” |
| `duration_min`, `duration_max` | 秒数，非负、包含边界 |
| `since` | ISO 时间，createdAt >= since |
| `limit`, `offset` | limit 限制到 1–200，offset 为非负整数；**不传 limit 保留全量行为**以兼容网页，技能应显式分页 |
| `similarTo` | 当前账号内参考素材 UUID；返回其他视频中「同 sourceUrl」或「同 folder 且时长±10%」的候选 |

稳定排序为 `created_at DESC,id DESC`；先筛选/分页，再聚合该页标签与关系。offset 分页期间若有新增/删除，页边界仍可能移动；不是数据库快照。

相似查询响应附 `similarityReasons`；不是视觉相似度，不自动删除或合并。宽高/编码记录于 `mediaMetadata`，供二次筛选和下载比对。已确认角色对照集示例：

```text
GET /api/assets?characterWork=海贼王&characterName=汉库克&confirmedCharacter=true&limit=10
GET /api/assets?similarTo=<UUID>&limit=10
```

## 上传和替换

元数据使用共享字段表；上传支持 `name/source/sourceUrl/tags/used/folder/characterName/characterCategory/characterCandidates/socialMetrics/parentAssetIds`。`note` 与音乐字段需上传后整理。服务端保留上传的文件字节；兼容转码在本地技能完成。

| 请求 | 请求体 | 成功响应 |
| --- | --- | --- |
| `POST /api/assets` | multipart：`file` + 元数据；最大 2 GiB，图片或视频 | 201 `{asset:Asset}` |
| `POST /api/assets/upload-ticket` | JSON `{filename,contentType}` | 200 `{objectKey,url,contentType,expiresIn:600}` |
| `POST /api/assets/finalize-upload` | JSON `{objectKey,...元数据}` | 201 `{asset:Asset}` |
| `POST /api/assets/:id/file` | multipart：`file` | 200 `{asset:Asset}` |
| `POST /api/assets/:id/file/finalize` | JSON `{objectKey}` | 200 `{asset:Asset}` |
| `POST /api/assets/import-urls` | JSON `{items:[{url,fileName?,...元数据}]}`；也接受 `{urls:string[]}` 或 `{ossUrls:string[]}` | 200 或全失败时 422；`{total,succeeded,failed,results:[{index,url,ok,asset?,error?,fields?}]}` |

直传流程：申请 ticket → PUT 文件到 url（Content-Type 与 ticket 一致，Cache-Control 为 `private, max-age=86400, immutable`）→ finalize。ticket 只用于当前账号的对象；finalize 会验证对象归属、大小及类型。替换文件必须保持素材类型，保留标题/标签/关系，更新哈希/时长/媒体参数，清空旧音乐识别及视频音乐关联。

`import-urls` 默认最多 50 项（由 MAX_BATCH_IMPORT_ITEMS 配置），URL 必须直接返回视频，**不解析 Instagram/TikTok 页面**。平台页面交给本地 video-inspiration-import。每项独立执行。

## 整理和批量写回

规范入口为 `PATCH /api/assets/:id/organize`，JSON 是共享字段表中需要修改的字段，成功返回 200 `{asset:Asset}`。标签和关联 ID 是整体替换。建议附 `before`：

```json
{
  "before":{"name":"旧标题","tags":["旧标签","舞蹈","红裙"]},
  "name":"海贼王-汉库克-舞蹈-红色印花裙",
  "tags":["海贼王","汉库克","舞蹈","红色印花裙"]
}
```

服务端在事务内锁定素材并比对快照，tags/parentAssetIds 忽略顺序，JSON 对象忽略键顺序；不一致返回 409，整项不写入。未提交 before 的直接 API 调用不享有预览漂移保护。客户端保存 plan 并在确认后使用同一快照。

`PATCH /api/assets` 支持两种请求，均为 1–100 个不同 UUID：

```json
{"items":[{"id":"<UUID>","before":{"used":false},"used":true}]}
```

```json
{"ids":["<UUID-1>","<UUID-2>"],"changes":{"favorite":true}}
```

返回 200 `{results:[{id,status,ok,asset?,error?,fields?}]}`。每项独立事务，允许部分成功；客户端只重试失败项，避免覆盖已成功项的新改动。

兼容入口仍保留，不作为新 Agent 的写入选择：

| 请求 | 请求/响应 | 限制 |
| --- | --- | --- |
| `PATCH /api/assets/:id` | JSON 元数据 → 200 `{asset}` | 保留旧网页调用；不支持 tags/characterCandidates/before，tags 明确返回 400 |
| `POST /api/assets/:id/tags` | JSON `{tags:string[]}` → 204 | 整体替换标签；无 before |
| `DELETE /api/assets/:id` | 无 body → 204 | 软删除，找不到返回 404 |

尚未移除旧端点，也未设置 HTTP Deprecation/Sunset 头。后续迁移网页调用后再统一移除，不能让现有客户端失效。

## 取流和外链

| 请求 | 响应 |
| --- | --- |
| `GET /api/assets/:id/stream` | 307，Location 为 COS 签名直链 |
| `GET /api/assets/:id/download` | 302，Location 为有效期 300 秒的下载签名链接 |
| `GET /api/assets/:id/download-url` | 200 `{url,filename}`，链接有效期 600 秒 |

视频详情的 streamUrl 需首跳鉴权。图片原图 directUrl、封面 thumbUrl 已签名，不需要把 Shelf Cookie 给外站；链接过期重新读取。缩略图生成失败时 thumbUrl 为空，不能假定每条都有封面。共享客户端 `download` 自动跟随跳转并检查字节数；`request` 返回跳转目标供调用方选择。

## 评论

Comment 结构：`{id,assetId,content,author,createdAt,updatedAt}`。

| 请求 | 请求体 | 响应 |
| --- | --- | --- |
| `GET /api/assets/:id/comments` | 无 | 200 `{comments:Comment[]}`，按创建时间倒序 |
| `POST /api/assets/:id/comments` | `{content}`，去空白后 1–5000 字符 | 201 `{comment:Comment}` |
| `PATCH /api/comments/:id` | `{content}`，同上 | 200 `{comment:Comment}` |
| `DELETE /api/comments/:id` | 无 | 204，无权访问或不存在为 404 |

## 部署和验证边界

启动服务时执行 `server/schema.sql`，幂等新增 `character_candidates JSONB DEFAULT '[]'`、`media_metadata JSONB DEFAULT '{}'` 和 `social_metrics JSONB DEFAULT '{}'`。旧素材不自动重下载、不回填角色确认、不改标题标签。

`social_metrics` 保存来源平台的社交计数快照（点赞/评论/转发/分享/收藏/播放），字段为非负整数或 null，null 表示平台未提供该指标；`capturedAt` 是抓取时刻。结构见共享字段表。计数由本地 `video-inspiration-import` 采集并随上传写入；`PATCH /api/assets/:id/organize` 也可单独回写，用于刷新快照。服务端不判断互动高低，也不据此排序。

完整音视频解码与静音/音量分析由本地 `import_video.py verify` 生成结构化报告，prepare 把报告和哈希写入 state。服务端 mediaMetadata 只表示 ffprobe 参数，不能据此声称音频听感正常。需要持久化检查结论时，可先生成 note 的 diff，再经整理流程写入；不会塞入音乐识别字段。

### 真实环境验收

以下命令在仓库根目录执行，需要显式提供已授权写入的服务地址、独立的新输出目录、FFmpeg 路径。脚本自动创建随机命名的临时验收账号，不使用现有用户会话；依赖 Python 标准库和仓库内完整技能目录。输出目录含临时登录凭据，禁止提交到 Git。

```bash
python3 server/test/asset-workflow-live.py \
  --api "$AIGC_SHELF_URL" \
  --workdir "$ACCEPTANCE_DIR" \
  --ffmpeg "$FFMPEG_BIN"
```

真实平台视频须先用导入技能 `prepare` 生成独立清单，并完成画面识别、标题、标签及角色依据填写。以下命令上传到上一步的临时账号，检查去重、下载字节、完整解码、整理与刷新；会更新该清单/state 并清理生成的预览，不能使用正式工作中正在操作的清单。FFmpeg/ffprobe 需可由导入技能发现。

```bash
python3 server/test/video-import-live.py \
  --api "$AIGC_SHELF_URL" \
  --workdir "$ACCEPTANCE_DIR" \
  --manifest "$ACCEPTANCE_MANIFEST"
node server/test/asset-browser-live.cjs \
  "$AIGC_SHELF_URL" "$ACCEPTANCE_DIR" "$PLAYWRIGHT_MODULE_PATH"
```

浏览器验收需要 Chrome、Playwright 及其 WebKit，当前夹具约定为两条至少 5 秒的 1080×1920 示例视频，输出桌面与移动尺寸截图及回执。检查播放推进和拖动进度，不替代人工试听或手机实机测试。

无论验收成功或失败，都保留 `receipt.json` 并清理该次创建的账号。把此回执复制到对应服务容器后，在容器中执行：

```bash
node scripts/cleanup-acceptance.js /tmp/acceptance-receipt.json --confirm
```

清理器先校验回执中每个 UUID 与数据库邮箱一致，且邮箱必须符合 `acceptance-<32位十六进制>@example.invalid`，再删除这些账号专属前缀的 COS 对象和账号。不能传业务账号或替换成全库清理。结束后删除临时凭据、测试容器与测试库，保留脱敏验收记录；2026-11-02 的实际结果见 [发布验收记录](deployment-20261102.md)。
