# 配置参考

配置优先级：**CLI 参数 > shell 环境变量 > 项目 `.env` > 代码默认值**。复制仓库根目录 `.env.example` 为 `.env` 后按需修改；`.env` 仅加载 `PHONE_AGENT_` 前缀变量。布尔值接受 `on/off`、`true/false`、`1/0`。

## 必填：模型

| 变量 | 说明 |
|---|---|
| `PHONE_AGENT_BASE_URL` | OpenAI-compatible API 地址 |
| `PHONE_AGENT_MODEL` | 主模型 ID（需要视觉多模态能力） |
| `PHONE_AGENT_API_KEY` | API Key |

## 常用

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_DEVICE_ID` | 自动识别 | ADB 设备序列号 |
| `PHONE_AGENT_LANG` | `cn` | Prompt 语言：`cn` / `en` |
| `PHONE_AGENT_MAX_STEPS` | `100` | 单轮最大模型调用数（防跑飞保险丝，非成本手段） |
| `PHONE_AGENT_TOKEN_BUDGET` | `1000000` | 单轮 token 总预算（成本上限），耗尽即终止 |
| `PHONE_AGENT_SAFETY_MODE` | `wary` | 安全门控：`off` / `wary` / `hard` / `reviewer`，见[安全模式](safety.md) |
| `PHONE_AGENT_GROUNDING_PROVIDER` | `hybrid` | 落地方式：`hybrid` / `accessibility` / `locateanything` |

## 长任务（预算与压缩）

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_TOKEN_WARN_REMAINING` | `100000` | 剩余预算降至该值时提醒模型收尾 |
| `PHONE_AGENT_COMPACT` | `true` | 两级 auto-compact 开关 |
| `PHONE_AGENT_COMPACT_WARN_RATIO` | `0.75` | 上下文窗口用量预警线 |
| `PHONE_AGENT_COMPACT_TRIGGER_RATIO` | `0.92` | 自动生成 handoff 摘要并折叠历史的触发线 |
| `PHONE_AGENT_CONTEXT_WINDOW` | 按模型推断（兜底 256k） | 手动覆盖上下文窗口 |
| `PHONE_AGENT_MEMORY_MODEL` | 主模型 | compact 摘要用的纯文本模型 |
| `PHONE_AGENT_IMAGE_KEEP` | `2` | 历史中保留的截图消息数 |

## 记忆

### App-KB（本机应用事实库）

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_APP_KB` | `true` | App-KB 总开关 |
| `PHONE_AGENT_MEMORY_DIR` | `memory` | 本地记忆根目录 |
| `PHONE_AGENT_APP_LIST_MAX` | `40` | 注入 prompt 的本机应用名上限 |
| `PHONE_AGENT_DREAM` | `manual` | 整理时机：`off` / `auto` / `manual`（仅 `--dream`） |
| `PHONE_AGENT_IMPLICIT_ALIAS` | `true` | 隐式纠正：叫法失手→包名成功时自动记别名 |

### 经验与回想（建设中）

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_EXPERIENCE` | `on` | episode 任务档案记录开关（observe-only） |
| `PHONE_AGENT_EXPERIENCE_DIR` | `memory/experience` | 档案目录 |
| `PHONE_AGENT_EPISODE_KEEP` | `500` | 保留完整档案数，更老的归档为聚合统计 |
| `PHONE_AGENT_MEMORY_RAG` | `shadow` | 语义回想：`off` / `shadow`（只观测不注入）/ `on`（预留） |
| `PHONE_AGENT_EMBED_MODEL` | `mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ` | 本地嵌入模型（MLX，可换） |
| `PHONE_AGENT_RECALL_TOP_K` | `5` | 回想候选数上限 |
| `PHONE_AGENT_RECALL_MIN_SCORE` | `0.35` | 相似度阈值，低于则静默（宁可不召回） |

## Grounding 与视觉定位

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | 未设置 | 本地视觉定位模型路径 |
| `PHONE_AGENT_LOCATE_MAX_SIZE` | `0` | locate 输入档位：`0`=原图（默认）；>0=最长边上限 |
| `PHONE_AGENT_SCOPE_PADDING_RATIO` | `0.05` | scope 圈定区域时的边缘扩展比例 |
| `PHONE_AGENT_ACCESSIBILITY_MAX_MARKS` | `80` | 单次观测最多输出的界面元素数 |

## 完成验收与诊断

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PHONE_AGENT_FINISH_VERIFY` | `auto` | finish 验收器：`off` / `auto` / `always` |
| `PHONE_AGENT_VERIFIER_MODEL` | 主模型 | 独立验收模型 |
| `PHONE_AGENT_TRACE` | `true` | 运行 trace（JSONL，脱敏）开关 |
| `PHONE_AGENT_TRACE_DIR` | `.traces` | trace 目录 |

!!! tip "原则"
    所有模型、阈值、窗口、档位都是配置项——如果你的部署有特殊限制（采样值固定、请求头等），用对应的环境变量覆盖，不需要改代码。完整可选项见仓库 `.env.example` 注释。
