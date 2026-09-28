# 图片缩略图、可缓存 URL 与媒体读请求优化实施说明

> 修订日期：2026-09-28
> 当前状态：代码已在本地实现，尚未部署；部署前需完成代码 Review 和生产环境 dry-run。

## 一、背景

2026 年 9 月 COS 数据显示：

- 存储约 3.27GB；
- 总流量约 278.46GB；
- 内网下行约 240.30GB；
- 外网下行约 38.16GB；
- 请求约 25.4 万次，几乎全部为读请求。

现有实现存在四个主要放大点：

1. 图片网格优先加载原图，没有图片缩略图；
2. COS SDK 每次生成不同的缩略图签名 URL，浏览器无法复用同一缓存键；
3. 网格为每个视频挂载 `<video preload="metadata">`，即使不播放也会请求视频；
4. 每个视频/音频 Range 请求先调用 `headObject`，随后再调用 `getObject`，一次浏览器请求对应两次 COS 读请求。

## 二、本次改造范围

### 2.1 图片与视频封面

- 图片上传、直传 finalize、远程导入和文件替换时生成 480px JPG 缩略图；
- 视频封面沿用现有抽帧逻辑；
- 新生成的图片缩略图、视频封面和头像统一设置：

```http
Cache-Control: private, max-age=86400, immutable
Content-Type: image/jpeg
```

使用 `private` 是为了只允许浏览器私有缓存，不允许共享代理缓存登录用户的素材。

### 2.2 稳定签名 URL

缩略图与头像使用稳定签名 URL：

- 同一个对象在 24 小时窗口内生成完全一致的 URL；
- 签名有效期覆盖当前窗口及后续一个窗口，共 48 小时；
- 浏览器缓存时间为 24 小时，与 URL 稳定窗口对齐；
- 图片原图仍使用短期 SDK 签名 URL，不改为稳定签名；
- 下载 URL、上传 ticket 和原图签名逻辑保持不变。

签名实现要求：

1. 签名使用原始未编码 Object Key；
2. URL 路径按 segment 编码；
3. `!'()*` 必须额外百分号编码；
4. `q-sign-time` 和 `q-key-time` 使用同一个固定窗口。

### 2.3 前端网格

素材网格必须进行小范围修改：

- 图片使用 `asset.thumb || asset.src`；
- 视频只展示 `asset.thumb`，不在网格挂载视频 `src`；
- 图片和视频封面增加 `loading="lazy"` 与 `decoding="async"`；
- 视频只在用户打开详情后加载。

### 2.4 视频与音频流

- `/api/assets/:id/stream` 使用数据库中的 `size_bytes`，不再为每个分片调用 `headObject`；
- `/api/music-tracks/:id/stream` 做相同修改；
- 媒体流响应覆盖全局 `no-store`，使用：

```http
Cache-Control: private, max-age=3600
ETag: "<sha256>"
```

本阶段仍由应用服务器转发视频流；COS/CDN 直链属于后续阶段。

## 三、改动文件

```text
server/src/index.js
server/scripts/backfill-video-previews.js
server/scripts/set-thumb-cache-headers.js
src/main.jsx
src/styles.css
docs/方案-缩略图与可缓存URL-实施任务书.md
```

## 四、存量数据脚本

### 4.1 `backfill-video-previews.js`

默认只处理：

```sql
WHERE deleted_at IS NULL
  AND thumb_key IS NULL
  AND type IN ('video', 'image')
```

支持：

```bash
# 只查看待处理对象，不下载和写入
node scripts/backfill-video-previews.js --dry-run

# 处理缺失缩略图的图片和视频
node scripts/backfill-video-previews.js
```

禁止在生产环境直接运行：

```bash
node scripts/backfill-video-previews.js --regenerate
```

除非明确需要重新生成全部图片和视频封面。

### 4.2 `set-thumb-cache-headers.js`

脚本只能查询并修改：

- `assets.thumb_key`；
- `video_accounts.avatar_key`。

不得查询或修改 `assets.object_key`，避免对原图和原视频执行元数据替换。

脚本支持：

```bash
# 只打印对象列表
node scripts/set-thumb-cache-headers.js --dry-run

# 对缩略图和头像执行自拷贝，补齐缓存元数据
node scripts/set-thumb-cache-headers.js
```

自拷贝参数必须包含：

```js
MetadataDirective: 'Replaced'
ContentType: 'image/jpeg'
CacheControl: 'private, max-age=86400, immutable'
```

生产脚本不为 Region 提供静默默认值；缺失数据库、Bucket、Region 或密钥配置时立即失败。

## 五、本地验证

```bash
node --check server/src/index.js
node --check server/scripts/backfill-video-previews.js
node --check server/scripts/set-thumb-cache-headers.js
npm run build
```

检查改动：

```bash
git diff --check
git diff --stat
git diff -- server/src/index.js \
  server/scripts/backfill-video-previews.js \
  server/scripts/set-thumb-cache-headers.js \
  src/main.jsx \
  src/styles.css
```

## 六、上线前 Review 重点

1. 图片网格是否确实使用 `asset.thumb || asset.src`；
2. 视频网格是否完全不再挂载 `/api/assets/:id/stream`；
3. 稳定签名是否与 COS SDK 对相同 KeyTime 的签名结果一致；
4. 缓存脚本 SQL 是否只包含 `thumb_key` 和 `avatar_key`；
5. 自拷贝是否显式保留 `Content-Type: image/jpeg`；
6. 两个回填脚本是否支持 `--dry-run`；
7. 生产环境是否显式配置 `TENCENT_COS_REGION`；
8. 图片上传、直传 finalize、远程导入和文件替换是否全部生成缩略图；
9. 视频/音频流是否不再执行 `headObject`。

## 七、生产执行顺序

部署新代码后按以下顺序操作：

```bash
# 1. 健康检查
curl -s http://127.0.0.1:18080/api/health

# 2. 查看将修改的存量缩略图和头像
node scripts/set-thumb-cache-headers.js --dry-run

# 3. 确认列表无 object_key 后补缓存元数据
node scripts/set-thumb-cache-headers.js

# 4. 查看缺少缩略图的素材
node scripts/backfill-video-previews.js --dry-run

# 5. 补齐缺少的图片/视频缩略图
node scripts/backfill-video-previews.js
```

在容器部署环境中，用 `docker exec` 包裹上述 node 命令。

## 八、验收标准

### 8.1 功能验收

1. 上传新图片后，响应中的 `thumb` 非空；
2. 替换图片文件后，`thumb` 指向新 Object Key；
3. 上传和替换视频后，视频封面正常；
4. 头像上传和展示正常；
5. 相册封面展示正常；
6. 视频详情播放和 Range 拖动正常；
7. 音频播放正常。

### 8.2 网络验收

1. 同一 24 小时窗口内，两次调用 `/api/assets`，同一 `thumb` URL 完全一致；
2. 对缩略图执行 GET Range 请求，响应包含：

```http
Cache-Control: private, max-age=86400, immutable
Content-Type: image/jpeg
```

3. 素材网格的图片请求优先使用 `.jpg` 缩略图；
4. 网格中不应出现 `/api/assets/:id/stream` 请求；
5. 第二次刷新时缩略图显示 `from memory cache` 或 `from disk cache`；
6. 播放一个视频分片时，COS 不应再额外产生对应的 HEAD Object 请求。

### 8.3 数据验收

```sql
SELECT type, count(*)
  FROM assets
 WHERE deleted_at IS NULL
 GROUP BY type;

SELECT type, count(*)
  FROM assets
 WHERE deleted_at IS NULL
   AND thumb_key IS NOT NULL
 GROUP BY type;
```

图片和视频的有效记录应都有 `thumb_key`；无法生成缩略图的异常文件需要单独记录，不得静默忽略。

## 九、预期效果与边界

本次改造预计：

- 显著降低图片原图外网下行；
- 消除素材网格中的视频 metadata 批量请求；
- 视频和音频每个 Range 请求减少一次 COS HEAD；
- 同一天内重复浏览时，缩略图和头像命中浏览器缓存。

不承诺固定的“25.4 万降到 3～4 万”数值；实际效果取决于真实播放次数、刷新次数和图片尺寸。上线后应分别观察：

- COS 外网下行；
- COS 内网下行；
- GET 请求；
- HEAD 请求；
- 应用服务器公网出流量。

## 十、回滚

代码可通过 Git 回滚，但脚本造成的 COS 元数据修改不会随 Git 自动回滚。

当前脚本只处理缩略图和头像，因此即使代码回滚，保留其私有缓存元数据通常不影响功能。若需要恢复，必须通过另一份元数据脚本显式修改，不能仅执行 `git revert`。

## 十一、已验证的 COS 实施注意事项

| 场景 | 正确处理 |
|---|---|
| 对含中文、空格或括号的路径签名 | FormatString 使用原始未编码 Key，最终 URL 路径再分段编码 |
| `encodeURIComponent` 不编码 `!'()*` | 使用 `encodeCosSegment` 将这五个字符补充为百分号编码 |
| 自拷贝修改 Cache-Control | 必须设置 `MetadataDirective: 'Replaced'`，并显式保留 `ContentType` |
| `CopySource` 使用短格式时报错 | 使用 `bucket.cos.region.myqcloud.com/<编码后key>` 完整格式 |
| GET 签名 URL 使用 `curl -I` 返回 403 | 签名只覆盖 GET，使用 GET Range 请求验证，不使用 HEAD |
| `--regenerate` 同名覆盖缩略图 | 旧稳定 URL 可能在浏览器私有缓存中保留最多 24 小时，生产执行前需明确告知 |
| 播放接口返回 404 或长度异常 | 优先核对 COS 对象是否存在，以及数据库 `size_bytes` 是否与对象实际大小一致 |

视频流 URL 带有基于 SHA-256 的 `v` 查询参数。替换视频文件后 SHA-256 改变，前端会获得新的播放 URL，避免命中替换前的浏览器缓存。
