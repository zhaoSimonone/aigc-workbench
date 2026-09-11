# ACRCloud 迁移 Review 文档

> 供 Codex 对本次「本地音乐识别从 ShazamIO 切换到 ACRCloud」改动做只读 Review 使用。
> 仓库：`/Users/simon/Desktop/dev/code/aigc-workbench`
> 提交：`d1f35a0 feat: switch local music recognition from ShazamIO to ACRCloud`
> 分支：`main`（领先 `origin/main` 5 个提交，未推送）

---

## 1. 任务背景

`aigc-asset-organize` skill 原有的本地音乐识别 CLI `recognize_music.py` 使用 ShazamIO 作为后端。ShazamIO 在当前网络环境不可用，需改用 ACRCloud 识别方案，同时：

- 保持 CLI 兼容：`python3 scripts/recognize_music.py "/path/to/file.mp4" --pretty`
- 不把密钥写入代码/文档/日志/CLI 参数/Git，只从环境变量读取
- 不接入 AudD、Shazam 或其他第三方付费服务
- 保留既有音乐库构建与内容去重逻辑不变
- 保留所有已实现的错误类型，新增 `acrcloud_credentials_missing` / `acrcloud_request_error`

任务提示词原文：`docs/音乐识别方案切换-后续Agent提示词.md`。

## 2. 改动清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `skills/aigc-asset-organize/scripts/recognize_music.py` | 重写 | ShazamIO → ACRCloud Identify Protocol V1，保留 `MusicRecognitionProvider` 接口 |
| `skills/aigc-asset-organize/requirements.txt` | 改 | `shazamio` → `requests>=2.28` |
| `skills/aigc-asset-organize/SKILL.md` | 改 | 「本地歌曲识别」章节改写为 ACRCloud，说明环境变量与安全边界 |
| `skills/aigc-asset-organize/README.md` | 改 | 安装/运行说明改为 ACRCloud |
| `skills/aigc-asset-organize/references/music-recognition.md` | 重写 | ACRCloud 方案、签名规则、错误码表、UTF-8 输出示例 |
| `skills/aigc-asset-organize/tests/__init__.py` | 新增 | 测试包标识 |
| `skills/aigc-asset-organize/tests/test_recognize_music.py` | 新增 | 22 个脱敏 mock 单元测试 |
| `.gitignore` | 改 | 追加 `__pycache__/` |
| `docs/*.md` | 新增 | 原始任务书与方案切换提示词归档 |

`organize_assets.py` 与音乐库构建/去重逻辑**未改动**（符合任务边界）。

## 3. ACRCloud 接入实现要点

### 3.1 端点与签名

- 端点：`POST https://{ACRCLOUD_HOST}/v1/identify`（host 默认 `identify-cn-north-1.acrcloud.cn`，项目名 `music_identify`）
- Content-Type：`multipart/form-data`（由 `requests.post(files=..., data=...)` 自动生成）
- 签名串（`build_string_to_sign`）：

  ```
  POST\n/v1/identify\n{access_key}\naudio\n1\n{timestamp}
  ```

- 签名（`sign_request`）：`base64(HMAC-SHA1(access_secret, signature_string))`，只用标准库 `hmac` + `hashlib.sha1` + `base64`
- 表单字段：`sample`（音频文件二进制）、`sample_bytes`（文件大小）、`access_key`、`timestamp`（秒级整数 `int(time.time())`）、`signature`、`data_type=audio`、`signature_version=1`

### 3.2 凭证管理

- 凭证只从环境变量读取：`ACRCLOUD_HOST` / `ACRCLOUD_ACCESS_KEY` / `ACRCLOUD_SECRET_KEY`（`resolve_credentials`）
- `ACRCLOUD_HOST` 未设置时默认 `identify-cn-north-1.acrcloud.cn`
- `ACRCLOUD_ACCESS_KEY` 或 `ACRCLOUD_SECRET_KEY` 缺失 → 返回 `acrcloud_credentials_missing`
- 脚本从不回显密钥，stderr 调试日志也不打印密钥
- CLI 不接受密钥参数（`parse_args` 没有相关 flag）
- 已 grep 扫描确认提交 diff、README、SKILL.md、references、tests 均无真实密钥

### 3.3 响应解析与 UTF-8 修复

- `response.encoding = "utf-8"` 在 `response.json()` 之前强制设置。**这是踩过的坑**：ACRCloud 不在 Content-Type 里带 charset，`requests` 会退回 ISO-8859-1，导致中文曲名乱码（如 "故事里你先转身" 变成 "æ\u95…äº‹é‡Œ…"）。已加 `test_request_shape_and_signature` 守护此行为。
- `normalize_acrcloud_result` 解析 `metadata.music[0]`：
  - `title` / `artists[0].name` / `album.name` / `acrid` → `acrcloud_id`
  - 可选字段（仅当 ACRCloud 返回时才出现，不猜测）：`score` / `spotify_id` / `youtube_id` / `deezer_id` / `genre` / `release_date` / `duration_ms`
  - `shazam_id` 和 `shazam_url` 保留为空字符串，便于下游消费旧字段名的代码继续工作
  - `source` 固定为 `"acrcloud"`

### 3.4 错误码区分

`normalize_acrcloud_result` 根据 ACRCloud `status.code`：

| `status.code` | 处理 | 输出 `error` |
| --- | --- | --- |
| `0` | 解析 `metadata.music` | 成功（或 `no_match` 若 music 列表为空） |
| `1001` | No result | `no_match`（继续重试下一段） |
| 其他非零（2003/3001/3013…） | 抛 `acrcloud_request_error` | `acrcloud_request_error`（不重试同一段，最终聚合到 result） |

**这样区分的原因**：凭证错误 / 签名错误 / 服务端错误是请求级问题，重试不会修复；而音频无匹配是正常结果。混在一起会让 Agent 无法判断是配置问题还是真没匹配到。

### 3.5 音频片段与重试

- FFprobe 读取时长和音轨（`probe_media`），无音轨 → `no_audio`
- ≤15s 文件直接用原音频识别（音频文件不重编码，视频才抽音频）
- \>15s 文件按 10% / 45% / 72% 位置切 **8～10 秒**片段（ACRCloud 单次约 10s 上限，从原来的 12s 下调到 `SEGMENT_MAX_SECONDS=10.0`）
- 每个片段转单声道 44.1kHz 16-bit PCM WAV，`tempfile.TemporaryDirectory` 自动清理
- 全部失败后对最后一个片段做 `-af volume=1.5` 轻微音量标准化再试一次
- 任一片段成功即返回；全部失败按是否有 request error 决定返回 `acrcloud_request_error` 还是 `no_match`

## 4. 输出 JSON 形状

成功（真实识别结果，OpenMontage 10.9s mp4）：

```json
{
  "success": true,
  "title": "故事里你先转身",
  "artist": "不逢海",
  "album": "选条不归路",
  "shazam_id": "",
  "shazam_url": "",
  "acrcloud_id": "138a753414e6b3454ae8bfb086d21e76",
  "matches": 1,
  "source": "acrcloud",
  "segment_start": 0.0,
  "segment_duration": 10.892,
  "score": 100,
  "release_date": "2026-05-31",
  "duration_ms": 188000,
  "attempts": 1
}
```

失败（8 种错误类型，对应任务要求）：

| `error` | 触发条件 |
| --- | --- |
| `file_not_found` | 输入路径不存在 |
| `no_audio` | 媒体没有音轨 |
| `invalid_media` | FFprobe 无法解析或 FFmpeg 处理失败（原 `media_processing_error`/`audio_extract_error` 已合并到这里，符合任务列出的错误类型） |
| `ffmpeg_not_found` | 找不到 `ffmpeg` 可执行文件 |
| `ffprobe_not_found` | 找不到 `ffprobe` 可执行文件 |
| `acrcloud_credentials_missing` | 未设置 `ACRCLOUD_ACCESS_KEY` 或 `ACRCLOUD_SECRET_KEY` |
| `acrcloud_request_error` | ACRCloud 请求失败、超时、非 JSON、或 `status.code` 非 0/1001 |
| `no_match` | ACRCloud 成功响应但无匹配 |

stdout 始终只输出一个 JSON 对象；stderr 才写调试日志；异常不抛 Python traceback 到 stdout。

## 5. 测试

### 5.1 静态校验

```bash
python3 -m py_compile skills/aigc-asset-organize/scripts/recognize_music.py skills/aigc-asset-organize/tests/test_recognize_music.py  # OK
python3 /Users/simon/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/aigc-asset-organize  # Skill is valid!
git diff --check  # 无空白错误
grep -rE "e9892587|3Vafm0TB" skills/aigc-asset-organize/ docs/  # 无凭证泄漏
```

### 5.2 单元测试（22 个，全部通过，无需真实凭证）

```bash
python3 -m unittest tests.test_recognize_music -v  # Ran 22 tests in ~1.6s, OK
```

覆盖：

- `StringToSignTests` — 签名串格式、HMAC-SHA1 向量
- `NormalizeResultTests` — 成功 payload、no-match payload、空 music 列表、缺 title/acrid
- `BuildSegmentCandidatesTests` — 短文件用整段、长文件 3 个不同起点
- `ResolveCredentialsTests` — 缺凭证、env 读取、override 优先、默认 host
- `RecognizeFileFailureTests` — `file_not_found` / `acrcloud_credentials_missing` / `no_audio` / `invalid_media` / `ffprobe_not_found` / `ffmpeg_not_found`
- `ACRCloudRequestTests` — multipart 请求形状、签名一致性、UTF-8 encoding 守护、`requests.Timeout` → `acrcloud_request_error`、no-match payload → `no_match`、auth error（code 2003）→ `acrcloud_request_error`

### 5.3 真实端到端识别（使用用户提供的凭证）

| 输入 | 期望 | 实际结果 |
| --- | --- | --- |
| 5s 正弦波 wav（fake 凭证） | `acrcloud_request_error`（code 3001） | ✅ `acrcloud_request_error`，message 含 "3001: Missing/Invalid Access Key" |
| 5s 正弦波 wav（真凭证） | `no_match`（正弦波非真实歌曲） | ✅ `no_match`，2 次尝试 |
| 5s 视频（含 440Hz 音轨，真凭证） | `no_match`（FFmpeg 抽音轨链路正常） | ✅ `no_match`，2 次尝试 |
| 30s 视频（含 440Hz 音轨，真凭证） | `no_match`，多片段重试 | ✅ `no_match`，4 次尝试（3 片段 + 1 标准化） |
| OpenMontage 8.3s mp4（真凭证） | 成功识别 | ✅ "Kavkaz" by "Starly"，score 91，`acrcloud_id=a83ccea845e50a2343a97f737fb98145` |
| OpenMontage 10.9s mp4（真凭证） | 成功识别（含中文） | ✅ "故事里你先转身" by "不逢海"，score 100，UTF-8 正确 |

## 6. 已知限制

1. **依赖网络和 ACRCloud 凭证**：不是离线识别；凭证失效或网络/DNS 受限时返回 `acrcloud_request_error`。
2. **单次请求 ≤10s 音频**：ACRCloud 硬限制；长视频只取 3 个 8-10s 片段，若音乐只在未取到的位置可能漏识别。
3. **`shazam_id`/`shazam_url` 保留为空字符串**：兼容下游旧字段名，不再有真实 Shazam 数据。
4. **`organize_assets.py` 服务端逻辑未改动**：音乐库构建与内容去重仍是本地执行、服务端只存关系。
5. **AudD/Shazam 未接入**：按任务要求只使用 ACRCloud。
6. **凭证需在当前 shell 设置**：未提供 `.env` 模板（避免误提交），README 已说明用 `export` 设置。
7. **`MusicRecognitionProvider` 接口保留**：当前只有 `ACRCloudProvider` 一个实现，便于未来扩展但本次未实现其他 Provider。

## 7. Git 提交

```
d1f35a0 feat: switch local music recognition from ShazamIO to ACRCloud
10 files changed, 1925 insertions(+), 131 deletions(-)
```

工作树干净，未推送（本地领先 `origin/main` 5 个提交）。

## 8. Review 建议关注点

以下是我在实现时反复确认过、但希望 Review 二次把关的点：

### 8.1 安全

- [ ] 确认 `git show HEAD` 的完整 diff 中无 `e9892587...` / `3Vafm0TB...` 字样（真实 access_key / secret_key）
- [ ] 确认 CLI `parse_args` 不接受任何密钥参数
- [ ] 确认 stderr 调试日志只打印 ACRCloud 返回的错误消息，不打印签名串里的密钥
- [ ] 确认 `tests/test_recognize_music.py` 里所有 `ACRCLOUD_ACCESS_KEY` / `ACRCLOUD_SECRET_KEY` 都是 `AK_TEST` / `SK_TEST` / `fake_...` 等占位值

### 8.2 签名与请求

- [ ] `build_string_to_sign` 的 6 行顺序与 ACRCloud 文档一致（method → uri → access_key → data_type → signature_version → timestamp）
- [ ] `sign_request` 用 `hashlib.sha1`，密钥和消息都 `.encode("ascii")`，结果 base64 后 `.decode("ascii")`
- [ ] `timestamp` 用 `int(time.time())`（秒级整数），文档示例是 `time.time()`（浮点），两者 ACRCloud 都接受；确认实现一致
- [ ] multipart `files` 字段名是 `"sample"`，`data` 字段名是 `access_key`/`sample_bytes`/`timestamp`/`signature`/`data_type`/`signature_version`，和 ACRCloud 文档完全一致
- [ ] `sample_bytes` 用 `audio_path.stat().st_size`，和上传文件大小一致（测试已断言）

### 8.3 错误处理

- [ ] `status.code` 非 0/1001 抛 `acrcloud_request_error` 而非返回 `no_match`（`normalize_acrcloud_result`）
- [ ] `requests.Timeout` / `requests.ConnectionError` 等 HTTP 异常 → `acrcloud_request_error`（`recognize_with_acrcloud` 的 `except Exception`）
- [ ] 非 JSON 响应 → `acrcloud_request_error`（`recognize_with_acrcloud` 的 `except ValueError`）
- [ ] `run_command` 把 `subprocess.CalledProcessError` / `TimeoutExpired` / `FileNotFoundError` 映射为 `invalid_media` / `invalid_media` / `ffmpeg_not_found`（注意：`FileNotFoundError` 在 `run_command` 里映射为 `ffmpeg_not_found`，这可能不够精确——如果 `ffprobe` 调用触发 `FileNotFoundError`，错误会是 `ffmpeg_not_found` 而非 `ffprobe_not_found`。但实际 `resolve_binary` 会先于 `run_command` 抛 `ffprobe_not_found`，所以不会走到这条。Review 可确认此路径。）
- [ ] `extract_audio_segment` 失败映射为 `invalid_media`（原 `audio_extract_error` 已合并）

### 8.4 重试逻辑

- [ ] `recognize_file` 的 `had_request_error` 标志：只要有任何一次 request error，最终结果就是 `acrcloud_request_error`，即使其他片段返回了 `no_match`（避免把凭证错误误报为 "音频无匹配"）
- [ ] 最后一次标准化尝试的 `try/except/else` 结构：`RecognitionFailure` 在 except 里处理，`normalized` 判断在 else 里，避免 `normalized` 变量泄漏
- [ ] `tempfile.TemporaryDirectory` 在 `with` 块退出时自动清理（包括异常路径）

### 8.5 UTF-8 修复

- [ ] `response.encoding = "utf-8"` 在 `response.json()` 之前（`recognize_with_acrcloud`）
- [ ] `json.dumps(result, ensure_ascii=False, ...)` 在 `main` 里（中文不转义为 `\uXXXX`）
- [ ] 测试 `test_request_shape_and_signature` 断言 `captured["response"].encoding == "utf-8"`

### 8.6 文档一致性

- [ ] `SKILL.md` / `README.md` / `references/music-recognition.md` 不再把 ShazamIO 作为默认方案
- [ ] 错误码表和实现一致（8 种错误类型）
- [ ] 环境变量名 `ACRCLOUD_HOST` / `ACRCLOUD_ACCESS_KEY` / `ACRCLOUD_SECRET_KEY` 在代码和文档中一致

### 8.7 边界遵守

- [ ] AIGC Shelf 服务器不下载视频、不解析音频（`recognize_music.py` 完全本地处理，只和 ACRCloud 通信）
- [ ] 不接入 AudD、Shazam 或其他第三方付费服务
- [ ] 不引入大型 AI 模型
- [ ] 依赖轻量：只用 `requests` + Python 标准库
- [ ] 音乐库构建逻辑（`organize_assets.py`）未改动
- [ ] 不擅自合并人工复核候选（`reviewCandidates` 逻辑未动）

## 9. 复现命令

```bash
cd /Users/simon/Desktop/dev/code/aigc-workbench

# 单元测试
cd skills/aigc-asset-organize && python3 -m unittest tests.test_recognize_music -v

# 失败模式（无需凭证）
python3 scripts/recognize_music.py "/tmp/does-not-exist.mp4" --pretty
env -u ACRCLOUD_ACCESS_KEY -u ACRCLOUD_SECRET_KEY python3 scripts/recognize_music.py "/path/to/real.wav" --pretty

# 真实识别（凭证从环境变量传入，不写入任何文件）
ACRCLOUD_ACCESS_KEY=... ACRCLOUD_SECRET_KEY=... ACRCLOUD_HOST=identify-cn-north-1.acrcloud.cn \
  python3 scripts/recognize_music.py "/path/to/video.mp4" --pretty

# 静态校验
python3 -m py_compile skills/aigc-asset-organize/scripts/recognize_music.py
python3 /Users/simon/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/aigc-asset-organize
git diff --check
```

## 10. 我的不确定点（希望 Review 指出）

1. **`run_command` 里 `FileNotFoundError` 映射为 `ffmpeg_not_found`**：理论上 `subprocess.run` 在命令路径无效时抛 `FileNotFoundError`，但 `resolve_binary` 已经在调用 `run_command` 之前验证了路径，所以不应走到这里。如果走到，错误信息会不够精确。是否需要更细分为 `ffprobe_not_found` / `ffmpeg_not_found`？

2. **`invalid_media` 合并了多个原错误**：原代码有 `media_processing_error` 和 `audio_extract_error`，任务要求里只列了 `invalid_media`，所以我把两者合并了。是否过于宽泛？

3. **`timestamp` 用 `int(time.time())` 而非 `time.time()`**：ACRCloud Python 示例用 `str(time.time())`（浮点字符串），Java 示例用毫秒级整数。我用秒级整数，测试通过。是否需要在某些环境下兼容浮点 timestamp？

4. **`resolve_binary` 的 fallback 路径硬编码 `/opt/homebrew/bin` 和 `/usr/local/bin`**：macOS 上常见，但 Linux 上可能不存在。是否需要更通用的 PATH 搜索？目前 `shutil.which` 已经会搜索 PATH，fallback 只是额外保险。

5. **`MusicRecognitionProvider` 接口保留但只实现一个 Provider**：任务提示词提到「实现可配置 Provider 并默认使用 ACRCloud」，我保留了接口但没实现 `ShazamProvider`（因为 ShazamIO 不可用）。是否需要保留 `ShazamProvider` 的占位实现？我倾向于不保留，避免引入不可用的依赖。

6. **测试里的 `test_ffmpeg_not_found_when_extraction_needed`**：mock 了 `resolve_binary`，但让 `ffprobe` 正常解析，只让 `ffmpeg` 失败。这个测试依赖本机有 `ffprobe`。是否应该在 `ffprobe` 也不可用时 skip？

7. **未推送**：本地领先 `origin/main` 5 个提交，我没有 push。Review 通过后是否需要我 push？

---

以上。如需我针对 Review 反馈做修改，请把 Review 结论贴回来，我会按点修复并补一个修复提交。
