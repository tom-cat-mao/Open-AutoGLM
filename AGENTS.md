# Open-AutoGLM Agent Guide

## What This Project Is

Open-AutoGLM is a LangGraph-based Android Phone Agent. It takes a natural-language task,
captures screenshots, asks a VLM to decide actions, executes them on a real Android device,
and reflects on results in a loop.

```
Screenshot -> VLM inference -> Parse action -> Execute on device -> Reflect -> Repeat
```

The core loop is implemented by LangGraph `StateGraph`. Detailed architecture lives in
`CLAUDE.md` / `README.md`; the graph topology source of truth is the
`phone_agent/graph/builder.py` docstring; roadmap state is in `docs/future-roadmap.md`.

## Project Layout

```
phone_agent/
  agent.py          # PhoneAgent entry point (run / run_structured / run_live)
  graph/            # LangGraph StateGraph: nodes, edges, state, tools, trace, observation
    goal.py             # Declarative GoalContract + typed CriterionSpec data model
    goal_compiler.py    # Goal contract compilation chain: External > LLM (self-check + 1 repair pass) > Heuristic
    goal_requirements.py # Independent TaskRequirementSet (app metadata) + structural ContractAdequacyValidator
    goal_evidence.py    # Bounded privacy-safe evidence ledger: model_observation / screen_text_digest / effect_event / seals
    goal_evaluator.py   # Per-criterion ledger fold consumed by the acceptance node
    guidance.py         # Mechanism-level failure advisories (parse_failure → mechanism_suggestion)
    predicates.py       # Typed predicates, exact code sensors, evidence authority
    fact_providers.py   # Concrete node-local fact providers (exact device-state sensors only)
    runtime_goal.py     # Per-run private Goal values; never serialized
    compatibility_adapters.py # Legacy vocabulary shadow telemetry only
    runtime_observation.py # Node-local screenshot context; never serialized
    nodes/goal_node.py  # Compiles GoalContract once at task start; optional HITL approval
    nodes/plan.py       # Plan node (cacheable goal block; one guided validation replan via after_plan)
    nodes/execute.py    # Safety gate + capability check + dispatch; finish only sets pending_finish
    nodes/reflect.py    # Reflect node: single-step verdict + model screen-reads (criteria_observations) + gap list channel
    nodes/acceptance.py # Finish-claim acceptance: ledger fold, rejection feedback + acceptance_verdicts
  actions/          # Action IR pipeline: adapter, validator, repair, safety gate
    capability.py   # ToolCapability registry; availability enforced after Safety Gate
    receipt.py      # ActionReceipt dispatch evidence; never Goal progress
  model/            # ModelClient (OpenAI-compatible, prompt cache support)
  adb/              # Android device abstraction
  grounding/        # Mark providers: accessibility tree, LocateAnything
  config/           # System prompts, app registries, i18n
    app_registry.py # AppIdentity, installed inventory, and LaunchPolicy boundaries
    policy.py       # Versioned safety vocabulary and verification thresholds
  checkpoint/       # Egress redaction + trusted Goal resume contract; in-process HITL resume saver is opt-in (enable_hitl_resume)
main.py             # CLI entry point (task text is a positional argument)
bench/grounding/    # Grounding benchmark suite
tests/              # pytest suite
```

## Common Commands

```bash
# Run PhoneAgent (task text is a positional argument)
.venv/bin/python main.py --device-id <serial> "task description"

# Run tests
.venv/bin/pytest tests/ -q

# Run grounding benchmark
.venv/bin/python -m bench.grounding.run_locateanything --manifest <path>

# Web research
smart-search search "query" --format json
```

## How to Work on This Project

- **Always `.venv/bin/python` / `.venv/bin/pytest` / `.venv/bin/pip`** — never system Python.
- **File search**: use `rg` / `rg --files`; do not use `find` or `grep`.
- **Web research**: use `smart-search-cli` skill or Context7 for third-party API docs; prefer them over generic built-in web search.
- **Real device diagnosis**: prefer `.agents/skills/phone-agent-live-diagnosis/SKILL.md`; default entry: `.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "<target>"` (`.claude/skills/phone-agent-live-diagnosis` symlinks to it).
- **Escalation**: sandbox/network/GPU/Metal/ADB `Operation not permitted` failures → rerun the failing command through the agent frontend's escalated/non-sandboxed permission mechanism with a user-readable justification. Never relax the sandbox for `rm`, `git reset`, `git push --force`, or other destructive commands.
- **Planning/review**: use OMX canonical workflow: `$ralplan` = Planner → Architect → Critic; `$autopilot` = deep-interview → ralplan → ultragoal → code-review → ultraqa. Do not load heavy design workflows for ordinary tasks.
- **OMX subagents**: use `.codex/agents/`; skills from `.codex/skills/`. AGENTS.md is the orchestration brain.

## P0 Constraints (Must Never Violate)

These are the hard invariants. Violating them causes crashes, data loss, or security issues.
The graph topology source of truth is the `phone_agent/graph/builder.py` docstring; architecture
detail lives in `CLAUDE.md` / `README.md`.

| # | Domain | Constraint |
|---|--------|------------|
| 1 | **Coordinate** | Model outputs 0-1000 relative coords. Tool must call `convert_relative_to_absolute()` before device execution. |
| 2 | **Action Parse** | Old text DSL (`parse_action()`) is **deleted**. Only `json_schema` / `tool_calls` / `auto` via adapter. Never `eval()`. If a future text parser is needed, use `ast.parse` + `ast.literal_eval`. |
| 3 | **Image** | Strip historical images from messages after each step via `MessageBuilder.remove_images_from_message()`. Only current screenshot goes to next VLM inference. |
| 4 | **HITL** | Payment/privacy → `confirm_node`; login/captcha → `takeover_node`. Both use LangGraph `interrupt()`. |
| 5 | **Edge Terminal Guard** | `after_goal()` / `after_plan()` / `after_execute()` / `after_interrupt()` / `after_acceptance()` / `should_continue()` must check `state.finished` or `state.error` first, return `"end"` (or the non-loop route) before any other routing. Stale `pending_interrupt`/`pending_execute`/`action_parsed` must not route to `confirm`/`takeover`/`reflect`/`replan`. |
| 6 | **messages reducer** | `plan_node` returns only new messages (append). `execute_node` returns full rebuild (replace). Wrong semantics = token explosion. |
| 7 | **ActionIR Pipeline** | Adapter → Validator → (Repair → Validator) → Safety Gate → Executor. Repair never after Safety Gate. Executor only receives validated + safety-approved IR. Fail-closed at every stage. |
| 8 | **Grounding** | Only `target_mark_id` is executable. `LocateAnything`/`Fake`/`OCR`/`UIAutomator`/`SoM` only generate marks. `target_text_hint` is a hint, not an executable target. Default `PHONE_AGENT_GROUNDING_PROVIDER=hybrid` (accessibility tree first, LocateAnything fallback). |
| 9 | **Grounding Fail-Closed** | Grounding failure (bad screenshot, provider missing, low confidence, bad bbox, stale/hash mismatch, multi-candidate) → fail-closed (wait/takeover/reobserve). Never fall back to raw VLM coordinate tap or pass black image to model. |
| 10 | **Privacy** | Phone/email/order/captcha/API key/JWT/base64 in state → regex-redact on write. Trace/checkpoint → no raw screenshot, hint, API key, or response. Prompt-side handling provides no content-level privacy because the screenshot channel already carries full content; privacy enforcement lives at trace/checkpoint/log egress, password-field source suppression, and the high-risk regex set. Predicates declare `evidence_scope`; accessibility existential absence is `unknown`, never contradiction. |
| 11 | **Context** | `context_mode=inject` (default). Context injection must not bypass HITL. `prompt_version` only supports `context_harness_v1`. |
| 12 | **Device** | All device ops through `DeviceFactory` → `phone_agent/adb/`. No direct ADB calls. |
| 13 | **Reflection** | Per-step verifier answers only “did this action take effect?” and must not judge task progress. `reflect_node` maintains `reflection_verdict`/`failure_cause`/`suggested_strategy`; Plan reads them next round. ExpectedOutcome is validated before model reflection. |
| 13a | **Finish Gate** | `finish` is a claim, not execution success. Execute sets `pending_finish`; Acceptance validates `GoalContract` evidence before `finished=True`. Criterion evidence is settled layer by layer from the ledger (seal > ledger observed > judge reference); a judge `satisfied` verdict must carry a valid `evidence_step` reference; programmatic contradiction requires a positive counter-observation and overrides self-attestation; existential absence is `unknown`. Unsettled with no reference / missing evidence → `unknown` → `goal_not_satisfied` and replan, never success. `element_scoped` `ui.toggle_state`/`ui.object_rank` remain fail-closed `unknown` on multi-element screens until evidence is bound to a selected element. |
| 13b | **Trajectory / Progress Liveness** | `trajectory_liveness` alone answers “is the trajectory advancing, exploring, or stuck?” from criterion history and novel `(surface, screen_id)` states. The separate `progress_exhaustion()` detector is also pure ledger/history logic: it must not read single-step verdicts or UI-relative oscillation signals, and novel screens alone never prove progress. Stuck/dry signals may request a model declaration (`finish` / `take_over` / `progress_claim`) or end as `progress_evidence_exhausted` only after rejected/missing claims and grace steps. |
| 14 | **No Force Push** | Never `git push --force` to `main` or `feature/langgraph-refactor`. |
| 15 | **No Auto-Commit** | Don't create commits unless explicitly requested. |

## Doc & Skill Routing (Load on Demand)

The legacy TraeCLI rule tree (`.trae/`) is retired. Current sources of truth:

| When you need... | Load... |
|---|---|
| Graph topology / node contracts | `phone_agent/graph/builder.py` docstring + `CLAUDE.md` Architecture section |
| GoalContract / acceptance semantics | P0 #13/13a/13b above + `phone_agent/graph/goal*.py` docstrings |
| Roadmap & phase status | `docs/future-roadmap.md` |
| Historical batch execution logs | `docs/archive/` |
| Planning/design review | `.codex/skills/ralplan/SKILL.md` (OMX Planner → Architect → Critic) |
| Autopilot execution | `.codex/skills/autopilot/SKILL.md` |
| Real-device diagnosis | `.agents/skills/phone-agent-live-diagnosis/SKILL.md` |

## Version Management

When a Phase completes:
1. Update `docs/future-roadmap.md` (current status + mark the phase done).
2. If architecture/API/eval/trace changed: sync `README.md`, `AGENTS.md`, `CLAUDE.md` together — they are kept in lockstep.
3. Commit (only when explicitly requested): `feat(graph): <goal>` / `feat(grounding): <goal>` / `feat(actions): <goal>`.

## Compact Instructions

When auto-compacting, preserve: current task, P0 constraints table, doc routing table, and in-progress roadmap state.
