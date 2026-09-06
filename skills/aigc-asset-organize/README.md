# AIGC 素材整理 Skill

这个 skill 用真实的画面和音频内容整理 AIGC Shelf 素材。它可以生成标题、精简标签、备注、人物和音乐字段的提案，并在展示 diff、得到确认后才写回服务器。

## 安装

服务端整理脚本只需要 Python 3。若需要本地识别音乐，安装 FFmpeg/FFprobe 和 ShazamIO：

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 常用命令

```bash
python3 scripts/organize_assets.py inspect --tab 灵感收集 --music --json
python3 scripts/organize_assets.py music-library --build --confirm --json
python3 scripts/organize_assets.py music-library --dedupe-content --json
python3 scripts/recognize_music.py "/path/to/video.mp4" --pretty
```

音乐识别和音乐库音频提取都在本地完成。服务器只保存素材、音频对象和关系元数据；ShazamIO 会将本地抽取的音频片段发送到 Shazam，因此识别功能需要网络且不是完全离线方案。完整的字段规则、diff 写回约束和音乐识别说明见 [SKILL.md](SKILL.md) 与 [references/music-recognition.md](references/music-recognition.md)。
