# Source Map (thin-loop v2)

This file maps live-diagnosis symptoms to TaskWizard **v2** source files. It is the
reference behind `scripts/sourcemap.py::V2_SOURCE_RULES` (which the report renders as
clickable `path:line` anchors) and the taxonomy in `scripts/taxonomy.py`.

## Architecture note — there is no graph

The v1 LangGraph node model is **deleted**. Do not map a finding to `goal` / `plan` /
`execute` / `reflect` / `acceptance` nodes, to `GoalContract` / `goal_evaluator` /
`goal_requirements`, or to `evals/run_eval.py` — none of them exist in v2.

v2 is a **thin loop**: one model call per step on LangChain `create_agent`. The model
owns the workflow; the harness supplies tools + middleware and records traces. Behavior
lives in exactly four owners:

```text
model ──(tool call)──▶ [ safety HITL ] [ image prune ] [ trace ] [ TaskDoc ] [ diagnostic ]
                              │              │            │           │            │
                       dangerous→interrupt  keep 1 img   JSONL     pin board   evidence.jsonl
                                                                                     │
                                                        tool returns a RESULT STRING ┘
```

- **Tools** (`phone_agent/v2/tools/`) — 14 core tools, plus the TaskDoc tool and two
  capability-mounted deliverable tools when enabled. Each returns a **result string**; its
  leading text is the loose contract the taxonomy classifies. Fail-closed: an error string,
  never a faked action or a silent success.
- **Finish gate** (`tools/control.py::finish`) — the only "is the task done?" authority.
  Fail-closed: requires non-empty evidence **and** a route with no open TaskDoc items.
- **TaskDoc board** (`taskdoc.py`, `middleware/taskdoc.py`) — goal + route + facts,
  pinned every step (compression-immune). `goal_base` is harness-seeded and
  model-immutable; the model writes only via `update_task_doc`.
- **Middleware** (`middleware/`) — safety HITL, image pruning, JSONL trace, model-call
  limit, and the opt-in diagnostic evidence stream.

Marks-first grounding is the addressing contract: `tap` binds a mark (direct
`target_mark_id`, or `target_description` resolved to a **unique** mark, fail-closed on
ambiguity/no-match); raw coordinates are only a `swipe` fallback. 0-1000 → pixel
conversion happens only inside tools (`coords.py`); the model never sees absolute pixels.

## Result-prefix → class → category → source

The canonical table is `scripts/taxonomy.py::RESULT_CLASSES` (ordered most-specific
first). A contract test (`tests/skill/test_taxonomy.py`) asserts each prefix literal
still appears in its source file, so a tool that rewords its return string breaks the
test rather than the diagnosis.

| Result-text prefix | class | report category | Source: symbol |
|---|---|---|---|
| `OK. 已创建文档` | `deliverable_created` | deliverable | `tools/deliverable.py::write_document` |
| `OK. 已更新文档` | `deliverable_updated` | deliverable | `tools/deliverable.py::update_document` |
| `OK. ` | `success` | success | `tools/actuation.py` `tap/long_press/type_text/scroll/swipe/back/home/wait/launch_app` |
| `[OBS] 此屏被系统级保护（登录/支付页）。` | `secure_screenshot_blocked` | secure_screenshot | `tools/_obs.py::auto_observation` ← `session.py::screenshot` ← `adb/screenshot.py::get_screenshot` |
| `[OBS] app=` | `observation` | success | `tools/_obs.py::auto_observation`; `tools/perception.py::read_screen` |
| `[OBS] (re-observation failed:` | `obs_capture_failed` | observation | `tools/_obs.py::auto_observation` (swallows ScreenshotError) |
| `error: pass only one of target_mark_id` | `addressing_conflict` | grounding_addressing | `tools/actuation.py::_resolve_target` |
| `error: one of target_mark_id or target_description is required` | `addressing_missing` | grounding_addressing | `tools/actuation.py::_resolve_target` |
| `stale mark:` | `stale_mark` | grounding_addressing | `tools/actuation.py::_resolve_target` ← `resolver.py`/`session.py` StaleMarkError |
| `ambiguous:` | `ambiguous_resolve` | grounding_addressing | `tools/actuation.py::_resolve_target` ← ResolveAmbiguousError / LocateAmbiguousError |
| `未定位:` | `locate_no_match` | grounding_addressing | `tools/perception.py::locate` ← LocateAmbiguousError |
| `定位失败:` | `locate_provider_error` | grounding_addressing | `tools/perception.py::locate` (provider unavailable) |
| `error: start must be [x, y]` / `error: end must be [x, y]` | `bad_coords` | actuation_arg | `tools/actuation.py::swipe` |
| `error: unknown direction` | `bad_direction` | actuation_arg | `tools/actuation.py::scroll` |
| `ambiguous app ` + `score=` | `ambiguous_app` | launch | `tools/actuation.py::launch_app` ← `names.py::generate_candidates/decide_name` |
| `ambiguous app ` | `ambiguous_app` | launch | `tools/actuation.py::launch_app` (legacy form retained) |
| `denied:` | `launch_denied` | launch | `tools/actuation.py::launch_app` |
| `error: 未能启动` | `launch_failed` | launch | `tools/actuation.py::launch_app` |
| `error: …未安装` | `app_not_installed` | launch | `tools/actuation.py::launch_app` |
| `unknown app ` + `排序候选：` | `unknown_app` | launch | `tools/actuation.py::launch_app` ← `names.py::generate_candidates/decide_name` |
| `unknown app ` | `unknown_app` | launch | `tools/actuation.py::launch_app` (legacy/no-ranked-candidate form retained) |
| `error: deliverable already exists; use update_document` | `deliverable_exists` | deliverable | `tools/deliverable.py::write_document` |
| `error: document was not written (` | `deliverable_write_failed` | deliverable | `tools/deliverable.py::write_document` |
| `error: document was not updated (` | `deliverable_update_failed` | deliverable | `tools/deliverable.py::update_document` |
| `未写入（输入无效）：` | `taskdoc_input_invalid` | taskdoc | `tools/taskdoc.py::update_task_doc` |
| `未写入（校验失败）：` | `taskdoc_validation_failed` | taskdoc | `tools/taskdoc.py::update_task_doc` ← `TaskDoc.validate` |
| `已更新任务板。` | `taskdoc_ok` | taskdoc | `tools/taskdoc.py::update_task_doc` |
| `error: finish requires non-empty evidence` | `finish_no_evidence` | finish_gate | `tools/control.py::finish` |
| `路线仍有未完成项：` | `finish_blocked_open_items` | finish_gate | `tools/control.py::finish` (TaskDoc `has_open_items`) |
| `已记录完成声明` | `finish_ok` | finish_gate | `tools/control.py::finish` |
| `[ASK_USER] ` | `ask_user` | hitl | `tools/control.py::ask_user` |
| `已请求人工接管:` | `takeover_requested` | hitl | `tools/control.py::take_over` |

Underlying exception → emitted prefix (the causal chain the report links):

| Exception | → prefix |
|---|---|
| `StaleMarkError` (`session.py`) | `stale mark:` |
| `ResolveAmbiguousError` (`resolver.py`) | `ambiguous:` |
| `LocateAmbiguousError` (`session.py`) | `ambiguous:` (actuation) / `未定位:` (perception) |
| `ScreenshotError` (`session.py`) | `[OBS] (re-observation failed:` (swallowed by `auto_observation`, not a hard error) |
| `ScreenshotError(failure_code=secure_screenshot_blocked)` (`session.py`) | `[OBS] 此屏被系统级保护（登录/支付页）。` (no image; protected-screen guidance) |

## Report category → v2 source (V2_SOURCE_RULES)

| category | layer / severity | Primary files | What to check |
|---|---|---|---|
| `grounding_addressing` | grounding / P0 | `tools/actuation.py`, `tools/perception.py`, `resolver.py`, `session.py` | marks-first binding; stale = no `read_screen` before the action; ambiguous = non-unique description; no-match = neither accessibility nor LocateAnything hit (check provider + `max_marks`). |
| `actuation_arg` | actuation / P1 | `tools/actuation.py`, `coords.py` | `swipe` needs `[x,y]` 0-1000; `scroll` accepts only up/down/left/right; conversion is tool-internal. |
| `launch` | launch / P1 | `tools/actuation.py`, `names.py`, `config/apps.py`, `config/policy.py` | app registry + launch policy: denied = safety policy, not_installed/unknown = inventory, ambiguous = name too broad. |
| `resolver` | resolver / P1 | `names.py`, `tools/actuation.py`, `middleware/trace.py` | exact/lexical/pinyin/embedding candidate generation; package-deduplicated ranking; threshold + top-two margin; `resolution_attempt` decision/winner/candidates. |
| `deliverable` | deliverable / P1 | `tools/deliverable.py`, `capabilities.py` | run-id-derived path, 256 KiB limit, create-vs-update state, regular-file/symlink and atomic-write gates. |
| `finish_gate` | finish / P0 | `tools/control.py`, `taskdoc.py` | fail-closed: non-empty evidence + no open route items. Blocked → complete / mark `blocked` (with reason) / fix the route via `update_task_doc`. Never widen the gate. |
| `taskdoc` | taskdoc / P2 | `tools/taskdoc.py`, `taskdoc.py` | validation: ≤1 `in_progress`, ≤15 items, `blocked` needs a reason, ≤10 facts ≤120 chars each. |
| `hitl` | safety / P1 | `tools/control.py`, `middleware/safety.py` | HITL hard gate lives only in `safety.py`. `ask_user` → respond, `take_over` → always interrupt. Mis/under-trigger → `SafetyPolicyRegistry` + `SENSITIVE_APP_KEYWORDS`. |
| `observation` | observation / P1 | `tools/_obs.py`, `session.py`, `adb/screenshot.py` | `auto_observation` folds non-secure observation failures into a note; a successful action with a failed re-observation can mask a later stale mark — watch consecutive `obs_capture_failed`. |
| `secure_screenshot` | observation / P0 | `tools/_obs.py`, `session.py`, `adb/screenshot.py` | ADB-protected or uniformly black screenshot → `secure_screenshot_blocked`; no placeholder image or stale mark authority reaches the model; use accessibility or `take_over`. |
| `context` | context / P2 | `middleware/images.py`, `middleware/taskdoc.py` | history screenshots pruned to the newest one; TaskDoc re-pinned every step. Peak image-message count should stay 1; `taskdoc_present` should be true every step. |
| `visual` | visual / P0 | `tools/_obs.py`, `tools/actuation.py`, `middleware/images.py` | D2 visual reflow: tool returns should carry `[OBS text + image block]`. `tool_results_with_image == 0` means screenshots are not reflowing with tool returns and the model is operating blind. |
| `model` | model / P1 | `model.py`, `agent.py` | gateway behind Cloudflare needs a browser-like UA; sampling caps are model-enforced. High latency → check prompt-prefix cache + image pruning. |
| `recall` | memory / P2 | `recall.py` | split ranking: deterministic app mentions do not consume episode `top_k`; episode retrieval uses semantic score with recency only as tie-break. Check `incremental_upsert`/`reconcile_index` and schema-v2 hit/package metrics. |
| `capabilities` | assembly / P1 | `capabilities.py` | five owned seams (middleware, tools, prompt, run hooks, CLI); pending/off do not mount; mode changes release before apply and leave zero residue. |

## Where the diagnosis machinery itself lives

| Concern | Files |
|---|---|
| Diagnostic evidence stream (opt-in middleware; full-fidelity + screenshots-on-disk) | `phone_agent/v2/middleware/diagnostic.py` |
| Shared redaction primitives (base64-drop + sensitive substrings) | `phone_agent/v2/middleware/_redact.py` (delegates to `phone_agent/config/redact.py`) |
| P0 #6 production trace (64-char, base64-free) — **untouched by full-fidelity** | `phone_agent/v2/middleware/trace.py` |
| Config fields `diagnostic_evidence` / `diagnostic_evidence_dir` / `diagnostic_unredacted` | `phone_agent/v2/config.py` |
| Middleware wiring (diagnostic appended last, guarded) | `phone_agent/v2/agent.py` |
| Run driver + preflight + subcommands | `scripts/run_diagnosis.py` |
| Evidence read/index | `scripts/evidence.py` |
| Taxonomy (result prefix → class) | `scripts/taxonomy.py` |
| Analysis → summary.json dimensions | `scripts/analyze.py` |
| Category → v2 source map | `scripts/sourcemap.py` |
| HTML report | `scripts/report.py` |

## Config surface (all `PHONE_AGENT_*`)

Config resolves CLI > shell env > `.env` > `V2Config` default (`config.py`). Keys the
diagnosis cares about: `BASE_URL`, `MODEL`, `API_KEY`, `DEVICE_ID`, `MAX_MODEL_CALLS`,
`GROUNDING_PROVIDER`, `ACCESSIBILITY_TIMEOUT`, `ACCESSIBILITY_MAX_MARKS`,
`LOCATEANYTHING_MODEL`, `LOCATEANYTHING_MAX_SIZE`, `LANG`, `TASKDOC`,
`TASKDOC_NUDGE_STEPS`, `TRACE_DIR`, `TRACE`, and the diagnosis-only
`DIAG_EVIDENCE` / `DIAG_EVIDENCE_DIR` / `DIAG_UNREDACTED` (all forced on by the skill:
evidence on, full fidelity on; default OFF/false elsewhere).

There is **no** v1 remote-grounding config (`PHONE_AGENT_REMOTE_GROUNDING_*`) in v2, and
**no** grounding-sidecar / output-mode / context-mode / thinking knobs. If a `.env`
still carries those keys, the preflight should treat them as stale noise.
