---
name: aigc-asset-organize
description: Analyze and organize AIGC Shelf assets by real visual/audio content, updating titles, tags, notes, and optional music metadata with a reviewable diff before any write.
---

# AIGC 素材整理

整理 AIGC Shelf 中已有的图片和视频。这个 skill 的结果必须来自对素材内容的实际观察，不能仅根据原文件名、来源或一组泛化标签猜测。

## 范围解析

使用 `scripts/organize_assets.py inspect` 取数。参数优先级如下：

1. 用户指定一个或多个 `--asset-id` 时，只处理这些 ID；ID 不存在或不属于当前账号时报告错误。
2. 未指定 ID 但指定 `--tab` 时，处理该文件夹：`灵感收集`、`我的创作`、`角色设定`、`项目资料`。
3. 两者都没有时，默认处理 `灵感收集`。

例如：`inspect --asset-id <素材 UUID> --json` 只取指定素材；`inspect --tab 角色设定 --music --json` 取角色设定中需要整理且包含音乐检查的视频；不带范围参数的 `inspect --json` 默认取灵感收集。

按 tab 批量整理时，默认跳过已经有明确标题、非占位标签且已有有效备注的素材；用户说“全部”“重新整理”或明确要求覆盖时才传 `--force`。指定 ID 时，即使素材看起来已整理，也要展示它并说明为什么默认跳过；只有用户确认强制覆盖才改写。

## 内容分析

先获取并观察素材本身，再提出整理结果：

- 图片至少检查完整画面；视频至少检查封面和 3 个均匀分布的画面时刻，必要时增加关键转场或动作发生处的画面。可用 `--download-dir` 将已登录素材下载到临时目录，并使用可用的媒体查看器或 `ffmpeg` 截帧。
- 标题写成可检索的具体短语，优先包含主体/人物、动作或变化、场景，以及镜头或内容用途；多个信息块之间统一使用 `+`，不要使用 `·`、`.` 或类似分隔符。例如“粉色短发女孩+街头回头转身”。不要把 `tiktok-video-20260903162324` 这类平台加类型加时间戳的名称当成明确标题。
- 标签控制在 3–5 个，硬上限为 5 个。只保留最能代表素材特色的主体、动作、场景、镜头/节奏或视觉风格；删除“人物”“视频”“竖屏”等过于泛化、对检索帮助小的标签。只写画面能支持的事实，不臆测人物身份、品牌、地点或剧情。
- 备注用一句到两句记录可复用的画面证据和剪辑价值，例如动作发生在第几秒、转场如何衔接、构图/光线有什么特点。不要写“很有感觉”“适合创作”之类没有信息量的话。
- `角色设定` 只有在画面确实能确认人物时才补 `characterName`/`characterCategory`；无法确认就保留为空并在备注说明。

## 音乐整理

用户指定“音乐整理”或传 `--music` 时，对范围内视频额外分析音轨：

- 先听取音轨（必要时下载原视频），再结合原始来源链接、画面字幕/描述和可用的音频识别能力判断曲名与艺人。只凭旋律相似度不能写成确定答案。
- 能确认时填写 `musicTitle`、`musicArtist`、`musicSource`、`musicStatus=已识别`、`musicConfidence`（`高`/`中`/`低`）和 `musicEvidence`（识别依据）。无法确认时使用 `musicStatus=待确认`，标题可留空，证据写明“未找到可靠匹配”；禁止编造歌名。
- ACRCloud 的 `title`、`artist`、`album` 仅是候选元数据，`score=100` 只说明音频指纹命中其曲库，不能单独证明这些字段是公开发行曲目的正式信息。只有曲名和艺人能由来源链接、画面/原始发布信息，或 ACRCloud 返回的可访问第三方曲库 ID 交叉确认时，才可写入 `musicStatus=已识别`。没有交叉证据时保留 `musicStatus=待确认`，将候选值和 ACRCloud ID 记入 `musicEvidence` 供人工复核；不要把疑似上传者（如以 `@` 开头的名称）当作艺人，也不要把未验证的 `album` 当作专辑名。
- 为便于统一剪辑，优先使用音乐结构化字段检索；不要为了音乐无限增加标签。只有音乐是素材的明确特色且仍不超过 5 个标签时，才加入一个 `音乐` 或 `音乐待识别` 标签，不再使用 `音乐·曲名` 这类额外标签。不要删除原有仍然准确的特色标签。
- 无音轨、只有人声或无法听清时，记录 `musicStatus=无音乐/无法判断` 及原因，而不是把人声当成歌曲。

### 本地歌曲识别

当用户要求识别某个本地视频/音频的歌曲，或对已下载的素材执行“音乐识别”时，读取 [references/music-recognition.md](references/music-recognition.md)，再运行本地 CLI：

```bash
python3 /path/to/skills/aigc-asset-organize/scripts/recognize_music.py "/path/to/video.mp4" --pretty
```

识别后端为 ACRCloud（项目名称 `music_identify`，host 默认 `identify-cn-north-1.acrcloud.cn`）。脚本只接受环境变量 `ACRCLOUD_HOST`、`ACRCLOUD_ACCESS_KEY`、`ACRCLOUD_SECRET_KEY`，不要把密钥写入命令行参数、Skill、README、日志或 Git；用户已在会话中提供真实凭证时由 Agent 在当前 shell 设置环境变量后执行，且最终回复不得回显密钥。

CLI 默认输出单个 JSON；将 `success=true` 的 `title`、`artist`、`acrcloud_id`、`score`、`segment_start` 和 `attempts` 作为识别证据，仍需结合画面/来源判断置信度后再生成整理提案。识别失败时保留 `musicStatus=待确认`，不要猜测曲名。视频下载、音频抽取和临时文件处理都在本地完成；ACRCloud 只接收抽取出的短音频片段，AIGC Shelf 服务器不下载视频、不解析音频。脚本只使用 ACRCloud，不接入 AudD、ShazamIO 或其他第三方付费服务。

如果 Python DNS 在当前 macOS 环境中无法解析 ACRCloud、但系统 `curl` 可联网，CLI 会自动通过 `curl` 重试；密钥仅经标准输入传入该请求，不会出现在命令行、日志或文件中。

### 音乐库构建

当用户要求“把所有视频的音乐提取出来”“构建音乐库”或“去重音频”时，使用服务端的音乐库流程，而不是猜测歌名：

```bash
python3 /path/to/skills/aigc-asset-organize/scripts/organize_assets.py music-library
python3 /path/to/skills/aigc-asset-organize/scripts/organize_assets.py music-library --build
python3 /path/to/skills/aigc-asset-organize/scripts/organize_assets.py music-library --dedupe-content --json
```

第一条只读取并列出当前账号的音乐库。第二条默认是预览提醒；得到用户明确确认后，才加 `--confirm` 执行：

```bash
python3 /path/to/skills/aigc-asset-organize/scripts/organize_assets.py music-library --build --confirm --json
```

构建过程全部在本地执行，参考 OpenMontage 的 FFmpeg/ffprobe 媒体检查方式：skill 从服务端读取视频并下载到临时目录，在本地将第一条音轨统一转换为单声道 44.1kHz PCM WAV，再计算 SHA-256；服务端只接收已提取的音频文件和关联 ID，不下载视频也不解析音频。相同标准化音频只上传一份 COS 对象，多个视频通过关系表指向同一曲目；这是精确内容去重，不是声学歌曲识别，不能据此填写真实曲名。新曲目状态为 `待标记`，可在网页“音乐库”中试听并人工填写库内名称、曲名、艺人、备注和状态。人工标记会同步到所有关联视频的音乐字段，便于后续按曲目统一剪辑。

没有可提取音轨的视频会计入“无音乐/无法判断”，失败项会单独列出原因；不会因为无法识别歌名而伪造标签或艺人。

`--dedupe-content` 是独立的本地内容去重步骤：它下载音乐库中的 WAV，在本地计算短时频谱指纹，并用时间偏移对齐来匹配同一首音乐的不同截取片段、起止点和轻微编码差异。自动合并要求持续重叠片段至少 5 秒，平均相似度至少 0.97 且最差四分位相似度至少 0.95，并采用全组一致（complete-link）聚类，避免一条边缘相似记录把不同音乐串成大组。自动合并时优先保留音频时长更长的记录，时长相同再考虑人工标记状态和来源数量。相似度达到 0.94 但未达到自动合并阈值的记录会输出为 `reviewCandidates` 人工复核候选，不会自动删除。它先输出待合并组和相似度，不带 `--confirm` 不会删除关系或音频对象；确认后使用：

```bash
python3 /path/to/skills/aigc-asset-organize/scripts/organize_assets.py music-library --dedupe-content --confirm --json
```

去重不是仅按时长、文件名或文件 SHA-256 合并；相似但未达到高置信度阈值的记录会保留，供人工试听确认。合并会保留所有来源视频；若重复记录中已有人工标记，只有在时长相同的情况下才用于选择幸存曲目。

## 提案、diff 与写回

整理分为“观察提案”和“写回”两阶段，不能跳过审阅：

1. 为每个候选生成提案 JSON。每项必须包含 `id`、`before`、`after` 和 `reason`；`after` 只放需要变更的字段，字段名使用 API 形式：`name`、`tags`、`note`、`folder`、`characterName`、`characterCategory`、`musicTitle`、`musicArtist`、`musicSource`、`musicStatus`、`musicConfidence`、`musicEvidence`。
2. 提案中的 `reason` 要引用实际观察证据；音乐提案还要包含置信度和识别依据。没有变化的素材不要生成空更新项。

   最小格式示例：`{"items":[{"id":"素材 UUID","before":{"name":"原名","tags":["待整理"]},"after":{"name":"夜街回头转身","tags":["人物","夜景","回头动作"]},"reason":"第 2 秒人物在夜间街道回头，镜头从中景快速推近"}]}`。
3. 先运行：

   ```bash
   python3 /path/to/skills/aigc-asset-organize/scripts/organize_assets.py apply --plan /tmp/aigc-asset-organize-plan.json
   ```

   该命令只打印逐字段 diff 和风险提醒，不会更新服务器。把 diff 展示给用户，明确列出标题、标签、备注、文件夹和音乐字段的改动，并等待用户明确同意（例如“确认写回”“按此更新”）。
4. 得到明确同意后，重复命令并加 `--confirm` 才能写回。脚本会重新读取当前值，若素材在预览后已被别人修改则拒绝该项，避免覆盖新内容；写回使用原子 organize API。
5. 汇报每项结果：已更新、因无变化跳过、因冲突拒绝或失败。没有 `--confirm` 的运行绝不能声称已完成整理。

## 登录与安全边界

脚本复用 `~/.codex/aigc-shelf-session.json` 的 AIGC Shelf 会话；失效时使用 `AIGC_SHELF_EMAIL`/`AIGC_SHELF_PASSWORD` 或安全提示登录。不要输出密码、Cookie、COS 密钥，也不要下载到仓库或把媒体复制到长期目录。整理是外部状态变更，只能在用户确认 diff 后执行。

脚本默认使用 `https://aigc.chatcanvas.online`，可用 `AIGC_SHELF_URL` 或 `--api` 覆盖。
