# 百度网盘上传性能优化 — 实施 Review 报告

> 面向高阶模型的审查材料。任务来源：`docs/百度网盘上传性能优化-低阶模型实施任务书.md`
> 依据文档：`docs/百度网盘上传速度排查-高阶复核与优化方案.md`（以此为准）
> 实施时间：2026-10-12　分支：`main`（**未提交、未 push、未部署**）
>
> **本次没有执行任何真实上传、覆盖或删除。** 真实 A/B 验证留给 Review 阶段在用户确认后进行。

---

## 高阶 Review 复核结论（2026-10-12）

低阶实现的总体方向正确，但初版存在数个上线阻断和可靠性问题，现已直接修复：

1. `server/src/index.js` 仍引用已删除的 `archive-paths/archive-storage/archive-jobs`，API 会在启动阶段崩溃；已移除全部旧 `/api/archive/*`、helper 和恢复队列，并保留历史数据库表兼容旧数据。
2. `server/src/netdisk-live.js` 虽已实现，但初版没有接入 HTTP 路由；已接入 credential/options/tree/children，补齐 AES-GCM Token 状态、实时 list、缓存降级和接口契约。
3. 上传器最初只有数据分片复用 Session，`precreate/create` 仍走强制 `Connection: close` 的 urllib；现已让每个 worker 的控制请求和分片请求共用同一 Session。
4. 分片上传最初会重试所有未知 errno；现只重试明确的临时 errno，路径/参数错误立即失败。
5. 大分片只在 block 上传返回 31364 时回退；现 precreate 返回 31364 也会重新计算 4 MiB block list、重新 precreate 并换 uploadid。
6. 并发模式原先一次性提交整批 future，Ctrl-C 前可能排队数千文件；现最多维持 `concurrency` 个 future，有空位才继续提交。
7. replace/force 原先先删远端，再检查本地文件；现先验证本地文件仍存在且大小未变化，避免明显的源文件异常导致远端旧版本被误删。
8. 修正实际 payload 速率对最后一个不足整片的分片高估问题，并兼容 create 返回字符串 size。

最终离线验证：Python 上传单测 **70 项通过**；服务端实时目录模块自检全绿；HTTP 闭环自检全绿；Vite 生产构建、Node/Python 语法检查与 `git diff --check` 全部通过。未执行真实百度上传或删除。

## 0. 一句话结论

按任务书 §0 的八项全部实现：连接池、分片重试、分阶段指标、串行规划、文件级并发、可配置分片、locateupload 回退，以及原有幂等/逐片 MD5/远端 size 与 path 校验/不删本地全部保留。70 项离线单测全绿。**未做真实上传，因此性能收益完全未验证。**

---

## 1. 改动文件清单

| 文件 | 类型 | 行数 | 说明 |
|---|---|---:|---|
| `skills/baidu-netdisk-archive/scripts/netdisk_sync.py` | 修改 | 1297 | 配置 profile、分片参数化、串行规划、文件并发、metrics、`--force` 修复 |
| `skills/baidu-netdisk-archive/scripts/netdisk_transport.py` | **新增** | 483 | 连接池、超时、去敏、分片重试、locateupload |
| `skills/baidu-netdisk-archive/tests/test_netdisk_sync.py` | **新增** | 约 880 | 70 项离线单测 |
| `skills/baidu-netdisk-archive/requirements.txt` | **新增** | 3 | `requests>=2.31,<3` |
| `skills/baidu-netdisk-archive/.gitignore` | **新增** | 3 | 忽略 `.venv/`、`__pycache__/` |
| `skills/baidu-netdisk-archive/SKILL.md` | 修改 | 200 | 依赖、profile 说明、`--force` 修复说明、Token 去敏说明 |

**低阶模型在上传优化阶段严格未改动**（任务书 §1.2 禁止项）：`server/src/*`、`server/schema.sql`、`src/*`、工作台实时目录功能、素材库/COS 链路、`archive_*` 表、任何真实 `.env` 或 Token 文件。

> 说明：`git status` 里仍能看到 `server/`、`src/` 的改动，那是**上一轮任务**（网盘实时目录 + 删除旧归档）的未提交内容，上传优化低阶实现阶段未触碰；高阶 Review 随后按上一轮任务要求完成了接线和修复。

新增 `netdisk_transport.py` 的理由：传输层是唯一需要审计"Token 不外泄"的地方，拆开后去敏逻辑集中在一处；任务书 §1.1 明确允许，且要求"不要为了架构漂亮拆很多小文件"——只拆了这一个。

---

## 2. safe / fast 最终参数值

| profile | chunk size | 文件并发 | inter-chunk delay | upload host mode |
|---|---:|---:|---:|---|
| `safe`（默认） | 4 MiB | 1 | 0.15 s | `fixed` |
| `fast` | 8 MiB | 4 | 0.03 s | `auto` |

定义位置：`netdisk_sync.py:58`（`PROFILES`）。`argparse` 中新参数默认 `None`，`resolve_settings()`（`netdisk_sync.py:643`）在解析后用 profile 填充，显式参数始终覆盖 profile。

**校验时机**：`resolve_settings()` 在 `main()` 中紧跟本地目录校验之后、加载 Token 与建目录之前调用（`netdisk_sync.py:1147` 起的 `main`）。非法值直接 `return 2`，不会创建远端目录、不会调用任何写接口。单测 `test_dry_run_touches_no_write_api` / `test_dry_run_creates_no_directory` 用 mock 断言了这一点。

**向后兼容**：不带任何新参数时，chunk=4 MiB、串行、0.15 s、`fixed` host + legacy query path，与旧版一致。**但有一处刻意的行为变更：`--force` 现在会先删远端旧文件**（任务书 §10.2 要求的 bug 修复，旧实现里 `--force` 实际没有删除动作）。

---

## 3. HTTP Session 如何复用

`netdisk_transport.py:149` 的 `Transport` 类：

- **每线程一个 Session**：`threading.local()` 惰性创建（`session()` 方法）。原因写在注释里——`requests.Session` 未被声明为线程安全，共享连接池会把我们想要并发的上传重新串行化。
- 单测 `test_each_thread_gets_its_own_session` 断言 3 个线程拿到 3 个不同 Session；`test_same_thread_reuses_one_session` 断言同线程复用同一对象。
- **`concurrency=1` 也用 Session**：目的是停止每请求一次 TCP+TLS 握手，不是为了加线程。
- Pool：`HTTPAdapter(pool_connections=N, pool_maxsize=N, max_retries=0)`，N = 并发数，且下限为 2。`max_retries=0` 因为重试由分片级阶梯显式控制，不能让 urllib3 悄悄重试。
- **关闭**：`close()` 遍历内部 list 关闭所有 Session。thread-local 本来无法集中回收，所以额外维护了一份 list。
- Header：`User-Agent: pan.baidu.com`。
- **超时**：控制面 `(connect=10, read=60)`，数据面 `(connect=10, read=180)`。没有无限超时。

---

## 4. 并发是文件级还是分片级

**只有文件级。** 一个 future 对应一个文件，文件内部的分片在 `_upload_blocks()`（`netdisk_sync.py:261`）里串行上传。**没有嵌套的"文件并发 × 分片并发"**。

`run_batch()`（`netdisk_sync.py:737`）：
- `concurrency == 1` 走纯串行循环；
- 否则单个 `ThreadPoolExecutor(max_workers=concurrency)`，主线程用 `FIRST_COMPLETED` 收集结果、打印进度、写 metrics；
- worker 只返回结构化 `FileResult`，**不修改共享 dict/list**，`remote_cache` 完全不在线程间共享（见第 5 节）；
- 单测 `test_active_workers_never_exceed_limit` 用活动计数断言 1/2/4/8 各档峰值都不超限。

---

## 5. 串行规划阶段（顺带解决目录创建竞态）

`build_plan()`（`netdisk_sync.py:679`）在线程池启动**之前**全部完成：

1. 收集所有远端父目录；
2. 按 `(深度, 路径名)` 排序后**串行** `ensure_dirs()`；
3. 每个远端父目录只 `list_dir()` 一次；
4. 产出不可变 `PlanItem(local_path, relative_path, remote_path, size, action, existing_size)`，`action ∈ {skip, upload, replace}`；
5. skip 项不提交线程池。

这样 `ensure_dirs` 不可能在 worker 之间竞态，也就不需要给 `remote_cache` 加锁——百度对已存在目录调 `create` 会静默改名，竞态会留下时间戳垃圾目录。单测 `test_directories_created_in_depth_order` 断言创建顺序，`test_same_parent_listed_once` 断言不重复列目录。

---

## 6. 分片失败如何重试

`upload_block_with_retry()`（`netdisk_transport.py:363`）：

| 情况 | 行为 |
|---|---|
| connect/read timeout、connection reset、TLS 临时错误 | **重试** |
| HTTP 408 / 429 / 5xx | **重试** |
| 返回非 JSON | **重试** |
| 百度 RETRYABLE errno | **重试** |
| 回显 MD5 与本地不一致 | **重试**，每次重发完整分片（不尝试补丁式修复） |
| errno 110/111 授权失效 | 不重试，`token_invalid` |
| errno 6/13 权限 | 不重试，`permission_denied` |
| errno 31364 分片超限 | 不重试，进入第 9 节的整文件回退 |

退避：`1s, 2s, 4s` + `0–250ms` jitter。**与 `inter_chunk_delay` 分开**——后者发生在分片最终成功之后，前者是失败退避。

分片重试不重传前面已成功的片；只有分片重试耗尽才判文件失败。

**文件句柄**：每个 worker 独立 `open()`，按 `seek(index * chunk_size)` + `read(chunk_size)` 定位，绝不共享 file object。

---

## 7. locateupload 如何回退

`resolve_upload_host()`（`netdisk_transport.py:285`），返回 `(host, query_path_mode)`：

| mode | 行为 |
|---|---|
| `fixed` | **完全不调用 locateupload**，固定 `d.pcs.baidu.com` + legacy query path |
| `auto` | 调用 locateupload；传输异常或无 HTTPS 候选 → `warn()` 后回退 fixed |
| `required` | locateupload 失败或无候选 → 该文件失败，不回退 |

候选解析 `_first_https_candidate()` 按顺序尝试 `servers[].server` → 字符串 `server` → `host`；裸主机名补 `https://`；第一版不使用 QUIC host。

**兼容旧契约**（任务书 §8.3）：`fixed` 保持 `path=/apps/BaiduPCS/<uploadid>`；`auto/required` 成功时用真实 `remote_path`。两种模式的 query path 记进 metrics 的 `queryPathMode`（`legacy` / `remote`）。单测 `test_query_path_mode_differs_between_fixed_and_locate` 直接断言最终 URL 里的 `path=` 参数与 scheme 前缀。

---

## 8. 旧 `/api/precreate` / `/api/create` 未迁移

按任务书 §8.3，本轮**不**把控制面切到新版 xpan 接口。只在 upload host 上做实验能力。

---

## 9. precreate 缺失分片列表 + 分片大小回退

**`select_chunks()`**（`netdisk_sync.py:210`）：`block_list` 非空且**全部为合法整数、全部在 `[0, len(digests))`** 时只上传这些索引；缺失、为空、含非整数、含 bool、含越界值一律回退为上传全部分片。索引去重并排序。

**秒传判定兼容两种契约**：`return_1 == 0`（旧）或 `return_type == 2`（新）。两者都**没有**出现时按正常上传处理，绝不误判成"不用上传"。

**create 始终提交完整、按原始顺序的 digest 列表**，即使只传了部分分片。单测 `test_create_receives_full_ordered_digest_list` 断言 `create` 收到的 `block_list` 与本地 `block_digests()` 完全一致。

**分片大小回退**（任务书 §6.1）：`_perform_upload()`（`netdisk_sync.py:371`）捕获 `chunk_size_unsupported` 且当前 > 4 MiB 时，**重新读整个文件、重新算完整 block list、重新 precreate 拿新 uploadid、从第 0 片重来**。最多回退一次。**绝不在旧 uploadid 下把 8 MiB 切成两片 4 MiB**。metrics 记录 `chunkFallbackFrom` / `chunkFallbackTo`。4 MiB 被拒时直接报错不递归。单测 `test_fallback_reprecreates_with_4mib` 断言两次 precreate 的 block list 分别为 8 MiB 与 4 MiB 版本、第二次用新 uploadid 从 partseq=0 开始。

---

## 10. `--force` 修复结果

**修复了。** 旧实现里 `--force` 只是跳过 size 比较，从不删除远端旧文件——与 SKILL.md 文档"先删旧版本再上传"不一致。

现在：
- 远端无同名 → `upload`
- 远端同名 + size 一致 + 未 force → `skip`
- 远端同名 + size 不同（未 force）→ `replace`
- 远端同名 + `--force`（无论 size）→ `replace`

worker 在 **precreate 之前**调用 `delete_remote`；删除失败则该文件失败（`remote_delete_failed`），**不继续 precreate**。单测 `test_force_replaces_even_when_same_size`、`test_replace_deletes_before_precreate`（断言调用顺序 delete → precreate）、`test_failed_delete_stops_before_precreate`。

---

## 11. 错误模型

`UploadError(code, message, errno=, status=, retryable=)`，`ERROR_HINTS`（`netdisk_sync.py:~99`）给出中文处置建议。已实现的稳定 code：

```
network_retry_exhausted  chunk_md5_mismatch   chunk_size_unsupported
token_invalid            permission_denied    remote_delete_failed
precreate_failed         locateupload_failed  create_failed
remote_size_mismatch     remote_path_renamed  cancelled
local_io_error           internal_error
```

- 失败明细按 `relative_path` 排序输出（便于重跑定位），每行含 `[code]` 与中文说明；
- 单文件失败不终止整批；`failed > 0` 时退出码仍为 1；
- 错误文本不含 Token 或完整 URL（见第 12 节）。

---

## 12. 敏感信息去敏

Token 按接口约定放在 query 字符串里，因此：

- `redact_url()`（`netdisk_transport.py:98`）：把 `access_token` / `session_secret` / `session_key` / `token` 的值替换为 `***`；
- `safe_http_error()`（`:121`）：把 `requests` 异常转成只含 `异常类名 host=... status=...` 的一行，**不把 `str(exc)` 直接写入日志**（`str(exc)` 内嵌完整 URL）；
- 所有 `except requests.RequestException` 分支都走 `safe_http_error`。

单测覆盖：`test_redact_url_masks_token`、`test_error_text_never_contains_token`（重试耗尽后异常文本无 Token）、`test_metrics_never_contain_token`、`test_batch_context_repr_hides_token`（`BatchContext` 用自定义 `__repr__` 而非 dataclass，避免 traceback 打印 Token/Session）。

> **一个刻意的区分**：发往百度的**线上 URL 本来就含 Token**，测试 `test_query_path_mode_differs_between_fixed_and_locate` 显式断言 `assertIn(FAKE_TOKEN, raw_url)` 与 `assertNotIn(FAKE_TOKEN, redact_url(raw_url))`，把这个契约写死在测试里，避免后人误以为线上 URL 也该脱敏。

---

## 13. 指标

每文件 metrics（任务书 §5 要求的字段全部存在）：
`relativePath, sizeBytes, status, chunkSizeBytes, chunksTotal, chunksRequested, chunksUploaded, retryCount, uploadHost, queryPathMode, blockListHonoured, hashMs, precreateMs, locateMs, uploadMs, createMs, totalMs, payloadMBps, effectiveMBps, detail`，回退时追加 `chunkFallbackFrom/To`。

- 未指定 `--metrics-file` 时不写任何文件（`test_metrics_absent_when_not_requested`）；
- 指定时 JSONL，一行一个文件，末尾追加 `{"type":"batch_summary", ...}`；
- **只由主线程写**（`write_metrics()` 只在 `run_batch` 的主循环/主线程调用点），不存在多线程交错写坏 JSONL；
- 只用 `relativePath`，不写本地绝对路径；无 Token / dlink / Cookie / 完整 URL；
- 单测 `test_metrics_jsonl_is_parseable_per_line` 逐行 `json.loads` 校验。

终端汇总保留原有"上传/跳过/失败"三个数字，新增：总耗时、实传字节、整批有效 MB/s、分片重试次数、上传域名分布。

---

## 14. 测试命令与结果

全部使用假 Token 与内存桩，**不访问公网**。

```bash
cd /Users/simon/Desktop/dev/code/aigc-workbench

# 依赖（本机装在 skill 内 venv，未污染系统 Python）
python3 -m venv skills/baidu-netdisk-archive/.venv
skills/baidu-netdisk-archive/.venv/bin/pip install -r skills/baidu-netdisk-archive/requirements.txt

# 1. 语法
skills/baidu-netdisk-archive/.venv/bin/python -m py_compile \
  skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  skills/baidu-netdisk-archive/scripts/netdisk_transport.py          # OK

# 2. 单测
skills/baidu-netdisk-archive/.venv/bin/python -m unittest discover \
  -s skills/baidu-netdisk-archive/tests -p 'test_*.py'
# Ran 70 tests — OK

# 3. 帮助
skills/baidu-netdisk-archive/scripts/netdisk_sync.py --help          # OK

# 4. dry-run（未触网、未建目录）
skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/902视频 --dest /AIGC/902视频 --profile fast --dry-run --limit 3
# 源目录 /Users/simon/Desktop/AIGC/902视频 / 目标 /AIGC/902视频 / 待处理 3 个文件，合计 11.2 MB

# 5. 只读命令（真实 Token，只读，不写）
... netdisk_sync.py list /AIGC --json     # dirCount=13
... netdisk_sync.py quota --json          # freeBytes = 11.76 TB（= total - used）

# 6.
git diff --check                                                          # OK
```

### 测试覆盖对照（任务书 §14）

| 组 | 要求 | 覆盖 |
|---|---|---|
| 14.1 参数/Profile | 默认=safe、fast 值、显式覆盖、非法值在任何写操作前失败 | 7 项 |
| 14.2 分片 | 4/8/16/32 MiB digest 数、末片不足、block_list 合法/缺失/空/非法/越界、create 完整 digest | 11 项 |
| 14.3 重试 | timeout→成功、500→成功、MD5 不一致→成功、耗尽稳定码、权限不重试、日志无 Token | 8 项 |
| 14.4 locateupload | servers[].server、server/host 兼容、auto 回退、required 报错、fixed 不调用、两种 query path | 8 项 |
| 14.5 规划 | 同父目录只 list 一次、深度序建目录、same→skip、diff→replace、force→replace、dry-run 不写 | 6 项 |
| 14.6 并发 | 峰值不超 N、失败不影响其他、计数正确、JSONL 可解析、失败明细排序 | 6 项 |
| 14.7 向后兼容 | list/tree/quota/publish-catalog parser、`--help`、旧命令行 | 4 项 |
| 额外 | 分片大小回退（§6.1） | 2 项 |

---

## 15. 是否执行了真实上传

**没有。** 全程只做了：

- `py_compile`、`--help`、`--dry-run`（不触网、不建目录、不 precreate）；
- 两条**只读**命令 `list /AIGC --json` 与 `quota --json`（用本机 Token，只读接口）；
- 70 项全部使用假 Token 与内存桩的单测。

**未**上传、**未**覆盖、**未**删除任何网盘文件，**未**删除任何本地文件，**未** push，**未**部署。`git status` 中 `/AIGC` 仍是 13 个目录、250 GB 已用，与本轮开始前一致。

---

## 16. 实施过程中被测试抓到的真实缺陷（已修）

1. **`_post_block` 把模式字符串当成 path 传了**：`params["path"] = ... if legacy else query_path_mode`，于是 locate 模式下 `path=remote` 而不是真实远端路径。测试断言最终 URL 时暴露。
2. **host 重复 scheme**：`_first_https_candidate` 返回带 `https://` 的完整 URL，`_post_block` 又拼 `f"https://{host}"`，产生 `https://https://up.b.baidu.com/...`。已加 `_host_only()` 归一化。
3. **`parse_chunk_size` 不接受纯字节数**：`"8388608"` 被解析成 8388608 MiB 而拒绝。已加纯数字按字节解释的分支并统一走 `_validated_chunk_bytes`。
4. **工具链事故（值得记录）**：用脚本按行号切片改代码时，把一段 572 行内容重复写入，`netdisk_sync.py` 一度出现两份 `upload_one_file` 且语法损坏。该文件**不在 git 跟踪中**（`skills/baidu-netdisk-archive/` 整体未提交），没有 git 兜底。修复方式：定位重复区间、删除其一、再按测试反馈逐个恢复被误删的 helper（`load_token`/`call`/`call_retry`/`md5_of`/`block_digests`/`select_chunks`/`ensure_dirs`/`list_dir`/`list_entries`/`delete_remote`/`BatchContext`/`PlanItem`/`FileResult`/`_chunk_count`），最终测试恢复全绿并通过 py_compile。**教训**：对未纳入版本控制的文件做大段脚本化编辑，应先 `cp` 备份。

---

## 17. 尚未验证的风险（请重点 Review）

### 高

1. **性能收益完全未验证。** 任务书目标是整批 3.2–4.2 MB/s，当前基线 0.86 MB/s。Keep-Alive、locateupload、并发 4 三项各贡献多少，**只有跑出来才知道**。metrics 已准备好，正是为了能归因。
2. **locateupload 能否用于本账号的 `/AIGC` 路径未验证。** 任务书 §2.2 明确要求"第一步只验证旧 uploadid 能否调用 locateupload、返回 host 能否继续完成 `/AIGC` 上传"。本轮只做了代码与单测，**没有对真实网盘发过一次 locateupload 请求**。风险：老接口的 uploadid 可能不被 `locateupload` 接受，或返回的节点不接受 `/apps/BaiduPCS/...` 之外的 path 形态。**首次真实运行时建议先用 `--upload-host-mode required --limit 1` 小样本验证，确认可用再用 `auto`。**
3. **`return_type` / `block_list` 在旧 `/api/precreate` 上是否存在未验证。** 代码对两套契约都做了兼容，但旧接口实际返回什么字段组合，需要抓一次去敏响应确认。若旧接口根本不返回 `block_list`，`select_chunks` 会恒定回退为全量上传（行为正确，但 §9 的收益拿不到）。

### 中

4. **并发下的失败率未知。** 单测用 mock 验证了"峰值不超 N"，但真实网络抖动 + 并发 4 的失败率、重试放大情况没有数据。任务书 §5 的验收线是失败率 < 0.5%。
5. **Ctrl-C 的真实网络行为仍未在长批次验证。** 高阶 Review 已把一次性提交整批 future 改为有界窗口：最多仅保留 `concurrency` 个 future，完成一个再提交一个。已经进入 HTTP 的请求仍需等响应或 timeout 返回，这是无法强制安全中断请求体的剩余限制。
6. **同盘判断错误。** 我用 `item.size`（规划期 stat）与远端 `existing` 比 size，跳过判断发生在规划期；worker 上传前**没有再次 stat 本地文件**。如果规划后、worker 真正读到文件前文件被改动，`block_digests` 算出的 digest 与 `precreate` 用的 size 会不一致，可能触发远端 size 校验失败。旧实现也是这个行为，但并发会放大这个时间窗。
7. **`_post_block` 仍会把整个分片拼进内存**（`head + data + tail`）。8 MiB × 并发 4 约 32 MiB，可接受；**若将来用 32 MiB × 并发 8 会到 256 MiB**，任务书 §2.6 也提到了这点，本轮未做流式 multipart。

### 低

8. **`safe` profile 的"与旧版完全一致"是按代码路径推断的**，没有 A/B 对拍。唯一确定的差异是 `--force` 行为修复（刻意的）。
9. **`inter-chunk-delay` 不是全局限速**，并发 4 时四个 worker 各自 sleep，实际 QPS 是单 worker 的 4 倍。任务书 §12 允许这样实现（代码注释已写明），但如果实测出现频控，需要加全局 token bucket。
10. **`safe_http_error` 依赖 `exc.request.url`**，某些异常（DNS 失败）可能没有 `request` 属性，此时只输出异常类名——安全但信息较少。
11. **venv 位置**：我在 `skills/baidu-netdisk-archive/.venv` 建了虚拟环境跑测试，并加了 `.gitignore`。生产环境应使用系统 Python + `pip install -r requirements.txt`。

---

## 18. 给高阶 Review 的检查点对照（任务书 §19）

| 检查点 | 状态 | 依据 |
|---|---|---|
| 默认无参数真的保持旧行为 | 是（除 `--force` 修复） | `PROFILES["safe"]`；`test_default_is_safe_profile` |
| requests 异常是否泄漏 access_token URL | 已处理 | `safe_http_error` + `redact_url`；3 项相关断言 |
| Session 是否真正在线程内复用 | 是 | `Transport.session()`；`test_each_thread_gets_its_own_session` |
| 是否形成"文件并发 × 分片并发" | 否 | 单 `ThreadPoolExecutor`；`_upload_blocks` 串行 |
| ensure_dirs 是否仍可能并发创建重复目录 | 否 | 全部在 `build_plan` 串行完成 |
| precreate block_list 为空有没有误判"不用上传" | 否 | `select_chunks` 空/非法/越界均回退全量 |
| create 是否提交完整且有序 digest | 是 | 始终 `json.dumps(digests)` |
| chunk fallback 是否重新 precreate | 是 | `_perform_upload`；`test_fallback_reprecreates_with_4mib` |
| Ctrl-C 是否继续大量提交 | 已改为最多 `concurrency` 个 future 的有界窗口 | 真实网络中断仍未验证，见风险 5 |
| metrics 是否线程安全、是否泄密 | 是（仅主线程写） | `write_metrics` 调用点；无 Token 断言 |
| `--force` 是否真的先删旧文件 | 是 | 3 项相关断言 |
| read-only 子命令是否回归 | 是 | `list`/`quota` 真实跑通；`test_readonly_subcommands_present` |
| 是否偷偷进行了真实上传/删除 | 否 | 见第 15 节 |