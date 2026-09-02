---
name: aigc-asset-upload
description: Upload user-selected local videos and images to the AIGC Shelf asset library, including metadata, when the user explicitly asks to import or batch-upload creative assets.
---

# AIGC 素材上传

将用户明确指定的本地视频或图片上传到 AIGC Shelf。AIGC Shelf 服务端会把文件写入腾讯云 COS，并把素材元数据写入按用户隔离的数据库；本 Skill 不读取、复制或要求用户提供 COS SecretId/SecretKey。

## 工作流程

1. 只处理用户明确指定的文件。若消息包含附件路径，使用这些路径；若用户给出目录，先列出其中的视频/图片并确认范围，不上传无关文件。
2. 从用户请求中提取元数据。默认值为：所在文件夹 `灵感收集`、来源 `本地导入`、标签 `待整理`、未使用。用户已经明确给出的名称、来源、原链接、标签、人物相册、人物分类、使用状态和关联素材 ID 要传入；只有会改变归类结果的必要信息缺失时才提问。
3. 当目标文件夹为 `角色设定` 时，需要人物相册名称；人物分类只有用户明确要求或业务流程要求时再询问。多个文件使用同一批元数据，名称由脚本自动追加文件名以避免重名。
4. 执行 `scripts/upload_assets.py`。脚本会先检查本机保存的 AIGC Shelf 会话；会话失效时使用 `AIGC_SHELF_EMAIL`/`AIGC_SHELF_PASSWORD` 环境变量或安全提示输入登录信息。不要把密码写入命令行、Skill 文件或仓库。
5. 等脚本输出每个文件的成功/失败结果后再向用户汇报。成功项必须包含素材名称和数据库 ID；失败项说明文件名和服务端错误，不把失败说成已上传。

## 调用

脚本默认使用生产地址 `https://aigc.chatcanvas.online`，也可通过 `AIGC_SHELF_URL` 或 `--api` 覆盖。示例：

```bash
python3 /Users/simon/.codex/skills/aigc-asset-upload/scripts/upload_assets.py \
  /path/to/video.mp4 /path/to/reference.png \
  --name "林小栀角色参考" \
  --source "本地导入" \
  --tags "人物,正脸,参考" \
  --folder "角色设定" \
  --character-name "林小栀" \
  --character-category "正脸"
```

不需要指定名称时省略 `--name`，服务端使用原文件名。`--source-url` 保存原视频/参考链接；`--used` 标记已使用；可重复传入 `--parent-asset-id` 关联原素材。

## 安全与边界

- 上传是外部状态变更，只在用户明确要求上传时执行。
- 不读取 `/Users/simon/Desktop/dev/cloud/tencent` 下的凭据文件，不在日志、参数、提交或回复中输出 COS 密钥、密码或完整会话 Cookie。
- 会话文件默认保存到 `~/.codex/aigc-shelf-session.json`，脚本设置为仅当前用户可读；发现权限异常时提醒用户，不复制到仓库。
- 默认单文件限制 2 GB，服务端负责生成视频封面、识别时长和写入数据库。
