# Source Map

This file maps live diagnosis symptoms to Open-AutoGLM source files. It is the
reference the SKILL.md "Source Mapping" table and `run_diagnosis.py`
`SOURCE_RULES` build on.

## Architecture note

The reflection / finish-gate layer replaced the old `phone_agent/graph/task_goal.py`
module with a declarative `GoalContract` system (see P0 constraint #13a in
`AGENTS.md`). When a trace surfaces reflection/finish signals, inspect the goal
files, not `task_goal.py` (which no longer exists).

The remote OpenAI-compatible grounding provider was reverted (commit `e0a2e4b`).
`PHONE_AGENT_REMOTE_GROUNDING_*` env variables are stale and ignored by the current
grounding factory — do not treat their presence as evidence that remote grounding
is wired up.

The 9-phase goal/evidence/privacy rebuild (commit `7dc6946`) added new subsystems
that produce their own failure modes: capability gating (`actions/capability.py`,
`actions/receipt.py`), goal contract adequacy validation (`graph/goal_requirements.py`),
versioned safety classification (`config/policy.py`), and HMAC-bound goal resume
(`checkpoint/goal_resume.py`). Signal rows for these layers are included below.

## Signal → source map

| Signal | Source Files | Review Focus |
|---|---|---|
| `screenshot_unavailable`, `secure_screenshot_blocked`, `adb_screencap_failed`, `screenshot_pull_failed` | `phone_agent/adb/screenshot.py`, `phone_agent/graph/screenshot_status.py`, `phone_agent/graph/nodes/plan.py`, `phone_agent/graph/nodes/reflect.py` | Fail closed before model calls; avoid using placeholder screenshots as valid evidence; compare screenshot dimensions with real device size. |
| `invalid_json`, `parse_error`, `unsupported_tool_call`, `model_request_failed` | `phone_agent/model/client.py`, `phone_agent/actions/adapter.py`, `phone_agent/graph/nodes/plan.py` | Output mode, response parsing, retry behavior, structured action contract. |
| `mark_required`, `unknown_mark`, `stale_mark`, `hash_mismatch`, `grounding_no_candidate`, `grounding_no_usable_candidate`, `grounding_ambiguous`, `low_confidence`, `bad_bbox`, `provider_unavailable`, `missing_provider_hash` | `phone_agent/actions/grounding.py`, `phone_agent/grounding/provider.py`, `phone_agent/grounding/fallback.py`, `phone_agent/grounding/accessibility.py`, `phone_agent/grounding/locateanything.py`, `phone_agent/grounding/factory.py`, `phone_agent/graph/observation.py`, `phone_agent/graph/marks.py` | MarkRegistry binding, provider fallback, hint-aware usability, candidate count, hash consistency. |
| `unknown_app`, `unknown_action`, `missing_field`, `unsafe_value`, `invalid_metadata` | `phone_agent/actions/validator.py`, `phone_agent/actions/repair.py`, `phone_agent/config/apps.py` | Canonical ActionIR validation, app registry normalization, limited repair scope. |
| `action_safety_rejected`, `confirmation_required`, `sensitive_tap_requires_confirmation`, HITL count unexpected | `phone_agent/actions/safety.py`, `phone_agent/graph/edges.py`, `phone_agent/graph/nodes/confirm.py`, `phone_agent/graph/nodes/takeover.py`, `phone_agent/graph/nodes/execute.py` | confirm/takeover routing, pending_execute, terminal guard ordering. |
| `dispatch_failed`, `execution_failed`, `missing_action`, tap/swipe no-op | `phone_agent/graph/nodes/execute.py`, `phone_agent/graph/tools/coords.py`, `phone_agent/graph/tools/tap.py`, `phone_agent/graph/tools/swipe.py`, `phone_agent/graph/tools/type_text.py`, `phone_agent/graph/tools/launch.py`, `phone_agent/adb/device.py`, `phone_agent/adb/input.py` | Coordinate conversion, ADB command return codes, keyboard switching, app launch resolution. |
| `model_reflection_failed`, `repeated_action`, wrong reflection verdict, repeated wait, `postcondition_unverified`, `missing_postconditions`, `dynamic_change_only`, `verifier_unknown`, `verifier_failure`, `goal_not_satisfied`, `finish_validation_unknown`, `finish_validation_failure`, `needs_recompile`, `matched_terminal_evidence`, `missing_terminal_evidence`, `soft_match_accepted`, `programmatic_contradiction_override` | `phone_agent/graph/goal.py`, `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal_evaluator.py`, `phone_agent/graph/nodes/goal_node.py`, `phone_agent/graph/expected_outcome.py`, `phone_agent/graph/nodes/reflect.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/context.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/predicates.py`, `phone_agent/graph/goal_evidence.py`, `phone_agent/graph/runtime_goal.py`, `phone_agent/graph/compatibility_adapters.py` | GoalContract compilation, finish-gate evaluation, `vlm_judge` criterion not named in `matched_terminal_evidence` is `missing` (hard gate), `needs_recompile` has no writer today, verifier precedence, failure memory and repeated failure detection. `soft_match_accepted` means the finish relied on the detail-only soft match (evidence relaxation, no content-hash confirmation) — verify the opened detail page manually. `programmatic_contradiction_override` means programmatic signals overrode vlm_judge self-attestation. |
| `capability_missing`, `capability_unavailable`, `capability_rejected` | `phone_agent/actions/capability.py`, `phone_agent/actions/receipt.py`, `phone_agent/graph/nodes/execute.py` | ToolCapability registry coverage for the action name; `implementation_status=unavailable` means the stub action now fails closed instead of reporting pseudo-success; ActionReceipt only describes dispatch, never Goal progress. |
| `unsupported_semantics`, `needs_goal_clarification`, `goal_contract_invalid`, `goal_approval_replacement_inadequate`, `runtime_goal_binding_invalid`, `runtime_goal_binding_unavailable`, `runtime_goal_context_missing` | `phone_agent/graph/nodes/goal_node.py`, `phone_agent/graph/goal_requirements.py`, `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal.py`, `phone_agent/graph/goal_binding.py` | Most common root cause: task verb not in `TaskRequirementExtractor._OPERATIONS` (limited zh/en keyword set) → `operation_kind=unknown` → adequacy rejection. Check `task_requirement_set.safe_projection` in trace before changing code. |
| `goal_resume_hmac_mismatch`, `goal_resume_rehydration_failed`, `goal_resume_untrusted`, `trusted_goal_resume_invalid` | `phone_agent/checkpoint/goal_resume.py`, `phone_agent/checkpoint/serde.py` | HMAC key consistency across resume; serde collapses `goal_evidence_ledger` to `[]` at checkpoint egress — progress must come from the trusted_goal_resume projection, never from checkpoint content. |
| `action_safety_rejected`, HITL miscount with policy mismatch | `phone_agent/config/policy.py`, `phone_agent/actions/safety.py` | SafetyPolicyRegistry classification (`policy_match` / `uncertain_fail_closed`); a misrouted HITL with no vocabulary hit usually means a missing term in the versioned registry, not an edges bug. |
| context metrics missing, prompt pollution | `phone_agent/graph/context.py`, `phone_agent/graph/nodes/plan.py`, `phone_agent/graph/trace.py` | Request-only compaction, privacy redaction, selected_sections, context budget. |
| result/trace missing | `evals/run_eval.py`, `phone_agent/agent.py`, `phone_agent/graph/trace.py` | RunResult serialization, trace writer, eval summary shape. |

## Remote grounding — explicitly NOT supported

| Stale env var | Status |
|---|---|
| `PHONE_AGENT_REMOTE_GROUNDING_BASE_URL` | ignored (provider reverted in `e0a2e4b`) |
| `PHONE_AGENT_REMOTE_GROUNDING_API_KEY` | ignored |
| `PHONE_AGENT_REMOTE_GROUNDING_MODEL` | ignored |
| `PHONE_AGENT_REMOTE_GROUNDING_PROFILE` | ignored |
| `PHONE_AGENT_REMOTE_GROUNDING_REASONING_EFFORT` | ignored |

If a `.env` still contains these, the preflight report should treat them as
stale configuration noise, not as a working remote-grounding setup.
