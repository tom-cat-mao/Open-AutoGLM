# Source Map

This file maps live diagnosis symptoms to Open-AutoGLM source files.

| Signal | Source Files | Review Focus |
|---|---|---|
| `screenshot_unavailable`, `secure_screenshot_blocked`, `adb_screencap_failed`, `screenshot_pull_failed` | `phone_agent/adb/screenshot.py`, `phone_agent/graph/screenshot_status.py`, `phone_agent/graph/nodes/plan.py`, `phone_agent/graph/nodes/reflect.py` | Fail closed before model calls; avoid using placeholder screenshots as valid evidence; compare screenshot dimensions with real device size. |
| `invalid_json`, `parse_error`, `unsupported_tool_call` | `phone_agent/model/client.py`, `phone_agent/actions/adapter.py`, `phone_agent/graph/nodes/plan.py` | Output mode, response parsing, retry behavior, structured action contract. |
| `mark_required`, `unknown_mark`, `stale_mark`, `hash_mismatch`, `grounding_no_candidate`, `grounding_no_usable_candidate`, `grounding_ambiguous`, `low_confidence`, `bad_bbox` | `phone_agent/actions/grounding.py`, `phone_agent/grounding/provider.py`, `phone_agent/grounding/fallback.py`, `phone_agent/grounding/accessibility.py`, `phone_agent/grounding/locateanything.py`, `phone_agent/graph/observation.py`, `phone_agent/graph/marks.py` | MarkRegistry binding, provider fallback, hint-aware usability, candidate count, hash consistency. |
| `unknown_app`, `unknown_action`, `missing_field`, `unsafe_value` | `phone_agent/actions/validator.py`, `phone_agent/actions/repair.py`, `phone_agent/config/apps.py` | Canonical ActionIR validation, app registry normalization, limited repair scope. |
| `action_safety_rejected`, `confirmation_required`, HITL count unexpected | `phone_agent/actions/safety.py`, `phone_agent/graph/edges.py`, `phone_agent/graph/nodes/confirm.py`, `phone_agent/graph/nodes/takeover.py`, `phone_agent/graph/nodes/execute.py` | confirm/takeover routing, pending_execute, terminal guard ordering. |
| `dispatch_failed`, `execution_failed`, tap/swipe no-op | `phone_agent/graph/tools/coords.py`, `phone_agent/graph/tools/tap.py`, `phone_agent/graph/tools/swipe.py`, `phone_agent/graph/tools/type_text.py`, `phone_agent/graph/tools/launch.py`, `phone_agent/adb/device.py`, `phone_agent/adb/input.py` | Coordinate conversion, ADB command return codes, keyboard switching, app launch resolution. |
| `model_reflection_failed`, wrong reflection verdict, repeated wait | `phone_agent/graph/nodes/reflect.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/context.py` | Reflection schema, verifier precedence, failure memory and repeated failure detection. |
| context metrics missing, prompt pollution | `phone_agent/graph/context.py`, `phone_agent/graph/nodes/plan.py`, `phone_agent/graph/trace.py` | Request-only compaction, privacy redaction, selected_sections, context budget. |
| result/trace missing | `evals/run_eval.py`, `phone_agent/agent.py`, `phone_agent/graph/trace.py` | RunResult serialization, trace writer, eval summary shape. |
