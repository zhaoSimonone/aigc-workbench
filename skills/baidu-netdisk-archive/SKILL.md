---
name: baidu-netdisk-archive
description: Upload a local AIGC video/image directory to Baidu Netdisk (百度网盘) over the open-platform HTTP API, preserving folder structure and verifying every block; also list the netdisk directory tree as JSON and optionally warm the AIGC 工作台 cache. Use when the user asks to archive, back up, 归档, or 上传到百度网盘 local creative assets, or asks which files already exist in the netdisk. The 工作台 web page reads /AIGC live on its own and does not need this script.
---

# 百度网盘归档

把本地 AIGC 素材目录同步到百度网盘。使用官方 OAuth Access Token 走开放平台 HTTP 接口，**不需要安装百度网盘桌面客户端**。保留完整目录结构，每个分片都用服务端回显的 MD5 逐片校验。

## 默认行为

- 源目录：`/Users/simon/Desktop/AIGC`
- 网盘目标：`/AIGC`
- 保留子目录结构与原文件名，自动跳过 `.DS_Store` 和隐藏文件
- 远端已存在且大小一致的文件直接跳过；大小不一致时先删除旧版本再上传
- 上传是幂等的：重复执行不会产生 `xxx_20261012_142614.mp4` 这类时间戳副本
- **本 Skill 只上传，绝不删除本地文件**

## 工作流程

1. 确认范围。用户指定目录时按指定目录同步；未指定时用默认的 `/Users/simon/Desktop/AIGC`。先跑 `--dry-run` 把待传文件清单和体积报给用户确认，尤其是首次同步大目录时。
2. 确认网盘目标路径。默认 `/AIGC`，与本地目录同名的一级子目录会自动落到 `/AIGC/<目录名>`。
3. 执行 `scripts/netdisk_sync.py`，等脚本输出完整的成功/跳过/失败汇总后再汇报。
4. 汇报时必须包含三个数字：上传数、跳过数、失败数。失败项逐个列出文件名和错误原因，不要把失败说成已上传。
5. 用户要求"上传后删本地"时，**不要**由脚本自动删除。先用 `--dry-run` 核对清单，再按"安全与边界"里的流程让用户确认后手动处理。

## 调用

```bash
# 先看清单，不实际上传
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/902视频 --dest /AIGC/902视频 --dry-run

# 同步整个 AIGC 目录到 /AIGC
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py

# 同步某个子目录（推荐先 --dry-run）
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC/1010换装 --dest /AIGC/1010换装

# 只传匹配的文件/目录名
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  --include "*.mp4" --include "成片/*"

# 远端已存在也强制重传（会先删远端旧版本）
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py --force
```

常用参数：`--limit N` 试跑前 N 个文件，`--exclude "*.tmp"` 排除模式，`--dest` 换网盘目标目录。

## 上传性能

### 两档 profile

| profile | 分片 | 文件并发 | 分片间隔 | 上传域名 |
|---|---:|---:|---:|---|
| `safe`（默认） | 4 MiB | 1 | 0.15 s | 固定 `d.pcs.baidu.com` |
| `fast` | 8 MiB | 4 | 0.03 s | 先问百度推荐域名，失败回退固定 |

**不带任何新参数时行为与旧版一致**（`safe` 档）。

```bash
# 大批量备份（当前账号是 SVIP6）
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC --dest /AIGC --profile fast \
  --metrics-file outputs/netdisk-upload-metrics.jsonl
```

`fast` 做了什么：

- **8 MiB 分片**：减少分片请求数。普通用户只支持 4 MiB，会员最高 32 MiB，
  4/8/16/32 都可显式指定。分片变大不改变校验强度——每个字节仍被所属分片的
  MD5 覆盖，只是分片失败时最多重传 8 MiB 而不是 4 MiB。
- **文件并发 4**：同一账号约 75% 的文件不足 4 MiB，本来就只有一个分片，
  真正的浪费是每个文件的 precreate/create 两次往返。并发让这些往返互相重叠。
  并发是**文件级**的，一个文件内部的分片仍然串行；不做嵌套并发。
- **自动定位上传域名**：拿到 `uploadid` 后调用 `locateupload` 问百度推荐节点，
  失败自动回退 `d.pcs.baidu.com`。

### 建议

- 并发越高失败率越高，**默认不超过 4**。当前实测网络通用上行约 5.05 MB/s，
  百度聚合峰值约 4.05 MB/s，再往上堆并发没有收益。
- `--upload-host-mode required` 可在推荐域名拿不到时直接失败而不是回退，
  用于排查节点问题。
- `--metrics-file` 会为每个文件写一行 JSONL（主线程写入），包含分阶段耗时、
  分片数、重试次数、实际上传域名和两个速率。**里面没有 Token、没有 dlink、
  没有完整请求 URL**，可以安全分享。

```bash
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py \
  /Users/simon/Desktop/AIGC --dest /AIGC --profile fast --dry-run --limit 3
```

## 只读工具（不触碰本地文件，不上传）

这些子命令只读网盘，stdout 输出 JSON，可被 MCP 直接当工具调用。上传仍然只发生在本机与百度网盘之间，**视频字节不会经过 AIGC 工作台服务器**。

| 工具名 | 命令 | 作用 |
| --- | --- | --- |
| `netdisk_list` | `netdisk_sync.py list <网盘目录> --json` | 列出一层目录 |
| `netdisk_tree` | `netdisk_sync.py tree --root /AIGC --json` | 递归列出目录树 |
| `netdisk_quota` | `netdisk_sync.py quota --json` | 查询网盘容量 |
| `netdisk_publish_catalog` | `netdisk_sync.py publish-catalog --root /AIGC` | 把目录快照交给工作台 |

```bash
# 看网盘上已经有什么
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py list /AIGC --json
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py tree --root /AIGC/1007视频 --json

# 写出目录快照，再由工作台本机接口读取
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog --root /AIGC

# 直接发布到本机工作台（需要工作台登录 Cookie）
python3 skills/baidu-netdisk-archive/scripts/netdisk_sync.py publish-catalog \
  --root /AIGC --api http://127.0.0.1:18080/api/netdisk/catalog --cookie-env AIGC_SESSION_COOKIE
```

节点 JSON 字段（`list` 与 `tree` 一致）：

```json
{
  "path": "/AIGC/1007视频/成片",
  "name": "成片",
  "parentPath": "/AIGC/1007视频",
  "isDir": true,
  "sizeBytes": 0,
  "serverMtime": 1791787162,
  "fsId": 497116331455040,
  "category": 6,
  "kind": "directory"
}
```

`kind` 取 `directory/image/video/audio/document/archive/other`，只按扩展名分类，不代表文件在 COS。`tree` 可以用 `--depth` / `--max-nodes` 截断（输出里 `truncatedByDepth`、`truncatedByNodeLimit` 会标记），但同一层永远翻页拉完。

### 输出里不会出现的东西

`list` / `tree` / `publish-catalog` 的输出经过统一清洗，**不含** `access_token`、`dlink`、`cookie`、`session_secret`。工作台的 catalog 接口同样会拒绝带这些字段的请求体。

### 目录白名单

只允许列出 `/AIGC`（可用 `NETDISK_LIST_ROOT` 覆盖）。传 `/`、`/Pictures` 或任何 `/AIGC` 之外的路径会直接报错，避免把网盘其它内容刷进工作台。

### 与工作台的分工

- **浏览目录不需要跑本脚本。** 工作台“工作文档”页会用自己的方式实时 list `/AIGC`：在网页顶部「百度网盘授权」里粘贴 access_token（或授权回调整段链接）即可，之后点文件夹就是最新的。
- 工作台那枚 Token 加密存在 `netdisk_credentials`，**只用于 `GET /api/list`**；服务端不做上传、下载、删除，也不代理 dlink。授权页与本 Skill 用的是同一个 client_id，所以是同一枚 Token。
- 本 Skill 的 Token 仍然只放本机（环境变量或 token 文件）。工作台那枚是独立保存的另一份，过期后两边各自重新授权。
- `publish-catalog` 现在只是**可选的离线暖缓存**：正常浏览用不到；只有在网盘暂时不可用、希望先让页面有内容可看时才需要跑。
- 需要把网盘文件下载回本机时，本 Skill 目前**还没有下载命令**，请在百度网盘网页端下载；不要让工作台服务器代为下载。

### 安全边界（不变）

- 本脚本仍然**绝不删除本地文件**。
- `list` / `tree` / `publish-catalog` 的输出不含 `access_token`、`dlink`、`cookie`、`session_secret`。
- 只允许列出 `/AIGC`（可用 `NETDISK_LIST_ROOT` 覆盖）。

## 依赖

上传的数据分片、precreate/create 和动态节点定位使用 `requests` 的线程内连接池（标准库 `urllib` 每次请求都会强制 `Connection: close`，无法 Keep-Alive）。只读子命令暂时保留原有 `urllib` 路径，以保持输出契约。

```bash
python3 -m pip install -r skills/baidu-netdisk-archive/requirements.txt
```

## 凭据

Token 按以下顺序读取，都不是则报错退出：

1. `--token` 参数
2. 环境变量 `BAIDU_NETDISK_ACCESS_TOKEN`
3. `~/.config/aigc-workbench/baidu_netdisk_token`（权限 600，不在仓库内）

获取方式：打开
`https://openapi.baidu.com/oauth/2.0/authorize?response_type=token&client_id=QHOuRXiepJBMjtk0esLhrPoNlQyYd0mF&redirect_uri=oob&scope=basic,netdisk&qrcode=1`
登录授权后，从地址栏 `#access_token=` 到 `&session_secret` 之间取。有效期 30 天，过期报 errno 111 时重新走一次授权。

**绝不在日志、命令行参数、回复、提交或截图里输出完整 Token。** 脚本和本文件都不存放 Token。

## 已知接口行为

这些是实测结论，不要凭直觉改写：

- **文件级 MD5 不可用于校验。** `/api/list` 和 `xpan/multimedia` 返回的 md5 是百度打码后的值（如 `fba93620dmd8804115a627c69115fefc`），和本地真实 md5 不一致。校验只能依赖分片回显 + 落盘 size 比对，脚本已按此实现。
- **`ondup=overwrite` 无效。** 在 `precreate` 里传该参数，`create` 阶段仍会自动改名生成时间戳副本。所以幂等必须靠上传前查询目标目录，脚本已实现该逻辑。
- **建目录同样会静默改名。** 对已存在的目录调 `/api/create?isdir=1`，百度不会报错，而是新建一个 `xxx_20261012_142834` 的空目录并返回它的路径。盲目建目录会让每传一个文件就多一个垃圾目录。脚本因此先查父目录列表，已存在就跳过；必须新建时按服务端返回的实际路径继续往下走。**不要把这段改回无条件 create。**
- **`xpan/filemanager` 删除接口对本 Token 返回 400 `Unsupported open api`。** 脚本改用旧的 `/api/filemanager?opera=delete`。注意这个 Token 有写和删权限，但没有 xpan 管理接口权限。
- **秒传**（`precreate` 返回 `return_1 == 0`，或新版 `return_type == 2`）表示内容已在网盘，脚本会跳过分片上传直接 `create`。
- **`--force` 必须先删远端旧版本再上传。** 旧实现只在 size 不同时才删，`--force` 实际没有删除动作；现在 `--force` 无论大小是否一致都标记为 replace，worker 在 precreate 之前删除，删除失败则该文件失败、不继续上传。

## 安全与边界

- 上传是外部状态变更，只在用户明确要求时执行。远端目标目录写错不会影响本地文件，但会在网盘留下垃圾目录，事后需手工清理。
- **Token 只出现在发往百度的请求里。** 百度接口把 `access_token` 放在 query，
  这是接口约定；但日志、metrics、异常信息里一律经过脱敏，只保留 host、API 名、
  HTTP 状态码、errno 和重试次数。
- **脚本不含任何删除本地文件的逻辑。** 真要腾本地空间时，按顺序操作：先 `--dry-run` 核对清单 → 执行同步 → 重跑一次确认全部"跳过" → 用 `--include` 精确圈定要删的目录再人工删除。不要用通配符批量删本地视频。
- Token 代表整个网盘的权限，只写入 `~/.config/aigc-workbench/`，不复制进仓库。
- 大目录首次同步前建议 `--limit 5` 试跑，确认网盘侧结构和大小无误再放开跑全量。