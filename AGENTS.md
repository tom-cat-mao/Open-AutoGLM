# TaskWizard Agent Guide

> LLM 驱动的安卓手机操作 Agent：看一眼屏幕、想一步、动一下；编码前先读本文件，P0 约束不可违反。

## What This Is

TaskWizard is a **thin-loop (v2)** Android phone agent: an LLM drives a real device through
tools — one model call per step, on LangChain `create_agent`. The harness only supplies tools,
enforces safety boundaries, keeps context hygienic, and records traces plus privacy-minimal
experience episodes. It does **not** route a
workflow. The v1 LangGraph node architecture was deleted; `adb/`, `grounding/`, and
`config/{policy,app_registry,redact}` are retained as libraries used by `phone_agent/v2/`.
The optional `phone_agent/web/` NiceGUI frontend observes runs through
`ThinPhoneAgent(extra_middleware=...)`; it must not own device access, tool execution, or workflow
routing, and the headless `ThinPhoneAgent.run(...)` path must remain usable without the web layer.

## Development Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/pytest tests -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
```

Real-device diagnosis starts from `.agents/skills/phone-agent-live-diagnosis/SKILL.md`; read the
skill before running or monitoring a device task.

## P0 Constraints (Must Never Violate)

| # | Domain | Constraint |
|---|--------|------------|
| 1 | **Coordinate** | 0-1000 relative → absolute pixels conversion happens **only inside tools** (`phone_agent/v2/coords.py`). The model never receives absolute pixels. |
| 2 | **Marks-first Grounding** | Execution actions must bind a mark. `tap` dual addressing: `target_mark_id` (direct) \| `target_description` (resolved to a unique mark, fail-closed on ambiguity/no-match). Raw coordinates only for `swipe` fallback. Mark ids carry a **batch badge** `ax_1@e<epoch>` (see #15); a stale-batch id fails closed in `resolve_mark`. `locate` is hint-first (`PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` bounds intent/text hints) and uses the original-resolution frame by default (`PHONE_AGENT_LOCATE_MAX_SIZE=0`); optional container/anchor scope ids must resolve in the current batch, crop the original frame, and map the hit back to full-frame coordinates without changing the epoch. |
| 3 | **Image Hygiene** | Historical screenshots are rolled off before each model call (`middleware/images.py`); only the newest `PHONE_AGENT_IMAGE_KEEP` (default 2) image-bearing messages keep their image blocks, older marks digests fold. Tools **always** emit a fresh image on success (A4 removed same-screen image dedup — it never fired; growth is bounded on the history side, not the produce side). |
| 4 | **Safety Warning Flow (U2)** | `middleware/safety.py::classify_tool_call` maps a call to `ToolCallVerdict{level: none\|recall\|reviewer\|hard}` (detection semantics unchanged: irreversible commit = commit verb + irreversible object e.g. 确认支付, password-box `type_text`, credential/captcha input, self-declared sensitive; soft candidates never gate; `launch_app` reversible → soft). What a gate verdict *does* is **mode-dependent**. Default `wary`: `SafetyWarningMiddleware.wrap_tool_call` short-circuits a flagged execution call (tap/long_press/type_text/launch_app) — **not executed, no human summoned** — returning a warning `ToolMessage` (world fact + option space: resend with `confirm_irreversible=true` / abandon / ask_user\|take_over) and a non-blocking stdout notice; the model resends with `confirm_irreversible=true` to actually act. `sensitive=true` is the model's self-declare channel (always warns). `hard` keeps the legacy HITL interrupt (approve/reject) for unattended runs; `off` disables the gate; `reviewer` = wary + second-model precision-ranking of soft candidates (fail-closed on error). `ask_user` → respond and `take_over` → interrupt in **every** mode. `PHONE_AGENT_SAFETY_MODE=off\|wary\|hard\|reviewer` (default `wary`). |
| 5 | **Tool Fail-Closed** | Tools return a result **string**; on failure they return an error string and never execute a device action or fake success. Errors stay in the transcript. |
| 6 | **Trace Redaction** | Every model/tool event is logged to `<trace_dir>/<run_id>.jsonl`: text >64 chars truncated, sensitive substrings redacted via `config/redact.py`, screenshot base64 never logged (only `screen_seq` + byte length). |
| 7 | **Device via DeviceFactory** | All device ops go through `DeviceFactory` → `phone_agent/adb/`. No direct ADB calls. |
| 8 | **Config via V2Config** | CLI override > shell env > `.env` (`PHONE_AGENT_` prefix, non-overwriting) > default. No hardcoded endpoints/keys/params. |
| 9 | **No Force Push** | Never `git push --force` to `main` or `feature/thin-loop-v2`. |
| 10 | **No Auto-Commit** | Don't create commits unless explicitly requested. |
| 11 | **TaskDoc Board + Flow Line** | `goal_base` is seeded **only** by the harness at run start; the model writes the board exclusively via `update_task_doc` (never `goal_base`). The `[TASK_DOC]` block is pinned into context before every model call (compression-immune); `finish` is fail-closed while route items are open. **Transition discipline (A4)**: `validate(previous=...)` rejects a `pending`→`completed` jump (must pass through `in_progress`) and rejects batch-marking multiple `pending`→`completed` in one write. **Flow line (U3)**: the pinned block ends with a `## 流程线` section derived **purely from the transcript** (`AIMessage.tool_calls` + matching `ToolMessage` receipts, recent 8) — `#N <intent> → <tool><target> → <result>`, missing intent → `（未声明）`. No session state backs it. |
| 11b | **Output Contract (intent-in-args, U3)** | Every tool carries `intent: str` (this step's goal, system prompt mandates it) + `note: str \| None` (this step's discovery). Actuation receipts state what actually happened (e.g. `已点击「上海」(ax_3)`) so the flow line derives a truthful ledger. The old `seen_states`/`nudged` stagnation machinery is **deleted** (`observe()` still computes `screen_hash` for audit/screen-binding only); `PHONE_AGENT_TASKDOC_NUDGE_STEPS` / `taskdoc_nudge_steps` is a retained-but-deprecated no-op. |
| 12 | **Finish Two-Step + Verifier** | `finish` is two-step (review packet → `confirm=true`, seq-guarded); `completed` items require `evidence_note`. The independent-context verifier (`v2/verify.py`) sees only goal + evidence route + trailing screenshot(s), **never the actor transcript**. Unlike the safety gate, the verifier is **fail-open**: any setup/call failure lands the finish (L1 two-step already gated it). `PHONE_AGENT_FINISH_VERIFY=off\|auto\|always` (default `auto`). |
| 13 | **Token Budget (cost ceiling)** | Cost is measured in **tokens** (`middleware/budget.py`): cumulative `AIMessage.usage_metadata` input+output (fallback `len//4`+1500/image), accumulated in `after_model` so it survives compaction. `PHONE_AGENT_TOKEN_BUDGET` (default 1M) hard-stops the run at exhaustion (`_build_result` → `token_budget_exhausted`); `PHONE_AGENT_TOKEN_WARN_REMAINING` (default 100k) injects a one-time L0 remaining-token mirror. `ModelCallLimit` (`PHONE_AGENT_MAX_STEPS`, default **100**) is now only a runaway-loop fuse (`loop_fuse`). |
| 14 | **Two-Threshold Auto-Compact** | `middleware/compact.py` runs **before** context-pruning. T1 (`PHONE_AGENT_COMPACT_WARN_RATIO`, 0.75 of the window) injects a one-time "write facts into TaskDoc / wind down exploration" hint. T2 (`PHONE_AGENT_COMPACT_TRIGGER_RATIO`, 0.92) calls a text-only LLM (`config.memory_model`→main) for a structured phone hand-off summary and rebuilds the transcript via `REMOVE_ALL_MESSAGES` (system prompt + `[COMPACT_SUMMARY]` + recent tail + fresh-observation hint + pinned TaskDoc). The cut never splits a `tool_use`/`tool_result` pair, never folds pinned blocks; iterative (prior summary fed back), PTL retry ≤3 by dropping the oldest turn-group, **fail-open** (skip the fold if the summariser fails). Window inferred from model name (256k default, `PHONE_AGENT_CONTEXT_WINDOW` override). Master switch `PHONE_AGENT_COMPACT` (default on). |
| 15 | **Atomic Observation Lifecycle (U1)** | `session.observe()` is the **single observation producer**: before every sampling attempt it sleeps `PHONE_AGENT_OBSERVE_SETTLE_MS` (default 300ms, 0 disables); execution-tool/read-screen `settle_ms` replaces that default and clamps to 0–5000ms. The atomic window remains foreground-before → screenshot → accessibility dump (`refresh_marks(shot)` reuses that one screenshot, no 2nd capture) → foreground-after; a mid-capture foreground change retries the window **once** after another settle, a second instability raises `ScreenshotError`. Decoded screenshots whose RGB channel maxima are all ≤4 fail closed through `secure_screenshot_blocked` when `PHONE_AGENT_BLACK_SCREEN_DETECT=on` (default). Each success bumps `session.epoch` (+1) and mints every external mark id as a **batch badge** `ax_1@e<epoch>` (`MarkCandidate.epoch`/`Observation.epoch`/`ScreenBinding.observation_epoch` set); the provider-internal id is the pre-`@e` prefix (provenance only). `resolve_mark` is the **freshness gate**: a badged id from a superseded batch fails closed (`StaleMarkError`) *before* the marks lookup. An observation failure **invalidates the whole batch** (`marks` cleared, epoch frozen) — no stale addressing authority survives. `locate` mints its hit into the **current** batch (no epoch bump), does not use observe settle, and its tool returns the **same frame** the visual model ran on (via `last_locate_frame()`, no extra observe). **Parallel tool calls are disabled** (`build_chat_model` sets `model_kwargs={"parallel_tool_calls": False}` so it survives `create_agent`'s per-turn `bind_tools`; `PHONE_AGENT_PARALLEL_TOOL_CALLS` default false, opt-out true). |
| 16 | **Experience Plane (WP-I1)** | Every completed `run()` appends exactly one fixed-schema `episode_outcome`; tool receipts append fixed-schema `experience_event` records. This plane is **observe-only**: it never changes actor input, routing, tool execution, or result semantics, and all setup/write failures fail open. Only the explicit privacy allowlist may persist; `goal_text` passes `config/redact.py`, while tool args/results, typed text, mark text, screenshots/base64, and model reasoning never enter the schema. `events.jsonl` remains append-only truth; `episodes.json` is a rebuildable run-id view. The WP-I1/WP-I2 episode schema is a hard cross-workstream contract and must not be changed. |
| 16 | **RAG Shadow Recall (I2)** | `v2/recall.py` owns a rebuildable sqlite-vec/FTS5 index over WP-I1 `episode_outcome` records and App-KB aliases. Default `PHONE_AGENT_MEMORY_RAG=shadow`: retrieve once at run start, write candidates to the production trace, never modify actor messages, then compare recalled packages with successful `launch_app` receipts and accumulate `memory/experience/recall_stats.json`. `on` is reserved and does not inject context in WP-I2. The MLX Qwen3 embedder imports/loads only on the first real `embed()`; tests use `HashEmbedder`. |
| 16 | **Implicit App Alias Evidence (I3)** | With `PHONE_AGENT_IMPLICIT_ALIAS=on` (default), an unknown `launch_app(name)` keeps only package names actually shown in that failure receipt as run-local evidence. A later device-confirmed launch by an exact listed package may persist `name → package` as `kind=learned`; empty/non-matching evidence, cross-run state, equal terms, and the `off` switch must never write. |

## Architecture Map

| Area | Contract |
|---|---|
| `main_v2.py` | CLI entry; resolves CLI overrides and starts one thin-loop run, App-KB dream pass, or `--rebuild-vec` maintenance pass. |
| `phone_agent/v2/agent.py` | Assembles LangChain `create_agent`, middleware, tools, checkpointer, and terminal result. |
| `phone_agent/v2/session.py` | Owns device/session state, run-local implicit-alias evidence, and the atomic observation lifecycle. |
| `phone_agent/v2/tools/` | Perception, mark-bound actuation, TaskDoc updates, finish, user query, and takeover. |
| `phone_agent/v2/middleware/` | Safety, TaskDoc pinning, image hygiene, compaction, token budget, trace, and diagnostics. |
| `phone_agent/v2/{taskdoc,resolver,review,verify}.py` | Task state, unique mark resolution, finish review packet, and independent verification. |
| `phone_agent/v2/experience.py` | Append-only, privacy-allowlisted run/tool experience records and rebuildable episode view. |
| `phone_agent/v2/{taskdoc,resolver,review,verify,recall}.py` | Task state, unique mark resolution, finish review/verification, and shadow semantic recall. |
| `phone_agent/v2/{model,config,prompts}.py` | Model transport, `V2Config`, and the thin-loop system contract. |
| `phone_agent/v2/{appkb,dream}.py` | Persistent local application knowledge, verified-launch feedback, and rule-based App-KB/experience maintenance. |
| `phone_agent/{adb,grounding,config}/` + `device_factory.py` | Retained device, grounding, policy/registry/redaction libraries; v2 consumes them through their contracts. |

The v1 routed LangGraph workflow and its `graph/`, `actions/`, `checkpoint/`, old `agent.py`, old
`main.py`, and `evals/` paths were deleted. Do not recreate or route new v2 behavior through them.

## Environment Gotchas (things you can't learn from the filesystem)

- **Always `.venv/bin/python` / `.venv/bin/pytest` / `.venv/bin/pip`** — never system Python.
- **File search**: use `rg` / `rg --files`; do not use `find` or `grep`.
- **Model gateway is behind Cloudflare**: requests need a browser-like User-Agent
  (`v2/model.py::build_default_headers` handles it). A raw openai client without the UA gets
  `403 Your request was blocked`.
- **Gateway enforces per-model sampling limits** (e.g. only `temperature=1`, `top_p=0.95`,
  `frequency_penalty=0` for some models). Override per deployment via `PHONE_AGENT_TEMPERATURE` /
  `PHONE_AGENT_TOP_P` / `PHONE_AGENT_FREQUENCY_PENALTY` — never hardcode sampling params.
- **Sandbox**: ADB / Metal / network `Operation not permitted` → rerun the failing command through
  the agent frontend's escalated permission mechanism with a user-readable justification. Never
  relax the sandbox for destructive commands (`rm`, `git reset`, force push).

## Doc Routing (load on demand)

| When you need... | Load... |
|---|---|
| Install / run / CLI flags / examples | `README.md`, `.venv/bin/python main_v2.py --help` |
| Config keys (all `PHONE_AGENT_*`) | `docs/configuration.md`; sources: `.env.example` + `phone_agent/v2/config.py` docstrings |
| Budget / auto-compact internals | `phone_agent/v2/middleware/{budget,compact,_tokens}.py` docstrings |
| Architecture status & deferred items | `docs/future-roadmap.md` |
| Module contracts | docstrings in `phone_agent/v2/` (agent, session, tools, middleware) |
| Real-device diagnosis | `.agents/skills/phone-agent-live-diagnosis/SKILL.md` (v2; local-first full-fidelity report, screenshots on disk, `--share` redacts) |
| Local Web watch/steer UI | `README.md`, `phone_agent/web/{bridge,app}.py` docstrings |
| Historical batch logs (v1 era) | `docs/archive/` |

## Version Management

- Roadmap state lives in `docs/future-roadmap.md`; update it when a phase completes.
- If architecture/tools/middleware/config change, sync `README.md` and `AGENTS.md` in the same commit.
- Commit only when explicitly requested.
