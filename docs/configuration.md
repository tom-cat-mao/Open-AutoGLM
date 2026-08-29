# Open-AutoGLM v2 配置

运行时配置由 `V2Config` 统一解析，优先级为：**CLI 参数 > shell 环境变量 > 项目 `.env` > 代码默认值**。复制 [`.env.example`](../.env.example) 为 `.env` 后按需修改；`.env` 仅加载 `PHONE_AGENT_` 前缀，且不会覆盖 shell 中已有的同名变量。

下表的“默认值”指未设置环境变量时的运行时默认值。`.env.example` 可能给出便于本地启动的推荐值，例如 `PHONE_AGENT_LOCATEANYTHING_MODEL=models/LocateAnything-3B-4bit`。布尔值通常接受 `1/true/yes/on`；标为“默认开启”的开关可用 `0/false/no/off` 关闭。

## 模型与网关

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_BASE_URL` | `http://localhost:8000/v1` | OpenAI-compatible API 地址；CLI `--base-url` 可覆盖。 |
| `PHONE_AGENT_MODEL` | `autoglm-phone-9b` | 主模型 ID；CLI `--model` 可覆盖。 |
| `PHONE_AGENT_API_KEY` | `EMPTY` | API Key。 |
| `PHONE_AGENT_MODEL_TIMEOUT` | `180` | 单次模型请求超时，单位秒。 |
| `PHONE_AGENT_MODEL_MAX_RETRIES` | `2` | 模型请求最大重试次数。 |
| `PHONE_AGENT_TEMPERATURE` | 未设置 | 可选采样参数，浮点数。 |
| `PHONE_AGENT_TOP_P` | 未设置 | 可选采样参数，浮点数。 |
| `PHONE_AGENT_FREQUENCY_PENALTY` | 未设置 | 可选采样参数，浮点数。 |
| `PHONE_AGENT_USER_AGENT` | 内置浏览器 UA | 覆盖默认请求 `User-Agent`。 |
| `PHONE_AGENT_HTTP_HEADERS` | 未设置 | 附加请求头，格式为 `K1=V1;K2=V2`。 |
| `PHONE_AGENT_CF_ACCESS_CLIENT_ID` | 未设置 | Cloudflare Access Client ID，必须与 Secret 成对设置。 |
| `PHONE_AGENT_CF_ACCESS_CLIENT_SECRET` | 未设置 | Cloudflare Access Client Secret，必须与 ID 成对设置。 |

## 设备、语言与循环上限

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_DEVICE_ID` | 未设置 | ADB 设备序列号；CLI `--device-id` 可覆盖。 |
| `PHONE_AGENT_LANG` | `cn` | Prompt 语言：`cn` 或 `en`；CLI `--lang` 可覆盖。 |
| `PHONE_AGENT_MAX_STEPS` | `100` | 单次运行最大模型调用数，仅作为 runaway-loop 保险丝；CLI `--max-steps` 可覆盖。 |
| `PHONE_AGENT_MAX_HITL_RESUMES` | `20` | 外层人工中断恢复次数上限。 |
| `PHONE_AGENT_BUDGET_WARN_RATIO` | `0.8` | 已废弃兼容项；token 预算启用后不再被预算中间件读取。 |

## App-KB 本地记忆

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_MEMORY_DIR` | `memory` | 本地记忆根目录，App-KB 写入其 `app_kb/` 子目录。 |
| `PHONE_AGENT_APP_KB` | `true` | App-KB 总开关；关闭后不进行同步、读取、验证启动写回或 prompt 注入。 |
| `PHONE_AGENT_APP_LIST_MAX` | `40` | 注入 system prompt 的本机应用规范名数量上限。 |
| `PHONE_AGENT_DREAM` | `manual` | `off` / `auto` / `manual`；`auto` 在运行后轻量合并，`manual` 仅由 `--dream` 触发。 |

更多数据边界和演进设计见 [App-KB 记忆设计](app-kb-memory-design.md)。

## Token 预算与自动压缩

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TOKEN_BUDGET` | `1000000` | 单次运行 input + output token 总预算，耗尽时终止。 |
| `PHONE_AGENT_TOKEN_WARN_REMAINING` | `100000` | 剩余 token 降至该值时注入一次余量提醒。 |
| `PHONE_AGENT_COMPACT` | `true` | 两级 auto-compact 总开关。 |
| `PHONE_AGENT_COMPACT_WARN_RATIO` | `0.75` | 上下文用量达到窗口比例时提醒写 TaskDoc、收敛探索。 |
| `PHONE_AGENT_COMPACT_TRIGGER_RATIO` | `0.92` | 达到窗口比例时生成结构化 handoff 并折叠远端历史。 |
| `PHONE_AGENT_CONTEXT_WINDOW` | 按模型推断，兜底 `256000` | 手动覆盖上下文窗口 token 数。 |
| `PHONE_AGENT_MEMORY_MODEL` | 主模型 | auto-compact 的纯文本摘要模型。 |

单次运行的 token 预算统一计入 actor 与 side model 调用（auto-compact 摘要器、finish verifier、安全 reviewer）；provider 未返回 usage metadata 时使用本地估算。

## 上下文卫生与 Grounding

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_IMAGE_KEEP` | `2` | 历史中保留 image block 的含图消息数，至少为 1。 |
| `PHONE_AGENT_OBS_MARKS_KEEP` | `2` | 历史中保留完整 marks digest 的观测数，至少为 1。 |
| `PHONE_AGENT_GROUNDING_PROVIDER` | `hybrid` | `hybrid` / `accessibility` / `locateanything`；CLI `--grounding-provider` 可覆盖。 |
| `PHONE_AGENT_ACCESSIBILITY_TIMEOUT` | `3.0` | Accessibility dump 超时，单位秒。 |
| `PHONE_AGENT_ACCESSIBILITY_MAX_MARKS` | `80` | 单次 accessibility 观测最多输出的 marks 数。 |
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | 未设置 | LocateAnything 模型路径；`.env.example` 推荐 `models/LocateAnything-3B-4bit`。 |
| `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE` | `960` | 送入 LocateAnything 前的图像最长边。 |
| `PHONE_AGENT_PARALLEL_TOOL_CALLS` | `false` | 默认禁用并行工具调用；仅在网关拒绝该参数时设为 `true`。 |

## TaskDoc、完成验收与安全

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TASKDOC` | `true` | TaskDoc 任务板与 pinned context 总开关。 |
| `PHONE_AGENT_TASKDOC_NUDGE_STEPS` | `5` | 已废弃、无效果；仅为配置兼容保留。 |
| `PHONE_AGENT_FINISH_VERIFY` | `auto` | `off` / `auto` / `always`；控制 finish 两段式及独立验收器触发策略。 |
| `PHONE_AGENT_FINISH_VERIFY_K` | `1` | 交给 finish verifier 的尾部截图数量。 |
| `PHONE_AGENT_VERIFIER_MODEL` | 主模型 | finish 独立验收模型。 |
| `PHONE_AGENT_SAFETY_MODE` | `wary` | `off` / `wary` / `hard` / `reviewer`，见下方模式说明。 |
| `PHONE_AGENT_SAFETY_REVIEWER_MODEL` | verifier 或主模型 | `reviewer` 模式的风险精排模型。 |

安全模式：`wary` 对风险执行调用返回预警，模型带 `confirm_irreversible=true` 重发才执行；`hard` 使用人工 approve/reject 中断；`reviewer` 在 wary 前增加第二模型精排；`off` 关闭执行动作门控。`ask_user` 与 `take_over` 在所有模式下仍会中断。

## Trace 与真机诊断

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TRACE` | `true` | 生产 JSONL trace 开关。 |
| `PHONE_AGENT_TRACE_DIR` | `.traces` | 生产 trace 目录；CLI `--trace-dir` 可覆盖。 |
| `PHONE_AGENT_DIAG_EVIDENCE` | `false` | live-diagnosis skill 的完整证据流开关。 |
| `PHONE_AGENT_DIAG_EVIDENCE_DIR` | `outputs/live-diagnosis/.evidence` | 诊断证据输出目录。 |
| `PHONE_AGENT_DIAG_UNREDACTED` | `false` | 本机诊断全保真模式；不影响生产 trace 的脱敏规则。 |

## 网关注意事项

- 若模型网关位于 Cloudflare 后，请保留浏览器风格的 `User-Agent`；`phone_agent/v2/model.py` 默认已设置。原始 OpenAI client 若不带该请求头，可能收到 `403 Your request was blocked`。
- Cloudflare Access 的 Client ID 与 Secret 必须同时配置，否则启动时会报错。
- 部分模型部署只接受固定采样值，例如 `temperature=1`、`top_p=0.95`、`frequency_penalty=0`。通过对应的 `PHONE_AGENT_*` 变量按部署覆盖，不要把限制硬编码进代码。
- CLI 可直接覆盖 `device-id`、`max-steps`、`model`、`base-url`、`grounding-provider`、`lang` 和 `trace-dir`；完整参数见 `.venv/bin/python main_v2.py --help`。
