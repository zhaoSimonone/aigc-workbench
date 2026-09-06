# 任务：实现本地免费音乐识别 Skill

请在当前本地项目中实现一个可供 Codex / Agent 自动调用的 `music-recognition` Skill。

目标是：

> 用户只需要提供一个本地视频或音频文件，Skill 自动提取音频、调用免费 Shazam 识别方案，返回歌曲名、歌手等结构化结果，全流程无需用户人工介入。

本阶段只实现**免费方案**，不要接入 AudD、ACRCloud 或其他付费 API。

---

# 一、技术方案

使用：

- FFmpeg / FFprobe
- Python 3
- ShazamIO
- 本地 CLI
- JSON 标准输出

整体流程：

```text
本地视频 / 音频
       ↓
判断文件类型
       ↓
视频 → FFmpeg 提取音频
       ↓
选择适合识别的音频片段
       ↓
ShazamIO
       ↓
Shazam 音乐识别
       ↓
标准化 JSON
       ↓
返回给 Codex / Agent
```

不需要：

- Shazam App 人工识别
- API Key
- Apple Developer 账号
- AudD
- ACRCloud
- 浏览器人工操作

---

# 二、目录结构

优先创建：

```text
skills/
└── music-recognition/
    ├── SKILL.md
    ├── requirements.txt
    ├── README.md
    └── scripts/
        └── recognize_music.py
```

如果当前项目已有 Skill 目录规范，请适配现有目录，不要机械创建重复目录。

---

# 三、依赖

Python：

```text
shazamio
```

系统依赖：

```text
ffmpeg
ffprobe
```

macOS 可以通过：

```bash
brew install ffmpeg
```

安装。

`requirements.txt`：

```text
shazamio
```

不要锁死版本，除非当前最新版存在兼容问题。

---

# 四、核心 CLI

最终必须支持：

```bash
python scripts/recognize_music.py "/path/to/video.mp4"
```

以及：

```bash
python scripts/recognize_music.py "/path/to/audio.mp3"
```

最好额外支持：

```bash
python scripts/recognize_music.py "/path/to/video.mp4" --pretty
```

默认输出机器可读 JSON。

---

# 五、支持的文件类型

至少支持：

## 视频

```text
.mp4
.mov
.mkv
.webm
.avi
.m4v
.flv
```

## 音频

```text
.mp3
.wav
.m4a
.aac
.flac
.ogg
```

如果 FFmpeg 实际还能支持其他格式，可以允许自动处理，不要把代码限制得过死。

---

# 六、视频音频提取策略

不要默认把完整视频转成大音频文件再识别。

对于视频，优先自动选择多个短片段。

目标是兼顾：

- TikTok
- Instagram Reels
- 抖音
- Cosplay 舞蹈
- 换装视频
- 二创视频
- 短剧背景音乐

典型短视频长度可能只有 5～30 秒。

## 视频 ≤ 15 秒

直接提取完整音频进行识别。

例如：

```bash
ffmpeg -i input.mp4 \
-vn \
-ac 1 \
-ar 44100 \
output.wav
```

## 视频 > 15 秒

自动选择多个约 10～12 秒的片段：

```text
第一次：
视频 10% 附近

第二次：
视频中间附近

第三次：
视频 70%～80% 附近
```

例如一个 60 秒视频：

```text
片段 1：6s ～ 18s
片段 2：27s ～ 39s
片段 3：43s ～ 55s
```

只要某个片段成功识别，即可停止后续调用。

---

# 七、识别失败重试策略

需要实现自动 retry，但不要无脑重复相同片段。

建议：

```text
Attempt 1
原始提取音频

Attempt 2
换视频中间位置

Attempt 3
换后半段

Attempt 4
对最可能片段做简单音量标准化后再识别
```

可以用 FFmpeg：

```bash
-af loudnorm
```

或者：

```bash
-af volume=1.5
```

但不要过度修改音频。

原则：

> 优先保持原始音乐特征，因为 Shazam 依赖音频指纹。

---

# 八、ShazamIO 使用方式

使用异步接口。

示例逻辑：

```python
from shazamio import Shazam

shazam = Shazam()
result = await shazam.recognize(audio_path)
```

不要使用已经废弃的旧接口。

从结果里至少解析：

```text
track.title
track.subtitle
track.key
track.url
matches
```

其中：

```text
title
```

是歌曲名称。

```text
subtitle
```

通常是歌手 / Artist。

---

# 九、标准 JSON 输出

成功时：

```json
{
  "success": true,
  "title": "Nervy Funk (Super Slowed)",
  "artist": "DJ KVNXD",
  "shazam_id": "123456",
  "shazam_url": "https://www.shazam.com/...",
  "matches": 1,
  "source": "shazamio",
  "segment_start": 0,
  "segment_duration": 8.5
}
```

如果可以从 Shazam 返回数据里可靠获取，还可以增加：

```json
{
  "album": "...",
  "genre": "...",
  "cover_url": "...",
  "apple_music_url": "...",
  "spotify_url": "..."
}
```

但这些字段不是强制。

不要为了补字段而猜测。

---

# 十、失败输出

没有识别到：

```json
{
  "success": false,
  "error": "no_match",
  "message": "No music match was found.",
  "attempts": 3
}
```

文件不存在：

```json
{
  "success": false,
  "error": "file_not_found",
  "message": "Input file does not exist."
}
```

缺少 FFmpeg：

```json
{
  "success": false,
  "error": "ffmpeg_not_found",
  "message": "FFmpeg or FFprobe is not installed."
}
```

ShazamIO 调用异常：

```json
{
  "success": false,
  "error": "recognition_error",
  "message": "..."
}
```

CLI 不应该直接输出巨大 Python traceback 污染 stdout。

需要：

```text
stdout → JSON
stderr → 调试日志
```

方便 Agent 调用。

---

# 十一、临时文件处理

必须使用系统临时目录，例如：

```python
tempfile.TemporaryDirectory()
```

运行完成自动删除：

```text
segment_1.wav
segment_2.wav
segment_3.wav
```

不要把临时 wav 文件永久留在用户项目目录。

---

# 十二、FFprobe 获取视频长度

使用类似：

```bash
ffprobe \
-v error \
-show_entries format=duration \
-of default=noprint_wrappers=1:nokey=1 \
input.mp4
```

不要依赖 OpenCV 读取视频长度。

原因：

```text
FFprobe 更轻量
依赖更少
与 FFmpeg 本身配套
```

---

# 十三、Python 核心设计

建议拆成以下函数：

```python
get_media_duration(path)

detect_media_type(path)

extract_audio_segment(
    input_path,
    output_path,
    start,
    duration
)

recognize_with_shazam(audio_path)

build_segment_candidates(duration)

recognize_file(input_path)

main()
```

不要把所有代码堆进 `main()`。

---

# 十四、SKILL.md

创建：

```text
skills/music-recognition/SKILL.md
```

内容需要让 Codex 知道什么时候调用 Skill。

例如：

```markdown
# Music Recognition

## Purpose

Identify background music or songs in local video/audio files.

Use this skill when the user asks:

- 这个视频是什么音乐
- 识别这个视频的 BGM
- 这个配音是什么曲子
- 这个视频用了什么歌
- 识别 mp4 里的歌曲
- identify this song
- recognize music from this video

## Inputs

A local video or audio file path.

Supported examples:

- mp4
- mov
- mkv
- webm
- mp3
- wav
- m4a
- flac

## Execution

Run:

python scripts/recognize_music.py "<input_file>"

## Behavior

1. Detect video/audio type.
2. Extract short audio samples with FFmpeg when necessary.
3. Try multiple sections automatically.
4. Recognize using ShazamIO.
5. Return structured JSON.
6. Do not require user interaction.
7. Do not ask the user to manually open Shazam.

## Output

Success:

{
  "success": true,
  "title": "...",
  "artist": "...",
  "shazam_url": "..."
}

Failure:

{
  "success": false,
  "error": "no_match"
}
```

可以根据当前 Codex Skill 规范进一步完善。

---

# 十五、README.md

README 至少包含：

```text
1. 这个工具是什么
2. 安装方式
3. FFmpeg 安装方式
4. Python 依赖安装
5. CLI 使用方法
6. JSON 示例
7. 常见问题
```

macOS：

```bash
brew install ffmpeg
```

Python：

```bash
python3 -m venv .venv

source .venv/bin/activate

pip install -r requirements.txt
```

调用：

```bash
python scripts/recognize_music.py demo.mp4
```

---

# 十六、必须考虑的异常

至少处理：

```text
文件不存在

没有音轨的视频

损坏的视频

FFmpeg 不存在

FFprobe 不存在

ShazamIO 网络请求异常

Shazam 没有匹配

视频非常短

视频超过数小时

路径中有中文

路径中有空格

macOS 文件路径

音频自身就是 wav/mp3，不需要重新抽取
```

---

# 十七、禁止事项

不要：

```text
1. 接入 AudD

2. 接入 ACRCloud

3. 添加任何收费 API

4. 要求用户提供 API Key

5. 要求人工打开 Shazam

6. 使用 Selenium

7. 自动打开浏览器

8. 依赖 GUI

9. 把用户音视频上传到额外第三方存储

10. 为识别音乐而引入大型 AI 模型
```

本阶段保持：

```text
免费
轻量
CLI
Agent-friendly
```

---

# 十八、隐私说明

这个方案虽然不需要上传整个视频，但 ShazamIO 识别过程中会向 Shazam 服务发送用于音乐识别的数据。

README 中明确说明：

```text
视频本身不会主动上传到自建服务器。

程序先在本地使用 FFmpeg 提取音频片段，
再通过 ShazamIO 调用 Shazam 的识别服务。

ShazamIO 并非 Apple 官方公开 REST API。
```

不要宣传成：

```text
100% 完全离线音乐识别
```

因为不是。

---

# 十九、测试

实现完成后直接进行测试，不要只写代码。

至少测试：

## Test 1：正常短视频

使用本地现有：

```text
mp4
```

确认：

```text
FFmpeg 正常抽音频
ShazamIO 正常识别
JSON 合法
```

## Test 2：音频

使用 wav/mp3：

```bash
python scripts/recognize_music.py test.wav
```

## Test 3：不存在的文件

确认输出：

```json
{
  "success": false,
  "error": "file_not_found"
}
```

## Test 4：没有音乐

确认：

```text
程序不会 crash
最终返回 no_match
```

---

# 二十、验收标准

最终必须满足：

```text
[ ] 本地 mp4 可以一条命令识别

[ ] mp3/wav 可以直接识别

[ ] 不需要 API Key

[ ] 不需要人工介入

[ ] 不需要 GUI

[ ] 自动选择音频片段

[ ] 自动重试

[ ] 输出 JSON

[ ] JSON stdout 可直接给 Agent 消费

[ ] 临时文件自动删除

[ ] 异常不会抛出一堆 traceback

[ ] README 完整

[ ] SKILL.md 完整

[ ] 至少实际跑一次本地视频验证
```

---

# 二十一、完成后给我的结果

不要只告诉我“代码已完成”。

请最终输出：

```text
1. 新增/修改了哪些文件

2. 整体实现架构

3. 实际执行过的命令

4. 测试结果

5. 使用示例

6. 如果识别失败，具体失败在哪一步

7. 当前还有哪些已知限制
```

如果测试过程中发现 ShazamIO 最新版本接口发生变化，请查当前安装版本的代码/API 后适配，不要直接删除该能力。

---

# 二十二、后续预留，但本次不要实现

代码架构可以为未来预留：

```text
Provider interface

ShazamProvider
AudDProvider
ACRCloudProvider
```

例如：

```python
class MusicRecognitionProvider:
    async def recognize(self, audio_path):
        ...
```

当前只实现：

```text
ShazamProvider
```

不要实现其他付费 Provider。

后续如果免费方案稳定性不足，我再增加：

```text
ShazamIO
   ↓ fail
AudD
   ↓ fail
ACRCloud
```

本次做到：

```text
Local Video
   ↓
FFmpeg
   ↓
ShazamIO
   ↓
JSON
```

即可。