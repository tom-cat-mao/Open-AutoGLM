---
name: phone-agent-live-diagnosis
description: >-
  Use when the user wants to run or monitor the TaskWizard thin-loop (v2) PhoneAgent
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

Run an TaskWizard thin-loop task, collect a diagnostic **evidence stream**, map what
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
| **Tools** | 15 base/TaskDoc tools plus two capability-mounted deliverable tools return a **result string**; the leading text is a loose contract for what happened (`OK. `, `未定位:`, `路线仍有未完成项：`, …). Fail-closed: an error string, never a faked action. | `phone_agent/v2/tools/{actuation,perception,control,taskdoc,deliverable,_obs}.py` |
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
will receive (after image pruning + TaskDoc injection), its `wrap_model_call` captures the
model's own turn (thinking + tool calls + token usage), and its `wrap_tool_call` captures
the *raw* tool return. It writes one JSON object per line to `<run_id>.evidence.jsonl`:

| event | meaning |
|---|---|
| `run_start` | run id, goal, config digest (incl. `device_id`, `unredacted`) |
| `model_request` | per step: message/image counts, pruned-screen count, taskdoc presence, context chars |
| `model_response` | per step: the model's **thinking full text**, its tool calls, and token usage (when reported) |
| `taskdoc_snapshot` | the task board whenever it changes (goal/route/facts, with per-item `evidence_note`) |
| `tool_invoke` / `tool_observation` | each tool call: args in, then latency + full result, parsed `[OBS]` block, and an `image{present,screen_seq,bytes,path}` summary |
| `hitl_decision` | human approve/reject/respond at an interrupt (written by the driver) |
| `stagnation_nudge` | the one-shot "you look stuck" nudge fired |
| `run_end` | terminal state (finished / takeover_reason / finish_summary) |

### Local-first full fidelity (the reader is you)

The diagnosis report's reader is **the device owner on their own machine**, so the
diagnostic products default to **full fidelity, un-redacted**: the evidence stream keeps
sensitive substrings verbatim and does **not** truncate text. The skill drives this by
setting `V2Config.diagnostic_unredacted=True` (env `PHONE_AGENT_DIAG_UNREDACTED`) for every
`run`/`--dry-run`. Two structural guarantees still hold **unconditionally**, in both
fidelity modes:

- the JSONL never carries screenshot `base64` — image blocks are reduced to
  `{present, screen_seq, bytes, path}` and the pixels are **written to disk** (below);
- multimodal `[text + image]` tool content is always *split* (text → `result_text`,
  image → the summary above).

Redaction only returns for an explicit **`--share`** export (see below). This full-fidelity
behavior is confined to the diagnosis mode; it **never** touches the P0 #6 production trace
(`trace.py` stays a single, un-flippable 64-char-truncate + redact + no-base64 branch), so
`traces/<run_id>.jsonl` is always the compliance artifact. When `--share` captures with
`diagnostic_unredacted=False` the stream falls back to the earlier "full text but redacted +
bounded at `DIAG_MAX_TEXT=4000`" behavior.

### Screenshots on disk

In diagnosis mode the middleware **decodes each screenshot to disk** at
`<run_dir>/screenshots/screen-<seq>.png` — every image-bearing tool result plus the opening
observation. The write is idempotent (the same `screen_seq` overwrites its file), files are
`0600`, and the `image` field of the evidence gains a relative `path` (e.g.
`screenshots/screen-3.png`). The report renders the *real screenshot* next to each step; the
base64 itself is still never written into the JSONL.

`scripts/analyze.py` classifies each recorded result string against the taxonomy
(`scripts/taxonomy.py`) **at analysis time** — the middleware records raw text only, so
the production tools never depend on a classifier. It also assembles a per-step **`replay`**
(model thinking + tool calls + results + screenshot `path`) that powers the step-by-step
report view.

## Default Command

From the repository root:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "测试目标"
```

Offline pipeline check (no model, no device — fastest smoke test of the whole pipeline):

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "完成一个本地 smoke 任务" --dry-run
```

Share a diagnosis with someone else (redacted, screenshot-free copy alongside the
full-fidelity report):

```bash
# writes report.html (local-first full-fidelity) AND report-share.html (redacted, no screenshots)
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "测试目标" --share
# or derive just the share copy from an existing run
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py report outputs/live-diagnosis/<run_id> --share
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
--share                      # also emit a redacted, screenshot-free report-share.html
--quiet
```

The diagnostic evidence stream is forced on (**full fidelity, un-redacted**) for every
`run`/`--dry-run` — it writes into the run dir and lands screenshots on disk; you do not
pass a flag for it. Add `--share` to also emit a redacted, screenshot-free
`report-share.html` (see below). There is no v1 grounding-sidecar knob
(`--locateanything-structure-mode`, …), no `--output-mode`/`--context-mode`/`--thinking-*`,
and no `--trace-*` debug flag — the diagnostic stream safely supersedes those.

## Configuration Precedence

`run_diagnosis.py` calls `load_project_env()` before parsing args, so resolution is:

1. explicit CLI flag → 2. shell env (`PHONE_AGENT_*`, never overwritten) →
3. project `.env` at repo root (keys prefixed `PHONE_AGENT_`) → 4. the `V2Config` default.

A bare `run_diagnosis.py "目标"` already picks up `.env` values for base URL, model, key,
device, and grounding provider. Verify what was actually resolved in `preflight.json` and
in `summary.json → command` rather than assuming — pass only flags you intend to override.
(Local-first: `command` records the target verbatim; the run dir is owner-private, `0600`.)

## Evidence Contract — the run folder

```text
outputs/live-diagnosis/<run_id>/
  preflight.json            # python/.venv, adb + wm size, MLX-Metal (hybrid/LA), config digest
  <run_id>.evidence.jsonl   # the raw diagnostic stream (middleware output)
  evidence.jsonl            # stable-named copy of the stream
  screenshots/screen-<n>.png# decoded screenshots (one per screen_seq; 0600)
  summary.json              # analyzed dimensions (below)
  report.html               # primary deliverable — local-first, full fidelity, step replay
  report-share.html         # (only with --share) redacted, screenshot-free share copy
  summary-share.json        # (only with --share) the redacted summary behind report-share
  status.json               # completed|failed|error + verdict + paths
  traces/<run_id>.jsonl     # the P0 #6 production trace (64-char, base64-free)
```

All produced artifacts (summary/report/evidence/status/preflight/screenshots) are chmod
`0600` — the run dir carries un-redacted, local-first data.

`summary.json` top level: `run_id, created_at, target(goal), verdict
(success|failed|takeover|max_steps|uncertain), run_dir, command, duration_sec,
steps, evidence_stream, trace, artifacts{}`, then these dimension blocks:

- `terminal` — finished / finish_summary / takeover_reason / reason / returncode
- `finish_gate` — attempted / accepted / blocked_by_open_items / open_items_at_finish / rejections[]
- `taskdoc_final` — goal_base / amendments / items(+evidence_note) / facts / counts / open_item_count / terminal_state
- `stagnation` — nudged / nudge_step / max_seen_states / stagnant_streak_peak
- `context` — peak_message_count / peak_image_messages / pruned_screen_total / taskdoc_pinned_every_step / avg_context_chars
- `hitl` — interrupts / decisions[] / approvals / rejections / responds / ask_user_count / take_over_count
- `tool_health` — total_calls / total_errors / error_rate / by_tool{calls,ok,error,error_classes,avg/p95 latency}
- `grounding` — mark_addressing / resolve_failures / locate / launch
- `visual` — tool_results_with_image / total_image_bytes / first_image_step / last_image_step
- `model` — calls / latency (from the trace) / token_usage{input,output,total} (when reported)
- `replay[]` — per step: thinking, model tool calls, tool results (+screenshot `path`), context stats, HITL
- `findings[]` / `recommendations[]` — category → v2 source file, with `path:line` anchors

`verdict` order: `takeover_reason` → takeover; `finished` → success; `max_model_calls` →
max_steps; any tool error / rejected finish → failed; else uncertain.

## Report Design

Interactive HTML dashboard (primary blue `#1E40AF`, amber `#F59E0B`, Fira Code),
**local-first full fidelity** — the reader is the device owner on their own machine:

- Tabs: 概览与终局 / 逐步回放 / 问题分析 / 性能与维度 / 源码归因 / 原始文件.
- **概览与终局** — header (任务 / 终局 / 耗时 / 步数 / token 用量 / 设备) + the terminal
  verdict card (with the open-items banner when `finish` was blocked) + the task-board
  terminal card (goal / route items with status **and per-item evidence** / key facts) +
  the 80/20 top-three recommendations.
- **逐步回放 (the core)** — one card per step: the **real screenshot thumbnail**
  (`<img src="screenshots/screen-N.png">`, click to open full-size) + the model's **thinking
  full text** + each tool call & args + the full result + latency.
- **问题分析** — taxonomy error tally, stagnation segment, finish review/rejection history,
  HITL events.
- **性能与维度** — token usage, context hygiene, visual reflow, grounding, and the per-tool
  latency distribution (p95 bars).
- **源码归因** — findings + recommendations mapped to v2 source files with `path:line` anchors.
- **原始文件** — links to summary.json / evidence.jsonl / traces / run dir + embedded JSON.
- `<base target="_blank">`; `word-break: break-all` on long paths; data embedded as a
  `</`-safe JSON island (offline, never executes the payload).
- **Full fidelity, local-first**: `report.html` shows the real (un-redacted) text and
  references the on-disk screenshots — the old "never store an image / never render private
  text" constraint is **gone**, it belonged to the share world. The base64 payload is still
  never embedded (screenshots are file references). The report **must sit in the run dir**
  (next to `screenshots/`) for the thumbnails to resolve.

### Sharing (`--share`)

`--share` writes an additional `report-share.html` (+ `summary-share.json`) that is safe to
hand to someone else: every string is redacted via the production `_redact` primitive and
every screenshot `path` is dropped, so the share copy **references no screenshot and leaks
no sensitive text**. Derive it at run time (`run … --share`) or after the fact
(`report <run_id> --share`). The full-fidelity `report.html` is untouched.

## Workflow

1. Confirm the user gave a concrete test target. If not, ask.
2. Run `scripts/run_diagnosis.py` with the target and any user-provided flags. Add `--share`
   if the user wants a copy safe to hand to someone else.
3. Read `summary.json` and the `report.html` path (open it from inside the run dir so the
   step-replay screenshots resolve).
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
  bytes,path}` (never base64; the pixels are decoded to `screenshots/screen-<seq>.png`). If
  `visual.tool_results_with_image == 0` on a real run, the model is operating blind on a
  text-only marks summary — the report flags it red. Check `_obs.py` / `actuation.py` for
  whether the image block is being reflowed.
- **Screenshots are file references, not embedded.** The report shows real thumbnails via
  `<img src="screenshots/…">`; open `report.html` **from inside the run dir** or the images
  404. Moving the HTML elsewhere breaks the thumbnails (the data is intact — re-render in
  place). The `--share` copy intentionally has no screenshots at all.
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
