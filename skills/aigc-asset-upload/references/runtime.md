# 跨 Agent 配置

技能路径取自当前 Agent 实际加载目录，不要求 Codex。MiniMax 可把完整技能包安装到其技能目录；`video-inspiration-import`、`aigc-asset-organize`、`video-overlay-censor` 应与 `aigc-asset-upload` 同级安装。只复制一个脚本会缺少共享客户端；不同目录安装时设置 `AIGC_UPLOAD_SCRIPT` 指向上传包的 `scripts/upload_assets.py`。

## 配置项

| 变量 | 默认/用途 |
| --- | --- |
| `AIGC_SHELF_URL` | `https://aigc.chatcanvas.online` |
| `AIGC_SHELF_COOKIE_FILE` | 显式指定该服务的会话 JSON，优先级最高 |
| `AIGC_WORKBENCH_STATE_DIR` | 默认 `${XDG_CONFIG_HOME:-~/.config}/aigc-workbench`，存会话和 GIF 缓存 |
| `AIGC_SHELF_EMAIL`, `AIGC_SHELF_PASSWORD` | 未登录时使用；可改为终端安全提示输入 |
| `MINIMAX_DATA_DIR` / `MINIMAX_HOME` | MiniMax 运行时根目录，探测其 tools、tools/bin、bin、video-inspiration-venv/bin 和旧会话 |
| `AIGC_WORKBENCH_BIN_DIR` | 自定义 yt-dlp、ffmpeg、ffprobe 所在目录 |
| `YT_DLP_PATH`, `FFMPEG_PATH`, `FFPROBE_PATH` | 各工具完整路径；优先于 PATH |
| `XDG_DATA_HOME` | 探测其 aigc-workbench/bin 与 aigc-workbench/venv/bin |
| `AIGC_UPLOAD_SCRIPT` | 上传技能脚本绝对路径，非同级安装时使用 |
| `HTTPS_PROXY`, `HTTP_PROXY`, `SSL_CERT_FILE` | 按当前网络/证书环境设置，不复制另一台电脑的固定代理端口或证书路径 |

新会话默认写入 `<state-dir>/aigc-shelf-session.json`。默认目录没有会话时，兼容探测运行时旧会话；未配置运行时则探测 `~/.minimax` 和历史 `~/.codex`。只有一个旧文件时复用，多个时不猜账号，使用新会话或显式指定。旧裸 Cookie 字典兼容读取，新格式含服务地址、保存时间和过期时间，权限为 0600。损坏文件明确报错；不要覆盖后假装已登录。

## 依赖

- 上传和 HTTP 客户端只需 Python 3.9+ 标准库，不再需要 requests。
- 视频下载/校验需 yt-dlp、ffmpeg、ffprobe；ffmpeg 需支持 libx264/AAC。创建私有 venv，避免修改系统 Python。curl-cffi 是 yt-dlp 某些站点解析的可选依赖。
- ClipDock 是可选的播放捕获回退。已运行客户端时只需本地 API；从源码启动才需要对应 Node 环境和 video-downloads 项目。yt-dlp 成功时不依赖 ClipDock。
- 普通标题标签整理不需要 ffmpeg；媒体抽帧、验证、音频提取和遮挡合成才需要。音乐识别等可选功能的凭据按相应技能另行配置。

可用的通用私有目录示例：

```bash
python3 -m venv "$HOME/.config/aigc-workbench/venv"
"$HOME/.config/aigc-workbench/venv/bin/python" -m pip install 'yt-dlp[default]' curl-cffi
```

ffmpeg/ffprobe 可放到 MiniMax 的 tools 目录或显式配置路径；不要假设 Homebrew 必须存在。技能加载由目标 Agent 决定，安装后检查其技能列表；直接给出 `SKILL.md` 的实际路径也能让 Agent 读取工作流，但不等于注册成功。

凭据通过本机已有会话或安全输入配置，不把真实密码/Token 写入提示词模板、技能、仓库或日志。下载高清与上传本身不需要图像生成 API Key 或百度网盘 Token。
