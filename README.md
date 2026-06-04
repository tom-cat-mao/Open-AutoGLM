# Phone Agent

基于 LangGraph 的手机端智能助理框架。通过视觉语言模型理解屏幕内容，自动规划并执行操作流程。

## 架构

核心采用 **Plan-Execute-Reflect** 三节点拓扑，由 LangGraph StateGraph 驱动：

```
START → plan → execute → [confirm|takeover|reflect|replan|end]
                         ├─ confirm → after_interrupt → [execute|reflect|end]
                         ├─ takeover → after_interrupt → [reflect|end]
                         ├─ reflect → should_continue → [replan|end]
                         ├─ replan → plan
                         └─ end → END
```

- **plan** — 截图 + 模型推理 + 解析 action
- **execute** — `dispatch_tool()` 路由到 `@tool` 函数执行动作
- **reflect** — 再截图 + 模型判断动作是否生效
- **confirm / takeover** — LangGraph `interrupt()` 实现 Human-in-the-Loop；当前保证 interrupt 路由语义，持久 resume 将在后续 checkpoint/resume 阶段完善

### 项目结构

```
phone_agent/
├── agent.py                     # PhoneAgent 入口，使用 StateGraph
├── device_factory.py            # 设备工厂
├── model/
│   └── client.py                # ModelClient (OpenAI 兼容；text/json/tool_calls 输出适配)
├── actions/
│   ├── handler.py               # parse_action(), ActionResult, do(), finish()
│   └── adapter.py               # JSON/tool_calls → canonical action 适配与校验
├── grounding/                   # GroundingProvider、LocateAnything MLX、fake provider、bbox parser
├── adb/                         # Android 设备控制
├── config/
│   ├── apps.py                  # 应用包名映射
│   ├── prompts.py / prompts_zh.py / prompts_en.py  # prompt contract + legacy rollback
│   └── timing.py
└── graph/                       # LangGraph 核心
    ├── state.py                 # AgentState TypedDict
    ├── builder.py               # create_agent_graph()
    ├── edges.py                 # 条件边路由
    ├── trace.py                 # 本地 JSONL trace 与脱敏
    ├── context.py               # context selector、request compaction、预算裁剪与脱敏
    ├── nodes/
    │   ├── plan.py
    │   ├── execute.py
    │   ├── reflect.py
    │   ├── confirm.py           # interrupt() 确认
    │   └── takeover.py          # interrupt() 接管
    └── tools/                   # @tool 函数
        ├── __init__.py          # dispatch_tool, get_tool_map
        ├── coords.py            # 坐标转换
        ├── tap.py / type_text.py / swipe.py / launch.py
        ├── navigation.py        # back / home
        ├── press.py             # double_tap / long_press
        ├── wait.py
        └── misc.py              # note / call_api / interact
```

## 快速开始

### 环境要求

- Python 3.10+
- Android 设备（7.0+），开启 USB 调试
- ADB 已安装并可用
- AutoGLM 模型服务（本地部署或 API）

### 安装

```bash
git clone git@github.com:tom-cat-mao/Open-AutoGLM.git
cd Open-AutoGLM
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
```

### ADB Keyboard 安装（Android 文本输入必需）

下载 [ADBKeyboard.apk](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk) 安装到设备，并在设置中启用。

### 运行

```bash
# 交互模式
python main.py --base-url http://localhost:8000/v1 --model "autoglm-phone-9b"

# 指定任务
python main.py --base-url http://localhost:8000/v1 "打开美团搜索附近的火锅店"

# 使用智谱 API
python main.py --base-url https://open.bigmodel.cn/api/paas/v4 --model "autoglm-phone" --apikey "your-key"

# 英文模式
python main.py --lang en --base-url http://localhost:8000/v1 "Open Chrome browser"
```

### Python API

```python
from phone_agent import PhoneAgent
from phone_agent.model import ModelConfig

agent = PhoneAgent(model_config=ModelConfig(
    base_url="http://localhost:8000/v1",
    model_name="autoglm-phone-9b",
    output_mode="text_dsl",  # text_dsl | json_schema | tool_calls | auto
))

result = agent.run("打开淘宝搜索无线耳机")
print(result)
```

`run()` 保持返回字符串的兼容行为；评测或观测场景可使用结构化 API：

```python
structured = agent.run_structured("打开淘宝搜索无线耳机")
print(structured.to_dict())
# 包含 success / finished / steps / duration / error / hitl_count / trace_id / trace_path
# failure_cause / retry_count / context_mode / context_strategy / prompt_version
# selected_sections / messages_before/after / approx_tokens_before/after 等 context metrics
```

默认启用本地 JSONL trace，文件写入 `.traces/{trace_id}.jsonl`。trace 记录 run id、trace id、step id、node、event、timestamp 与脱敏后的 payload；截图、prompt、API key、任务文本、thinking、reflection、HITL 消息默认不会以原文写入。

### Context & Observability Harness

默认启用短期 context 观测模式，用于记录可比较的执行上下文和失败模式，但默认不注入模型 Plan：

| 模式 | 行为 |
|------|------|
| `off` | 不生成新增 context 指标 |
| `observe` | 默认模式；生成 state/trace/eval 指标，但不向 Plan 注入 context block |
| `inject` | 注入脱敏、裁剪后的短期 context block |

`AgentConfig(context_mode="observe")` 可切换模式。context 字段包括 `screen_belief`、`action_outcome_summary`、`failure_memory`、`summarized_history` 与预算/截断指标；默认预算为 failure memory 最近 3 条、screen belief 摘要 300 字符、history 摘要 800 字符、context block 1500 字符。姓名、手机号、邮箱、订单号、验证码、API key/token、长 base64/JWT 等敏感文本默认脱敏，context 不绕过 HITL/confirm/takeover。

#### LangGraph-native Context Engineering Harness

Phase 14 将 prompt、context selector 与 context-window compaction 收敛为 LangGraph 原生请求构造层，不替换现有 `StateGraph` 拓扑：

| 能力 | 行为 |
|------|------|
| Prompt contract | `get_system_prompt(lang, output_mode, prompt_version)` 由 System Contract + Action Schema + Policy + Context Rules + 单一输出契约组成 |
| Prompt rollback | 默认 `prompt_version="context_harness_v1"`；`legacy_text_dsl` 保留旧 text DSL prompt 作为回滚路径 |
| Context selector | `select_plan_context()` 输出 `context_strategy`、`selected_sections`、脱敏 context block 与计数指标 |
| Request compaction | `compact_messages_for_request()` 仅压缩传给 `model_client.request()` 的消息，不改写 `state["messages"]` |
| 图片预算 | 历史图片从模型请求中剥离，最新用户截图保留；保持 `messages_reducer` append/replace 语义 |
| 隐私指标 | trace/eval 只持久化 section IDs、消息数、字符数、近似 token、截断状态等，不持久化 raw prompt/context/image |

Context selector 与 compaction 只能影响模型请求构造和脱敏观测指标，不得修改 `action_raw`、`action_parsed`、`pending_execute`、`interrupt_result`、`action_confirmed` 或 Safety/HITL 路由字段。

### Model Output Adapter

默认保持原有 text DSL 输出兼容，同时支持 provider-facing JSON 与已聚合 OpenAI `tool_calls`：

| 模式 | 行为 |
|------|------|
| `text_dsl` | 默认模式；解析 `do(...)` / `finish(...)` 与 `<answer>...</answer>` 包裹输出 |
| `json_schema` | 模型输出 JSON，经 adapter 映射为内部 canonical action |
| `tool_calls` | 聚合 streaming tool_calls delta 后，经 adapter 映射为内部 canonical action |
| `auto` | 自动识别 JSON，否则回退 text DSL |

所有格式最终都进入统一执行路径：adapter 只生成 canonical action，不直接调用工具；真实执行仍由 `execute_node -> dispatch_tool()` 完成。JSON/tool_calls 仅允许白名单 action 与字段，坐标保持 0-1000 相对值并在 tool 层转换为绝对像素；未知 action、缺字段、越界坐标、非 literal/危险结构均 fail-closed 为 `model_parse_failed`，不会伪装成成功 `finish`，也不会绕过 confirm/takeover HITL。

解析观测字段会进入 trace/eval 相关链路，包括 configured mode、detected format、adapter used、parse success/error code；`parse_error`、截图、API key、任务文本与隐私文本默认脱敏。

### LocateAnything 本地 Grounding（可选）

Open-AutoGLM 支持把主 VLM 的语义/意图与本地视觉定位拆开：主 VLM 在 JSON/tool_calls 模式输出 `IntentIR`（例如 `target_text_hint` / `target_role` / `target_intent` 或 `target_mark_id`），本地 `GroundingProvider` 将当前截图 + 目标描述定位为 0-1000 bbox/center，再进入 canonical `ActionIR -> Validator -> Repair -> Validator -> Safety/HITL -> Executor`。

```bash
# 默认测试不需要 MLX；真实 LocateAnything 仅作为可选 extra
.venv/bin/pip install -e ".[locateanything]"

# 可选：启用本地 LocateAnything provider
export PHONE_AGENT_GROUNDING_PROVIDER=locateanything
export PHONE_AGENT_LOCATEANYTHING_MODEL=models/LocateAnything-3B-4bit
```

安全边界：`target_mark_id` 优先走 MarkRegistry；`target_text_hint` 描述路径才调用 LocateAnything。target-required grounding 失败（provider 缺失、超时、hash mismatch、stale screen、低置信、bad bbox 等）会 fail-closed 为 `model_parse_failed`，不会回退为主 VLM 直接坐标 Tap。trace/eval 只记录 provider、bbox/center、screen/hash、latency、failure code 与脱敏 target summary，不记录原始截图或 raw target text。

## 模型部署

### 选项 A：第三方 API

| 服务商 | base-url | model |
|--------|----------|-------|
| 智谱 BigModel | `https://open.bigmodel.cn/api/paas/v4` | `autoglm-phone` |
| ModelScope | `https://api-inference.modelscope.cn/v1` | `ZhipuAI/AutoGLM-Phone-9B` |

### 选项 B：本地部署

需要 NVIDIA GPU（建议 24GB+ 显存）：

```bash
# vLLM
python3 -m vllm.entrypoints.openai.api_server \
  --served-model-name autoglm-phone-9b \
  --allowed-local-media-path / \
  --mm-encoder-tp-mode data \
  --mm_processor_cache_type shm \
  --mm_processor_kwargs '{"max_pixels":5000000}' \
  --max-model-len 25480 \
  --chat-template-content-format string \
  --limit-mm-per-prompt '{"image":10}' \
  --model zai-org/AutoGLM-Phone-9B \
  --port 8000

# SGLang
python3 -m sglang.launch_server \
  --model-path zai-org/AutoGLM-Phone-9B \
  --served-model-name autoglm-phone-9b \
  --context-length 25480 \
  --mm-enable-dp-encoder \
  --mm-process-config '{"image":{"max_pixels":5000000}}' \
  --port 8000
```

部署后用检查脚本验证：

```bash
python scripts/check_deployment_cn.py --base-url http://localhost:8000/v1 --model autoglm-phone-9b
```

## 支持的操作

| 操作 | 说明 |
|------|------|
| `Launch` | 启动应用 |
| `Tap` | 点击坐标 |
| `Type` / `Type_Name` | 输入文本 |
| `Swipe` | 滑动屏幕 |
| `Back` | 返回 |
| `Home` | 回到桌面 |
| `Double Tap` | 双击 |
| `Long Press` | 长按 |
| `Wait` | 等待加载 |
| `Take_over` | 人工接管（interrupt） |

运行 `python main.py --list-apps` 查看支持的应用列表。

## 远程调试

```bash
# WiFi 连接
adb connect 192.168.1.100:5555

# 指定设备运行
python main.py --device-id 192.168.1.100:5555 --base-url http://localhost:8000/v1 "打开抖音刷视频"
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PHONE_AGENT_BASE_URL` | 模型 API 地址 | `http://localhost:8000/v1` |
| `PHONE_AGENT_MODEL` | 模型名称 | `autoglm-phone-9b` |
| `PHONE_AGENT_API_KEY` | API Key | `EMPTY` |
| `PHONE_AGENT_MAX_STEPS` | 最大步数 | `100` |
| `PHONE_AGENT_DEVICE_ID` | 设备 ID | 自动检测 |
| `PHONE_AGENT_LANG` | 语言 | `cn` |
| `PHONE_AGENT_GROUNDING_PROVIDER` | 可选 grounding provider：`locateanything` / `fake` / `off` | `off` |
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | LocateAnything-3B-4bit 模型路径 | `models/LocateAnything-3B-4bit` |

## 开发

```bash
# 安装开发依赖
.venv/bin/pip install -e ".[dev]"

# 运行测试
.venv/bin/pytest tests/ -v

# 运行 graph 测试
.venv/bin/pytest tests/graph -v

# 本地 dry-run 评测 smoke（不依赖模型和设备）
.venv/bin/python evals/run_eval.py --dry-run

# 指定 trace 输出目录
.venv/bin/python evals/run_eval.py --dry-run --trace-dir .traces/smoke
```

当前 Evaluation Harness 覆盖结构化结果、基础指标、HITL interrupt routing 计数、trace 文件关联、retry count、failure cause histogram，以及 `context_mode`、`context_strategy`、`prompt_version`、`selected_sections`、`messages_before/after`、`message_chars_before/after`、`approx_tokens_before/after`、`context_block_chars`、`context_truncated`、`failure_memory_hit_count`、`repeated_failure_count` 等 context 指标；不承诺跨进程持久 resume，完整 resume 指标将在 checkpoint/resume 阶段补齐。

```bash
.venv/bin/python evals/run_eval.py --dry-run --context-mode observe --trace-dir .traces/smoke
.venv/bin/python evals/run_eval.py --dry-run --context-mode inject --trace-dir .traces/smoke
.venv/bin/python evals/run_eval.py --dry-run --context-mode off --trace-dir .traces/smoke
```

### TraeCLI 项目配置

本仓库包含项目级 TraeCLI 配置，用于约束开发流程和持续执行编排：

| 范围 | 路径 |
|------|------|
| 全局 TraeCLI 配置 | `.trae/traecli.yaml` |
| LangGraph roadmap 与执行约束 | `.trae/rules/graph.mdc` |
| RALPLAN 共识规划协议 | `.trae/rules/ralplan.mdc` |
| Autopilot 流水线协议 | `.trae/rules/autopilot.mdc` |
| Slash commands | `.trae/commands/ralplan.md`, `.trae/commands/autopilot.md` |
| Skills | `.trae/skills/ralplan/SKILL.md`, `.trae/skills/autopilot/SKILL.md` |
| Project agents | `.trae/agents/*.md` |
| Hooks | `.trae/hooks/ralplan.py`, `.trae/hooks/autopilot.py` |

Autopilot 已完成 TraeCLI-native 编排：`ralplan -> execution -> ralph -> qa -> complete`。它复用 RALPLAN `planner` / `architect` / `critic`，并新增 `executor`、`debugger`、`test-engineer`、`designer`、`code-reviewer`、`security-reviewer` 六个项目级 stage agents；不迁移 `.omc` runtime，不修改 `.trae/traecli.yaml`，不改变 `phone_agent/` 业务运行时。涉及 prompt/context/grounding harness 的执行计划、验收矩阵与阶段状态以 `.trae/rules/graph.mdc` 为准；TraeCLI 文档和 AGENTS 约束必须同步更新。项目命令必须优先使用 `.venv/bin/python`、`.venv/bin/pytest`、`.venv/bin/pip`。

```bash
.venv/bin/pytest tests/trae/test_autopilot_hook.py -q
```

## 常见问题

**设备未找到**：`adb kill-server && adb start-server && adb devices`，检查 USB 调试和数据线。

**能打开应用但无法点击**：开启「USB 调试（安全设置）」。

**文本输入不工作**：确认 ADB Keyboard 已安装并启用。

**截图黑屏**：敏感页面（支付/银行）的正常现象，系统会自动处理。
