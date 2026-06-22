# Future Roadmap

> 本文档记录 LangGraph roadmap 的阶段进展与未来方向。详细执行约束以 `.trae/rules/graph.mdc` 为准。

---

## 当前状态

- **Phase 1-14 + Hybrid Accessibility Grounding Hardening**: ✅ 已完成当前 graph roadmap 中已批准的主要实现范围；Phase 11A/11B/11C 完成 Context & Observability Harness，Phase 12A/12B/12C 完成结构化模型输出适配，Phase 13A-13E 完成 Canonical Action IR & Safety Pipeline 阶梯架构，Phase 14A-14E 完成 LangGraph-native Context Engineering Harness；legacy text DSL 删除、mark binding、多候选 grounding fail-closed、bounded context window、hybrid/accessibility 诊断字段已落地。最新审查仍要求补齐 hybrid executable mark 过滤、diagnostic code 字段 strict sanitizer、child `parse_summary` 透传、accessibility failure code taxonomy；long-term memory 与 LangChain provider abstraction 仍待另行规划
- **测试**: 已恢复可执行 graph/actions/evals 回归测试与安装门禁；当前本地门禁为 `.venv/bin/pytest tests -q` 全绿
- **架构**: LangGraph Plan-Execute-Reflect StateGraph
- **图拓扑**: `plan → execute → [confirm|takeover|reflect|replan|end]`
- **结构化 API**: 已提供 `PhoneAgent.run_structured()` / `RunResult`，`run()` 继续保持字符串返回兼容
- **可观测性**: 已提供默认本地 JSONL trace，`RunResult` / eval JSON 可通过 `trace_id` 与 `trace_path` 关联 trace 文件；默认脱敏敏感截图、prompt/API key 与隐私文本
- **短期 Context Harness**: 已支持 `context_mode=off|observe|inject`，默认 `inject`；记录 `screen_belief`、`action_outcome_summary`、`failure_memory`、`summarized_history`、`short_term_memory`、`action_ledger` 与 context 指标；inject 模式通过单一 `build_plan_context_block()` 从 raw state 字段重建并 regex 替换敏感文本后注入 Plan；state 写入路径只做 regex 替换、不 stub，stub 策略仅在 `phone_agent/checkpoint/serde.py::RedactingSerializer` 的 checkpoint egress 触发；request-only compaction 不改写 `state["messages"]`，保留最新截图并裁剪旧请求文本
- **策略反思**: 已支持结构化 `reflection_verdict`、`failure_cause`、`suggested_strategy`，下一轮 plan 可读取失败原因和建议策略
- **评测地基**: 已提供 `evals/run_eval.py --dry-run` smoke harness，以及 `bench/grounding/` LocateAnything benchmark 体系；当前支持 post-training raw JSONL 转 manifest、固定 suite、prediction JSONL、summary JSON、离线复评与 target type / area bucket 分组指标
- **收益验证**: eval 已输出 `context_mode`、`context_strategy`、`prompt_version`、`selected_sections`、`messages_before/after`、`message_chars_before/after`、`approx_tokens_before/after`、`context_block_chars`、`context_truncated`、`failure_memory_hit_count`、`repeated_failure_count`，支持 off/observe/inject 对比
- **输出适配**: `ModelConfig.output_mode=json_schema|tool_calls|auto`；旧 text DSL 不再作为动作执行协议；JSON、已聚合 OpenAI `tool_calls` 与 IntentIR 统一归一到 canonical action，parse/adapter/validation failure fail-closed，不绕过 HITL
- **本地 Grounding**: LocateAnything-3B-4bit (MLX) 与 Android UiAutomator accessibility tree 均已收敛为 MarkRegistry mark 生成层；默认 `PHONE_AGENT_GROUNDING_PROVIDER=hybrid` 先用 accessibility tree 的结构化 bounds/text/class/clickable 信息，失败时再 fallback 到 LocateAnything；UiAutomator provider 产出 `structure_kind=accessibility` 的 `ScreenStructure` sidecar，LocateAnything 可在 `PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE=target|screen` 显式开启时产出 `structure_kind=visual` 的视觉 sidecar。Plan 基于 composite `screen_structures + MarkRegistry` 构建 observation-local `ObjectRegistry`，允许主 VLM 在 Screen Objects block 中选择 `target_object_id` 或 `object_role`+`ordinal`/strict `object_filter`；这些 selector 只在 grounding 层编译为唯一 atomic `target_mark_id` 后才能执行。Hybrid accessibility hardening 已补齐 whitespace/signed bounds、控制字符清洗、XML parse fail-closed summary、`fallback_chain` taxonomy、`hybrid_factory` 降级可观测和 plan/reflect trace summary；但默认 hybrid wrapper 当前仍会把 hint-mismatched tree marks 放进最终 executable marks，且 child `parse_summary` 尚未透传到顶层 hybrid result。截图无效或 secure screenshot blocked 会 fail-closed，不把黑图继续发给模型；所有结果仍进入 canonical ActionIR/Safety/HITL/Executor；LocateAnything 默认输入图最长边为 `max_size=960`，默认不注入额外 context，必须经 `apply_chat_template(..., num_images=1)`，可通过 bounded `locateanything_context_max_chars` 灰度短 context；multi-box 在默认 `off` 模式下不得 first-box 执行，只能 fail-closed 为歧义，结构模式下也必须经 visual object eligibility 再执行

---

## 已完成 MVP: LocateAnything Local Mark Provider

**目标**: 将主 VLM 的语义规划与 GUI 元素定位分层，使用 LocateAnything-3B-4bit (MLX) 在本地把受控 hints 转为 screen-bound mark candidates，并保持现有 harness engineering 安全边界。

**当前状态**: LAG-1 至 LAG-4 已落地。默认 CI 和单测使用 fake provider，不依赖 MLX 或真实模型；真实 LocateAnything 需要本机 Apple Silicon + Metal + `mlx-vlm` 环境，`mlx-vlm` 不进入默认 `requirements.txt`。

**已落地方案**:
- `phone_agent/grounding/`: 提供 `MarkProvider` contract、`ScreenBinding`、`MarkProviderResult`、fake provider、LocateAnything MLX lazy provider、`<box>` parser 与 provider factory；LocateAnything provider 会读取完整截图并按 `max_size` 等比例缩小后推理，默认 `960`。
- `phone_agent/actions/adapter.py`: `IntentIR` 的屏幕目标点击类动作必须带 `target_mark_id` 或有效 observation-local object selector；`target_text_hint`/`target_role` 仅保留为非执行 hint metadata；拒绝 provider/backend/命令类危险字段。
- `phone_agent/actions/grounding.py`: 仅将 `target_mark_id` 通过 MarkRegistry 编译为 canonical Tap/Double Tap/Long Press ActionIR；description hints 不再直接走 provider 或生成 ActionIR。
- `phone_agent/graph/nodes/plan.py`: 在 Plan 请求前构建 MarkRegistry 并注入 marks block；provider 失败或无 marks 只记录 mark_provider_observation，不回退为主 VLM 直接坐标。
- `phone_agent/graph/context.py` / `trace.py` / `evals/run_eval.py`: 新增 `grounding_observation` section、grounding latency/failure histogram 与 target hint 默认脱敏。
- `bench/grounding/`: 新增 LocateAnything benchmark 体系，支持 post-training raw JSONL 数据接入、0-1 bbox 转 0-1000 bbox、clean/trusted filtering、random/balanced sampling、统一 scoring/reporting、prediction JSONL 与 summary JSON。

**安全边界**:
- 主 VLM 不选择 provider/backend，不输出 ADB/shell/绝对像素；屏幕目标点击类动作只引用 `target_mark_id`；adapter/validator 对危险字段 fail-closed。
- Provider unavailable、timeout、bad bbox、low confidence、stale screen、hash mismatch、missing hint 等只影响 mark generation 或 mark lookup，不会直接执行设备动作。
- bbox/center 保持 0-1000 相对坐标；绝对像素转换仍只在 tool/backend 层。
- trace/eval 只记录 screen_id、raw screenshot hash、provider input hash、bbox/center、latency、failure code 与脱敏 target summary；不记录 raw screenshot/raw target text。

**验证命令**:

```bash
.venv/bin/pytest tests/actions tests/graph tests/evals -q
.venv/bin/pytest tests -q
```

**启用真实 provider**:

```bash
PHONE_AGENT_GROUNDING_PROVIDER=locateanything \
PHONE_AGENT_LOCATEANYTHING_MODEL=models/LocateAnything-3B-4bit \
PHONE_AGENT_LOCATEANYTHING_MAX_SIZE=960 \
.venv/bin/python main.py --output-mode json_schema "打开设置"
```

`PHONE_AGENT_LOCATEANYTHING_MAX_SIZE` 是 provider 专属覆盖项，优先于通用 `PHONE_AGENT_GROUNDING_MAX_SIZE`；运行时 config 中 `locateanything_max_size` 优先于 `grounding_max_size`。非法或非正整数回落默认 `960`。本地 preprocess benchmark 显示 `960` 相比 `1280` 在当前截图集上保持主要 bbox 一致性，同时显著降低延迟；naive 三段切分/并发切分未作为默认路径。

**Benchmark 命令**:

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

固定 manifest 后用 `bench.grounding.score_predictions` 离线复评 predictions。正式比较不同 grounding 模型时，必须复用同一 manifest，并同时报告 clickability 指标（`center_hit_rate`）与 bbox 指标（`acc_iou_0_3`、`acc_iou_0_5`、`mean_iou`）。MLX/Metal 在沙箱或 headless 环境中可能不可用，真实 LocateAnything benchmark 需要在可访问 Metal 的本机 shell 运行。

---

## 已完成 MVP: Accessibility Tree + LocateAnything Hybrid Grounding

**目标**: 用 Android accessibility tree 作为低延迟、结构化的首选 mark 来源；当 tree 为空、不可用、无候选，或 tree marks 与 provider hint 没有弱匹配时继续调用 LocateAnything，降低平均 grounding 延迟，同时保持主 VLM 只能选择 `target_mark_id` 的执行边界。

**已落地方案**:
- `phone_agent/grounding/accessibility.py`: 新增 UiAutomator XML parser 与 `AccessibilityTreeProvider`，解析 `bounds/text/content-desc/resource-id/class/clickable/focusable/enabled`，过滤不可见/不可用节点，输出 0-1000 `MarkCandidate`。parser 支持 signed/whitespace bounds，清洗非法 XML 控制字符；未转义 ampersand、截断 hierarchy 或结构错乱以 `accessibility_xml_parse_error` fail-closed，不做语义修复。
- `phone_agent/adb/device.py`: 新增 `dump_uiautomator_xml()` 与 `get_screen_marks()`；默认命令为 `adb exec-out uiautomator dump /dev/tty`，避免写入设备临时文件。
- `phone_agent/device_factory.py` 与 `phone_agent/graph/nodes/plan.py`: 支持 `accessibility_marks` 直接把 tree marks 注入 MarkRegistry；并向 provider factory 注入 `accessibility_tree_dump`，供 hybrid fallback 使用。
- `phone_agent/grounding/fallback.py` / `factory.py`: 新增 ordered fallback provider；`PHONE_AGENT_GROUNDING_PROVIDER=hybrid` 先尝试 `accessibility_tree`，只有 tree marks 与 hint 弱匹配或无 hint 时才停止；不匹配、失败或无候选时继续调用 `locateanything_mlx`。缺少 accessibility dump callback 或显式跳过 tree child 时，provider 仍可降级到 LocateAnything，但 `hybrid_factory` 与 synthetic `fallback_chain` skip row 会记录 `accessibility_dump_callback_missing` / `skip_accessibility_provider`。当前审查发现：hint-mismatched tree marks 仍会被合并进最终 executable `marks`，后续应改为只保留在 diagnostic `candidates` / structures 中。
- `phone_agent/grounding/locateanything.py`: Prompt 保持官方 `apply_chat_template(..., num_images=1)` 路径；默认 instruction 极短；`locateanything_context_max_chars` 仅允许追加 bounded 单行 `Context:`，默认 `0` 关闭。

**配置**:

```bash
# 推荐：tree 优先，tree 不覆盖 hint 时调用 LocateAnything
PHONE_AGENT_GROUNDING_PROVIDER=hybrid \
PHONE_AGENT_ACCESSIBILITY_MAX_MARKS=80 \
PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS=0 \
.venv/bin/python main.py --output-mode json_schema "打开设置"

# 只把 tree marks 注入 MarkRegistry，不启用小模型 provider
PHONE_AGENT_ACCESSIBILITY_MARKS=true \
.venv/bin/python main.py --output-mode json_schema "打开设置"
```

**核心不变量**:
- Accessibility tree 与 LocateAnything 都只是 mark provider；它们不能直接生成可执行 ActionIR。
- Tree bounds 必须转换为 0-1000；绝对像素转换仍只在 tool/backend 层。
- Tree 失败、XML parse 失败、无候选 marks、LocateAnything 失败或歧义，都不能 fallback 为主 VLM raw coordinate tap。
- LocateAnything context 默认关闭；开启时只能通过 `locateanything_context_max_chars` 注入短 context，不能构造长 prompt 或绕过官方 chat template。
- 新增诊断字段只允许 code/count/bool/hash/length：`parse_summary`、`fallback_chain`、`hybrid_factory`、`screen_structure_summary` 与 `object_registry_summary` 不得包含 raw XML、raw hint、raw OCR 或隐私文本。
- `fallback_chain` 固定保留 provider、success、failure_code、mark/candidate/structure count、usable、skip_reason、latency；当所有 provider 有 marks 但不匹配 hint 时统一 fail-closed 为 `grounding_no_usable_candidate`。
- 当前未闭环项：不匹配 hint 的 provider marks 不应进入最终 executable `marks`；diagnostic code 字段需要 strict enum/safe-identifier sanitizer；default hybrid result 需要汇总 child provider 的 safe `parse_summary`；新增 accessibility failure codes 需要同步 Plan grounding taxonomy。

**验证命令**:

```bash
.venv/bin/pytest tests/actions/test_grounding_provider.py tests/graph/test_state.py tests/graph/test_plan_reflect.py -q
```

---

## 已完成 MVP: Structured Object Grounding And Verification

**目标**: 在不破坏 atomic `target_mark_id` 执行边界的前提下，为动态列表、搜索结果、视频 feed 等页面补充结构化 object/list/ordinal 语义，解决 flat marks 难以表达“第一个视频/第二个结果”的问题。

**已落地方案**:
- `phone_agent/grounding/accessibility.py`: UiAutomator XML 解析 signed/negative bounds，并输出 `MarkCandidate` 与 trace-safe `ScreenStructure`；仅依赖真实 dump 支持的 `bounds/text/content-desc/resource-id/class/clickable/focusable/focused/checkable/checked/scrollable/enabled/visible-to-user` 字段。
- `phone_agent/graph/objects.py`: 新增 `StructureNode`、`ScreenStructure`、`ScreenObject`、`ObjectRegistry`、prompt block、object set version、topology digest 与 selected-object hash/stub evidence。
- `phone_agent/graph/observation.py` / `plan.py`: `Observation` 构建 composite `screen_structures`、legacy `screen_structure` 与 `object_registry` 一等 sidecar；Plan prompt 固定先注入 hash-only Screen Objects block，再注入 Screen Marks block；state/trace 输出 `screen_structure_summary`（structure count、kind distribution、merge order、composite digest）、`object_registry_summary`（source/tier/eligible counts）、`object_registry_binding`、`object_set_version`、`structure_topology_digest` 与 `object_trace_summary`，不持久化 raw object title/text。
- `phone_agent/grounding/locateanything.py` / `factory.py`: 新增 opt-in `locateanything_structure_mode=off|target|screen`，默认 `off`。`target` 为当前 hint bbox 生成 visual sidecar；`screen` 使用 bounded category prompts，并受 `locateanything_max_visual_candidates`、`locateanything_visual_category_budget`、`locateanything_max_structure_calls` 限制。显式 config/AgentConfig 非法值报错；env 非法值回落 `off` 并在 provider metadata 记录 `invalid_structure_mode`。
- `phone_agent/graph/objects.py` / `actions/grounding.py`: `ScreenObject` 增加 `source_kind/source_provider/confidence_tier/executable_selector/selector_confidence/selector_reasons/sensitivity_tags`。visual object 在 prompt 标注 `source=visual tier=weak eligible=true|false`，resolver 对 `executable_selector` 做硬校验；低置信、多候选、重叠或未授权 visual selector fail-closed 为 `visual_object_not_executable` / `visual_object_ambiguous`，不会回退 raw bbox tap。
- `phone_agent/actions/adapter.py` / `selectors.py`: JSON/tool_calls/auto 接受 allowlisted selector fields：`target_object_id`、`ordinal`、`object_role`、`object_filter`。adapter 只产出非执行 IntentIR，不读取 ObjectRegistry、不解析 ordinal、不生成 compiled mark。
- `phone_agent/actions/grounding.py`: resolver 顺序为 ObjectRegistry binding/version -> object/ordinal 唯一 primary mark -> MarkRegistry binding/confidence/semantics -> sensitivity merge -> canonical ActionIR。object selector 成功后 canonical ActionIR 只含 action + element/message，不保留 object selector 字段。
- `phone_agent/graph/expected_outcome.py`: selected-object evidence 只作为 verifier-only hash/stub 字段进入 ExpectedOutcome，不进入 ActionIR、Validator、Safety Gate、Executor 或 pending_execute。

**Prompt 与 selector 限制**:
- Screen Objects block 排序为 screen summary、limits、lists、top visible objects/cards、selector rules；atomic marks 作为独立 Screen Marks block 紧随其后。Screen Objects block 只展示 object type/role/list/ordinal、primary mark 和 hash/lineage evidence，不展示 raw title/text；默认 lists 5、objects 30、总 block 4000 chars。
- `object_id`、`list_id`、`ordinal` 仅在当前 observation 内有效；reobserve 后必须重新选择或重新验证。
- `object_filter` v1 只能是 strict flat JSON object，key 仅限 `object_type`、`role`、`source`、`list_id`、`title_hash_prefix`、`text_hash_prefix`、`resource_id_hash_prefix`、`lineage_hash_prefix`；value 只能是 1-64 字符 string，hash prefix 为 6-16 位 hex；禁止 raw title/text、regex、array、nested object、provider/backend/device 字段。

**Failure codes 与 HITL**:
- object selector failure codes: `object_registry_missing`、`object_stale`、`unknown_object`、`ordinal_out_of_range`、`object_ambiguous`、`object_without_mark`、`mark_stale`、`mark_low_confidence`。
- 这些 failure codes 只表示 grounding fail-closed；不会生成 raw coordinate Tap，不进入 `pending_execute`。
- 敏感 object evidence 不是 GroundingError：payment/privacy 返回 canonical Tap + confirmation message；login/OTP 返回 canonical `Take_over`，继续由 Safety Gate 路由到 `confirm_node` / `takeover_node`。

**验证命令**:

```bash
.venv/bin/pytest tests/actions/test_adapter.py tests/actions/test_grounding_provider.py tests/graph/test_objects.py tests/graph/test_plan_reflect.py::test_plan_node_includes_object_registry_sidecars_and_prompt tests/model/test_client.py -q
.venv/bin/pytest tests/actions tests/model tests/graph -q
.venv/bin/pytest tests -q
```

真实 Bilibili 实机 smoke 仍依赖可用 Android 设备与测试账号，不作为无设备 CI 的硬阻塞。

---

## 已完成 MVP: LangGraph-native Context Engineering Harness (Phase 14)

**目标**: 在保留现有 LangGraph `StateGraph` 的前提下，将 prompt contract、context selector、request-only compaction、trace/eval 指标收敛为可测试、可回滚、隐私安全的请求构造层。

**当前状态**: Phase 14A-14E 已落地并在后续 hardening 中收敛；后续引入 consumer-aware 脱敏重构：state 写入路径只 regex 替换、不 stub，`build_plan_context_block()` 收敛为 inject 模式唯一 builder，从 raw state 字段重建；stub 策略仅在 `phone_agent/checkpoint/serde.py::RedactingSerializer` 的 checkpoint egress 触发；`sanitize_context_payload()` 接受 `consumer=` 参数，`inject: bool` 保留为向后兼容别名。默认 `context_mode="inject"` 会注入脱敏、裁剪后的短期 context；可显式切到 `observe/off`；`prompt_version` 当前仅支持 `context_harness_v1`，旧 text DSL prompt 回滚路径已删除。

**已落地方案**:
- `phone_agent/config/prompts_zh.py` / `prompts_en.py`: 将 prompt 拆为 System Contract、Action Schema、Task Policies、Context Usage Rules 与单一 Output Contract，覆盖 `json_schema|tool_calls|auto`。
- `phone_agent/config/__init__.py`: 保留 `PROMPT_VERSION="context_harness_v1"`、`get_prompt_version()` 与 `get_system_prompt(..., prompt_version=...)`；不再支持 `LEGACY_PROMPT_VERSION` 或 `legacy_text_dsl`。
- `phone_agent/graph/context.py`: 新增 `ContextSelectionResult`、`select_plan_context()`、`compact_messages_for_request()`，输出 section IDs、策略标签和消息/字符/近似 token 计数；`sanitize_context_payload(consumer=...)` 按 `CONSUMER_POLICY` 选择 regex-only 或 stub；`build_plan_context_block()` 是 inject 模式唯一 builder，从 `reflection`、`action_parsed`、`action_result`、`screen_belief`、`failure_memory`、`summarized_history`、`gui_memory`、`grounding_observation` 等 raw 字段重建并 regex 替换。
- `phone_agent/checkpoint/serde.py`: 新增 `RedactingSerializer(inner=...)`，在 `dumps` / `dumps_typed` 时调用 `sanitize_context_payload(consumer="checkpoint")`，让未来接入 `SqliteSaver`/`PostgresSaver` 时 checkpoint 自动 stub `PRIVATE_CONTEXT_TEXT_KEYS` 对应字段、regex 替换其余字符串；`loads` / `loads_typed` 透传。
- `phone_agent/graph/nodes/plan.py`: 在调用 `model_client.request()` 前执行 context selection 与 request-only compaction；不改写 `state["messages"]`；reflect 上下文通过 `_build_reflection_context(consumer="inject")` 构造。
- `phone_agent/graph/nodes/reflect.py`: reflect prompt 使用 `sanitize_context_payload(consumer="reflect_prompt")` 对 `action_parsed` / `action_result` 做 regex-only 替换。
- `phone_agent/graph/nodes/execute.py`: gesture trace 使用 `sanitize_context_payload(consumer="trace_payload")`。
- `phone_agent/graph/marks.py`: mark `text_summary` 走 `sanitize_context_payload(consumer="checkpoint")` 进行 stub，避免任意屏幕文本经 `mark_registry.prompt_block()` 泄漏进 Plan prompt。
- `phone_agent/agent.py` / `evals/run_eval.py`: `RunResult` 与 eval JSON 输出 `context_strategy`、`prompt_version`、`selected_sections`、messages/chars/tokens 指标。
- `phone_agent/graph/trace.py`: trace 持久化保持独立 `sanitize_for_trace` 策略；raw prompt、raw context、截图、任务文本与隐私文本不落盘。

**核心不变量**:
- 不迁移到 LangChain Agent，不改变 `plan → execute → reflect` 图拓扑。
- runtime `context_mode` 仅限 `off|observe|inject`；`auto` 仍只属于 `output_mode`。
- selector/compaction 只影响模型请求构造和脱敏指标，不得修改 Action IR、HITL、pending/interrupt 或 safety route 字段。
- request compaction 只压缩传给模型的 `request_messages`；历史图片剥离，最新截图保留；`messages_reducer` 语义不变。
- prompt schema/provider tool specs 仅是格式契约，真实执行仍是 Adapter → Validator → Safety Gate → Executor。

**验证命令**:

```bash
.venv/bin/pytest tests/model tests/actions tests/graph tests/evals -v
.venv/bin/pytest tests -q
.venv/bin/python evals/run_eval.py --dry-run --context-mode off --trace-dir .traces/smoke
.venv/bin/python evals/run_eval.py --dry-run --context-mode observe --trace-dir .traces/smoke
.venv/bin/python evals/run_eval.py --dry-run --context-mode inject --trace-dir .traces/smoke
```

**明确非目标**: 不引入长期记忆、向量库、自动 `context_mode=auto`、跨进程 resume 或 LangChain Agent 迁移。后续若做自动 context selection，必须基于 off/observe/inject 指标收益另行规划。

---

## 已完成 MVP: Model Output Adapter

**目标**: 兼容不同 OpenAI-compatible provider 的结构化输出格式，在不替换 LangGraph 状态机、不扩大设备执行面的前提下，将 JSON、IntentIR 与 OpenAI `tool_calls` 安全映射到内部 canonical action。

**当前状态**: Phase 12A/12B/12C 已落地并在后续 hardening 中收敛。默认 `output_mode="json_schema"`；Python API 的 `ModelConfig(output_mode=...)` 仅支持 `json_schema`、`tool_calls` 或 `auto`。

**已落地方案**:
- `phone_agent/model/client.py`: response normalizer、Markdown code fence/空白清理、`output_mode`、streaming `tool_calls` delta 聚合、parse metadata；旧 text DSL 响应被拒绝。
- `phone_agent/actions/adapter.py`: provider-facing JSON / 已聚合 `tool_calls` 到 canonical action 的白名单 adapter，提供 `invalid_json`、`unknown_action`、`missing_field`、`unsafe_value`、`unsupported_tool_call` 等稳定错误码。
- `phone_agent/actions/result.py`: `ActionResult` 独立为执行结果类型；旧 `phone_agent/actions/handler.py` 与 `parse_action()` / `do()` / `finish()` text DSL helper 已删除。
- `plan_node` / `execute_node`: parse/adapter/validation/grounding/execution failure 返回 `action_parsed=None` 或 terminal failed `ActionResult`，不会包装成成功 `finish`，也不会 dispatch 未验证 tool。
- `trace`: 记录 configured mode、detected format、adapter used、parse success/error code；`parse_error` 与隐私文本默认脱敏。

**Canonical action schema**:
- do: `{"_metadata":"do","action":"Tap","element":[500,500]}`
- finish: `{"_metadata":"finish","message":"done"}`

**安全边界**:
- adapter 只生成 action dict，不直接执行工具；执行仍统一经过 `execute_node -> dispatch_tool()`。
- JSON/tool_calls 只允许白名单 action 和字段；坐标保持 0-1000 相对值，绝对像素转换只在 tool 层。
- 敏感 `Tap` 仍走 confirmation interrupt；`Take_over` 仍走 takeover interrupt；JSON/tool_calls 不自动授权。
- malformed / empty / unsupported 输出 fail-closed，并通过 `error_layer/error_code/recoverable/retry_policy` 分层归因，不会伪装成任务成功。

**验证命令**:

```bash
.venv/bin/pytest tests/model tests/actions tests/graph/test_plan_reflect.py tests/graph/test_execute.py -v
.venv/bin/pytest tests -q
```

**明确非目标**: 本阶段不使用 LangChain Agent 替换 LangGraph `StateGraph`，不强制替换 OpenAI Python SDK；LangChain `init_chat_model` / provider abstraction 仅作为后续 ADR/spike 候选。

---

## 已完成 MVP: Canonical Action IR & Safety Pipeline (Phase 13)

**目标**: 将多格式 parser/adapter 收敛为阶梯架构，建立 Canonical Action IR、独立 Validator、Safety Gate、有限 Repair 与统一 trace/eval 覆盖。

**当前状态**: Phase 13A-13E 已落地。94 项测试通过，code-reviewer 与 security-reviewer 最终复审无 blocking/major findings。

**阶梯架构**:

```text
provider output
  -> Parser/Adapter (格式归一、别名、包裹层)
  -> draft ActionIR
  -> Validator (schema/字段/坐标/白名单)
     -> valid: final validated IR -> Safety Gate
     -> invalid but repairable: Repair -> Validator again
     -> invalid unrecoverable / repair failed: fail-closed
  -> Safety Gate (纯决策层，只接收 validated IR)
     -> approved: Executor (validated + safety-approved IR only)
     -> confirm/takeover: LangGraph interrupt node
     -> rejected: fail-closed
```

**已落地方案**:
- `phone_agent/actions/ir.py`: `ActionIR` frozen dataclass + `ActionDict` TypedDict，`to_dict()` 确保 `_metadata` 权威。
- `phone_agent/actions/validator.py`: `validate_action()` 集中校验 action 白名单、必填字段、坐标 0-1000、Wait duration 格式与边界（正数，≤60s）、dangerous fields。
- `phone_agent/actions/repair.py`: `repair_action()` 仅修复 metadata 大小写、action 别名映射；禁止猜坐标/动作/隐私文本。
- `phone_agent/actions/safety.py`: `decide_safety()` 纯决策层，输出 `SafetyDecision(route="approved"|"confirm"|"takeover"|"rejected")`。
- `phone_agent/actions/adapter.py`: 新增 `DANGEROUS_PROVIDER_FIELDS`、`ALLOWED_PROVIDER_FIELDS_BY_ACTION`、tool calls envelope 校验；Wait 不再默认 duration。
- `phone_agent/graph/nodes/plan.py`: `_validate_with_limited_repair()` helper，adapter 后 validator → repair → second validator，fail-closed。
- `phone_agent/graph/nodes/execute.py`: 执行前 re-validate + safety gate，处理 `pending_execute_confirmed` 绕过确认。

**安全边界**:
- Fail-closed: parse/adapter/validation/repair/safety 任一失败不得伪装成 `finish`，不得执行工具。
- Repair 只能发生在 Safety Gate 之前；Safety Gate 只接收 final validated IR；Executor 只接收 validated + safety-approved IR。
- Adapter output always passes Validator；任何 provider path 不得绕过 Validator。
- HITL 仍使用 LangGraph `interrupt()`；confirm/takeover/pending_execute 语义不变。

**验证命令**:

```bash
.venv/bin/pytest tests/actions tests/model tests/graph/test_execute.py tests/graph/test_plan_reflect.py tests/graph/test_trace.py tests/evals -v
.venv/bin/pytest tests -q
```

**明确非目标**: 不引入 LangChain Agent，不改变 StateGraph 拓扑，不改变坐标转换位置。

---

## 已完成 MVP: Context & Observability Harness

**目标**: 在不提前进入长期记忆的前提下，先建立可观测、可评测、可回滚的短期 context 能力。

**当前状态**: Phase 11A/11B/11C 已落地。默认 `context_mode="inject"`，通过 `build_plan_context_block()` 从 raw state 字段重建并 regex 替换敏感文本后注入；state 写入路径只做 regex 替换、不 stub，stub 策略仅在 checkpoint egress 触发。

**已落地方案**:
- `phone_agent/graph/context.py`: context mode、failure taxonomy、consumer-aware 脱敏（`sanitize_context_payload(consumer=...)` 按 `CONSUMER_POLICY` 选择 regex-only 或 stub）、预算裁剪、context block 构造与 metrics。
- `AgentState`: `screen_belief`、`action_outcome_summary`、`failure_memory`、`summarized_history`、`context_budget`、`context_truncated`、`context_block_chars`、`failure_memory_hit_count`、`repeated_failure_count`。
- `plan_node`: observe 不注入，inject 才注入 context block。
- `execute_node` / `reflect_node`: 生成 action outcome、screen belief、failure memory 与 history summary；写入路径只做 regex 替换、不 stub。
- `RunResult` / eval: 输出 context 可比指标，支持 `--context-mode off|observe|inject`。

### ExpectedOutcome 与 Postcondition Verifier 补强

**当前状态**: 已落地 `ExpectedOutcome` sibling contract 与 deterministic postcondition verifier 补强。Plan 支持 provider envelope：`action` 仍走 canonical ActionIR 执行链路，`expected_outcome` 作为运行态 verifier contract 写入 state，但只保存 hash/哨兵结构；verifier 对当轮 UI 文本做现场 hash/片段 hash 匹配。外发/持久化路径（`action_raw`、trace、report、checkpoint）使用单独的 stub/hash summary；旧 action JSON 保持兼容，真实 `ModelClient` JSON schema/auto 路径会保留 envelope 供 Plan 拆分。Reflect 会基于动作后的截图/current_app 重新构建 after observation，并把动作前/动作后的脱敏 observation summary、focused/editable/keyboard/top activity 等只读信号交给 verifier；Reflect 默认只用 accessibility/device marks，不触发 LocateAnything fallback，除非显式开启 `reflect_enable_vlm_grounding`。`screen_changed` 不再作为 Tap/Type/Search/Video/Swipe 成功条件，只记录为弱信号；动态首页的广告、banner、推荐流、热词、计数器变化默认视为噪声。默认 Type/Tap outcome 采用隐私优先策略：不把目标 hint 默认为成功条件；provider 显式提供的自由文本 outcome 在运行态和 trace/report/checkpoint 均不保留原文。

**关键文件**:
- `phone_agent/graph/expected_outcome.py`: 定义 `ExpectedOutcome`、provider envelope 拆分、独立 normalization、runtime hash/哨兵 contract、trace-safe summary 和保守默认 outcome。
- `phone_agent/graph/verifier.py`: deterministic verifier 检查 Launch app match、文本/页面/目标出现、loading 消失、input focused/editable/keyboard signals 等后置条件；输出 `matched_postconditions`、`missing_postconditions`、`progress_signals`、`weak_signals`、`dynamic_change_only`。
- `phone_agent/graph/nodes/plan.py`: 将 `expected_outcome` 作为 sibling state 字段存储，不写入 `action_parsed`。
- `phone_agent/graph/nodes/reflect.py`: 重建 after observation，并从 state 中抽取 before observation summary；deterministic verifier 高置信时直接映射 reflection；unknown/低置信时才发 isolated verifier request 并注入 ExpectedOutcome、before/after observation summary 与 verifier signals；不追加到 `state["messages"]`。
- `phone_agent/model/client.py`: JSON schema/auto 模式允许 provider envelope，先校验 nested `action`，再保留原 envelope 给 Plan。

**验证重点**:
- `ExpectedOutcome` 不是执行授权，不进入 Validator/Safety/Executor；入 state/trace 前做 regex redaction。
- `screen_changed` 只作为 weak signal，不能覆盖 postcondition failure。
- Launch 等确定性 postcondition 命中时可高置信成功；Launch 使用包名/组件匹配而非 display name；Type/Tap 默认 outcome 保持隐私优先和保守 unknown，只有 provider 显式给出可验证 outcome 或 after observation 提供 focused/editable/keyboard/top activity、输入文本、Search/搜索按钮等证据时才提升置信或记录正向进展；只有按 action/outcome 绑定的 `strong_progress` 会阻止机械 takeover；`verifier_evidence` 只保留 stubbed/redacted matched/missing postconditions 与 progress/weak signals。

**默认预算与隐私**:
- failure memory 最近 3 条，action outcome 最近 1 条。
- screen belief 摘要 300 字符，history 摘要 800 字符，context block 1500 字符。
- 裁剪优先级：当前 screen belief > 最近 action outcome > latest failure memory > summarized history。
- state 写入路径对手机号、邮箱、订单号、验证码、API key/token、长 base64/JWT 等做 regex 替换；stub 策略仅在 `RedactingSerializer` checkpoint egress 触发；context/memory 不绕过 HITL/confirm/takeover。

**验证命令**:

```bash
.venv/bin/pytest tests -q
.venv/bin/python evals/run_eval.py --dry-run --context-mode observe --trace-dir .traces/smoke
.venv/bin/python evals/run_eval.py --dry-run --context-mode inject --trace-dir .traces/smoke
```

**明确非目标**: 本阶段不实现向量库、数据库、跨任务用户画像、云同步、完整 checkpoint/resume 或长期记忆。后续若做 long-term memory，必须基于隐私策略、删除/过期机制、HITL 安全门禁和 eval 收益证据另行规划。

---

## 已完成 MVP: 可观测性（本地 Trace 优先，LangFuse 可选）

**目标**: 让 Agent 的每一步行为可追踪、可回放。

**当前状态**: Phase 8 已落地默认本地 JSONL trace。`PhoneAgent.run_structured()` 返回 `trace_id` / `trace_path`，`evals/run_eval.py --dry-run` 输出的每条结果也包含可解析 trace 文件路径。

**为什么需要**: 长任务失败时需要定位具体 graph step；真实手机 GUI Agent 涉及隐私、支付、账号操作，必须能解释每一步为什么发生，并且默认不上传敏感数据。

**已落地方案**:

```
.traces/{trace_id}.jsonl
```

每行 JSON 包含：
- `run_id` / `trace_id`
- `step_id`
- `node`
- `event`
- `timestamp`
- `payload`（默认脱敏）

覆盖事件：
- `agent`: `run_start` / `run_end` / `run_error`
- `plan`: `plan_start` / `plan_result` / `plan_error`
- `execute`: `execute_result` / `execute_finish` / `confirm_interrupt` / `takeover_interrupt` / `execute_error`
- `reflect`: `reflect_start` / `reflect_result` / `reflect_error`
- `confirm` / `takeover`: interrupt 与 resume 结果事件

**可选增强方向**:

```python
# 可选：LangGraph / LangFuse callback 集成
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler(
    secret_key="...",
    public_key="...",
    host="https://cloud.langfuse.com"
)

result = graph.invoke(initial_state, config={
    "callbacks": [langfuse_handler],
    "configurable": {...}
})
```

**产出**:
- 默认本地 JSONL trace，关联 `RunResult.trace_id` / `RunResult.trace_path`
- Eval JSON 每条结果包含 `trace_id` / `trace_path`
- 每一步的动作、结果、reflection、HITL interrupt 可追踪；截图、prompt、API key、任务文本与隐私文本默认脱敏
- LangFuse Dashboard 作为可选增强，不作为本地运行和测试硬依赖

**验证命令**:

```bash
.venv/bin/pytest tests/graph tests/evals -q
.venv/bin/python evals/run_eval.py --dry-run --trace-dir .traces/smoke
```

---

## 已完成 MVP: 评估基准 (Evaluation Harness)

**目标**: 量化 Agent 的任务完成能力。

**当前状态**: Phase 7 已落地 MVP，且已在 Phase 8 接入 trace 关联：`RunResult` / `run_structured()` 提供结构化结果与 `trace_id` / `trace_path`，`evals/run_eval.py --dry-run` 可在无模型、无设备情况下输出稳定 JSON 指标与本地 JSONL trace 路径。

**为什么需要**: 当前没有任何量化指标。面试时无法回答"成功率多少？平均几步？"。

**已落地方案**:

```
evals/
├── tasks.json          # 标准任务集
└── run_eval.py         # 自动化评估脚本，支持 --dry-run
```

**tasks.json 示例**:

```json
[
  {
    "id": "wechat_send_msg",
    "task": "打开微信，给文件传输助手发一条消息：测试",
    "category": "social",
    "expected_apps": ["微信"],
    "max_steps": 15
  },
  {
    "id": "taobao_search",
    "task": "打开淘宝，搜索无线耳机",
    "category": "shopping",
    "expected_apps": ["淘宝"],
    "max_steps": 10
  }
]
```

**当前评估指标**:
- 成功率 (task completed / total)
- 平均步数
- 平均耗时
- 错误信息
- HITL interrupt routing 计数 (`hitl_count`)
- `trace_id` / `trace_path`，用于关联本地 JSONL trace

**暂未承诺**:
- Token 消耗统计
- 真实 AndroidWorld 规模 benchmark
- 跨进程 checkpoint/resume 成功率（Phase 10 后补）

**后续扩展复杂度**: 🟡 中等
- 接入真实设备状态检查器
- 保存历史结果与对比趋势
- 与 Phase 10 resume 指标打通

---

## 已完成 MVP: 策略级反思 (Strategic Reflection)

**目标**: 将当前二元反思（生效/不生效）升级为因果分析 + 策略切换。

**为什么需要**: 当前 reflect 只判断 continue/retry，Agent 失败后只会盲目重试。策略级反思让 Agent 分析失败原因并切换策略。

**当前状态**: Phase 9 已落地结构化 reflection schema、失败原因分类、建议策略、Plan 反馈闭环与 eval 统计。

**兼容旧状态**:

```python
# reflect_node 当前逻辑
action_succeeded = raw_action.startswith("continue")  # 二元判断
```

**已落地目标状态**:

```python
# 升级后的 reflect prompt 要求模型输出
{
    "reflection_verdict": "succeeded" | "failed" | "partial",
    "failure_cause": "element_not_found" | "app_not_responding" | "wrong_page" | "network_or_loading" | ...,
    "suggested_strategy": "retry" | "retry_with_offset" | "go_back" | "swipe_to_find" | "wait" | "finish",
    "reasoning": "..."
}
```

**图拓扑变更**: 无需变更。只改 `reflect_node` 的 prompt 和解析逻辑。

**State 新增字段**:

```python
class AgentState(TypedDict):
    # ... 现有字段 ...
    reflection_verdict: Optional[str] # succeeded / failed / partial
    failure_cause: Optional[str]      # 失败原因分类
    suggested_strategy: Optional[str] # 建议的恢复策略
    retry_count: int                  # failed / partial 累计次数
```

**Eval 输出**:
- `retry_count`
- `failure_cause_histogram`

**验证命令**:

```bash
.venv/bin/pytest tests/graph tests/evals -q
```

---

## P2: 跨会话记忆 (Cross-Session Memory)

**目标**: Agent 能记住上次 run 做了什么、用户偏好、已知事实。

**为什么需要**: 当前每次 `agent.run()` 都是全新开始，Agent 没有任何记忆。跨会话记忆是 Agent 从"工具"变成"助手"的关键一步。

### 记忆分层架构

```
┌─────────────────────────────────────────────┐
│ Layer 1: 会话记忆（Session Memory）          │
│ 一次 run 内的 messages 历史                  │
│ 已实现：AgentState.messages                  │
│ 状态：✅ 已有                                │
├─────────────────────────────────────────────┤
│ Layer 2: 跨会话记忆（Cross-Session Memory）  │
│ 上次 run 做了什么、用户偏好、已知事实         │
│ 需要：LangGraph Checkpointer + Store         │
│ 状态：❌ 待实现                              │
├─────────────────────────────────────────────┤
│ Layer 3: 技能记忆（Skill/Procedural Memory） │
│ "打开微信发消息"的完整操作序列                │
│ 需要：轨迹存储 + 模式提取 + 检索             │
│ 状态：❌ 待实现（需足够重复任务数据）         │
└─────────────────────────────────────────────┘
```

### Layer 2 实现方案

**依赖**: LangGraph 原生 `Checkpointer` + `Store`

```python
# 1. SQLite Checkpointer — 持久化 State（支持暂停/恢复）
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("checkpoints.db")
graph = create_agent_graph().compile(checkpointer=checkpointer)

# 2. LangGraph Store — 持久化跨会话记忆
from langgraph.store.sqlite import SqliteStore

store = SqliteStore.from_conn_string("memory.db")
```

**记忆写入**（run 结束后）:

```python
# agent.py: PhoneAgent.run()
def run(self, task: str) -> str:
    config = {
        "configurable": {
            "model_client": self.model_client,
            "device_factory": device_factory,
            "system_prompt": self.agent_config.system_prompt,
            "verbose": self.agent_config.verbose,
            "user_id": "default",           # 新增
            "thread_id": str(uuid.uuid4()), # 新增：每次 run 唯一
        },
        "store": self._store,               # 新增：注入 Store
    }
    result = self._graph.invoke(initial_state, config)

    # 存储关键信息到长期记忆
    self._store.put(
        ("memories", "default", f"run_{thread_id}"),
        {
            "task": task,
            "result": result.get("action_result", {}).get("message", ""),
            "steps": result["step_count"],
            "timestamp": datetime.now().isoformat(),
        }
    )
    return ...
```

**记忆检索**（plan_node 中）:

```python
# graph/nodes/plan.py: plan_node()
def plan_node(state, config):
    store = config.get("store")
    if store:
        user_id = config["configurable"].get("user_id", "default")
        # 语义搜索相关记忆
        memories = store.search(
            ("memories", user_id),
            query=state["task"],
            limit=5,
        )
        if memories:
            memory_text = "\n".join([
                f"- {m.value['task']} ({m.value['steps']} steps)"
                for m in memories
            ])
            # 注入到 user message 中
            memory_context = f"\n\n** Previous related tasks **\n{memory_text}"
    # ... 正常推理 ...
```

**State 新增字段**:

```python
class AgentState(TypedDict):
    # ... 现有字段 ...
    retrieved_memories: Optional[list[dict]]  # 检索到的相关记忆
    memory_context: Optional[str]             # 格式化后的记忆文本
```

**文件变更**:

| 文件 | 变更 |
|------|------|
| `phone_agent/agent.py` | 新增 `_store` 属性，`run()` 中注入 Store + 写入记忆 |
| `phone_agent/graph/state.py` | 新增 `retrieved_memories`, `memory_context` 字段 |
| `phone_agent/graph/nodes/plan.py` | 检索记忆并注入 prompt |
| `phone_agent/graph/builder.py` | 编译时传入 checkpointer |
| `setup.py` | 在现有安装入口补充 checkpoint 相关依赖（如后续 Phase 需要） |

**复杂度**: 🟡 中等
- 搭建: 0.5 天
- 实现: 2-3 天
- 调试: 1-2 天

### Layer 3 实现方案（远期）

**前提**: 需要有足够的重复任务数据积累。如果每次任务都不同，技能记忆不会产生价值。

**技能存储结构**:

```sql
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT,                    -- "打开微信发消息"
    trigger_pattern TEXT,         -- "发微信|微信消息|wechat"
    embedding BLOB,               -- 用于语义匹配
    steps JSON,                   -- [{"action": "Launch", "app": "微信"}, ...]
    success_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    avg_steps REAL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**工作流程**:

```
任务输入 → embedding 匹配 → 命中 skill？
  ├─ 是 → 直接回放 steps（跳过推理）
  └─ 否 → 正常 Plan-Execute-Reflect → 成功后存入 skills
```

**复杂度**: 🔴 高
- 搭建: 1 天
- 实现: 3-4 天
- 调试: 2-3 天
- 前提: 需要足够的重复任务数据

---

## 不做: Dreaming（离线经验巩固）

**理由**: Dreaming 的价值正比于会话量。Anthropic 的客户跑了成千上万次会话才有显著提升。个人项目跑几十次，提取不出有统计意义的模式。且实现复杂度高（10+ 天），ROI 低。

---

## 不做: 多 Agent 架构（Supervisor + Specialists）

**理由**: 当前场景是单一的手机自动化，所有操作共享同一套工具（Tap/Swipe/Type/Launch）。拆成"微信专家""淘宝专家"是过度设计——它们用的工具完全一样。多 Agent 的价值在于异构任务分工，当前场景不需要。

---

## 不做: Command 动态路由

**理由**: 当前条件边（`after_execute` / `after_interrupt` / `should_continue`）已覆盖所有路由场景。路由逻辑是纯函数式的（检查 State 字段），不需要 Node 内部动态决定路由。Command 适用于模型自主决定路由的场景，当前是规则路由。

---

## 不做: Prompt 外部化

**理由**: Prompt 已收敛为 `context_harness_v1` 的结构化 JSON/tool_calls 输出契约；当前代码内 prompt sections 与 adapter/validator/grounding 安全边界强绑定，外部化（YAML/JSON）会增加同步成本但无实际收益。当前没有多租户、A/B 测试需求。唯一值得做的是合并 `prompts_zh.py` 和 `prompts_en.py` 减少重复，但这是代码整洁而非架构升级。

---

## 未来方向: 增强结构化 Screen State 注入

**当前状态**: Accessibility Tree 已通过 `AccessibilityTreeProvider` 解析为 MarkRegistry marks，并通过 `mark_registry.prompt_block()` 注入主 VLM prompt（包含 role、text_summary、source、confidence）。`PHONE_AGENT_GROUNDING_PROVIDER=hybrid` 已实现 tree-first + LocateAnything fallback 的完整链路。

**剩余差距**: 当前 marks block 仅提供扁平化的元素列表（mark_id + role + text_summary），未利用以下结构化信息：
- 页面层级（parent-child 关系、嵌套深度）
- 可滚动区域标识（scrollable 容器及其子元素范围）
- 选中态 / 开关态（checked、selected、enabled 状态）
- 输入框当前值与 hint text

**方案设计**:

在 `AccessibilityTreeProvider` 解析阶段保留层级与状态信息，扩展 `Mark` dataclass 或新增 `ScreenStructure` 注入层：

```python
screen_structure = {
    "current_app": "Settings",
    "scrollable_regions": [{"mark_id": "ax_5", "child_count": 12}],
    "checked_elements": [{"mark_id": "ax_1", "checked": True, "text": "Wi-Fi"}],
    "input_fields": [{"mark_id": "ax_8", "hint": "搜索设置", "focused": False}],
}
```

在 `plan_node` 中作为补充 context block 注入，与 marks block 并列，不替代 marks block。

**依赖条件**:
- 需扩展 `parse_uiautomator_marks()` 保留 parent/child/checked/selected/focused 信息
- 需评估 VLM 对额外结构化 context 的利用能力
- 敏感信息过滤（密码输入框内容等）需同步扩展

**预估复杂度**: 🟡 中等 (3-5 天，核心解析已有基础)

---

## P2: UI 状态机 (App 内页面层级跟踪)

**问题**: Agent 只知道 `current_app="淘宝"`，不知道当前在 app 内的哪个子页面（首页 / 搜索页 / 商品详情页 / 购物车 / 结算页）。每次都要从截图重新推断。

**方案设计**:

为每个高频 app 维护一个简化的页面状态机：

```python
TAOBAO_PAGES = {
    "home": {"indicators": ["搜索", "首页"], "actions": {"search": "search_page"}},
    "search_page": {"indicators": ["搜索框", "搜索"], "actions": {"result": "search_results"}},
    "search_results": {"indicators": ["筛选", "排序"], "actions": {"item": "item_detail"}},
    "item_detail": {"indicators": ["加入购物车", "立即购买"]},
    "cart": {"indicators": ["全选", "结算"]},
    "checkout": {"indicators": ["提交订单"]},
}
```

在 `reflect_node` 中根据截图 + current_app + 上一步操作推断当前页面层级，写入 `state["current_page"]`，供 `plan_node` 使用。

**依赖条件**:
- 需要为每个目标 app 定义页面状态机（工作量大）
- 页面判断依赖 VLM 或结构化 UI 状态（P2-1）

**预估复杂度**: 🟡 中等 (3-5 天，不含 app 定义工作量)

---

## P2: 动态 Prompt 注入 (已部分实现，增强中)

**当前状态**: `MarkRegistry.prompt_block()` 已实现当前页面可交互元素的动态注入，输出格式如：
```
** Screen Marks (use target_mark_id; do not guess coordinates) **
- ax_1: role=Switch source=accessibility_tree confidence=1.0 text_summary=Wi-Fi
- ax_2: role=ListItem source=accessibility_tree confidence=0.8 text_summary=HomeNetwork_5G
- ax_3: role=Button source=accessibility_tree confidence=1.0 text_summary=添加网络
```

**剩余差距**:
1. App 列表注入仍使用静态 `APP_PACKAGES` registry，未调用 `pm list packages` 获取设备实际已安装 app
2. Marks block 不包含 scrollable/checked/focused 等状态信息（见上方"增强结构化 Screen State 注入"）
3. 无 app 内页面层级推断（见下方"UI 状态机"）

**后续增强方向**:
- 调用 `pm list packages` 与 `APP_PACKAGES` 取交集，只注入实际可用的 app
- 结合结构化 Screen State 注入 scrollable/checked/focused 状态
- 结合 UI 状态机注入当前页面层级信息

**预估复杂度**: 🟢 低 (1-2 天，核心注入机制已就绪)

---

## P3: Multi-model Pipeline

**当前状态**: Grounding 已使用专用模型（LocateAnything-3B-4bit）作为 MarkProvider，与主 VLM 分离。Reflect 仍使用主 VLM。

**方案设计**:

```
Plan:    大模型 (7B+) — 复杂推理 + 动作决策
Reflect: 小模型 (1-3B) — 截图对比 + 成败判断
Grounding: LocateAnything-3B-4bit (MLX) — 已实现，作为 MarkProvider 运行
```

在 `ModelConfig` 中支持多模型配置，`plan_node` 和 `reflect_node` 使用不同的 `model_client`。

**剩余工作**:
- 为 reflect_node 配置独立小模型
- 评估小模型 reflect 的准确率与延迟收益
- 部署多模型端点

**预估复杂度**: 🟡 中等 (2-3 天代码 + 部署)

---

## P3: Persistent Memory (文件系统方案)

**问题**: 每次 `agent.run()` 都是全新开始，Agent 不记住之前的任务执行结果。长上下文中历史信息被 compact 丢失。

**方案设计**:

参考 Manus 的文件系统方案，将关键信息持久化到本地文件：

```
.agent_memory/
├── task_history.jsonl      # 历史任务 + 结果
├── app_navigation.json     # 已学到的 app 导航路径
└── failure_patterns.json   # 已知的失败模式 + 修复策略
```

在 `plan_node` 的 step 0 中读取相关记忆并注入 prompt。在 `agent.run()` 结束后写入新学到的信息。

**依赖条件**: 需要足够的任务执行数据积累

**预估复杂度**: 🟡 中等 (3-4 天)

---

## P3: KV-cache Aware Prompt 管理

**问题**: 修改 prompt 前缀（如时间戳、动态 app 列表）会导致 provider 端 KV-cache 失效，增加推理延迟和成本。Manus 的经验表明"Keep your prompt prefix stable"是关键优化。

**方案设计**:

1. 将 prompt 分为稳定前缀和动态后缀
2. 稳定前缀：System Contract + Action Schema + Task Policies（不变）
3. 动态后缀：App Registry + 日期 + Context Block（每 run 变化）
4. 确保动态部分只出现在 prompt 尾部

**当前状态**: P0-1 已将 App Registry 放在 system prompt 末尾，符合此原则。日期仍在 `SYSTEM_CONTRACT` 开头，但每日只变一次，影响可控。

**预估复杂度**: 🟢 低 (1 天，主要是审计和调整)

---

## 优先级总览

| 优先级 | 功能 | 时间 | 简历价值 | 理由 |
|--------|------|------|----------|------|
| P0 | 本地 Trace + 可选 LangFuse | 2 天 | ⭐⭐⭐ | 先本地可追踪，再可选接 Dashboard |
| ✅ | 评估基准 MVP | 已完成 | ⭐⭐⭐⭐ | 已有结构化 API 与 dry-run smoke 指标，后续扩展真实 benchmark |
| P1 | 策略级反思 | 3.5 天 | ⭐⭐⭐⭐ | 不改架构，Agent 智能深度明显提升 |
| P2 | 跨会话记忆 | 4 天 | ⭐⭐⭐⭐ | LangGraph 原生能力，展示记忆设计 |
| P3 | 技能记忆 | 7 天 | ⭐⭐⭐⭐⭐ | 需要重复任务数据积累 |
| ❌ | Dreaming | 10+ 天 | — | 无足够会话数据 |
| ❌ | 多 Agent | 15+ 天 | — | 场景不需要 |
