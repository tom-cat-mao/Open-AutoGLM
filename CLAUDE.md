# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Open-AutoGLM is a LangGraph-based Android Phone Agent. A natural-language task is executed in a
**Plan-Execute-Reflect** loop: capture a screenshot → ask a VLM (OpenAI-compatible API) for an action
→ parse/ground/validate/safety-check it → execute on a real Android device via ADB → reflect on the
result → repeat until the task goal contract is satisfied. The model never taps raw coordinates on
the device; the harness grounds every screen target to an atomic mark before execution.

Entry point: `main.py` (CLI). Library entry: `phone_agent.PhoneAgent` (`run()` returns a string,
`run_structured()` returns a `RunResult` with eval/trace metrics).

## Commands

**Always use the repo venv — never system Python.** All commands below assume `.venv/bin/`.

```bash
# Install (first time or after pulling)
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ".[dev]"          # pytest, black, mypy, ruff

# Run the agent (needs a real device + model server)
.venv/bin/python main.py --base-url http://localhost:8000/v1 --model autoglm-phone-9b "打开美团搜索附近的火锅店"
.venv/bin/python main.py --lang en --base-url http://localhost:8000/v1 "Open Chrome browser"
.venv/bin/python main.py --list-apps       # show supported app package registry

# Tests
.venv/bin/pytest tests/ -q                 # full suite
.venv/bin/pytest tests/graph -v            # graph subsystem only
.venv/bin/pytest tests/graph/test_edges.py -v          # single file
.venv/bin/pytest tests/graph/test_edges.py::test_after_execute_finish -v   # single test
.venv/bin/pytest -k "goal" -v              # by name pattern

# Eval smoke (no model/device needed)
.venv/bin/python evals/run_eval.py --dry-run
.venv/bin/python evals/run_eval.py --dry-run --context-mode inject --trace-dir .traces/smoke

# Grounding benchmark (needs Apple Silicon + Metal + mlx-vlm; install mlx-vlm manually in MLX env)
.venv/bin/python -m bench.grounding.run_locateanything --manifest <path> ...
.venv/bin/python -m bench.grounding.score_predictions --manifest <path> --predictions <path> ...

# Lint/format (also runs via pre-commit)
.venv/bin/ruff check .                     # import sorting (--select I) is the enforced subset
.venv/bin/ruff format .
.venv/bin/mypy phone_agent
```

Pre-commit hooks (`.pre-commit-config.yaml`): **ruff** (`--select I` import sort + `ruff-format`),
**typos** spell check, **pymarkdown** markdown lint. `phone_agent/config/apps.py` and `README_en.md`
are excluded. The `ruff` hook uses `--fix`, so it will rewrite import order on commit.

Python **3.10+** required. `mlx-vlm` (LocateAnything grounding) is intentionally excluded from
default requirements — it only works on Apple Silicon/Metal and must be installed manually in a
local MLX environment before running the real LocateAnything provider or its benchmark.

## Architecture: the Plan-Execute-Reflect StateGraph

The whole agent is one compiled LangGraph `StateGraph` built in `phone_agent/graph/builder.py`.
State lives in `AgentState` (`graph/state.py`), a large `TypedDict` holding the conversation,
screen/observation, grounding, error taxonomy, context/observability, verifier, HITL, and goal
fields. Dependencies (model client, device factory, trace writer, config) are injected via the
LangGraph `config["configurable"]` dict, **not** imported globally inside nodes.

Topology (`builder.py` — its docstring is the source of truth):
```
START → goal → plan → execute → [confirm|takeover|acceptance|reflect|replan|end]
                         ├─ plan → after_plan → [execute|replan→plan]  (one guided self-loop on validation/adapter failure)
                         ├─ confirm → after_interrupt → [execute|reflect|end]
                         ├─ takeover → after_interrupt → [reflect|end]
                         ├─ acceptance → after_acceptance → [takeover|replan→goal|end]
                         ├─ reflect → should_continue → [takeover|replan→goal|acceptance|end]
                         └─ replan → goal → plan  (only internal no-observation capabilities)
```

`reflect` answers "did this action take effect?" on every step; `acceptance` answers
"is the task complete?" and runs only on a model `finish` claim. Resource fuses
(`step_cap`, optional wall-clock cap) stop the run honestly without fabricating a
finish claim.

- **`goal_node`** — compiles a declarative `GoalContract` (`graph/goal.py`) once at task start via the
  `External > LLM > Heuristic` chain (`goal_compiler.py`). On replan loops it is a no-op unless
  `needs_recompile`. Optional `require_goal_approval` triggers a LangGraph `interrupt()` for HITL.
  The contract (success criteria, constraints, non-goals, verification strategy) is **independent of
  message-history compaction** and survives context-window trimming.
- **`plan_node`** — captures a screenshot, builds mark providers → `Observation` → `MarkRegistry` /
  `ObjectRegistry`, builds the prompt (system + goal block + screen info + marks/objects + context),
  calls the model, and parses the response through the **Adapter → grounding → Validator → Repair →
  Validator** pipeline to produce a canonical `ActionIR` plus a sibling `ExpectedOutcome`
  postcondition contract. On a validation/adapter failure after repair it writes a structured
  `parse_failure` (expected/found) + `mechanism_suggestion` (`graph/guidance.py`) and takes **one**
  guided replan per run via `after_plan` before going terminal.
- **`execute_node`** — runs the safety gate, dispatches the validated `ActionIR` via
  `dispatch_tool()`, strips historical images from the message history, appends the assistant
  message. `finish` is a *claim* here — it sets `pending_finish` and routes to **acceptance**,
  never straight to END.
- **`reflect_node`** — re-captures the screen, builds an after-observation, runs the deterministic
  verifier (`graph/verifier.py`) for the single-step "did this action take effect?" verdict.
  High-confidence deterministic verdicts skip the VLM; otherwise it builds an isolated reflect
  prompt (not appended to `state["messages"]`) and parses a structured JSON reflection. It judges
  one action, never overall task progress. It also emits pure progress-exhaustion telemetry from
  ledger/history structure; that detector does not read single-step verdicts.
- **`acceptance_node`** — validates a pending `finish` claim against the `GoalContract` evidence
  ledger (`graph/goal_evaluator.py` fold). Criteria settle by authority order (seal > latest
  observed > judge citation > programmatic); unknown goal evidence **never auto-upgrades to
  success**. On rejection it writes structured rejection feedback, per-criterion verdicts
  (`acceptance_verdicts`), and the judge line into state for the next plan round.
- **Resource/progress review** — `step_cap` and optional `wall_clock_cap_seconds` are user-domain
  fuses, not task judgements. When evidence is dry for several rounds, Plan asks the model to choose
  `finish`, `take_over`, or provide a `progress_claim`; `validate_progress_claim()` accepts only
  strong ledger evidence such as criterion rank-up, value digest change, effect event, new latch, or
  stage advance.
- **`confirm_node` / `takeover_node`** — HITL via LangGraph `interrupt()`. Confirm handles
  payment/privacy-style sensitive Taps (then resumes into execute); takeover handles
  login/captcha/manual handoff. With the optional checkpointer (`AgentConfig.enable_hitl_resume` +
  `run_live()`) interrupts pause the graph and resume in-process via `Command(resume=...)`.

### The canonical Action pipeline (the single execution path)

Every provider output (JSON / aggregated `tool_calls` / auto) funnels through one fail-closed chain.
There is **no** legacy text-DSL parser — it was deleted. The path, in `phone_agent/actions/`:

```
provider JSON/tool_calls
  → adapter.py        (adapt_json_action / adapt_tool_calls: whitelist fields, reject dangerous
                       fields, coerce to canonical {do|finish|intent} metadata)
  → grounding.py      (only for IntentIR: compile target_mark_id / object selector → ONE atomic
                       mark in MarkRegistry; IntentIR is never accepted by executor)
  → validator.py      (semantic/schema/range checks; ActionValidationError carries stable code)
  → repair.py         (one narrow repair attempt on validation failure, then re-validate)
  → safety.py         (decide_safety → approved/confirm/takeover/rejected; never executes tools)
  → graph/tools/dispatch_tool → @tool fn → adb device op
```

Three IR types in `actions/ir.py`: **`ActionIR`** (`do`/`finish`, executable), **`IntentIR`**
(`intent`, provider-facing, must be grounded before validation), and `FinishActionDict` (carries
`matched_terminal_evidence[]` naming the GoalContract criteria it claims to satisfy — diagnostic
trace only, the naming gate is retired). The executor boundary accepts `ActionIR` only — never raw
`IntentIR`, never raw provider JSON.

### Grounding (screen targeting)

`target_mark_id` is the **only executable screen target**. `LocateAnything`/`Fake`/`OCR`/
`UIAutomator`/`SoM` providers only *generate marks* into a `MarkRegistry`; they never tap. The model
references a mark by id (or an object selector that the resolver compiles to one mark). Default
provider is `hybrid` = `FallbackMarkProvider([AccessibilityTreeProvider, LocateAnythingMLXProvider])`
from `grounding/factory.py`'s `build_mark_providers()` (plural — use this, not the legacy singular
`build_mark_provider()`). Any grounding failure (bad screenshot, missing provider, low confidence,
bad bbox, stale/hash mismatch, multi-candidate ambiguity) is **fail-closed** → reobserve/takeover,
never a fallback to raw VLM coordinate tap.

### Coordinates and images

The model emits **0-1000 relative coordinates**. The tool layer (`graph/tools/coords.py`) converts
to absolute device pixels via `convert_relative_to_absolute()`. After each step,
`MessageBuilder.remove_images_from_message()` strips historical images so only the current
screenshot reaches the next VLM inference — this keeps the request compact and is enforced by the
`messages_reducer`.

### `messages_reducer` (critical, easy to break)

`graph/state.py` defines a dual-mode reducer on `messages`:
- **plan_node** returns only *new* messages → **append** (system + user screenshot message).
- **execute_node** returns the *full rebuilt* list (images stripped + assistant appended) → **replace**.

The reducer heuristically distinguishes the two. Getting this wrong (returning a full list from plan,
or a single message from execute) causes token explosion or lost history. When editing plan/execute
returns, preserve this append-vs-replace semantic.

### Context & observability harness

`graph/context.py` implements the prompt/context layer (`context_mode`: `inject` default / `observe`
/ `off`). `select_plan_context()` / `select_reflect_context()` produce a redacted, budget-trimmed
context block (screen belief, failure memory, summarized history) injected into the prompt.
`compact_messages_for_request()` compacts only the messages sent to the model — it **must not** rewrite
`state["messages"]` or touch `action_raw`, `action_parsed`, `pending_execute`, `interrupt_result`,
`action_confirmed`, or any Safety/HITL routing field. Default `prompt_version="context_harness_v1"`
is the only supported prompt version.

### Trace & privacy

`graph/trace.py` writes `.traces/{trace_id}.jsonl` (one JSONL event per node/event). Screenshots,
prompts, API keys, task text, thinking, reflection, HITL messages, and raw target hints are
**redacted by default** (`trace_redact=True`). Only section IDs, counts, char lengths, approx tokens,
truncation status, and hash/length stubs are persisted. Never persist raw screenshots, raw XML,
raw private text, or API keys to trace/checkpoint/report paths.
Prompt-side handling does not claim content-level privacy because each model request already includes the full screenshot; retain high-risk regex redaction and password-field source suppression, while enforcing durable privacy at trace/checkpoint/log egress.

## Hard Invariants (P0 — never violate)

These are the load-bearing constraints. Violating them causes crashes, token explosion, false
success, or security/privacy leaks. The same table (with 13a/13b detail) lives in `AGENTS.md`;
the topology source of truth is the `builder.py` docstring.

1. **Coordinates** — model outputs 0-1000 relative; tools must call `convert_relative_to_absolute()`
   before device execution.
2. **Action parse** — old text DSL `parse_action()` is deleted. Only `json_schema`/`tool_calls`/`auto`
   via the adapter. Never `eval()`; if a future text parser is needed use `ast.parse` +
   `ast.literal_eval`.
3. **Images** — strip historical images after each step via `MessageBuilder.remove_images_from_message()`;
   only the current screenshot goes to the next inference.
4. **HITL** — payment/privacy → `confirm_node`; login/captcha → `takeover_node`. Both use LangGraph
   `interrupt()`.
5. **Edge terminal guard** — `after_goal()` / `after_plan()` / `after_execute()` / `after_interrupt()` /
   `after_acceptance()` / `should_continue()` must check `state.finished` or `state.error` **first**
   and return `"end"` (or the non-loop route) before any other routing. Stale
   `pending_interrupt`/`pending_execute`/`action_parsed` must not misroute into confirm/takeover/reflect.
6. **`messages_reducer`** — plan returns only new messages (append); execute returns a full rebuild
   (replace). Wrong semantics = token explosion.
7. **ActionIR pipeline order** — Adapter → Validator → (Repair → Validator) → Safety Gate → Executor.
   Repair never runs after the Safety Gate; the executor only receives validated + safety-approved IR.
   Fail-closed at every stage.
8. **Grounding** — only `target_mark_id` is executable. `target_text_hint` is a hint, not a target.
   Default `PHONE_AGENT_GROUNDING_PROVIDER=hybrid`.
9. **Grounding fail-closed** — grounding failure → fail-closed (wait/takeover/reobserve). Never fall
   back to raw VLM coordinate tap or pass a black image to the model.
10. **Privacy** — phone/email/order/captcha/API key/JWT/base64 in state → regex-redact on write.
    Trace/checkpoint → no raw screenshot, hint, API key, or response.
11. **Context** — `context_mode=inject` default; context injection must not bypass HITL.
    `prompt_version` only supports `context_harness_v1`.
12. **Device** — all device ops through `DeviceFactory` → `phone_agent/adb/`. No direct ADB calls.
13. **Reflection** — `reflect_node` maintains `reflection_verdict`/`failure_cause`/`suggested_strategy`;
    plan reads these next round. The deterministic verifier validates ExpectedOutcome first.
13a. **Finish gate** — `finish` is a claim, not success. Execute sets `pending_finish`; acceptance
    validates GoalContract evidence before `finished=True`. Criteria are settled by the ledger
    authority order (seal > latest observed > judge citation > programmatic); a judge `satisfied`
    must carry a valid `evidence_step` citation. Unsettled + no citation = `unknown`
    → `goal_not_satisfied` and replan; unknown never auto-upgrades to success.
    `finish.matched_terminal_evidence` is diagnostic trace only (naming gate retired).
13b. **Trajectory/progress liveness** — `trajectory_liveness` is advisory telemetry only. The
    separate `progress_exhaustion()` detector is also pure ledger/history logic, never reads
    single-step verdicts, and can only require a model declaration or end with
    `progress_evidence_exhausted` after rejected/missing claims and grace steps.
14. **No force push** — never `git push --force` to `main` or `feature/langgraph-refactor`.
15. **No auto-commit** — don't create commits unless explicitly requested.

## Where the detailed docs live

The legacy TraeCLI rule tree (`.trae/`) has been retired. Current sources of truth:

- **Architecture & behavior**: this file + `README.md` + the `phone_agent/graph/builder.py`
  docstring (topology) + `phone_agent/graph/state.py` (AgentState fields).
- **Roadmap / status**: `docs/future-roadmap.md`. Historical per-batch execution logs are archived
  in `docs/archive/`.
- **Planning/design-review workflows (OMX)**: `.codex/skills/` (`ralplan`, `autopilot`, `ultragoal`).

## Workflow conventions (from AGENTS.md)

- **File search**: use `rg` / `rg --files`; do not use `find` or `grep`.
- **Web research / third-party API docs**: prefer the `smart-search-cli` skill or Context7 over
  generic built-in web search.
- **Real device diagnosis**: `.agents/skills/phone-agent-live-diagnosis/SKILL.md`; default entry
  `.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "<target>"`.
  Claude Code discovers the local adapter at `.claude/skills/phone-agent-live-diagnosis`, which
  symlinks to the canonical `.agents/skills/...` directory.
- **Version sync after a phase**: when architecture/API/eval/trace changes, sync `README.md`,
  `docs/future-roadmap.md`, `AGENTS.md`, `CLAUDE.md` together — they are kept in lockstep.
- **Commit subjects**: `feat(graph): ...` / `feat(grounding): ...` / `feat(actions): ...`.

## Key environment variables

`PHONE_AGENT_BASE_URL`, `PHONE_AGENT_MODEL`, `PHONE_AGENT_API_KEY`, `PHONE_AGENT_OUTPUT_MODE`
(`json_schema`/`tool_calls`/`auto`, default `json_schema`), `PHONE_AGENT_CONTEXT_MODE`
(`inject`/`observe`/`off`, default `inject`), `PHONE_AGENT_MAX_STEPS` (default 100),
`PHONE_AGENT_DEVICE_ID`, `PHONE_AGENT_LANG` (`cn`/`en`), `PHONE_AGENT_GROUNDING_PROVIDER`
(`hybrid`/`locateanything`/`accessibility`/`fake`/`off`; aliases `accessibility_tree`/`uiautomator`
→accessibility, `accessibility_locateanything`/`uiautomator_locateanything`→hybrid). See README for
the full LocateAnything tuning set.
