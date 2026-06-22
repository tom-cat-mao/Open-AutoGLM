# Open-AutoGLM Agent Guide

## Core Loop

`Screenshot -> VLM inference -> Parse action -> Execute on device -> Reflect -> Repeat`

项目核心循环由 LangGraph `StateGraph` 实现；详细 roadmap 与阶段状态按需读取 `.trae/rules/graph.mdc`。

## Non-Negotiable Constraints

| 约束 | 要求 |
|---|---|
| 坐标 | 模型输出 0-1000 相对坐标；tool 内必须用 `convert_relative_to_absolute()` 转绝对像素 |
| 动作解析 | 旧 text DSL / `parse_action()` 已删除；模型输出只允许 `json_schema|tool_calls|auto`，经 adapter 白名单映射到 canonical ActionIR；禁止 `eval()`，若未来新增文本解析器必须使用 `ast.parse` + `ast.literal_eval` |
| 图片上下文 | 每步后必须用 `MessageBuilder.remove_images_from_message()` 剥离历史图片 |
| HITL | 支付/隐私走 `confirm_node`；登录/验证码走 `takeover_node`；均使用 LangGraph `interrupt()` |
| Edge Terminal Guard | `edges.py` 的三条条件边 `after_execute()` / `after_interrupt()` / `should_continue()` 都必须在任何其他路由判断之前先检查 `state.get("finished")` 或 `state.get("error")`，命中即返回 `"end"`；不得让滞留的 `pending_interrupt`、`pending_execute` 或 `action_parsed` 把已终止状态错路由到 `confirm`/`takeover`/`reflect`/`replan`；新增或修改条件边/路由分支必须遵守该不变式 |
| 设备抽象 | 设备操作统一经 `DeviceFactory` -> `phone_agent/adb/` |
| messages reducer | `plan_node` 只返回新增消息；`execute_node` 返回完整重建列表，避免 token 爆炸 |
| confirm-then-execute | confirm 接受敏感 Tap 后路由到 `execute`；`pending_execute` 分支不得再次 `_strip_and_append` |
| Tool DI | `execute_node` 从 graph config 注入 `device_factory`；tool schema 不得暴露 `device_factory` |
| Trace | 默认本地 JSONL trace；`RunResult.trace_id/trace_path` 与 eval JSON 可关联 `.traces/{trace_id}.jsonl`；敏感截图/API key/隐私文本默认脱敏 |
| Reflection | `reflect_node` 维护 `reflection_verdict/failure_cause/suggested_strategy`；Plan 下一轮必须能读取结构化失败原因和策略 |
| ExpectedOutcome | `ExpectedOutcome` 是 ActionIR 的 sibling verification contract；Plan 可从 provider envelope 拆出 `action` 与 `expected_outcome`，但 `expected_outcome` 不得进入 canonical ActionIR、Validator/Safety/Executor，也不提供执行授权；state 内只保存 hash/哨兵 contract，不保存 provider 自由文本，verifier 对当轮 UI 文本做现场 hash/片段 hash 匹配；selected-object fields 只允许 `selected_object_id_hash/object_type/object_evidence_hash/title_stub/title_hash/container_lineage_hash/list_lineage_hash/expected_page_type`，且仅用于 verifier hash/stub evidence；action_raw/trace/report/checkpoint 只使用 stub/hash summary |
| Context | `context_mode=off|observe|inject`，默认 inject；inject 注入 regex-redacted、按 budget 裁剪的 context block（`build_plan_context_block`）；observe 仅产出 section IDs 不构建 block；context 不得绕过 HITL |
| Context Selection | `select_plan_context()` 只产出 section IDs、脱敏 context block 与计数指标；不得修改 Action IR、HITL、pending_execute、interrupt 或 safety route 字段 |
| Request Compaction | `compact_messages_for_request()` 只能压缩传给 `model_client.request()` 的消息；不得改写 `state["messages"]`；必须保留最新截图并剥离历史图片 |
| Prompt Version | 默认且唯一支持 `context_harness_v1`；prompt schema 只约束格式，不授权执行 |
| Output Adapter | `ModelConfig.output_mode=json_schema|tool_calls|auto`；旧 text DSL 不再是执行协议；JSON/tool_calls 必须经 adapter 白名单映射到 canonical action，parse failure fail-closed，不得绕过 HITL |
| Postcondition Verifier | Reflect 必须优先验证 ExpectedOutcome 后置条件；`screen_changed` 只能记录为 weak signal，不能单独证明 Tap/Type/Search/Video/Swipe 成功；广告、banner、推荐流、热词、计数器等动态区域默认是噪声；只有按 action/outcome 绑定的 `strong_progress`（如 Type 后目标文本出现、input_focused 的 focused/keyboard 信号）可阻止机械 takeover |
| Grounding | 默认 `PHONE_AGENT_GROUNDING_PROVIDER=hybrid`；主 VLM 的屏幕目标点击类动作仍以 atomic `target_mark_id` 为唯一执行边界；`target_object_id`、`ordinal`、`object_role`、`object_filter` 只允许作为非执行 IntentIR selector，必须在 `phone_agent/actions/grounding.py` 用当前 `ObjectRegistry + MarkRegistry + ScreenBinding` 编译为唯一 `target_mark_id`；LocateAnything/Fake/OCR/UIAutomator/SoM 只能作为 MarkRegistry provider 生成 marks，`target_text_hint` 只能作为受控 provider hint，不能作为可执行目标；Android accessibility tree marks 来自 `adb exec-out uiautomator dump /dev/tty`，统一转换为 0-1000 bounds，并可生成 `structure_kind=accessibility` 的 `ScreenStructure`/`ObjectRegistry` sidecars；LocateAnything 结构模式默认 `off`，仅显式 `locateanything_structure_mode=target|screen` 时生成 `structure_kind=visual` sidecar，visual 对象必须带 source/tier/eligibility，resolver 必须硬校验 `executable_selector`，不能伪装 accessibility truth 或回退 raw bbox tap；hybrid 先用 accessibility tree，失败时才 fallback 到 LocateAnything；本地 provider 可在内存中使用 raw bounded hint，但 raw hint 不得进入 trace/checkpoint/prompt/report，远程 provider raw hint 必须显式 opt-in；provider 由 graph config/env 注入，不暴露给 tool schema；LocateAnything 默认输入图最长边 `max_size=960`，可用 `locateanything_max_size` / `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE` 灰度或回滚；LocateAnything prompt 必须通过 `apply_chat_template(..., num_images=1)`，默认不注入额外 context，仅允许 bounded `locateanything_context_max_chars` |
| Object Selectors | `object_filter` v1 必须是 strict flat JSON object，仅允许 `object_type/role/source/list_id/title_hash_prefix/text_hash_prefix/resource_id_hash_prefix/lineage_hash_prefix`；值仅 scalar string，最大 64 chars，hash prefix 为 6-16 hex；禁止 raw title/text、regex、array、nested object、provider/backend/device 字段；`object_id/list_id/ordinal` 只在当前 observation 内有效，reobserve 后必须重选或重新验证；Screen Objects prompt 默认 lists 5、objects 30、总 block 4000 chars，且只展示 type/role/list/ordinal、primary mark 与 hash/lineage evidence，不展示 raw title/text |
| Reflect Grounding | Reflect after-observation 默认 accessibility/device marks only，不触发 LocateAnything fallback；如确需 VLM grounding provider，必须显式开启 `reflect_enable_vlm_grounding` |
| Grounding Fail-Closed | target-required grounding 失败（截图无效/secure screenshot blocked、provider 缺失、低置信、bad bbox、stale/hash mismatch、多候选歧义等）不得回退为主 VLM 坐标 Tap，也不得把黑图继续发给模型；只能 fail-closed/等待/接管/重新观测 |
| Grounding Benchmark | 正式入口在 `bench/grounding/`；post-training bbox 0-1 必须转换为 0-1000 manifest；LocateAnything benchmark 用 `.venv/bin/python -m bench.grounding.run_locateanything`，固定 manifest 复评用 `.venv/bin/python -m bench.grounding.score_predictions`；真实 LocateAnything 依赖 Apple Silicon + Metal + mlx-vlm，`bench_output/` 默认不提交 |
| Action IR Pipeline | 阶梯架构：Adapter → draft ActionIR → Validator → (Repair → Validator) → Safety Gate → Executor；Repair 不得在 Safety Gate 之后；Executor 只接收 validated + safety-approved IR |
| Validator | 集中校验 action 白名单、必填字段、坐标 0-1000、Wait duration 正数且 ≤60s、dangerous fields；fail-closed |
| Safety Gate | 纯决策层 `decide_safety()`，输出 `approved|confirm|takeover|rejected`；不 dispatch、不调用设备 |
| Repair | 仅修复 metadata 大小写、action 别名；禁止猜坐标/动作/隐私文本；repair 后必须二次 Validator |
| Launch Registry | `get_app_registry_summary()` 以 `APP_PACKAGES` 为单一来源；Validator 对未知 Launch app fail-closed；registry 中每个名称必须可被 `normalize_app_name()` 归一化 |
| Context Privacy | 采用 consumer-aware 脱敏：state 写入路径只 regex 替换（手机号/邮箱/订单号/验证码/API key/JWT/base64 等），不 stub；stub 策略仅在 `sanitize_context_payload(consumer="checkpoint")` 触发，由 `phone_agent/checkpoint/serde.py::RedactingSerializer` 在 checkpoint egress 调用；`build_plan_context_block()` 是 inject 模式的唯一 builder，从 raw state 字段重建并 regex 替换；`inject: bool` 保留为向后兼容别名（`True` ≡ `consumer="inject"`、`False` ≡ `consumer="checkpoint"`），新代码应使用 `consumer=` 参数；context 不绕过 HITL |
| Streaming Stdout | `ModelConfig.stream_stdout=False` 默认关闭；reasoning/content 不打印到 stdout；仅显式 opt-in 才输出 |
| URL Redaction | `redact_url_for_display()` 隐藏 URL userinfo 和敏感 query 参数（api_key/token/secret 等）；所有 stdout 路径使用脱敏 URL |

## Version Management

- Phase 完成后按项目规范更新 `.trae/rules/graph.mdc`，若改动架构/API/评测/trace/TraeCLI 编排，同步更新 `README.md`、`docs/future-roadmap.md`、`.trae/traecli.yaml`、相关 `.trae/rules/*.mdc` 与本文件；commit message：`feat(graph): <phase 目标>`、`feat(trae): <phase 目标>` 或 `feat(grounding): <benchmark/grounding 目标>`。
- 禁止对 `main` 和 `feature/langgraph-refactor` 执行 `git push --force`。
- 未经用户明确要求，不主动创建 commit。

## Compact Instructions

压缩时保留：当前任务与进度、Non-Negotiable Constraints、Key Paths、Phase 8 trace 现状、Phase 10 Autopilot 现状、Phase 11 context harness 现状、Phase 12 output adapter 现状、Phase 14 prompt/context harness 现状、必要 roadmap 状态与未完成 TODO。
