# Open-AutoGLM Agent Guide

## What This Project Is

Open-AutoGLM is a LangGraph-based Android Phone Agent. It takes a natural-language task,
captures screenshots, asks a VLM to decide actions, executes them on a real Android device,
and reflects on results in a loop.

```
Screenshot -> VLM inference -> Parse action -> Execute on device -> Reflect -> Repeat
```

The core loop is implemented by LangGraph `StateGraph`. Detailed architecture and roadmap
state are in `.trae/rules/graph.mdc` (load on demand).

## Project Layout

```
phone_agent/
  agent.py          # PhoneAgent entry point
  graph/            # LangGraph StateGraph: nodes, edges, state, tools, trace, observation
    goal.py             # Declarative GoalContract + typed CriterionSpec data model
    goal_compiler.py    # Goal contract compilation chain: External > LLM > Heuristic
    goal_requirements.py # Independent TaskRequirementSet + ContractAdequacyValidator
    goal_evidence.py    # Bounded privacy-safe criterion evidence ledger
    goal_evaluator.py   # Pure typed ledger fold + legacy migration evaluator
    predicates.py       # Typed predicates, neutral facts, matchers, evidence authority
    fact_providers.py   # Concrete node-local fact providers + authority resolution
    runtime_goal.py     # Per-run private Goal values; never serialized
    compatibility_adapters.py # Legacy vocabulary shadow telemetry only
    runtime_observation.py # Node-local screenshot context; never serialized
    nodes/goal_node.py  # Compiles GoalContract once at task start; optional HITL approval
    nodes/plan.py       # Plan node (injects goal_contract_block as separate cacheable message)
    nodes/reflect.py    # Reflect node (GoalEvaluator integration, reflect context window)
    actions/          # Action IR pipeline: adapter, validator, repair, safety gate
      capability.py   # ToolCapability registry; availability enforced after Safety Gate
      receipt.py      # ActionReceipt dispatch evidence; never Goal progress
  model/            # ModelClient (OpenAI-compatible, prompt cache support)
  adb/              # Android device abstraction
  grounding/        # Mark providers: accessibility tree, LocateAnything
  config/           # System prompts, app registries, i18n
    app_registry.py # AppIdentity, installed inventory, and LaunchPolicy boundaries
    policy.py       # Versioned safety vocabulary and verification thresholds
  checkpoint/       # Redaction + trusted Goal resume contract; runtime saver not enabled
main.py             # CLI entry point
bench/grounding/    # Grounding benchmark suite
tests/              # pytest suite
```

## Common Commands

```bash
# Run PhoneAgent
.venv/bin/python main.py --task "task description" --device-id <serial>

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
- **Web research**: use `smart-search-cli` skill or Context7 for third-party API docs. Never use TraeCLI built-in WebSearch/WebFetch unless smart-search is unavailable.
- **Real device diagnosis**: prefer `.agents/skills/phone-agent-live-diagnosis/SKILL.md` (ZCode-discoverable skill); default entry: `.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "<target>"`. The legacy copy under `.trae/skills/phone-agent-live-diagnosis/` remains for TraeCLI.
- **Escalation**: sandbox/network/GPU/Metal/ADB/Operation not permitted → in ZCode, rerun the failing diagnosis command via the Bash tool with `dangerouslyDisableSandbox: true` and a user-readable justification; in TraeCLI, retry with `sandbox_permissions="require_escalated"`. Never relax the sandbox for `rm`, `git reset`, `git push --force`, or other destructive commands.
- **Planning/review**: use OMX canonical workflow: `$ralplan` = Planner → Architect → Critic; `$autopilot` = deep-interview → ralplan → ultragoal → code-review → ultraqa. Do not load heavy design workflows for ordinary tasks.
- **OMX subagents**: use `.codex/agents/`; skills from `.codex/skills/`. AGENTS.md is the orchestration brain.

## P0 Constraints (Must Never Violate)

These are the hard invariants. Violating them causes crashes, data loss, or security issues.
Full subsystem constraints are in `.trae/rules/*.mdc` — load them when touching those files.

| # | Domain | Constraint |
|---|--------|------------|
| 1 | **Coordinate** | Model outputs 0-1000 relative coords. Tool must call `convert_relative_to_absolute()` before device execution. |
| 2 | **Action Parse** | Old text DSL (`parse_action()`) is **deleted**. Only `json_schema` / `tool_calls` / `auto` via adapter. Never `eval()`. If a future text parser is needed, use `ast.parse` + `ast.literal_eval`. |
| 3 | **Image** | Strip historical images from messages after each step via `MessageBuilder.remove_images_from_message()`. Only current screenshot goes to next VLM inference. |
| 4 | **HITL** | Payment/privacy → `confirm_node`; login/captcha → `takeover_node`. Both use LangGraph `interrupt()`. |
| 5 | **Edge Terminal Guard** | `after_execute()` / `after_interrupt()` / `should_continue()` must check `state.finished` or `state.error` first, return `"end"` before any other routing. Stale `pending_interrupt`/`pending_execute`/`action_parsed` must not route to `confirm`/`takeover`/`reflect`/`replan`. |
| 6 | **messages reducer** | `plan_node` returns only new messages (append). `execute_node` returns full rebuild (replace). Wrong semantics = token explosion. |
| 7 | **ActionIR Pipeline** | Adapter → Validator → (Repair → Validator) → Safety Gate → Executor. Repair never after Safety Gate. Executor only receives validated + safety-approved IR. Fail-closed at every stage. |
| 8 | **Grounding** | Only `target_mark_id` is executable. `LocateAnything`/`Fake`/`OCR`/`UIAutomator`/`SoM` only generate marks. `target_text_hint` is a hint, not an executable target. Default `PHONE_AGENT_GROUNDING_PROVIDER=hybrid` (accessibility tree first, LocateAnything fallback). |
| 9 | **Grounding Fail-Closed** | Grounding failure (bad screenshot, provider missing, low confidence, bad bbox, stale/hash mismatch, multi-candidate) → fail-closed (wait/takeover/reobserve). Never fall back to raw VLM coordinate tap or pass black image to model. |
| 10 | **Privacy** | Phone/email/order/captcha/API key/JWT/base64 in state → regex-redact on write. Trace/checkpoint → no raw screenshot, hint, API key, or response. Prompt-side handling provides no content-level privacy because the screenshot channel already carries full content; privacy enforcement lives at trace/checkpoint/log egress, password-field source suppression, and the high-risk regex set. Predicates declare `evidence_scope`; accessibility existential absence is `unknown`, never contradiction. |
| 11 | **Context** | `context_mode=inject` (default). Context injection must not bypass HITL. `prompt_version` only supports `context_harness_v1`. |
| 12 | **Device** | All device ops through `DeviceFactory` → `phone_agent/adb/`. No direct ADB calls. |
| 13 | **Reflection** | Per-step verifier answers only “did this action take effect?” and must not judge task progress. `reflect_node` maintains `reflection_verdict`/`failure_cause`/`suggested_strategy`; Plan reads them next round. ExpectedOutcome is validated before model reflection. |
| 13a | **Finish Gate** | `finish` is a claim, not execution success. Execute sets `pending_finish`; Acceptance validates `GoalContract` evidence before `finished=True`. `finish.matched_terminal_evidence` names each required criterion; programmatic contradiction requires a positive counter-observation and overrides self-attestation; existential absence is `unknown`. Unknown/missing evidence → `goal_not_satisfied` and replan, never success. `vlm_judge` criteria omitted from the finish claim are `missing`. `element_scoped` `ui.toggle_state`/`ui.object_rank` remain fail-closed `unknown` on multi-element screens until evidence is bound to a selected element. |
| 13b | **Trajectory Liveness** | `trajectory_liveness` alone answers “is the trajectory advancing, exploring, or stuck?” from criterion history and novel `(surface, screen_id)` states. It must not read single-step verdicts or UI-relative oscillation signals. Stuck routing replans first; takeover means only a human can complete or recover the task. |
| 14 | **No Force Push** | Never `git push --force` to `main` or `feature/langgraph-refactor`. |
| 15 | **No Auto-Commit** | Don't create commits unless explicitly requested. |

## Rule Routing (Load on Demand)

| When you touch... | Load... |
|---|---|
| `phone_agent/graph/**`, `phone_agent/agent.py` | `.trae/rules/graph.mdc` + `.trae/rules/architecture.mdc` |
| `phone_agent/graph/goal*.py` | `.trae/rules/graph.mdc` (GoalContract + compiler + evaluator) |
| `phone_agent/actions/**` | `.trae/rules/actions.mdc` |
| `phone_agent/adb/**`, `device_factory.py` | `.trae/rules/devices.mdc` |
| Any Python file | `.trae/rules/style.mdc` (naming, typing, testing) |
| `phone_agent/model/**` | `.trae/rules/architecture.mdc` |
| Planning/design review | `.codex/skills/ralplan/SKILL.md` (OMX Planner → Architect → Critic) |
| Autopilot execution | `.codex/skills/autopilot/SKILL.md` |
| Grounding benchmark | `.trae/rules/tools.mdc` |

## Version Management

When a Phase completes:
1. Update `.trae/rules/graph.mdc` Roadmap (mark Phase ✅).
2. If architecture/API/eval/trace/TraeCLI changed: sync `README.md`, `docs/future-roadmap.md`, AGENTS.md, `.trae/traecli.toml`, relevant `.trae/rules/*.mdc`.
3. Commit: `feat(graph): <goal>` or `feat(trae): <goal>` or `feat(grounding): <goal>`.

## Compact Instructions

When auto-compacting, preserve: current task, P0 constraints table, rule routing table, and in-progress roadmap state.
