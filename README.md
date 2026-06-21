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
│   ├── result.py                # ActionResult
│   └── adapter.py               # JSON/tool_calls → canonical action 适配与校验
├── grounding/                   # MarkProvider、LocateAnything MLX、fake provider、bbox parser
├── adb/                         # Android 设备控制
├── config/
│   ├── apps.py                  # 应用包名映射
│   ├── prompts_zh.py / prompts_en.py  # structured prompt contract
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
    output_mode="json_schema",  # json_schema | tool_calls | auto
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

默认启用短期 context 注入模式，用于把脱敏、裁剪后的执行上下文和失败模式注入模型 Plan：

| 模式 | 行为 |
|------|------|
| `off` | 不生成新增 context 指标 |
| `observe` | 生成 state/trace/eval 指标，但不向 Plan 注入 context block |
| `inject` | 默认模式；注入脱敏、裁剪后的短期 context block |

`AgentConfig(context_mode="inject")` 为默认值，也可通过 `--context-mode` 或 `PHONE_AGENT_CONTEXT_MODE` 切换为 `observe/off`。context 字段包括 `screen_belief`、`action_outcome_summary`、`failure_memory`、`summarized_history` 与预算/截断指标；默认预算为 failure memory 最近 3 条、screen belief 摘要 300 字符、history 摘要 800 字符、context block 1500 字符。姓名、手机号、邮箱、订单号、验证码、API key/token、长 base64/JWT 等敏感文本默认脱敏，context 不绕过 HITL/confirm/takeover。

#### LangGraph-native Context Engineering Harness

Phase 14 将 prompt、context selector 与 context-window compaction 收敛为 LangGraph 原生请求构造层，不替换现有 `StateGraph` 拓扑：

| 能力 | 行为 |
|------|------|
| Prompt contract | `get_system_prompt(lang, output_mode, prompt_version)` 由 System Contract + Action Schema + Policy + Context Rules + 单一输出契约组成 |
| Prompt contract | 默认且唯一支持 `prompt_version="context_harness_v1"`；旧 text DSL prompt 不再作为回滚路径 |
| Context selector | `select_plan_context()` 输出 `context_strategy`、`selected_sections`、脱敏 context block 与计数指标 |
| Request compaction | `compact_messages_for_request()` 仅压缩传给 `model_client.request()` 的消息，不改写 `state["messages"]` |
| 图片预算 | 历史图片从模型请求中剥离，最新用户截图保留；保持 `messages_reducer` append/replace 语义 |
| 隐私指标 | trace/eval 只持久化 section IDs、消息数、字符数、近似 token、截断状态等，不持久化 raw prompt/context/image |

Context selector 与 compaction 只能影响模型请求构造和脱敏观测指标，不得修改 `action_raw`、`action_parsed`、`pending_execute`、`interrupt_result`、`action_confirmed` 或 Safety/HITL 路由字段。

### ExpectedOutcome 与动作验证

Plan 阶段支持 provider response envelope：`{"action": {...}, "expected_outcome": {...}}`。其中 `action` 继续经 adapter、grounding、validator、repair、safety gate 和 executor；`expected_outcome` 是 sibling postcondition contract，只写入 state/trace/context/verifier，绝不进入 canonical `ActionIR` 或 executor payload，也不提供执行授权。真实 `ModelClient` 会先校验 nested `action`，再保留 envelope 给 Plan 拆分；旧 plain action JSON 仍兼容。

`ExpectedOutcome` 支持 `kind`、`must_observe`、`must_not_observe`、`target_mark_id`、`target_text_hint`、`timeout_hint`、`dynamic_regions`。provider 未给出时会按动作生成保守默认：Launch 验证目标 app；Type 默认不把原始输入文本持久化到 `must_observe`，只有 provider 显式给出隐私安全的 outcome 时才做文本匹配；Tap/Double Tap/Long Press 默认保持 `generic`，避免把原本已存在的 target hint 当作成功证据；Swipe 只把内容位移作为弱确定信号；Wait 验证 loading/spinner/network error 等消失。

Reflect 阶段会基于动作后的截图/current_app 重新构建 after observation，并携带动作前/动作后的脱敏 observation 摘要运行 deterministic verifier；高置信 deterministic success/failure 会直接映射到结构化 reflection，只有 unknown/低置信才把 verifier signal、ExpectedOutcome、before/after observation summary 与当前截图作为 isolated verifier request 交给模型判断。该请求不追加到 `state["messages"]`，也不参与 request compaction 的持久状态。`screen_changed` 已降级为 `weak_signals.screen_changed`，不能单独证明 Tap/Type/搜索/打开视频成功。广告、banner、推荐流、热词、计数器等动态区域默认视为噪声；搜索框/输入框类 `input_focused` 后置条件还会参考 focused/editable/keyboard/top activity 等只读信号，trace 只记录 `verifier_evidence` 中 stubbed/redacted matched/missing postconditions、weak signals 与 redacted summaries。

### Model Output Adapter

默认使用结构化 JSON 输出，同时支持已聚合 OpenAI `tool_calls`；旧 text DSL 不再作为动作执行协议：

| 模式 | 行为 |
|------|------|
| `json_schema` | 模型输出 JSON，经 adapter 映射为内部 canonical action |
| `tool_calls` | 聚合 streaming tool_calls delta 后，经 adapter 映射为内部 canonical action |
| `auto` | 自动识别结构化 JSON / tool_calls；不回退旧 text DSL 执行 |

所有格式最终都进入统一执行路径：adapter 只生成 canonical action，不直接调用工具；真实执行仍由 `execute_node -> dispatch_tool()` 完成。JSON/tool_calls 仅允许白名单 action 与字段，坐标保持 0-1000 相对值并在 tool 层转换为绝对像素；未知 action、缺字段、越界坐标、非 literal/危险结构均 fail-closed，并按 `parse|adapter|validation` 等错误层记录，不会伪装成成功 `finish`，也不会绕过 confirm/takeover HITL。

解析观测字段会进入 trace/eval 相关链路，包括 configured mode、detected format、adapter used、parse success/error code；`parse_error`、截图、API key、任务文本与隐私文本默认脱敏。

### LocateAnything 本地 Mark Provider（可选）

> 完整架构文档见 [Grounding Architecture](docs/grounding-architecture.html)

Open-AutoGLM 支持把主 VLM 的语义/意图与本地视觉定位拆开：Observation 阶段先由静态 marks、设备 marks、LocateAnything/Fake 等 `MarkProvider` 生成当前屏幕的 `MarkRegistry`；主 VLM 在 JSON/tool_calls 模式只通过 `target_mark_id` 引用屏幕目标；本地 harness 再把 mark 编译为 canonical `ActionIR -> Validator -> Repair -> Validator -> Safety/HITL -> Executor`。

```bash
# 默认测试不需要 MLX；真实 LocateAnything 需要 Apple Silicon + Metal + mlx-vlm
# 当前仓库没有 pyproject optional extra，mlx-vlm 不在默认 requirements 中。
# 如需运行真实 provider/benchmark，请在本机 MLX 环境中安装兼容版本的 mlx-vlm。

# 可选：启用本地 LocateAnything mark provider
export PHONE_AGENT_GROUNDING_PROVIDER=locateanything
export PHONE_AGENT_LOCATEANYTHING_MODEL=models/LocateAnything-3B-4bit
# 可选：覆盖 LocateAnything 输入图最长边；默认 960，1280 可回滚到更高质量/更慢路径
export PHONE_AGENT_LOCATEANYTHING_MAX_SIZE=960

# 可选：优先使用 Android UiAutomator accessibility tree，失败时再调用 LocateAnything
export PHONE_AGENT_GROUNDING_PROVIDER=hybrid
export PHONE_AGENT_ACCESSIBILITY_MAX_MARKS=80
# 可选：直接把 accessibility tree marks 注入 MarkRegistry，不启用小模型 provider
export PHONE_AGENT_ACCESSIBILITY_MARKS=true
```

LocateAnything provider 会先读取当前完整截图，再按最长边 `max_size` 等比例缩小后送入模型；默认 `max_size=960`，这是基于本地 benchmark 在速度和 bbox 一致性之间的折中。运行时可通过 config `locateanything_max_size`（优先）或 `grounding_max_size` 覆盖，也可通过环境变量 `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE`（优先）或 `PHONE_AGENT_GROUNDING_MAX_SIZE` 灰度/回滚；非法或非正整数会回落默认值。

Android accessibility tree 通过 `adb exec-out uiautomator dump /dev/tty` 获取当前 UI XML，解析可交互节点的 `bounds/text/content-desc/resource-id/class/clickable/focusable/enabled` 等字段，并统一转换为 0-1000 MarkRegistry marks。`PHONE_AGENT_GROUNDING_PROVIDER=hybrid` 是推荐的省时路径：先尝试 accessibility tree；如果 tree marks 与 provider hint 没有弱匹配、树为空、不可用或没有候选 marks，会继续 fallback 到 LocateAnything 并合并候选。`PHONE_AGENT_ACCESSIBILITY_MARKS=true` 则把 accessibility marks 作为设备 base marks 注入；如果它与 `hybrid` 同时开启，direct/base accessibility mark 注入会被跳过，accessibility marks 由 hybrid provider 链统一生成和 gating，避免绕过 hint-aware fallback。

LocateAnything prompt 必须保持官方/库侧 chat template：代码通过 `mlx_vlm.prompt_utils.apply_chat_template(..., num_images=1)` 生成最终 prompt，fallback 仅为旧版 `mlx-vlm` 保留 `<image-0>` 前缀。默认 instruction 保持短句：`Locate the region that matches the following description: ...`。可选 `PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` / config `locateanything_context_max_chars` 只允许追加一行 bounded `Context:`，默认 `0` 表示不注入额外 context，避免小模型被长 prompt 干扰。

安全边界：屏幕目标点击类动作的唯一可执行 IntentIR target 是 `target_mark_id`。`target_text_hint` / 目标描述只能作为受控、bounded 的本地 MarkProvider hint，不能直接生成 ActionIR；本地 LocateAnything/Fake 可在内存中使用 raw hint 做 query-conditioned grounding，但 raw hint 不写入 trace、checkpoint、prompt marks block、eval JSON 或报告。LocateAnything/Fake/OCR/UIAutomator/SoM 只能生成 marks；未知 mark、缺失 registry、provider 缺失、stale/hash mismatch、低置信、bad bbox、多候选歧义等会 fail-closed 为 `error_layer=grounding`，不会回退为主 VLM 直接坐标 Tap。trace/eval 只记录 provider、mark id、bbox/center、screen/hash、latency、failure code、candidate_count 与脱敏 hint summary，不记录原始截图或 raw target text。若未来接入远程 grounding provider，raw hint 必须显式 opt-in，否则默认使用脱敏 hint。

### Grounding Benchmark

正式 benchmark 代码位于 `bench/grounding/`，用于评估 `screenshot + target description -> bbox` 的 provider 能力。benchmark 仍使用 description-to-bbox 任务形态；runtime 不把 benchmark target description 当执行协议，而是把 provider 输出转成 mark candidates 后再由 VLM 选择 `target_mark_id`。

核心文件：

| 文件 | 作用 |
|------|------|
| `bench/grounding/datasets.py` | post-training JSONL 读取、0-1 bbox 转 0-1000 bbox、clean/trusted 过滤、balanced sampling |
| `bench/grounding/scoring.py` | CI 可运行的纯 Python scoring primitives |
| `bench/grounding/reporting.py` | prediction enrichment、summary、按 target type / area bucket 分组 |
| `bench/grounding/run_locateanything.py` | LocateAnything 正式 benchmark runner |
| `bench/grounding/score_predictions.py` | 固定 manifest 后离线复评 predictions |

推荐先固定 manifest，再用同一 manifest 比较不同模型。当前 LocateAnything 主 suite：

```bash
.venv/bin/python -m bench.grounding.run_locateanything \
  --post-training-data /Users/bytedance/post-training/data/grounding_os_atlas_aw_mobile/raw.jsonl \
  --model /Users/bytedance/Open-AutoGLM/models/LocateAnything-3B-4bit \
  --limit 1000 \
  --seed 46 \
  --sampling balanced \
  --per-type-cap 120 \
  --per-area-cap 400 \
  --clean \
  --exclude-weak-types \
  --trusted-types-only \
  --min-area-ratio 0.0005 \
  --max-size 960 \
  --manifest-output bench_output/grounding/aw_mobile_clean_trusted_1000_manifest.json \
  --output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_predictions.jsonl \
  --summary-output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_summary.json
```

MLX/Metal 需要可访问 GPU 的 macOS arm64 环境。若在沙箱、headless 或虚拟化环境中出现 `No Metal device available`，需要在非沙箱 shell 中运行同一命令。`bench_output/` 是本地结果目录，默认不作为源码提交对象。

离线复评：

```bash
.venv/bin/python -m bench.grounding.score_predictions \
  --manifest bench_output/grounding/aw_mobile_clean_trusted_1000_manifest.json \
  --predictions bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_predictions.jsonl \
  --output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_scored.jsonl \
  --summary-output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_rescore_summary.json
```

关键指标包括 `parse_success_rate`、`success_rate`、`center_hit_rate`、`acc_iou_0_3`、`acc_iou_0_5`、`mean_iou`、`required_recall`、`precision`、`latency_ms_p50/p95`，summary 会同时输出 `by_target_type`、`by_area_bucket` 与 `parse_errors`。

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
| `PHONE_AGENT_OUTPUT_MODE` | 模型输出模式：`json_schema` / `tool_calls` / `auto` | `json_schema` |
| `PHONE_AGENT_CONTEXT_MODE` | Context harness 模式：`inject` / `observe` / `off` | `inject` |
| `PHONE_AGENT_MAX_STEPS` | 最大步数 | `100` |
| `PHONE_AGENT_DEVICE_ID` | 设备 ID | 自动检测 |
| `PHONE_AGENT_LANG` | 语言 | `cn` |
| `PHONE_AGENT_GROUNDING_PROVIDER` | grounding provider：`hybrid` / `locateanything` / `accessibility` / `fake` / `off`（别名：`accessibility_tree`/`uiautomator`→accessibility，`locateanything_mlx`/`mlx`→locateanything，`accessibility_locateanything`/`uiautomator_locateanything`→hybrid） | `hybrid` |
| `PHONE_AGENT_ACCESSIBILITY_MARKS` | 是否把 Android UiAutomator tree 作为设备 base marks 注入 MarkRegistry | `false` |
| `PHONE_AGENT_ACCESSIBILITY_TIMEOUT` | `uiautomator dump` 超时时间（秒） | `3.0` |
| `PHONE_AGENT_ACCESSIBILITY_MAX_MARKS` | 每屏最多保留的 accessibility marks 数量 | `80` |
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | LocateAnything-3B-4bit 模型路径 | `models/LocateAnything-3B-4bit` |
| `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE` | LocateAnything 输入图最长边；provider 专属配置，优先于通用 grounding max size | `960` |
| `PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` | LocateAnything prompt 的可选短 context 字符预算；0 表示关闭 | `0` |
| `PHONE_AGENT_GROUNDING_MAX_SIZE` | 通用 grounding 输入图最长边 fallback；当前仅 LocateAnything factory 消费 | `960` |

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

Autopilot 已完成 TraeCLI-native 编排：`ralplan -> execution -> ralph -> qa -> complete`。它复用 RALPLAN `planner` / `architect` / `critic`，并新增 `executor`、`debugger`、`test-engineer`、`designer`、`code-reviewer`、`security-reviewer` 六个项目级 stage agents；不迁移 `.omc` runtime，不改变 `phone_agent/` 业务运行时。`.trae/traecli.yaml` 只承载项目级 TraeCLI 行为约束、hook 与 MCP 配置；业务执行协议以 `json_schema|tool_calls|auto` 为准，不再包含旧 text DSL 回滚。涉及 prompt/context/grounding harness 的执行计划、验收矩阵与阶段状态以 `.trae/rules/graph.mdc` 为准；TraeCLI 规则、README、docs 与 AGENTS 约束必须同步更新。项目命令必须优先使用 `.venv/bin/python`、`.venv/bin/pytest`、`.venv/bin/pip`。

```bash
.venv/bin/pytest tests -q
```

## 常见问题

**设备未找到**：`adb kill-server && adb start-server && adb devices`，检查 USB 调试和数据线。

**能打开应用但无法点击**：开启「USB 调试（安全设置）」。

**文本输入不工作**：确认 ADB Keyboard 已安装并启用。

**截图无效或黑屏**：敏感页面（支付/银行）可能触发 Android secure screenshot block。运行时会 fail-closed，返回 `error_layer="grounding"`、`error_code="secure_screenshot_blocked"` 或 `screenshot_unavailable`，不会把黑图继续发给模型。
