# 图片缩略图 + 可缓存 URL 实施任务书

> 本任务书面向执行模型：**所有技术方案已定稿、核心代码已在线上环境验证通过**。请严格按步骤执行，不要自行改动设计、不要"顺手优化"。遇到与任务书不符的代码现状，停下来报告，不要猜测着改。

## 一、背景与目标

线上问题：素材库（449 视频 + 165 图片）每次浏览都会重新从 COS 下载全部缩略图和图片**原图**，原因是：

1. 图片素材在网格中加载的是原图（平均 1.77MB/张），没有缩略图；
2. 签名 URL 每次生成时间戳都不同，浏览器永远无法命中缓存。

目标：

1. 图片素材上传/替换时自动生成 480px 宽的 JPG 缩略图（网格自动使用，前端**零改动**）；
2. 缩略图与头像改用「24 小时窗口内完全一致 + 7 天有效期」的稳定签名 URL，并带 `Cache-Control: public, max-age=604800`，让浏览器命中本地缓存；
3. 存量数据通过两个脚本补齐（165 张图片生成缩略图；全部视频缩略图和头像补 Cache-Control 元数据）。

预期效果：月外网下行 38GB → 2–4GB，COS 请求数 25.4 万/月 → 3–4 万/月，二次浏览秒开。

## 二、已验证的关键技术结论（勿改动，直接照抄）

以下三个坑都已在生产环境实测踩过，**任务书给的代码就是修复后的版本**：

1. **签名要对原始 key 计算，URL 用编码后的路径**。COS 服务端把 URL 解码回原始 key 后验签。若对编码后路径签名，含中文/空格/括号的 key 全部 403。
2. **`encodeURIComponent` 不编码 `!'()*` 五个字符，COS 验签要求编码**，所以需要 `encodeCosSegment` 补转义。
3. **putObjectCopy 自拷贝必须带 `MetadataDirective: 'Replaced'`**，且 `CopySource` 必须是完整域名格式 `bucket.cos.region.myqcloud.com/<编码后key>`（短格式 `bucket/key` 会报 "CopySource format error"）。

另：签名 URL 只签了 `get` 方法，用 `curl -I`（HEAD）测试会得到 403——**这是预期行为，不是 bug**，验证时必须用 GET（见第六节命令）。

## 三、改动清单（仅服务端，前端零改动）

### 3.1 `server/src/index.js`

#### (a) 新增稳定签名函数（放在 `objectUrl` 函数后面）

```js
function encodeCosSegment(segment) {
  return encodeURIComponent(segment).replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
}

// 稳定签名 URL：24 小时窗口内多次调用生成完全一致的 URL（浏览器可命中缓存），签名本身 7 天有效。
// 注意：FormatString 必须用原始未编码的 key（与 cos-nodejs-sdk-v5 getAuth 行为一致）。
function stableSignedUrl(key) {
  const host = `${bucket}.cos.${region}.myqcloud.com`;
  const nowUnix = Math.floor(Date.now() / 1000);
  const windowSeconds = 24 * 3600;
  const windowStart = Math.floor(nowUnix / windowSeconds) * windowSeconds;
  const keyTime = `${windowStart};${windowStart + 7 * 24 * 3600}`;
  const encodedPath = key.split('/').map(encodeCosSegment).join('/');
  const httpString = `get\n/${key}\n\nhost=${host}\n`;
  const signKey = crypto.createHmac('sha1', process.env.TENCENT_COS_SECRET_KEY).update(keyTime).digest('hex');
  const sha1 = (data) => crypto.createHash('sha1').update(data).digest('hex');
  const stringToSign = `sha1\n${keyTime}\n${sha1(httpString)}\n`;
  const signature = crypto.createHmac('sha1', signKey).update(stringToSign).digest('hex');
  return `https://${host}/${encodedPath}?q-sign-algorithm=sha1&q-ak=${encodeURIComponent(process.env.TENCENT_COS_SECRET_ID)}&q-sign-time=${keyTime}&q-key-time=${keyTime}&q-header-list=host&q-url-param-list=&q-signature=${signature}`;
}
```

#### (b) `putObject` 支持可选 Cache-Control

当前签名：`function putObject(key, filePath, contentType)`（用 `grep -n "function putObject" server/src/index.js` 定位）。改为第四个可选参数，并透传给 SDK：

```js
function putObject(key, filePath, contentType, cacheControl) {
  // ...原有 cos.putObject 调用中，params 增加：
  // CacheControl: cacheControl || undefined
}
```

即 SDK 调用形如：

```js
cos.putObject({ Bucket: bucket, Region: region, Key: key, Body: fs.createReadStream(filePath), ContentType: contentType, ...(cacheControl ? { CacheControl: cacheControl } : {}) }, ...原有回调...);
```

#### (c) `ingestAsset`：图片生成缩略图

在 `ingestAsset` 函数内，现有 `if (type === 'video') {...}` 生成封面代码块的**后面**，增加图片分支（注意 ffmpeg 参数里 `\\(` 的双反斜杠是转义语法，照抄）：

```js
    if (type === 'image') {
      const previewPath = `${tempPath}.jpg`;
      try {
        await execFileAsync(ffmpegPath, ['-y', '-i', tempPath, '-vf', 'scale=min\\(480\\,iw\\):-2', '-frames:v', '1', '-q:v', '4', previewPath], { timeout: 60000 });
        thumbKey = `${objectKey}.jpg`;
        await putObject(thumbKey, previewPath, 'image/jpeg', 'public, max-age=604800');
      } catch (error) {
        console.warn('Image preview generation skipped:', error.message);
      } finally {
        await fsp.unlink(previewPath).catch(() => {});
      }
    }
```

同时把**视频分支里的缩略图上传**改为带 Cache-Control：

```js
        await putObject(thumbKey, previewPath, 'image/jpeg', 'public, max-age=604800');
```

#### (d) `applyAssetFileReplacement`：替换文件时同样处理

该函数内已有 `if (type === 'video')` 封面生成块。做两处修改：

1. 视频缩略图上传同样加 `'public, max-age=604800'`；
2. 复制 (c) 中的图片缩略图分支加在视频块后面（图片替换后旧缩略图指向旧 key，必须重新生成，否则网格会显示坏图）。

#### (e) `hydrateAsset`：缩略图改用稳定签名

当前：`row.thumb_key ? objectUrl(row.thumb_key) : Promise.resolve('')`。改为：

```js
    row.thumb_key ? Promise.resolve(stableSignedUrl(row.thumb_key)) : Promise.resolve(''),
```

**注意：图片的 `src` 字段（`row.type === 'video' ? ... : objectUrl(row.object_key)`）保持 `objectUrl` 不变**——原图不缓存，详情抽屉按需加载，这是定稿决策，不要改。

#### (f) `albumCoverMap`：相册封面改用稳定签名

函数内两处 `objectUrl(...)`（cover 的 src 和 thumb）改为 `Promise.resolve(stableSignedUrl(...))`。

#### (g) 头像：上传带 Cache-Control + 读取用稳定签名

1. `POST /api/video-accounts/avatar-upload` 里 `putObject(avatarKey, previewPath, 'image/jpeg')` → 增加 `'public, max-age=604800'`；
2. `publicVideoAccountWithAvatar` 里 `objectUrl(row.avatar_key)` → `stableSignedUrl(row.avatar_key)`（它不是 Promise，直接同步返回）。

#### (h) 明确不要动的部分

- `buildDownloadUrl` / `GET /api/assets/:id/download` / `GET /api/assets/:id/download-url` 的 SDK 签名（下载需要 `response-*` 覆盖参数，且一次性使用无需缓存）；
- `GET /api/assets/:id/stream`（视频播放走服务器转发，属阶段 2 范围）；
- `POST /api/assets/upload-ticket` 的 PUT 签名；
- 前端 `src/` 目录**完全不改**（网格已优先使用 `asset.thumb`）。

### 3.2 `server/scripts/backfill-video-previews.js`：补图片缩略图

1. 默认模式（无参数）的 SELECT 改为同时处理缺缩略图的视频和图片：

```js
  const { rows } = await pool.query(
    regenerate
      ? "SELECT id, type, object_key FROM assets WHERE deleted_at IS NULL AND (type='video' OR type='image') ORDER BY created_at ASC"
      : "SELECT id, type, object_key FROM assets WHERE deleted_at IS NULL AND thumb_key IS NULL AND (type='video' OR type='image') ORDER BY created_at ASC",
  );
```

2. `generatePreview` 增加图片分支（在现有视频逻辑旁）：

```js
async function generatePreview(input, output, type) {
  if (type === 'image') {
    await execFileAsync(ffmpegPath, ['-y', '-i', input, '-vf', 'scale=min\\(480\\,iw\\):-2', '-frames:v', '1', '-q:v', '4', output], { timeout: 60000 });
    return null;
  }
  // ...原有视频逻辑不变（时长 + pickPreviewTime + 抽帧）...
}
```

调用处传 `row.type`；图片时 duration 传 `null`。

3. 脚本里的 `putObject(thumbKey, output, 'image/jpeg')` → 加 `'public, max-age=604800'`。

### 3.3 新建 `server/scripts/set-thumb-cache-headers.js`

给**存量**缩略图和头像补 Cache-Control 元数据（新上传的已由 putObject 参数覆盖）。完整文件如下，直接创建：

```js
const { Pool } = require('pg');
const COS = require('cos-nodejs-sdk-v5');
require('dotenv').config({ path: process.env.DOTENV_CONFIG_PATH || require('path').resolve(process.cwd(), '.env') });

const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const bucket = process.env.TENCENT_COS_BUCKET;
const region = process.env.TENCENT_COS_REGION || 'ap-shanghai';
const cos = new COS({ SecretId: process.env.TENCENT_COS_SECRET_ID, SecretKey: process.env.TENCENT_COS_SECRET_KEY, Protocol: 'https:' });

function encodeCosSegment(segment) {
  return encodeURIComponent(segment).replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
}

async function main() {
  const { rows } = await pool.query(`
    SELECT object_key AS key FROM assets WHERE thumb_key IS NOT NULL AND deleted_at IS NULL
    UNION
    SELECT thumb_key AS key FROM assets WHERE thumb_key IS NOT NULL AND deleted_at IS NULL
    UNION
    SELECT avatar_key AS key FROM video_accounts WHERE avatar_key IS NOT NULL
  `);
  const host = `${bucket}.cos.${region}.myqcloud.com`;
  console.log(`Found ${rows.length} object(s) to update`);
  let done = 0;
  let failed = 0;
  for (const row of rows) {
    const key = row.key;
    try {
      await new Promise((resolve, reject) => {
        cos.putObjectCopy({
          Bucket: bucket,
          Region: region,
          Key: key,
          CopySource: `${host}/${key.split('/').map(encodeCosSegment).join('/')}`,
          MetadataDirective: 'Replaced',
          CacheControl: 'public, max-age=604800',
        }, (err) => (err ? reject(err) : resolve()));
      });
      done += 1;
    } catch (error) {
      failed += 1;
      console.error(`Failed ${key}: ${error.message}`);
    }
    if ((done + failed) % 50 === 0) console.log(`progress: ${done + failed}/${rows.length}`);
  }
  console.log(`Done. ok=${done} failed=${failed}`);
}

main().finally(() => pool.end());
```

## 四、本地验证（部署前）

```bash
node --check server/src/index.js
node --check server/scripts/backfill-video-previews.js
node --check server/scripts/set-thumb-cache-headers.js
cd server && npm install && cd ..
npm run build   # 前端无改动，构建应无 diff；若 vite 报 command not found 先 npm install
```

用 `git diff --stat` 核对：只应有 `server/src/index.js`、`server/scripts/backfill-video-previews.js`、新增 `server/scripts/set-thumb-cache-headers.js` 三个文件变化。出现 `src/` 下的改动即为跑偏，回滚重做。

## 五、部署（与既有流程一致）

```bash
git add server && git commit -m "feat: image thumbnails and cacheable stable URLs" && git push origin main
tar czf /tmp/aigc-server.tar.gz server/src server/schema.sql server/package.json server/package-lock.json server/scripts
scp /tmp/aigc-server.tar.gz tencent-personal:/tmp/
ssh tencent-personal 'cd /opt/aigc-shelf && sudo tar xzf /tmp/aigc-server.tar.gz && sudo docker compose up -d --build api && rm /tmp/aigc-server.tar.gz'
```

## 六、上线后操作（在服务器容器内执行，有先后顺序）

```bash
# 1. 健康检查
ssh tencent-personal 'curl -s http://127.0.0.1:18080/api/health'

# 2. 给存量缩略图/头像补 Cache-Control（约 600 个对象，几分钟）
ssh tencent-personal 'sudo docker exec aigc-shelf-api-1 node scripts/set-thumb-cache-headers.js'

# 3. 给 165 张存量图片生成缩略图（只处理 thumb_key IS NULL 的，不会碰已有视频）
ssh tencent-personal 'sudo docker exec aigc-shelf-api-1 node scripts/backfill-video-previews.js'
```

**注意：不要运行 `--regenerate`**，那会重建全部 449 个视频封面，无必要且耗时。

## 七、验收标准（逐条实测，全部满足才算完成）

1. **URL 稳定性**：登录后在两分钟内请求两次 `GET /api/assets`，两次响应中同一素材的 `thumb` 字段字符串**完全一致**（diff 确认）。
2. **Cache-Control 生效**：从 `/api/assets` 响应中任取一个 `thumb` URL，执行
   `curl -s -D - -o /dev/null -r 0-100 "<thumb url>"`，
   应返回 `HTTP/1.1 206` 且包含 `Cache-Control: public, max-age=604800`。
   （**禁止用 `curl -I`**：HEAD 请求对 get 签名必返 403，属预期。）
3. **图片缩略图**：第 3 步脚本执行后，`SELECT count(*) FROM assets WHERE type='image' AND thumb_key IS NOT NULL AND deleted_at IS NULL` 应等于 165；刷新网页，图片网格加载的是小图（开发者工具 Network 里图片请求体积应 < 300KB）。
4. **新上传回归**：网页上传一张新图片 → 网格正常显示缩略图；上传一个新视频 → 封面正常。再测试"编辑素材→替换文件"各一次，确认替换后网格缩略图正常（新 key 新图）。
5. **缓存命中**：同一浏览器当天第二次刷新素材库，开发者工具 Network 中缩略图请求显示 `from disk cache`（或请求数显著减少）。
6. **次日观察**：COS 控制台请求量应明显下降（预期 25.4 万/月 → 3–4 万/月量级）。

## 八、已知坑与禁止事项（再次强调）

| 坑 | 正确做法 |
|---|---|
| 对编码后路径签名 | 签名用原始 key，URL 用 `encodeCosSegment` 编码 |
| `encodeURIComponent` 漏掉 `!'()*` | 必须用任务书的 `encodeCosSegment` |
| `curl -I` 测签名 URL 得 403 | 用 GET（`curl -s -D - -o /dev/null -r 0-100`） |
| putObjectCopy 自拷贝报 illegal | 必须 `MetadataDirective: 'Replaced'` |
| CopySource 用 `bucket/key` 短格式报错 | 用 `bucket.cos.region.myqcloud.com/<编码后key>` 全格式 |
| 想改前端、下载签名、视频流、上传 ticket | 全部禁止，本方案前端零改动 |
| 想给原图（`src`）也加缓存 | 禁止，定稿决策：只缓存缩略图和头像 |

## 九、回滚

所有改动仅服务端：`git revert <commit>` 后按第五节重新打包部署即可。两个脚本只是幂等地补元数据/缩略图，无需回滚数据（缩略图对象可留在 COS，不影响任何功能）。
