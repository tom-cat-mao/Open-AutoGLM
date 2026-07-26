# Source Map

This file maps live diagnosis symptoms to Open-AutoGLM source files. It is the
reference the SKILL.md "Source Mapping" table and `run_diagnosis.py`
`SOURCE_RULES` build on.

## Architecture note

The reflection / finish-gate layer replaced the old `phone_agent/graph/task_goal.py`
module with a declarative `GoalContract` system (see P0 constraint #13a in
`AGENTS.md`). When a trace surfaces reflection/finish signals, inspect the goal
files, not `task_goal.py` (which no longer exists).

Reflection and acceptance are two separate nodes (split in commit `46f5bd6`):

```text
START → goal → plan → execute → [confirm|takeover|acceptance|reflect|replan|end]
                                 └ acceptance → after_acceptance → [takeover|replan→goal|end]
```

`reflect` answers "did this action work?" every step. `acceptance` answers "is the
whole task done?" and only on a finish claim — it is the sole finish gate. Shared
post-action observation lives in `nodes/observation_capture.py` so the two cannot
disagree about the current screen. Route finish-gate signals to
`nodes/acceptance.py`, not `nodes/reflect.py`.

Finish-gate comparisons run in one value domain: raw observed text. Commit
`6b133b4` removed the sha256/title-stub projections from `ExpectedOutcome` and
`object_selected_evidence()` in favour of a bounded raw `evidence_summary`, since
the compiler was emitting digests while the evaluator compared raw screen text —
structurally unsatisfiable with a fully green test suite. `sha256:` stubs are still
accepted as a legacy fallback via `LEGACY_SHA256_STUB_PATTERN`. Prompt/runtime
carries raw comparison text by design; privacy enforcement is at
trace/checkpoint/log egress only (see P0 #10).

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
| `model_reflection_failed`, `repeated_action`, wrong reflection verdict, repeated wait, `postcondition_unverified`, `after_observation_unavailable`, `missing_postconditions`, `dynamic_change_only`, `verifier_unknown`, `verifier_failure`, `context_lost` | `phone_agent/graph/nodes/reflect.py`, `phone_agent/graph/nodes/observation_capture.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/expected_outcome.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/predicates.py` | Per-step reflection only ("did this action work?"); it no longer decides task completion. Verifier precedence, postcondition matching, failure memory and repeated-failure detection. `after_observation` comes from the shared `nodes/observation_capture.py` and is reused by acceptance, so a reflect/acceptance disagreement about the current screen starts there. `context_lost` is emitted from plan, reflect and acceptance through that shared capture — check which node before attributing it. |
| `goal_not_satisfied`, `finish_validation_unknown`, `finish_validation_failure`, `needs_recompile`, `matched_terminal_evidence`, `missing_terminal_evidence`, `soft_match_accepted`, `programmatic_contradiction_override`, `acceptance_no_contract`, `acceptance_hard_veto`, `acceptance_error`, `pure_evaluation_degraded`, `typed_fact_not_yet_collected` | `phone_agent/graph/nodes/acceptance.py`, `phone_agent/graph/goal_evaluator.py`, `phone_agent/graph/goal.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/goal_evidence.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/predicates.py`, `phone_agent/graph/nodes/observation_capture.py`, `phone_agent/graph/compatibility_adapters.py`, `phone_agent/graph/runtime_goal.py` | The finish gate is its own node (`acceptance`), split out of reflect in `46f5bd6`. It runs only on a finish claim, authority ordered hard veto > hard confirm > semantic judgement, fail-closed throughout. A `vlm_judge` criterion not named in `matched_terminal_evidence` is `missing` (hard gate). `acceptance_no_contract` is a fail-closed rejection whose root cause is the goal layer, not this node. `acceptance_hard_veto` / `programmatic_contradiction_override` mean programmatic signals beat the model's self-attestation — trust the programmatic side. `needs_recompile` has no writer today. `soft_match_accepted` means the finish relied on the detail-only soft match (evidence relaxation, no content confirmation) — verify the opened detail page manually. A criterion parked on `typed_fact_not_yet_collected` means predicate and provider disagree; check `value_domain` alignment. |
| `capability_missing`, `capability_unavailable`, `capability_rejected` | `phone_agent/actions/capability.py`, `phone_agent/actions/receipt.py`, `phone_agent/graph/nodes/execute.py` | ToolCapability registry coverage for the action name; `implementation_status=unavailable` means the stub action now fails closed instead of reporting pseudo-success; ActionReceipt only describes dispatch, never Goal progress. |
| `unsupported_semantics`, `needs_goal_clarification`, `goal_contract_invalid`, `goal_approval_replacement_inadequate`, `runtime_goal_binding_invalid`, `runtime_goal_binding_unavailable`, `runtime_goal_context_missing`, `task_binding_mismatch`, `required_criteria_missing`, `predicate_unobservable`, `predicate_domain_mismatch`, `contract_adequacy_inadequate`, `contract_adequacy_needs_clarification`, `contract_adequacy_degraded` | `phone_agent/graph/nodes/goal_node.py`, `phone_agent/graph/goal_requirements.py`, `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal.py`, `phone_agent/graph/predicates.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/goal_binding.py` | Adequacy has three severities: structural → `inadequate` (takeover), semantic → `degraded` (keep working, weaker verification), ambiguous → `needs_clarification`. `STRUCTURAL_REASON_CODES` = {`task_binding_mismatch`, `required_criteria_missing`, `predicate_unobservable`, `predicate_domain_mismatch`}. `predicate_unobservable` = no fact provider can emit it; `predicate_domain_mismatch` = the predicate's declared `value_domain` (raw_text/digest/identifier/scalar/structured) differs from what the provider actually produces. These two exist to move "this contract can never be satisfied" from latent runtime failure to a compile-time rejection — fix the predicate binding or provider, never widen the gate. Another common root cause: task verb not in `TaskRequirementExtractor._OPERATIONS` (limited zh/en keyword set) → `operation_kind=unknown`. Check `task_requirement_set.safe_projection` in trace before changing code. **`contract_adequacy` only exists on the `goal_compile_result` trace event — `RunResult`/`result.json` do not carry `contract_adequacy_status`.** |
| `goal_resume_hmac_mismatch`, `goal_resume_rehydration_failed`, `goal_resume_untrusted`, `trusted_goal_resume_invalid` | `phone_agent/checkpoint/goal_resume.py`, `phone_agent/checkpoint/serde.py` | HMAC key consistency across resume; serde collapses `goal_evidence_ledger` to `[]` at checkpoint egress — progress must come from the trusted_goal_resume projection, never from checkpoint content. |
| `action_safety_rejected`, HITL miscount with policy mismatch | `phone_agent/config/policy.py`, `phone_agent/actions/safety.py` | SafetyPolicyRegistry classification (`policy_match` / `uncertain_fail_closed`); a misrouted HITL with no vocabulary hit usually means a missing term in the versioned registry, not an edges bug. |
| context metrics missing, prompt pollution | `phone_agent/graph/context.py`, `phone_agent/graph/nodes/plan.py`, `phone_agent/graph/trace.py` | Request-only compaction, privacy redaction, selected_sections, context budget. |
| result/trace missing | `evals/run_eval.py`, `phone_agent/agent.py`, `phone_agent/graph/trace.py` | RunResult serialization, trace writer, eval summary shape. |
| `existential_match`, `not_observed_in_view`, `existential_inconclusive`, `same_tier_conflict` | `phone_agent/graph/predicates.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/goal_evaluator.py` | Evidence scope per `(predicate, source)`: per-node accessibility facts fold existentially, so one hit among many non-matching siblings is a match and zero hits is `unknown` — never a contradiction. Summary sources (`visual_region`/`whole_screen`) keep unanimity, where a differing value IS real counter-evidence. `element_scoped` (`ui.toggle_state`, `ui.object_rank`) resolves `unknown` on multi-element screens by design. |
| `binding_never_observed`, `predicate_unobservable` on a prose binding | `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal_requirements.py`, `phone_agent/graph/goal_evidence.py` | Binding provenance: an `expected_value` must be a screen literal (quoted span or entity span), never a criterion description. `binding_never_observed` is `degraded`/diagnostic only and must not veto — the literal may need scrolling. |
| `advancing` / `exploring` / `stuck`, `novelty_streak`, unexpected takeover | `phone_agent/graph/context.py` (`trajectory_liveness`), `phone_agent/graph/edges.py`, `phone_agent/graph/nodes/reflect.py` | P0 #13b trajectory liveness. Progress is goal-relative only (criterion movement, or a state not visited before); surface/hash/mark changes and `typed_text_present` oscillate and are NOT progress. `exploring` is the intended state for search tasks. `stuck` replans before any takeover. |
| `observation_retry_count` at limit, `acceptance_round_count` growth | `phone_agent/graph/edges.py`, `phone_agent/graph/nodes/observation_capture.py`, `phone_agent/graph/nodes/acceptance.py`, `phone_agent/config/policy.py` | Three former meanings of `retry_count` are now separate: infrastructure retries (takeover at limit), acceptance rounds (replan, not error), and liveness (see above). `max_steps` exhaustion is an incomplete report, never a takeover. |

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
