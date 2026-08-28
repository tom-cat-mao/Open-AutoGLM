---
name: phone-agent-live-diagnosis
description: >-
  Use when the user wants to run or monitor the Open-AutoGLM thin-loop (v2) PhoneAgent
  on a real Android device, inspect what the model actually did, correlate the run's
  evidence stream with v2 source code, generate code-level modification recommendations,
  or produce an interactive HTML diagnosis report. Trigger on 实机测试, 监看, 实际效果,
  手机任务, live diagnosis, phone agent report, 源码归因, HTML 报告, real device run,
  trace analysis, or any request to verify what the phone agent actually did on a device.
when_to_use: |
  Real Android device runs of the v2 thin-loop PhoneAgent, post-run diagnosis linked to
  v2 source files, interactive HTML engineering reports, and natural-language test targets
  such as "打开设置并进入 Wi-Fi 页面". Do not use for pure unit tests, benchmark-only
  LocateAnything evaluation, or code edits without a diagnosis request.
---

# Phone Agent Live Diagnosis (thin-loop v2)

Run an Open-AutoGLM thin-loop task, collect a diagnostic **evidence stream**, map what
happened back to v2 source, and render an interactive HTML report.

> This is the **v2 rewrite**. The v1 LangGraph node model (`goal`/`plan`/`execute`/
> `reflect`/`acceptance` nodes, `evals/run_eval.py`, `PhoneAgent.run_live`) is gone.
> v2 is a thin loop: one model call per step on LangChain `create_agent`; the harness
> supplies tools + middleware and records traces, it does **not** route a workflow.

## Mental Model — diagnose a loop, not a graph

There are no nodes to attribute a finding to. Behavior lives in four places, and every
diagnosis maps to one of them:

| Owner | What it is | Where it lives |
|---|---|---|
| **Tools** | 15 tools return a **result string**; the leading text is a loose contract for what happened (`OK. `, `未定位:`, `路线仍有未完成项：`, …). Fail-closed: an error string, never a faked action. | `phone_agent/v2/tools/{actuation,perception,control,taskdoc,_obs}.py` |
| **Finish gate** | `finish` is fail-closed: it requires non-empty evidence **and** an all-clear TaskDoc route. This is the only "is the task done?" authority. | `phone_agent/v2/tools/control.py::finish` |
| **TaskDoc board** | Goal (`goal_base`, harness-seeded, model-immutable) + route items + facts, pinned into context every step (compression-immune). The model is the single writer via `update_task_doc`. | `phone_agent/v2/taskdoc.py`, `middleware/taskdoc.py` |
| **Middleware** | Cross-cutting: safety HITL (dangerous action → interrupt), image pruning (keep newest image only), JSONL trace (P0 #6), model-call limit, and — opt-in — the diagnostic evidence stream. | `phone_agent/v2/middleware/*` |

Marks-first grounding is the addressing contract: `tap` binds a mark (`target_mark_id`
direct, or `target_description` resolved to a **unique** mark, fail-closed on
ambiguity/no-match). Raw coordinates are only a `swipe` fallback. 0-1000 → pixel
conversion happens only inside tools; the model never sees absolute pixels.

## How diagnosis works — the evidence stream

The skill turns on an **opt-in** middleware, `DiagnosticEvidenceMiddleware`
(`phone_agent/v2/middleware/diagnostic.py`), that is **default-OFF and zero-cost** in
production. It is mounted last, so its `before_model` sees the *final* context the model
will receive (after image pruning + TaskDoc injection) and its `wrap_tool_call` captures
the *raw* tool return. It writes one JSON object per line to `<run_id>.evidence.jsonl`:

| event | meaning |
|---|---|
| `run_start` | run id, redacted goal, config digest |
| `model_request` | per step: message/image counts, pruned-screen count, taskdoc presence, context chars |
| `taskdoc_snapshot` | the task board whenever it changes (goal/route/facts) |
| `tool_invoke` / `tool_observation` | each tool call: redacted args in, then latency + full-text (bounded) result, parsed `[OBS]` block, and an `image{present,screen_seq,bytes}` summary |
| `hitl_decision` | human approve/reject/respond at an interrupt (written by the driver) |
| `stagnation_nudge` | the one-shot "you look stuck" nudge fired |
| `run_end` | terminal state (finished / takeover_reason / finish_summary) |

It shares the exact redaction primitives with the production trace
(`phone_agent/v2/middleware/_redact.py`): **sensitive substrings redacted, screenshot
base64 never written.** The only difference from the P0 trace is that diagnostic keeps
full text (bounded at `DIAG_MAX_TEXT=4000`) instead of truncating to 64 chars — the
report is "full picture but bounded", never "raw".

`scripts/analyze.py` classifies each recorded result string against the taxonomy
(`scripts/taxonomy.py`) **at analysis time** — the middleware records raw text only, so
the production tools never depend on a classifier.

## Default Command

From the repository root:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "测试目标"
```

Offline pipeline check (no model, no device — fastest smoke test of the whole pipeline):

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "完成一个本地 smoke 任务" --dry-run
```

Re-derive a summary or re-render a report from existing artifacts (no re-run):

```bash
# re-analyze an evidence stream (accepts the run dir or the .evidence.jsonl path)
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py analyze outputs/live-diagnosis/<run_id>
# re-render HTML from a summary.json (accepts the run dir or the summary.json path)
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py report  outputs/live-diagnosis/<run_id>
# print a run's status.json
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py status  outputs/live-diagnosis/<run_id>
```

Interactive HITL is native in v2: a dangerous action pauses for human input via the
checkpointer-backed `agent.run(hitl_handler=…)` path — a plain `run` **is** interactive,
so there is no separate `--live` flag. The skill wraps `input()` so each human decision
is appended to the evidence stream as a `hitl_decision` event.

Inspect a long-running job:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py status outputs/live-diagnosis/<run_id>
tail -f outputs/live-diagnosis/<run_id>/*.evidence.jsonl
tail -f outputs/live-diagnosis/<run_id>/traces/*.jsonl
```

> This `.agents/skills/...` directory is the canonical copy; `.claude/skills/phone-agent-live-diagnosis`
> is a symlink to it.

## Permission & Escalation

This skill runs real-device and local-model diagnostics, so some commands need host
access the default Bash sandbox may deny (ADB, Metal/MLX, network). When a command fails
with sandbox-shaped errors — `Operation not permitted`, permission denied writing outside
the workspace, `No Metal device available`, blocked device access, DNS/registry failures
— rerun that **specific** command through the Bash tool with `dangerouslyDisableSandbox:
true` and a short user-facing justification. Do not silently retry in-sandbox (it
reproduces the same failure), and never relax the sandbox for destructive commands
(`rm`, `git reset --hard`, `git push --force`).

Commands that typically need escalation:

- `--grounding-provider hybrid|locateanything|locateanything_mlx|mlx` → needs real
  LocateAnything / MLX / Metal.
- Any run with `--device-id` → needs host ADB server access.
- `--dry-run` does **not** need escalation; it runs with no model or device.

## CLI Flags

```bash
--dry-run                    # offline pipeline check (scripted model + fake session)
--device-id <adb-id>
--max-steps 20               # model-call budget (V2Config.max_model_calls)
--base-url http://localhost:8000/v1
--model autoglm-phone-9b
--apikey EMPTY
--model-timeout 180 / --model-max-retries 2
--grounding-provider hybrid  # off|fake|accessibility|accessibility_tree|uiautomator|
                             # locateanything|locateanything_mlx|mlx|hybrid|
                             # accessibility_locateanything|uiautomator_locateanything
--accessibility-timeout 3 / --accessibility-max-marks 80
--locateanything-model <id> / --locateanything-max-size 960
--lang cn|en
--no-taskdoc                 # disable the TaskDoc board (PHONE_AGENT_TASKDOC=false)
--nudge-steps 5              # stagnation nudge threshold
--reset-app <package>        # adb pm clear before the run
--output-dir outputs/live-diagnosis
--quiet
```

The diagnostic evidence stream is forced on for every `run`/`--dry-run` (it writes into
the run dir); you do not pass a flag for it. There is no v1 grounding-sidecar knob
(`--locateanything-structure-mode`, …), no `--output-mode`/`--context-mode`/`--thinking-*`,
and no `--trace-*` debug flag — the diagnostic stream safely supersedes those.

## Configuration Precedence

`run_diagnosis.py` calls `load_project_env()` before parsing args, so resolution is:

1. explicit CLI flag → 2. shell env (`PHONE_AGENT_*`, never overwritten) →
3. project `.env` at repo root (keys prefixed `PHONE_AGENT_`) → 4. the `V2Config` default.

A bare `run_diagnosis.py "目标"` already picks up `.env` values for base URL, model, key,
device, and grounding provider. Verify what was actually resolved in `preflight.json` and
in `summary.json → command` (API key redacted) rather than assuming — pass only flags you
intend to override.

## Evidence Contract — the run folder

```text
outputs/live-diagnosis/<run_id>/
  preflight.json            # python/.venv, adb + wm size, MLX-Metal (hybrid/LA), config digest
  <run_id>.evidence.jsonl   # the raw diagnostic stream (middleware output)
  evidence.jsonl            # stable-named copy of the stream
  summary.json              # analyzed dimensions (below)
  report.html               # primary deliverable
  status.json               # completed|failed|error + verdict + paths
  traces/<run_id>.jsonl     # the P0 #6 production trace (64-char, base64-free)
```

`summary.json` top level: `run_id, created_at, target(redacted goal), verdict
(success|failed|takeover|max_steps|uncertain), run_dir, command(redacted), duration_sec,
steps, evidence_stream, trace, artifacts{}`, then these dimension blocks:

- `terminal` — finished / finish_summary / takeover_reason / reason / returncode
- `finish_gate` — attempted / accepted / blocked_by_open_items / open_items_at_finish / rejections[]
- `taskdoc_final` — goal_base / amendments / items / facts / counts / open_item_count / terminal_state
- `stagnation` — nudged / nudge_step / max_seen_states / stagnant_streak_peak
- `context` — peak_message_count / peak_image_messages / pruned_screen_total / taskdoc_pinned_every_step / avg_context_chars
- `hitl` — interrupts / decisions[] / approvals / rejections / responds / ask_user_count / take_over_count
- `tool_health` — total_calls / total_errors / error_rate / by_tool{calls,ok,error,error_classes,avg/p95 latency}
- `grounding` — mark_addressing / resolve_failures / locate / launch
- `visual` — tool_results_with_image / total_image_bytes / first_image_step / last_image_step
- `model` — calls / latency (latency comes from the trace, not this stream)
- `findings[]` / `recommendations[]` — category → v2 source file, with `path:line` anchors

`verdict` order: `takeover_reason` → takeover; `finished` → success; `max_model_calls` →
max_steps; any tool error / rejected finish → failed; else uncertain.

## Report Design

Interactive HTML dashboard (primary blue `#1E40AF`, amber `#F59E0B`, Fira Code):

- Tabs: 终局与首页 / 运行维度 / 决策时间线 / 源码归因 / 修改建议 / 原始证据.
- The **overview** leads with three first-page blocks:
  1. **终局裁定** — verdict + terminal + finish-gate outcome (with the open-items banner
     when `finish` was blocked);
  2. **TaskDoc 板** — the terminal task board (goal / route items with status / facts);
  3. **80/20 三件事** — the top recommendations, each with target files + a verify step.
- `<base target="_blank">`; `word-break: break-all` on long paths; data embedded as a
  `</`-safe JSON island.
- **Never** render screenshot base64, API keys, verification codes, or unredacted private
  text (the stream is already redacted; the report adds nothing back).

## Workflow

1. Confirm the user gave a concrete test target. If not, ask.
2. Run `scripts/run_diagnosis.py` with the target and any user-provided flags.
3. Read `summary.json` and the `report.html` path.
4. Report briefly in Chinese: verdict, report path, trace path, and the top source-code
   findings. Explain *what actually happened* — never stop at "success=false".

## Gotchas

- **Don't diagnose against v1.** There is no `reflect`/`acceptance` node, no
  `GoalContract`, no `evals/run_eval.py`. If a finding wants to name a node, it is wrong —
  map it to a tool, the finish gate, the TaskDoc board, or a middleware (see the table).
- **`--dry-run` validates the pipeline only.** It injects a scripted model + fake session
  and runs the real middleware stack, so it proves the evidence → summary → report path is
  intact. It does **not** exercise real grounding or finish semantics; the report says so.
- **视觉回流 (visual reflow).** Tool returns are moving to `[OBS 文本 + 截图 image 块]`.
  The diagnostic stream splits that: text → `result_text`, image → `{present,screen_seq,
  bytes}` (never base64). If `visual.tool_results_with_image == 0` on a real run, the model
  is operating blind on a text-only marks summary — the report flags it red. Check
  `_obs.py` / `actuation.py` for whether the image block is being reflowed.
- **Absence of evidence is not failure.** A `未定位:` / `not observed` result means the
  target was not found *this instant via this channel* — it may be offscreen or need a
  scroll. Only a positive counter-observation (read the target, saw a different value) is a
  contradiction.
- **`stagnation` is goal-relative.** A rising `stagnant_streak_peak` with the nudge unfired
  is normal exploration; the nudge firing is the real "stuck" signal.
- **Real LocateAnything needs Apple Silicon + Metal.** MLX may import but fail at runtime
  with `No Metal device available`; when `--grounding-provider hybrid|locateanything` needs
  it, request an escalated rerun and record the MLX preflight rather than calling it a model
  failure.
- **Don't auto-edit business code** from this skill unless the user separately asks for a fix.
- **Prefer `.venv/bin/python`**; the worktree may not have its own `.venv` — fall back to
  the main-repo interpreter.

## References

- `references/source-map.md` — the v2 category → source-file map (the report renders this
  as clickable `path:line` anchors via `scripts/sourcemap.py::V2_SOURCE_RULES`).
- `scripts/taxonomy.py` — the canonical result-prefix → class → category table (§2), with a
  contract test that fails if a tool silently rewords its return string.
