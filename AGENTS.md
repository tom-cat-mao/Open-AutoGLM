# Open-AutoGLM Agent Guide

## What This Project Is

Open-AutoGLM is a **thin-loop (v2)** Android Phone Agent built on LangChain `create_agent`. It
takes a natural-language task, and the LLM perceives and operates a real Android device through
tools — one model call per step. The harness (middleware) only supplies tools, enforces safety
boundaries, keeps context hygienic, and records traces; it does **not** route a workflow.

```
system(minimal contract) + user(task + first observation w/ screenshot)
  -> create_agent tool loop: model -> [safety HITL] -> tool(s) -> model -> ...
  -> stop on: session.finished | session.takeover_reason | no tool_call | ModelCallLimit
```

> **v2 is live; v1 (the LangGraph goal→plan→execute→reflect→acceptance node graph) is retired.**
> The binding contract is `docs/refactor-thin-loop-v2.md`; roadmap state is in
> `docs/future-roadmap.md`.

## Project Layout

```
phone_agent/
  adb/                 # Device layer (screenshot/tap/swipe/type/launch/back/home/dump_uiautomator_xml/foreground) [retained]
  device_factory.py    # DeviceFactory [retained]
  grounding/           # MarkProvider stack: accessibility tree, LocateAnything, fallback, factory [retained]
  config/
    policy.py          # Versioned SafetyPolicyRegistry / VerificationPolicy (middleware reads safety vocab) [retained]
    app_registry.py    # AppIdentity / installed inventory / LaunchPolicy (launch_app resolution) [retained]
    apps.py / i18n.py / timing.py
    redact.py          # Privacy redaction primitives (trace + grounding share)
  v2/
    config.py          # V2Config: env/.env/CLI three-level resolution
    model.py           # build_chat_model(): ChatOpenAI factory + build_default_headers()
    session.py         # PhoneSession: device state + screenshot + current marks + locate
    coords.py          # 0-1000 relative coords -> absolute pixels
    resolver.py        # Target resolution: mark_id | description -> unique mark (fail-closed)
    prompts.py         # Minimal system prompt (cn/en)
    agent.py           # ThinPhoneAgent: create_agent assembly + run loop + HITL resume; RunResult
    tools/             # actuation (tap/long_press/type_text/scroll/swipe/back/home/launch_app/wait)
                       #   perception (read_screen/locate), control (finish/ask_user/take_over)
    middleware/
      safety.py        # Sensitive-tool-call predicate + HumanInTheLoopMiddleware (the only safety hard gate)
      images.py        # before_model: roll off historical screenshots, keep newest image only
      trace.py         # JSONL trace with egress redaction (no screenshot base64)
main_v2.py             # CLI entry point (task text is a positional argument)
bench/grounding/       # Grounding benchmark suite
tests/v2/              # v2 test suite (all fakes; no real device / MLX)
tests/grounding/       # retained grounding tests
```

## Common Commands

```bash
# Run ThinPhoneAgent (task text is a positional argument)
.venv/bin/python main_v2.py "task description" --device-id <serial> --max-steps 20

# Run tests (all fakes; no real device / MLX)
.venv/bin/pytest tests/v2 tests/grounding -q

# LangChain gateway compatibility spike (tool_calls + image content block + sampling)
.venv/bin/python scripts/spike_langchain_compat.py

# Run grounding benchmark
.venv/bin/python -m bench.grounding.run_locateanything --manifest <path>

# Web research
smart-search search "query" --format json
```

## How to Work on This Project

- **Always `.venv/bin/python` / `.venv/bin/pytest` / `.venv/bin/pip`** — never system Python.
- **File search**: use `rg` / `rg --files`; do not use `find` or `grep`.
- **Web research**: use `smart-search-cli` skill or Context7 for third-party API docs; prefer them over generic built-in web search.
- **Real device diagnosis**: `.agents/skills/phone-agent-live-diagnosis/SKILL.md` (currently depends on v1 run structure; needs a v2 adaptation — see roadmap known-breakage).
- **Escalation**: sandbox/network/GPU/Metal/ADB `Operation not permitted` failures → rerun the failing command through the agent frontend's escalated/non-sandboxed permission mechanism with a user-readable justification. Never relax the sandbox for `rm`, `git reset`, `git push --force`, or other destructive commands.
- **Planning/review**: use OMX canonical workflow: `$ralplan` = Planner → Architect → Critic; `$autopilot` = deep-interview → ralplan → ultragoal → code-review → ultraqa. Do not load heavy design workflows for ordinary tasks.
- **OMX subagents**: use `.codex/agents/`; skills from `.codex/skills/`. AGENTS.md is the orchestration brain.

## P0 Constraints (Must Never Violate)

These are the hard invariants for the thin-loop v2. Violating them causes crashes, data loss, or
security issues. The binding architecture contract is `docs/refactor-thin-loop-v2.md`.

| # | Domain | Constraint |
|---|--------|------------|
| 1 | **Coordinate** | Coordinate conversion (0-1000 relative → absolute pixels) happens **only inside tools** via `phone_agent/v2/coords.py`. Model never receives absolute pixels. |
| 2 | **Marks-first Grounding** | Execution actions must bind a mark. `tap` supports dual addressing `target_mark_id` (direct) \| `target_description` (natural language, resolved to a unique mark). Description resolution is fail-closed: ambiguous/no-match returns candidates and does **not** execute. Raw coordinates only for `swipe` fallback. |
| 3 | **Image Hygiene** | Historical screenshots are rolled off before each model call (`middleware/images.py`); only the newest image-bearing message keeps its image blocks. |
| 4 | **HITL Hard Gate** | Dangerous actions (payment/password/verification-code/sensitive-app launch) interrupt via `HumanInTheLoopMiddleware` for human approve/reject. `ask_user` → respond; `take_over` → always interrupt. The safety hard gate lives **only** in `middleware/safety.py`. |
| 5 | **Tool Fail-Closed** | Tools return a result **string**; on failure they return an error string (never raise to the model). The error stays in the transcript; a failed tool never executes a device action or fakes success. |
| 6 | **Trace/Log Egress Redaction** | Every model/tool event is logged to `<trace_dir>/<run_id>.jsonl` with redaction: text >64 chars truncated, sensitive substrings redacted via `phone_agent.config.redact.redact_context_text`, screenshot base64 never logged (only `screen_seq` + byte length). |
| 7 | **Device via DeviceFactory** | All device ops go through `DeviceFactory` → `phone_agent/adb/`. No direct ADB calls. |
| 8 | **Config via V2Config** | All configuration resolves only through `V2Config` three-level resolution: CLI override > shell env > `.env` (`PHONE_AGENT_` prefix, non-overriding) > default. |
| 9 | **No Force Push** | Never `git push --force` to `main` or `feature/thin-loop-v2`. |
| 10 | **No Auto-Commit** | Don't create commits unless explicitly requested. |
| 11 | **TaskDoc Board** | The task board's `goal_base` is seeded **only** by the harness at run start; the model writes the board **exclusively** via `update_task_doc` (it can never alter `goal_base`). The rendered `[TASK_DOC]` block is pinned into context before every model call (compression-immune), and `finish` is fail-closed while the route still has open items. |

## Doc & Skill Routing (Load on Demand)

The legacy TraeCLI rule tree (`.trae/`) is retired. Current sources of truth:

| When you need... | Load... |
|---|---|
| v2 architecture / module contracts | `docs/refactor-thin-loop-v2.md` (binding contract) + `README.md` 架构 section |
| Middleware semantics (safety/images/trace) | `docs/refactor-thin-loop-v2.md` §9 + `phone_agent/v2/middleware/*.py` docstrings |
| Agent assembly + run loop / RunResult | `docs/refactor-thin-loop-v2.md` §10 + `phone_agent/v2/agent.py` |
| Roadmap & phase status | `docs/future-roadmap.md` |
| Historical batch execution logs | `docs/archive/` |
| Planning/design review | `.codex/skills/ralplan/SKILL.md` (OMX Planner → Architect → Critic) |
| Autopilot execution | `.codex/skills/autopilot/SKILL.md` |
| Real-device diagnosis | `.agents/skills/phone-agent-live-diagnosis/SKILL.md` (v1-coupled; pending v2 adaptation) |

## Version Management

When a Phase completes:
1. Update `docs/future-roadmap.md` (current status + mark the phase done).
2. If architecture/API/tools/middleware/config changed: sync `README.md`, `AGENTS.md`, `CLAUDE.md`
   together — they are kept in lockstep.
3. Commit (only when explicitly requested): `feat(v2): <goal>` / `feat(grounding): <goal>`.

## Compact Instructions

When auto-compacting, preserve: current task, P0 constraints table, doc routing table, and in-progress roadmap state.
