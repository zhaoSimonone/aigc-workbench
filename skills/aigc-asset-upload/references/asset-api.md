# 素材 API 共享字段契约

上传、整理和视频导入技能共用此表。API 基址由 `AIGC_SHELF_URL` 配置；鉴权用服务端登录会话，所有读取和写入均限制在当前账号。完整端点说明在仓库 `docs/api.md`。

## 写入字段

| 字段 | 类型与限制 | 上传 | `/organize` |
| --- | --- | --- | --- |
| `name` | 中文标题；curated-v1 下去首尾空白后 1–32 个 Unicode 字符，禁用 `·`、`.` | 是 | 是 |
| `tags` | 字符串数组，3–5 个不同非空标签；每项不含空格或逗号；整体替换，顺序无意义 | 是 | 是 |
| `folder` | 通过 `GET /api/assets/metadata-policy` 读取；`成片` 映射为 `我的创作` | 是 | 是 |
| `source` | 来源名称，最多 120 字符 | 是 | 是 |
| `sourceUrl` | 原帖链接，最多 2000 字符；不是 COS 签名媒体 URL | 是 | 是 |
| `characterName` | 已确认角色名，最多 120 字符；未确认留空 | 是 | 是 |
| `characterCategory` | 角色分类，最多 40 字符 | 是 | 是 |
| `characterCandidates` | 最多 5 个候选，结构见下文；空数组清空 | 是 | 是 |
| `used` | boolean | 是 | 是 |
| `favorite` | boolean | 否 | 是 |
| `note` | 备注，最多 5000 字符 | 否 | 是 |
| `parentAssetIds` | 当前账号内未删除素材 UUID 数组；不可重复、不可关联自身；整体替换 | 是 | 是 |
| `musicTitle`, `musicArtist`, `musicSource`, `musicStatus`, `musicConfidence` | 各最多 200 字符的字符串；置信度是文字，例如「高」 | 否 | 是 |
| `musicEvidence` | 音乐识别依据，最多 1000 字符 | 否 | 是 |
| `before` | 修改前字段快照，仅用于并发校验；建议包含每个待改字段 | 否 | 是 |

JSON 写入使用原生数组/布尔值；multipart 上传的数组字段使用 JSON 字符串，`used` 为 `"true"` 或 `"false"`。标签不是追加操作；先读取原值再整体替换。

技能写入发送 `X-AIGC-Metadata-Policy: curated-v1`，启用机械校验。失败返回 HTTP 400：

```json
{"error":"素材元数据校验失败","fields":[{"field":"name","rule":"max32","message":"标题最多 32 个字符"}]}
```

该策略只校验提交字段，不自动清洗存量素材，不验证作品/角色是否属实。网页草稿和旧客户端未发送此头时保留原有宽松行为。

## 角色候选

```json
{
  "characterName": "汉库克",
  "characterCandidates": [
    {"work":"海贼王","name":"汉库克","evidence":"片头角色文字与蛇形耳饰、服装纹样吻合","confidence":0.95,"confirmed":true}
  ]
}
```

每项的 `work`、`name` 为 1–120 字符，`evidence` 为 1–1000 字符；`confidence` 为 0–1 的数值，`confirmed` 为布尔值。不确定时用 `confirmed:false` 并保留观察依据，不把候选名写成已确认角色。置信度来自调用方评估，不是服务端识别结果。

`GET /api/assets?characterWork=海贼王&characterName=汉库克&confirmedCharacter=true&limit=10` 查询同一候选同时符合条件的已确认参考素材。单独 `characterName` 查询顶层已确认角色字段。旧素材不会被自动标记为已确认；需实际看画面后整理补齐。

## 读取字段

详情返回 `{"asset":{...}}`；列表返回 `{assets:Asset[],total:number,limit:number|null,offset:number,hasMore:boolean}`。上传、详情、整理和列表中的素材使用相同结构：

| 字段 | 语义 |
| --- | --- |
| `id`, `name`, `type`, `contentType` | UUID、标题、`image` 或 `video`、MIME |
| 上表除 `before` 外的元数据字段 | 当前服务端真值；`tags` 固定排序，比较时仍按集合处理 |
| `sha256`, `sizeBytes`, `durationSeconds`, `createdAt` | 文件哈希、字节数、秒数（未知为 null）、ISO 创建时间 |
| `size`, `duration`, `date` | 兼容网页的人读显示字段，不用于数值比较 |
| `mediaMetadata` | 新上传/替换时 ffprobe 的 `width/height/videoCodec/pixelFormat/audioCodecs`；探测失败为 `{"probeFailed":true}`，旧素材为 `{}` |
| `streamUrl` | 视频的需鉴权相对路径，返回 307 重定向；图片为空 |
| `directUrl` | 图片/GIF 原文件的签名直链，视频为空；视频取 `/download-url` |
| `thumbUrl`, `thumb` | 封面签名直链，没有封面时为空 |
| `src` | 旧客户端兼容字段：视频为 streamUrl，图片为 directUrl |
| `parentAssetIds`, `derivedAssetIds` | 上游素材、下游衍生素材 ID 数组 |
| `similarityReasons` | 仅 similarTo 查询：`sameSourceUrl` 或 `sameFolderAndDurationWithin10Percent` |

签名链接会过期，需要重新读取，不写入长期清单或日志。外部图片检索能否抓取由对方网络和服务规则决定；不自动代理或公开私有素材。

## 客户端

共用 `scripts/shelf_client.py`，仅依赖 Python 标准库。`request()` 保留 `(status, payload)` 返回值；3xx 的 payload 含 `url/redirect/status`。下载字节使用 `download(url, destination)`，会跟随重定向，跨域不携带 Shelf Cookie。`last_response` 保留原始响应体、状态、响应头（去除 Set-Cookie），错误通过 `APIError.status/body/headers` 读取。

优先 `get_asset(id)`，不要全库回读。`list_assets_page(limit, offset, **filters)` 拉一页；`list_assets(**filters)` 自动分页。下载用 `download_url(id)` 再 `download(url, destination)`。旧部署缺少详情路由时才回退列表查 ID；真正的 JSON 404 不回退。`metadata_policy()` 仅在旧版 HTML 404 时使用兼容文件夹常量，鉴权或服务故障不会被静默吞掉。
