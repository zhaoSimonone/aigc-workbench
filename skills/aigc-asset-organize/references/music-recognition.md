# 本地音乐识别

`scripts/recognize_music.py` 是 `aigc-asset-organize` 的本地音乐识别子能力。它接收本机视频或音频，使用 FFprobe 读取时长和音轨，再用 FFmpeg 提取适合识别的短片段，最后通过 ShazamIO 返回结构化 JSON。

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

识别本地文件：

```bash
python3 scripts/recognize_music.py "/path/to/video.mp4"
python3 scripts/recognize_music.py "/path/to/audio.mp3" --pretty
```

stdout 始终只输出一个 JSON 对象，方便 Agent 继续生成素材整理提案。输入不存在、没有音轨、媒体损坏、依赖缺失、网络异常和无匹配都会返回 `success: false` 及结构化 `error`，不会把 Python traceback 写入 stdout。

## 采样和重试

- 15 秒以内的文件使用完整音频。
- 更长的文件依次尝试约 10%、45%、72% 位置的 8～12 秒片段。
- 视频和长音频片段转为单声道 44.1kHz 16-bit PCM WAV；15 秒以内的音频优先直接识别原文件，最后一次尝试才做 WAV 音量标准化。
- WAV 临时文件只存在于系统临时目录，命令结束后自动清理。

## 隐私和限制

视频不会上传到 AIGC Shelf 服务器，也不会复制到仓库。程序先在本地抽取音频片段，再通过 ShazamIO 请求 Shazam 的识别服务；因此它不是完全离线识别，也不是 Apple 官方公开 REST API。不会接入 AudD、ACRCloud 或其他付费 API，不需要 API Key。

识别结果只作为素材的音乐提案依据。只有匹配可靠时才写入 `musicTitle`/`musicArtist`；不可靠或无匹配时应保留 `待确认`，不能根据旋律相似度编造曲名。

## 成功输出示例

```json
{
  "success": true,
  "title": "Example Song",
  "artist": "Example Artist",
  "shazam_id": "123456",
  "shazam_url": "https://www.shazam.com/track/123456",
  "matches": 1,
  "source": "shazamio",
  "segment_start": 6.0,
  "segment_duration": 12.0,
  "attempts": 1
}
```
