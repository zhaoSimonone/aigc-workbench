# 可配置生图后端

核心设计与提示词生成不依赖生图 API。没有凭据时，先完成视频分析和 `prompt.md`，然后用 `--dry-run` 保存请求摘要。

## OpenAI Image API（默认适配器）

`scripts/generate_reference.py` 默认配置：

| 项目 | CLI | 环境变量 | 默认值 |
|---|---|---|---|
| Provider | `--provider` | `IMAGE_PROVIDER` | `openai` |
| Model | `--model` | `IMAGE_MODEL` | `gpt-image-2` |
| API base | `--api-base` | `IMAGE_API_BASE` | `https://hairfree.corp.kuaishou.com/v1` |
| API key | — | `IMAGE_API_KEY`，其次 `OPENAI_API_KEY` | 无 |
| Size | `--size` | `IMAGE_SIZE` | `1024x1536`（9:16） |
| Quality | `--quality` | `IMAGE_QUALITY` | `high` |
| Endpoint mode | `--endpoint-mode` | `IMAGE_ENDPOINT_MODE` | `generations` |

hairfree 默认使用 `/images/generations` 的 JSON 请求，和服务端已验证的 curl 形态一致；该模式不能携带 `--reference`，请把参考图的关键特征写进提示词。兼容支持图像输入的服务可显式传 `--endpoint-mode edits`，脚本才会使用 `/images/edits` multipart。`gpt-image-2` 的参考图工作流不应传 `input_fidelity`，模型会自动高保真处理图像输入。

```bash
export IMAGE_API_KEY="..."
export IMAGE_API_BASE="https://hairfree.corp.kuaishou.com/v1"
export IMAGE_MODEL="gpt-image-2"
export IMAGE_SIZE="1024x1536"
export IMAGE_QUALITY="high"
export IMAGE_ENDPOINT_MODE="generations"

python scripts/generate_reference.py \
  --prompt-file OUTPUT/concept-01/prompt.md \
  --reference assets/rem-identity-full.png \
  --reference assets/rem-long-wave.png \
  --reference OUTPUT/analysis/frames/frame-03.png \
  --out OUTPUT/concept-01/reference.png \
  --size 1152x2048 \
  --quality medium \
  --endpoint-mode edits
```

API Key 只能从环境变量读取。不得写入 `SKILL.md`、提示词、请求摘要、日志、版本库或分享文件。

官方参考：

- https://developers.openai.com/api/docs/guides/image-generation
- https://developers.openai.com/api/reference/resources/images

## OpenAI-compatible base URL

兼容 `/images/generations` 与 `/images/edits` 的服务可直接切换 base URL 与模型名：

```bash
export IMAGE_API_BASE="https://provider.example/v1"
export IMAGE_API_KEY="..."
export IMAGE_MODEL="provider-image-model"
```

若兼容服务不接受多张 `image[]`、字段名不同或只支持专用 SDK，请使用 command adapter。

## Command adapter

设置 `IMAGE_PROVIDER=command` 与 `IMAGE_GENERATOR_COMMAND`。命令模板可使用：

- `{prompt_file}`：UTF-8 提示词文件路径
- `{out}`：目标图片路径
- `{model}`、`{size}`、`{quality}`

参考图绝对路径会以 JSON 数组写入子进程环境变量 `IMAGE_REFERENCE_LIST`。

```bash
export IMAGE_PROVIDER="command"
export IMAGE_MODEL="my-image-model"
export IMAGE_GENERATOR_COMMAND='my-generator --prompt-file {prompt_file} --out {out} --model {model}'

python scripts/generate_reference.py \
  --prompt-file OUTPUT/concept-01/prompt.md \
  --reference assets/rem-identity-full.png \
  --out OUTPUT/concept-01/reference.png
```

自定义命令必须在成功退出前创建 `{out}`。这样可接入本地 ComfyUI、第三方 SDK 包装器或未来模型，而无需修改 Skill 的视频分析与蕾姆约束。

## 仅生成请求摘要

```bash
python scripts/generate_reference.py \
  --prompt-file OUTPUT/concept-01/prompt.md \
  --reference assets/rem-identity-full.png \
  --out OUTPUT/concept-01/reference.png \
  --dry-run
```

该命令生成 `reference.request.json`，不调用外部服务，也不包含 API Key。
