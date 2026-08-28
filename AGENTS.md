# Open-AutoGLM Agent Guide

## What This Is

Open-AutoGLM is a **thin-loop (v2)** Android phone agent: an LLM drives a real device through
tools — one model call per step, on LangChain `create_agent`. The harness only supplies tools,
enforces safety boundaries, keeps context hygienic, and records traces. It does **not** route a
workflow. The v1 LangGraph node architecture was deleted; `adb/`, `grounding/`, and
`config/{policy,app_registry,redact}` are retained as libraries under `phone_agent/v2/`.

## P0 Constraints (Must Never Violate)

| # | Domain | Constraint |
|---|--------|------------|
| 1 | **Coordinate** | 0-1000 relative → absolute pixels conversion happens **only inside tools** (`phone_agent/v2/coords.py`). The model never receives absolute pixels. |
| 2 | **Marks-first Grounding** | Execution actions must bind a mark. `tap` dual addressing: `target_mark_id` (direct) \| `target_description` (resolved to a unique mark, fail-closed on ambiguity/no-match). Raw coordinates only for `swipe` fallback. |
| 3 | **Image Hygiene** | Historical screenshots are rolled off before each model call (`middleware/images.py`); only the newest image-bearing message keeps its image blocks. |
| 4 | **HITL Hard Gate** | `middleware/safety.py::classify_tool_call` maps a call to `ToolCallVerdict{level: none\|recall\|reviewer\|hard}`. Hard gate (always interrupts): irreversible commit (commit verb + irreversible object, e.g. 确认支付), password-box `type_text`, credential/captcha input, self-declared sensitive. Soft candidates (bare vocab) do **not** popup in `hard` mode; `reviewer` mode routes them to a second model (fail-closed on error). `launch_app` is reversible → softened out of the hard gate. `ask_user` → respond; `take_over` → always interrupt. `PHONE_AGENT_SAFETY_MODE=off\|hard\|reviewer` (default `hard`). The hard gate lives **only** here. |
| 5 | **Tool Fail-Closed** | Tools return a result **string**; on failure they return an error string and never execute a device action or fake success. Errors stay in the transcript. |
| 6 | **Trace Redaction** | Every model/tool event is logged to `<trace_dir>/<run_id>.jsonl`: text >64 chars truncated, sensitive substrings redacted via `config/redact.py`, screenshot base64 never logged (only `screen_seq` + byte length). |
| 7 | **Device via DeviceFactory** | All device ops go through `DeviceFactory` → `phone_agent/adb/`. No direct ADB calls. |
| 8 | **Config via V2Config** | CLI override > shell env > `.env` (`PHONE_AGENT_` prefix, non-overwriting) > default. No hardcoded endpoints/keys/params. |
| 9 | **No Force Push** | Never `git push --force` to `main` or `feature/thin-loop-v2`. |
| 10 | **No Auto-Commit** | Don't create commits unless explicitly requested. |
| 11 | **TaskDoc Board** | `goal_base` is seeded **only** by the harness at run start; the model writes the board exclusively via `update_task_doc` (never `goal_base`). The `[TASK_DOC]` block is pinned into context before every model call (compression-immune); `finish` is fail-closed while route items are open. |
| 12 | **Finish Two-Step + Verifier** | `finish` is two-step (review packet → `confirm=true`, seq-guarded); `completed` items require `evidence_note`. The independent-context verifier (`v2/verify.py`) sees only goal + evidence route + trailing screenshot(s), **never the actor transcript**. Unlike the safety gate, the verifier is **fail-open**: any setup/call failure lands the finish (L1 two-step already gated it). `PHONE_AGENT_FINISH_VERIFY=off\|auto\|always` (default `auto`). |

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
| Config keys (all `PHONE_AGENT_*`) | `.env.example` + `phone_agent/v2/config.py` docstrings |
| Architecture status & deferred items | `docs/future-roadmap.md` |
| Module contracts | docstrings in `phone_agent/v2/` (agent, session, tools, middleware) |
| Real-device diagnosis | `.agents/skills/phone-agent-live-diagnosis/SKILL.md` (v2 rewrite in progress) |
| Historical batch logs (v1 era) | `docs/archive/` |

## Version Management

- Roadmap state lives in `docs/future-roadmap.md`; update it when a phase completes.
- If architecture/tools/middleware/config change, sync `README.md` and `AGENTS.md` in the same commit.
- Commit only when explicitly requested.
