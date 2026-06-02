# Open-AutoGLM Agent Guide

## Core Loop

`Screenshot -> VLM inference -> Parse action -> Execute on device -> Reflect -> Repeat`

项目核心循环由 LangGraph `StateGraph` 实现；详细 roadmap 与阶段状态按需读取 `.trae/rules/graph.mdc`。

## Non-Negotiable Constraints

| 约束 | 要求 |
|---|---|
| 坐标 | 模型输出 0-1000 相对坐标；tool 内必须用 `convert_relative_to_absolute()` 转绝对像素 |
| 动作解析 | 必须用 `ast.parse` + `ast.literal_eval`；禁止 `eval()` |
| 图片上下文 | 每步后必须用 `MessageBuilder.remove_images_from_message()` 剥离历史图片 |
| HITL | 支付/隐私走 `confirm_node`；登录/验证码走 `takeover_node`；均使用 LangGraph `interrupt()` |
| 设备抽象 | 设备操作统一经 `DeviceFactory` -> `phone_agent/adb/` |
| messages reducer | `plan_node` 只返回新增消息；`execute_node` 返回完整重建列表，避免 token 爆炸 |
| confirm-then-execute | confirm 接受敏感 Tap 后路由到 `execute`；`pending_execute` 分支不得再次 `_strip_and_append` |
| Tool DI | `execute_node` 从 graph config 注入 `device_factory`；tool schema 不得暴露 `device_factory` |
| Trace | 默认本地 JSONL trace；`RunResult.trace_id/trace_path` 与 eval JSON 可关联 `.traces/{trace_id}.jsonl`；敏感截图/API key/隐私文本默认脱敏 |
| Reflection | `reflect_node` 维护 `reflection_verdict/failure_cause/suggested_strategy`；Plan 下一轮必须能读取结构化失败原因和策略 |
| Context | `context_mode=off|observe|inject`，默认 observe；仅 inject 注入脱敏裁剪后的 context block；context 不得绕过 HITL |
| Context Selection | `select_plan_context()` 只产出 section IDs、脱敏 context block 与计数指标；不得修改 Action IR、HITL、pending_execute、interrupt 或 safety route 字段 |
| Request Compaction | `compact_messages_for_request()` 只能压缩传给 `model_client.request()` 的消息；不得改写 `state["messages"]`；必须保留最新截图并剥离历史图片 |
| Prompt Version | 默认 `context_harness_v1`；`legacy_text_dsl` 保留旧 text DSL prompt 作为回滚路径；prompt schema 只约束格式，不授权执行 |
| Output Adapter | `ModelConfig.output_mode=text_dsl|json_schema|tool_calls|auto`；JSON/tool_calls 必须经 adapter 白名单映射到 canonical action，parse failure fail-closed，不得绕过 HITL |
| Action IR Pipeline | 阶梯架构：Adapter → draft ActionIR → Validator → (Repair → Validator) → Safety Gate → Executor；Repair 不得在 Safety Gate 之后；Executor 只接收 validated + safety-approved IR |
| Validator | 集中校验 action 白名单、必填字段、坐标 0-1000、Wait duration 正数且 ≤60s、dangerous fields；fail-closed |
| Safety Gate | 纯决策层 `decide_safety()`，输出 `approved|confirm|takeover|rejected`；不 dispatch、不调用设备 |
| Repair | 仅修复 metadata 大小写、action 别名；禁止猜坐标/动作/隐私文本；repair 后必须二次 Validator |

## Version Management

- Phase 完成后按项目规范更新 `.trae/rules/graph.mdc`，若改动架构/API/评测/trace/TraeCLI 编排，同步更新 `README.md`、`docs/future-roadmap.md` 与本文件；commit message：`feat(graph): <phase 目标>` 或 `feat(trae): <phase 目标>`。
- 禁止对 `main` 和 `feature/langgraph-refactor` 执行 `git push --force`。
- 未经用户明确要求，不主动创建 commit。

## Compact Instructions

压缩时保留：当前任务与进度、Non-Negotiable Constraints、Key Paths、Phase 8 trace 现状、Phase 10 Autopilot 现状、Phase 11 context harness 现状、Phase 12 output adapter 现状、Phase 14 prompt/context harness 现状、必要 roadmap 状态与未完成 TODO。
