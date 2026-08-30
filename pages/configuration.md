# 配置参考

运行时配置由 `V2Config` 统一解析，优先级：**CLI 参数 > shell 环境变量 > 项目 `.env` > 代码默认值**。复制仓库根目录的 `.env.example` 为 `.env` 后按需修改；`.env` 仅加载 `PHONE_AGENT_` 前缀，且不覆盖 shell 已有同名变量。

布尔值接受 `1/true/yes/on`（开）与 `0/false/no/off`（关）。

## 模型与网关

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_BASE_URL` | `http://localhost:8000/v1` | OpenAI-compatible API 地址 |
| `PHONE_AGENT_MODEL` | `autoglm-phone-9b` | 主模型 ID |
| `PHONE_AGENT_API_KEY` | `EMPTY` | API Key |
| `PHONE_AGENT_MODEL_TIMEOUT` | `180` | 单次模型请求超时（秒） |
| `PHONE_AGENT_MODEL_MAX_RETRIES` | `2` | 模型请求最大重试次数 |
| `PHONE_AGENT_TEMPERATURE` / `TOP_P` / `FREQUENCY_PENALTY` | 未设置 | 可选采样参数；部分网关只接受固定值，按部署覆盖 |
| `PHONE_AGENT_USER_AGENT` | 内置浏览器 UA | 覆盖默认请求 User-Agent |
| `PHONE_AGENT_HTTP_HEADERS` | 未设置 | 附加请求头，格式 `K1=V1;K2=V2` |
| `PHONE_AGENT_CF_ACCESS_CLIENT_ID` / `SECRET` | 未设置 | Cloudflare Access，必须成对 |

## 设备与循环

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_DEVICE_ID` | 自动 | ADB 设备序列号 |
| `PHONE_AGENT_LANG` | `cn` | Prompt 语言：`cn` / `en` |
| `PHONE_AGENT_MAX_STEPS` | `100` | 最大模型调用数——**只是防跑飞保险丝**，成本控制走 token 预算 |
| `PHONE_AGENT_MAX_HITL_RESUMES` | `20` | 人工中断恢复次数上限 |

## Token 预算与自动压缩

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TOKEN_BUDGET` | `1000000` | 单次运行 input+output token 总预算，耗尽即终止 |
| `PHONE_AGENT_TOKEN_WARN_REMAINING` | `100000` | 剩余降至该值时注入一次余量提醒 |
| `PHONE_AGENT_COMPACT` | `true` | 两级 auto-compact 总开关 |
| `PHONE_AGENT_COMPACT_WARN_RATIO` | `0.75` | T1：窗口用量比例达到时提醒写 TaskDoc、收敛探索 |
| `PHONE_AGENT_COMPACT_TRIGGER_RATIO` | `0.92` | T2：生成结构化 handoff 并折叠远端历史 |
| `PHONE_AGENT_CONTEXT_WINDOW` | 按模型推断（兜底 256k） | 手动覆盖上下文窗口 |
| `PHONE_AGENT_MEMORY_MODEL` | 主模型 | compact 摘要用的纯文本模型 |

单次运行的 token 预算统一计入 actor 与所有旁路调用（compact 摘要器、finish verifier、安全 reviewer）。

## 上下文卫生与 Grounding

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_IMAGE_KEEP` | `2` | 历史中保留含图消息数（≥1） |
| `PHONE_AGENT_OBS_MARKS_KEEP` | `2` | 历史中保留完整 marks digest 的观测数（≥1） |
| `PHONE_AGENT_GROUNDING_PROVIDER` | `hybrid` | `hybrid` / `accessibility` / `locateanything` |
| `PHONE_AGENT_ACCESSIBILITY_TIMEOUT` | `3.0` | Accessibility dump 超时（秒） |
| `PHONE_AGENT_ACCESSIBILITY_MAX_MARKS` | `80` | 单次观测最多 marks 数 |
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | 未设置 | LocateAnything 模型路径（如 `models/LocateAnything-3B-4bit`） |
| `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE` | `960` | 送入 LA 前的图像最长边 |
| `PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` | `200` | LA 指令中 intent/可见文字提示的单字段上限 |
| `PHONE_AGENT_LOCATE_MAX_SIZE` | `0` | **locate 专用输入档位：`0`=原图直给（默认）；>0=最长边上限** |
| `PHONE_AGENT_SCOPE_PADDING_RATIO` | `0.05` | 可选 scope 裁剪框四边扩展比例 |
| `PHONE_AGENT_PARALLEL_TOOL_CALLS` | `false` | 默认禁用并行工具调用；仅在网关拒绝该参数时设 `true` |

## App-KB 本地记忆

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_MEMORY_DIR` | `memory` | 本地记忆根目录 |
| `PHONE_AGENT_APP_KB` | `true` | App-KB 总开关（同步/读取/写回/prompt 注入） |
| `PHONE_AGENT_APP_LIST_MAX` | `40` | 注入 system prompt 的本机应用名数量上限 |
| `PHONE_AGENT_DREAM` | `manual` | `off` / `auto` / `manual`；`auto` 在运行后轻量合并，`manual` 仅 `--dream` |
| `PHONE_AGENT_IMPLICIT_ALIAS` | `true` | 隐式纠正：中文叫法失手→包名成功时自动记 learned 别名（证据闭环） |

## TaskDoc、完成验收与安全

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TASKDOC` | `true` | TaskDoc 任务板总开关 |
| `PHONE_AGENT_FINISH_VERIFY` | `auto` | `off` / `auto` / `always`，见[安全模式](safety.md) |
| `PHONE_AGENT_FINISH_VERIFY_K` | `1` | 交给验收器的尾部截图数 |
| `PHONE_AGENT_VERIFIER_MODEL` | 主模型 | finish 独立验收模型 |
| `PHONE_AGENT_SAFETY_MODE` | `wary` | `off` / `wary` / `hard` / `reviewer`，见[安全模式](safety.md) |
| `PHONE_AGENT_SAFETY_REVIEWER_MODEL` | verifier 或主模型 | reviewer 模式的风险精排模型 |

## Trace 与诊断

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TRACE` | `true` | 生产 JSONL trace 开关 |
| `PHONE_AGENT_TRACE_DIR` | `.traces` | trace 目录 |
| `PHONE_AGENT_DIAG_EVIDENCE` | `false` | live-diagnosis 完整证据流开关 |
| `PHONE_AGENT_DIAG_EVIDENCE_DIR` | `outputs/live-diagnosis/.evidence` | 诊断证据目录 |
| `PHONE_AGENT_DIAG_UNREDACTED` | `false` | 本机诊断全保真模式 |

!!! note "网关注意事项"
    - 网关在 Cloudflare 后必须保留浏览器风格 UA（已内置），否则 `403 Your request was blocked`；
    - CF Access 的 Client ID 与 Secret 必须成对配置；
    - 部分模型部署只接受固定采样值——用环境变量按部署覆盖，不要改代码。
