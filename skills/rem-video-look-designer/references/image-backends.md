# 可配置生图后端

核心设计与提示词生成不依赖生图 API。没有凭据时,先完成视频分析和 `prompt.md`,然后用 `--dry-run` 保存请求摘要。

## OpenAI Image API(默认适配器)

`scripts/generate_reference.py` 默认配置:

| 项目 | CLI | 环境变量 | 默认值 |
|---|---|---|---|
| Provider | `--provider` | `IMAGE_PROVIDER` | `openai` |
| Model | `--model` | `IMAGE_MODEL` | `gpt-image-2` |
| API base | `--api-base` | `IMAGE_API_BASE` | `https://api.openai.com/v1` |
| API key | — | `IMAGE_API_KEY`,其次 `OPENAI_API_KEY` | 无 |
| Size | `--size` | `IMAGE_SIZE` | `1152x2048`(9:16) |
| Quality | `--quality` | `IMAGE_QUALITY` | `medium` |

端点按输入自动选择,不再提供 `--endpoint-mode`:带 `--reference` 时调用 `/images/edits`(multipart,支持多张 `image[]`);不带参考图时调用 `/images/generations`(JSON)。请求固定 `n: 1`。`gpt-image-2` 的参考图工作流不应传 `input_fidelity`,模型会自动高保真处理图像输入。API Key 只能从环境变量读取,不得写入 `SKILL.md`、提示词、请求摘要、日志、版本库或分享文件。

官方参考:

- https://developers.openai.com/api/docs/guides/image-generation
- https://developers.openai.com/api/reference/resources/images

## 企业/兼容代理注意事项(实测经验)

- 部分 OpenAI 兼容代理只接受经典像素尺寸(`1024x1024`、`1024x1536`、`1536x1024`),`1152x2048` 会被上游拒绝(实测 hairfree 返回 502),此时回退 `1024x1536`。
- `generations` JSON 端点不能携带参考图:对只支持该端点的服务,把参考图的关键特征写进提示词,不要传 `--reference`(否则会误走 `/images/edits`)。
- 代理默认值各不相同,建议每个后端在 `.env.local` 里显式固定 `IMAGE_SIZE`/`IMAGE_QUALITY`。

## OpenAI-compatible base URL

兼容 `/images/generations` 与 `/images/edits` 的服务可直接切换 base URL 与模型名:

```bash
export IMAGE_API_BASE="https://provider.example/v1"
export IMAGE_API_KEY="..."
export IMAGE_MODEL="provider-image-model"
```

若兼容服务不接受多张 `image[]`、字段名不同或只支持专用 SDK,请使用 command adapter。

## Command adapter

设置 `IMAGE_PROVIDER=command` 与 `IMAGE_GENERATOR_COMMAND`。命令模板可使用:

- `{prompt_file}`:UTF-8 提示词文件路径
- `{out}`:目标图片路径
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

自定义命令必须在成功退出前创建 `{out}`。这样可接入本地 ComfyUI、第三方 SDK 包装器或未来模型,而无需修改 Skill 的视频分析与蕾姆约束。

## toapis.cn 异步后端(已验证)

toapis 使用"提交任务 → 轮询 → 下载"的异步流,请求形态与 OpenAI 同步 API 不同:

- `POST /v1/images/generations` 创建任务,响应 `{"id":"tsk_img_...","status":"pending"}`;`GET /v1/images/generations/{id}` 轮询,`pending → in_progress → completed`;完成后 `result.data[0].url`(约 24h 过期,**必须立即转存**)或 `b64_json`。
- `size` 是比例字符串(`"4:5"`、`"9:16"`),不是像素;`metadata: {"resolution":"1K","orientation":"portrait"}`;`quality` 字段仅 `gpt-image-2.5-flare` 支持,`gpt-image-2` 不接受。
- 内容安全审查会拦截任务(`status=failed`,`billing` 已退款);**不得原样重试**,先按审查提示改写措辞(例如把"紧身/扭胯"改为"棉质圆领/身体轻轻摆动"类中性描述)再提交。
- 推荐经 command adapter 调用仓库根的 `scripts/toapis_generate.py`(该脚本封装提交/轮询/转存):

```bash
export IMAGE_PROVIDER="command"
export IMAGE_MODEL="gpt-image-2.5-flare"
export IMAGE_SIZE="9:16"
export IMAGE_QUALITY="high"
export IMAGE_GENERATOR_COMMAND='python3 scripts/toapis_generate.py --prompt-file {prompt_file} --out {out} --model {model} --size {size} --quality {quality}'
```

## 仅生成请求摘要

```bash
python scripts/generate_reference.py \
  --prompt-file OUTPUT/concept-01/prompt.md \
  --reference assets/rem-identity-full.png \
  --out OUTPUT/concept-01/reference.png \
  --dry-run
```

该命令生成 `reference.request.json`,不调用外部服务,也不包含 API Key。
