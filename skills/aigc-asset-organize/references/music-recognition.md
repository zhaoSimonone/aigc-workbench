# 本地音乐识别

`scripts/recognize_music.py` 是 `aigc-asset-organize` 的本地音乐识别子能力。它接收本机视频或音频，使用 FFprobe 读取时长和音轨，再用 FFmpeg 提取适合识别的短片段，最后通过 ACRCloud 识别服务返回结构化 JSON。

## 运行

在 skill 目录安装 Python 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

macOS 系统依赖：

```bash
brew install ffmpeg
```

ACRCloud 凭证（不写入仓库，只在当前 shell 设置）：

```bash
export ACRCLOUD_HOST=identify-cn-north-1.acrcloud.cn
export ACRCLOUD_ACCESS_KEY=...
export ACRCLOUD_SECRET_KEY=...
```

`ACRCLOUD_HOST` 未设置时默认为 `identify-cn-north-1.acrcloud.cn`。`ACRCLOUD_ACCESS_KEY` 和 `ACRCLOUD_SECRET_KEY` 必须设置；脚本不会回显密钥，也不会把它们写入日志或 stdout。

识别本地文件：

```bash
python3 scripts/recognize_music.py "/path/to/video.mp4"
python3 scripts/recognize_music.py "/path/to/audio.mp3" --pretty
```

stdout 始终只输出一个 JSON 对象，方便 Agent 继续生成素材整理提案。输入不存在、没有音轨、媒体损坏、依赖缺失、凭证缺失、网络异常和无匹配都会返回 `success: false` 及结构化 `error`，不会把 Python traceback 写入 stdout。

程序优先通过 Python `requests` 连接 ACRCloud。若当前 macOS 环境仅 Python DNS 解析异常、但系统 `curl` 能正常联网，会自动用 `curl` 重试同一请求。该降级请求的凭证通过标准输入传给 `curl`，不会写入磁盘、命令行参数或日志。

## 采样和重试

- 15 秒以内的文件使用完整音频。
- 更长的文件依次尝试约 10%、45%、72% 位置的 8～10 秒片段。ACRCloud 识别接口单次最多接受约 10 秒音频，因此长片段被切成 8～10 秒的窗口，而不是原来的 12 秒。
- 视频和长音频片段转为单声道 44.1kHz 16-bit PCM WAV；15 秒以内的音频优先直接识别原文件，最后一次尝试才做 WAV 音量标准化（`-af volume=1.5`）。
- WAV 临时文件只存在于系统临时目录，命令结束后自动清理。

## ACRCloud 请求与签名

脚本遵循 ACRCloud Identify Protocol V1：

- 请求方法：`POST https://{host}/v1/identify`
- Content-Type：`multipart/form-data`
- 表单字段：`sample`（音频文件）、`sample_bytes`（文件大小）、`access_key`、`timestamp`、`signature`、`data_type=audio`、`signature_version=1`
- 签名串：`POST\n/v1/identify\n{access_key}\naudio\n1\n{timestamp}`
- 签名：`base64(HMAC-SHA1(access_secret, signature_string))`

`timestamp` 使用秒级整数。每次请求都重新生成时间戳和签名，避免 ACRCloud 因时间偏差拒绝请求。

## 隐私和限制

视频不会上传到 AIGC Shelf 服务器，也不会复制到仓库。程序先在本地使用 FFmpeg 提取音频片段，再把片段发送到 ACRCloud 识别服务；因此它不是完全离线识别，需要网络和 ACRCloud 项目凭证。脚本只使用 ACRCloud，不接入 AudD、Shazam 或其他第三方付费服务。

识别结果只作为素材的音乐提案依据。只有匹配可靠时才写入 `musicTitle`/`musicArtist`；不可靠或无匹配时应保留 `待确认`，不能根据旋律相似度编造曲名。

ACRCloud 成功返回的 `title`、`artist` 和 `album` 是其曲库条目的候选元数据，不是独立的发行信息证明。即使 `score=100`，在没有来源链接、原始发布信息或可访问的第三方曲库 ID 交叉验证时，也不能把这些字段写入音乐库的正式名称、曲名或艺人。此时保留 `待标记/待确认`，并将候选字段和 `acrcloud_id` 作为人工复核证据。以 `@` 开头的 `artist` 尤其可能是上传者标识，不能直接当作艺人。

## 成功输出示例

```json
{
  "success": true,
  "title": "Example Song",
  "artist": "Example Artist",
  "album": "Example Album",
  "shazam_id": "",
  "shazam_url": "",
  "acrcloud_id": "6049f11da7095e8bb8266871d4a70873",
  "matches": 1,
  "source": "acrcloud",
  "segment_start": 6.0,
  "segment_duration": 10.0,
  "score": 100,
  "attempts": 1
}
```

`shazam_id` 和 `shazam_url` 保留为空字符串，便于下游消费旧字段名的代码继续工作；`acrcloud_id` 是 ACRCloud 的 `acrid`。`score`、`album`、`genre`、`spotify_id`、`youtube_id`、`deezer_id`、`release_date`、`duration_ms` 等字段在 ACRCloud 返回数据中存在时才会出现，不会猜测。

## 失败输出

| `error` | 说明 |
| --- | --- |
| `file_not_found` | 输入路径不存在 |
| `no_audio` | 媒体没有音轨 |
| `invalid_media` | FFprobe 无法解析媒体元数据 |
| `ffmpeg_not_found` | 找不到 `ffmpeg` 可执行文件 |
| `ffprobe_not_found` | 找不到 `ffprobe` 可执行文件 |
| `acrcloud_credentials_missing` | 未设置 `ACRCLOUD_ACCESS_KEY` 或 `ACRCLOUD_SECRET_KEY` |
| `acrcloud_request_error` | ACRCloud 请求失败、超时、返回非 JSON 或响应结构异常 |
| `no_match` | ACRCloud 成功响应但没有匹配到音乐 |
