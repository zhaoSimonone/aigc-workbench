# 安装与调用

> 本仓库内位置:`skills/rem-video-look-designer/`(目录名与 frontmatter 中的 skill 名 `rem-video-look-designer` 一致,无需安装)。以下为分发到其他 Agent 宿主时的安装方式。

## 本地 Codex

解压后，将 `bluehair-video-look-designer` 文件夹放入：

```text
~/.codex/skills/
```

重启或新开一个 Codex 会话后，使用：

```text
$rem-video-look-designer 分析我上传的视频，生成适配开场的成年蓝发女性妆造参考图，并给我 CapCut 的换脸换发提示词。
```

也可以直接说“使用蓝发角色视频妆造技能”。

## ChatGPT 网页版

打开“技能”页，点击加号，上传本压缩包。安装后，在聊天框中输入 `@蓝发角色视频妆造`，再上传视频。

## 可配置生图模型

默认脚本使用 `gpt-image-2`。可通过环境变量或脚本参数配置 provider、模型、base URL 和自定义命令适配器；密钥仅放在本地环境变量中，不写入提示词或技能文件。

该技能在生成视频编辑提示词时只使用参考图的人脸和发型；原视频是服装、领口、动作、背景、字幕和音频的唯一依据，并会要求先定位变装分界后再分段处理。
