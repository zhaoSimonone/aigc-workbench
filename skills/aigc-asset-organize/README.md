# AIGC 素材整理 Skill

这个 skill 用真实的画面和音频内容整理 AIGC Shelf 素材。它可以生成标题、精简标签、备注、人物和音乐字段的提案，并在展示 diff、得到确认后才写回服务器。

## 安装

服务端整理脚本只需要 Python 3。若需要本地识别音乐，安装 FFmpeg/FFprobe 和 `requests`：

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

本地音乐识别通过 ACRCloud 提供，需要项目凭证。请在当前 shell 中设置以下环境变量，不要把密钥写入仓库或 `.env`：

```bash
export ACRCLOUD_HOST=identify-cn-north-1.acrcloud.cn
export ACRCLOUD_ACCESS_KEY=...
export ACRCLOUD_SECRET_KEY=...
```

## 常用命令

```bash
python3 scripts/organize_assets.py inspect --tab 灵感收集 --music --json
python3 scripts/organize_assets.py music-library --build --confirm --json
python3 scripts/organize_assets.py music-library --dedupe-content --json
python3 scripts/recognize_music.py "/path/to/video.mp4" --pretty
```

音乐识别和音乐库音频提取都在本地完成。服务器只保存素材、音频对象和关系元数据；本地 FFmpeg 先把视频/音频切成短片段，再只把片段发送到 ACRCloud 识别。识别功能需要网络和 ACRCloud 项目凭证，不是完全离线方案。完整的字段规则、diff 写回约束和音乐识别说明见 [SKILL.md](SKILL.md) 与 [references/music-recognition.md](references/music-recognition.md)。
