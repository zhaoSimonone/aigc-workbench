# 音乐识别方案切换：后续 Agent 执行提示词

请在仓库 `/Users/simon/Desktop/dev/code/aigc-workbench` 继续实现本任务。

## 用户目标

将本地音乐识别能力集成到现有的 `aigc-asset-organize` skill 中。ShazamIO 在当前网络环境不可用，请改用 ACRCloud 识别方案。

## 重要边界

1. 音视频下载、FFprobe/FFmpeg 音频提取、片段选择和临时文件处理必须在本地执行。
2. AIGC Shelf 服务器只负责素材、音频对象和关系管理，不下载视频、不解析视频音频。
3. ACRCloud 只接收本地提取出的短音频片段。
4. 不要把 ACRCloud 密钥写入代码、Skill、日志、Git 或最终回复。
5. 使用环境变量：
   - `ACRCLOUD_HOST=identify-cn-north-1.acrcloud.cn`
   - `ACRCLOUD_ACCESS_KEY`
   - `ACRCLOUD_SECRET_KEY`
6. ACRCloud 项目名称为 `music_identify`。
7. 用户已在上一条消息提供真实 access key 和 secret key。执行时从当前会话读取并安全配置，禁止回显。

## 参考资料

- ACRCloud 文档：https://docs.acrcloud.cn/console/recognize-music.html
- 本地任务书：`/Users/simon/Desktop/dev/code/aigc-workbench/docs/本地音乐识别 Skill｜Codex 执行任务书.md`
- OpenMontage 参考项目：`/Users/simon/Desktop/dev/code/OpenMontage`

## 当前已有实现

- 当前提交：`18ffcb6 feat: integrate local music recognition into asset organize skill`
- Skill 目录：`/Users/simon/Desktop/dev/code/aigc-workbench/skills/aigc-asset-organize`
- 已有文件：
  - `SKILL.md`
  - `README.md`
  - `requirements.txt`
  - `references/music-recognition.md`
  - `scripts/organize_assets.py`
  - `scripts/recognize_music.py`
- 当前 `recognize_music.py` 使用 ShazamIO，需要改为 ACRCloud，或实现可配置 Provider 并默认使用 ACRCloud。
- 当前音乐去重规则已经完成：
  - 自动合并要求高置信度相似；
  - 相似但有混音差异的记录输出 `reviewCandidates`；
  - 自动合并时优先保留 `durationSeconds` 更长的记录。
- 不要擅自合并人工复核候选。

## 需要完成的工作

1. 阅读当前：
   - `skills/aigc-asset-organize/SKILL.md`
   - `skills/aigc-asset-organize/scripts/recognize_music.py`
   - `skills/aigc-asset-organize/references/music-recognition.md`
   - 用户任务书全文
2. 根据 ACRCloud 当前文档确认接口：
   - 请求路径
   - HTTP 方法
   - multipart 字段
   - access key、signature、data_type、signature_version、sample_bytes 等参数
   - HMAC-SHA1 签名规则
   - 返回 JSON 结构
3. 保持本地 CLI：

   ```bash
   python3 skills/aigc-asset-organize/scripts/recognize_music.py "/path/to/video.mp4" --pretty
   ```

4. 保留现有能力：
   - 支持 `mp4`、`mov`、`mkv`、`webm`、`avi`、`m4v`、`flv`
   - 支持 `mp3`、`wav`、`m4a`、`aac`、`flac`、`ogg`
   - FFprobe 判断时长和音轨
   - 15 秒以内音频直接识别
   - 长视频选择多个 8～12 秒片段
   - 自动重试不同片段
   - 最后一次可做轻微音量标准化
   - 临时文件使用 `tempfile.TemporaryDirectory` 自动清理
   - stdout 只输出一个 JSON
   - stderr 才输出调试信息
   - 错误不能输出 Python traceback
5. ACRCloud 识别成功后统一输出至少：

   ```json
   {
     "success": true,
     "title": "...",
     "artist": "...",
     "album": "...",
     "shazam_id": "",
     "shazam_url": "",
     "acrcloud_id": "...",
     "matches": 1,
     "source": "acrcloud",
     "segment_start": 0,
     "segment_duration": 10,
     "attempts": 1
   }
   ```

   不可靠字段留空，不要猜测。

6. 失败输出至少支持：
   - `file_not_found`
   - `no_audio`
   - `invalid_media`
   - `ffmpeg_not_found`
   - `ffprobe_not_found`
   - `acrcloud_credentials_missing`
   - `acrcloud_request_error`
   - `no_match`
7. 更新：
   - `skills/aigc-asset-organize/requirements.txt`
   - `skills/aigc-asset-organize/SKILL.md`
   - `skills/aigc-asset-organize/README.md`
   - `skills/aigc-asset-organize/references/music-recognition.md`

   文档中不要再把 ShazamIO 作为默认方案；应说明 ACRCloud 需要项目凭证和网络。

8. 依赖尽量轻量，优先使用 Python 标准库加 `requests` 或 `aiohttp`。不要引入大型 AI 模型。
9. 不要接入 AudD、Shazam 或其他第三方付费服务。
10. 保持音乐库构建逻辑不变：音乐库音频提取和内容去重仍然本地执行，服务器只保存音频对象及关系。

## 安全要求

- 不要把 access key / secret key 写入 `requirements.txt`、README、SKILL 或测试文件。
- 不要把密钥放进命令行参数。
- 不要提交 `.env`。
- 可以支持从环境变量读取。
- 最终回复不得输出完整密钥。

## 测试要求

1. 使用 `python3 -m py_compile` 或 AST 校验所有 Python 文件。
2. 运行：

   ```bash
   python3 /Users/simon/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
     skills/aigc-asset-organize
   ```

3. 测试不存在文件，确认输出 `file_not_found`。
4. 测试无音轨视频，确认输出 `no_audio`。
5. 使用本地 FFmpeg 生成短音频/视频，测试音频提取流程。
6. 使用 mock HTTP 或脱敏测试响应验证 ACRCloud 签名、请求和响应解析。
7. 如果当前网络和凭证可用，实际识别一个本地媒体；如果失败，明确区分：
   - 代码/签名错误
   - 凭证错误
   - ACRCloud 网络/DNS 限制
   - 音频无匹配
8. 不要把真实音频上传到除 ACRCloud 外的服务。
9. 提交前运行 `git diff --check`。

## 既有整理规则

- 标签最多 5 个。
- 标题分隔符使用 `+`，不要使用 `.`。
- 高相似但混音不同的音乐进入人工复核候选。
- 自动合并优先保留较长音频。
- 写回服务器前必须展示 diff 并等待确认。

## 完成后汇报

- 修改了哪些文件；
- ACRCloud 请求和签名实现方式；
- 本地音频提取和重试流程；
- 实际执行过的命令；
- 测试结果；
- 当前已知限制；
- Git 提交哈希。
