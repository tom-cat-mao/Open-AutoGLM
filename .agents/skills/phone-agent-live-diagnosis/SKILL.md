---
name: phone-agent-live-diagnosis
description: >-
  Use when the user wants to run or monitor Open-AutoGLM PhoneAgent on a real Android
  device, inspect actual execution effects, correlate trace results with source code,
  generate code-level modification recommendations, or produce an interactive HTML
  diagnosis report. Trigger on 实机测试, 监看, 实际效果, 手机任务, live diagnosis,
  phone agent report, 源码归因, HTML 报告, real device run, trace analysis, or any
  request to verify what the phone agent actually did on a device.
when_to_use: |
  Real Android device runs of PhoneAgent, post-run trace diagnosis linked to source
  files, interactive HTML engineering reports, and natural-language test targets
  such as "打开设置并进入 Wi-Fi 页面". Do not use for pure unit tests, benchmark-only
  LocateAnything evaluation, or code edits without a diagnosis request.
---

# Phone Agent Live Diagnosis

Run Open-AutoGLM real-device tasks, collect trace evidence, map failures back to
source code, and generate an interactive HTML report.

## Scope

Use this skill for:

- Real Android device execution monitoring for `PhoneAgent`.
- One-off natural-language test targets such as "打开设置并进入 Wi-Fi 页面".
- Post-run diagnosis that must explain actual behavior, source-code cause, and
  modification suggestions.
- Interactive HTML reports for engineering review.

Do not use this skill for pure unit-test review, benchmark-only LocateAnything
evaluation, or code modification without a real or dry-run diagnosis request.

## Default Command

From the repository root:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "测试目标"
```

Dry run (no model, no device):

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "完成一个本地 smoke 任务" --dry-run
```

Interactive HITL resume (confirm/takeover pauses for human input, then the agent
resumes in place via the checkpointer-backed `run_live` path):

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "登录后支付" --live
```

Inspect a long-running job:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py --status outputs/live-diagnosis/<run_id>
tail -f outputs/live-diagnosis/<run_id>/run_output.log
tail -f outputs/live-diagnosis/<run_id>/traces/*.jsonl
```

> This `.agents/skills/...` directory is the canonical copy; `.claude/skills/phone-agent-live-diagnosis`
> is a symlink to it.

## ZCode Permission & Escalation

This skill runs real-device and local-model diagnostics, so some commands need host
access that ZCode's default Bash sandbox may deny. ZCode does **not** use TraeCLI's
`sandbox_permissions="require_escalated"` / `prefix_rule` mechanism — those concepts
are gone. Instead:

When a diagnosis command fails with sandbox-shaped errors such as `Operation not
permitted`, permission denied while writing outside the workspace, unavailable
GPU/Metal, blocked device access, DNS/registry/network failures, or inability to
read user-level config, rerun the same command through the Bash tool with
`dangerouslyDisableSandbox: true` and include a short user-facing justification in
the description field. Example:

```text
command: .venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "打开设置" --grounding-provider hybrid
description: "ADML device access + hybrid grounding need host ADB/Metal; sandbox reports Operation not permitted"
dangerouslyDisableSandbox: true
```

Do not silently retry in-sandbox — that will reproduce the same failure. Do not relax
the sandbox for destructive commands (`rm`, `git reset --hard`, `git push --force`,
etc.). Use a narrow approach: only the specific diagnosis command needs the
elevated rerun.

Commands that typically need escalation:

- `--grounding-provider hybrid|locateanything|locateanything_mlx|mlx` → needs real
  LocateAnything / MLX / Metal.
- Any run with `--device-id` → needs host ADB server access.
- Runs that write status/trace artifacts under `outputs/` inside the repo usually
  succeed in-sandbox; only escalate when an actual `Operation not permitted` /
  device-access failure appears.

`--dry-run` does **not** need escalation; it runs without a model or device and is
the fastest way to verify the report pipeline.

## Useful Options

```bash
--device-id <adb-id>
--max-steps 10
--base-url http://localhost:8000/v1
--model autoglm-phone-9b
--apikey EMPTY
--output-mode json_schema            # or tool_calls | auto
--context-mode inject               # off | observe | inject
--grounding-provider hybrid         # off|fake|locateanything|locateanything_mlx|mlx|accessibility|accessibility_tree|uiautomator|hybrid|accessibility_locateanything|uiautomator_locateanything
--accessibility-timeout 3
--accessibility-max-marks 80
--locateanything-context-max-chars 0
--locateanything-structure-mode off # off | target | screen
--locateanything-max-visual-candidates 20
--locateanything-visual-category-budget 8
--locateanything-max-structure-calls 3
--output-dir outputs/live-diagnosis
--trace-raw-model-response          # debug: dump raw model reply text
--trace-request-messages            # debug: dump final request messages
--trace-prompt-blocks               # debug: dump prompt construction blocks
--trace-unredacted-prompt           # DANGEROUS: skip privacy redaction in trace
```

## Configuration Precedence

`run_diagnosis.py` calls `load_project_env()` before `parse_args()`, so the
resolution order is:

1. Explicit CLI flag.
2. Shell environment (`PHONE_AGENT_*` already exported — never overwritten).
3. Project `.env` at the repo root (only keys prefixed `PHONE_AGENT_`, quotes
   stripped, `export ` prefix tolerated).
4. The hardcoded argparse default.

So a bare `run_diagnosis.py "目标"` already picks up `.env` values for base URL,
model, API key, device ID, grounding provider, and the trace debug flags. Verify
what was actually resolved in `preflight.json` (`device_id`, `grounding_provider`,
`output_mode`) and in `summary.json` → `command` (API key redacted) rather than
assuming. Only pass flags you intend to override.

Two `.env` conditions to call out in the report rather than silently inherit:

- `PHONE_AGENT_TRACE_UNREDACTED_PROMPT=true` — records unredacted prompt text.
  Surfaces in `summary.json` → `dangerous_debug` and as a red banner in the HTML.
- `PHONE_AGENT_REMOTE_GROUNDING_*` — dead config (provider reverted in `e0a2e4b`).
  If a real API key is still sitting in these keys, flag it as a stale secret worth
  rotating; it has no effect on the run.

## Workflow

1. Confirm the user provided a concrete test target. If not, ask.
2. Run `scripts/run_diagnosis.py` with the target and any user-provided options.
3. Read the generated `summary.json` and `report.html` path.
4. Report the result briefly in Chinese, including:
   - verdict: success / failed / blocked / uncertain
   - report path
   - trace id/path if present
   - top source-code findings

During long real-device runs, inspect live progress instead of guessing:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py --status outputs/live-diagnosis/<run_id>
tail -f outputs/live-diagnosis/<run_id>/run_output.log
tail -f outputs/live-diagnosis/<run_id>/traces/*.jsonl
```

## Evidence Contract

The script creates a run folder:

```text
outputs/live-diagnosis/<run_id>/
  task.json
  preflight.json
  result.json
  run_output.log
  run_error.log
  status.json
  trace.jsonl
  trace_summary.json
  code_findings.json
  recommendations.json
  summary.json
  report.html
```

The HTML report is the primary deliverable. It must connect:

- real execution results
- trace events
- `error_layer` / `error_code` / `failure_cause`
- relevant source files and symbols
- concrete modification suggestions

## Graph Shape

The graph is `START → goal → plan → execute → [confirm|takeover|acceptance|reflect|replan|end]`,
with `acceptance → after_acceptance → [takeover|replan→goal|end]`.

**Three** questions are answered by three different owners, at three time scales.
Attributing a finding to the wrong one is the most common diagnosis error here:

- `reflect` — "did this action work?" — runs every step. **Forbidden from judging
  task progress or human takeover** (P0 #13).
- `acceptance` — "is the whole task done?" — runs **only** on a finish claim, and
  is the sole finish gate. Authority is ordered: hard veto > hard confirm >
  semantic judgement, fail-closed throughout (P0 #13a).
- `trajectory_liveness` — "is the trajectory advancing, exploring, or stuck?" —
  a pure function over bounded history in `graph/context.py`, **not a node** and
  not a counter (P0 #13b). It is the sole input to stuck-based routing and never
  reads a single-step verdict.

`nodes/observation_capture.py` is shared post-action observation capture, so
reflect and acceptance cannot disagree about "the screen right now". When the two
seem to contradict each other, look there first.

Do not attribute finish-gate failures to `nodes/reflect.py` — that split landed in
commit `46f5bd6`. Likewise do not attribute a stuck/looping trajectory or an
unexpected takeover to reflect: it no longer owns either. Reflect held a single
`retry_count` accumulator that never reset on success, so a normally-progressing
task could be handed to a human; that ownership moved out in this cycle.

## Source Mapping

When diagnosis points to a layer, inspect the corresponding files. The reflection
row now expands the old `task_goal.py` target into the declarative GoalContract
system (see P0 constraint #13a in `AGENTS.md`).

| Layer | Primary Files |
|---|---|
| screenshot | `phone_agent/adb/screenshot.py`, `phone_agent/graph/screenshot_status.py` |
| parse/adapter | `phone_agent/model/client.py`, `phone_agent/actions/adapter.py` |
| validation | `phone_agent/actions/validator.py`, `phone_agent/actions/repair.py` |
| grounding | `phone_agent/actions/grounding.py`, `phone_agent/grounding/`, `phone_agent/graph/observation.py`, `phone_agent/graph/marks.py` |
| safety/HITL | `phone_agent/actions/safety.py`, `phone_agent/config/policy.py`, `phone_agent/graph/edges.py`, `phone_agent/graph/nodes/confirm.py`, `phone_agent/graph/nodes/takeover.py` |
| capability | `phone_agent/actions/capability.py`, `phone_agent/actions/receipt.py`, `phone_agent/graph/nodes/execute.py` |
| execution | `phone_agent/graph/nodes/execute.py`, `phone_agent/graph/tools/`, `phone_agent/adb/device.py`, `phone_agent/adb/input.py` |
| goal contract | `phone_agent/graph/nodes/goal_node.py`, `phone_agent/graph/goal_requirements.py`, `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal.py`, `phone_agent/graph/predicates.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/goal_binding.py` |
| reflection (per-step) | `phone_agent/graph/nodes/reflect.py`, `phone_agent/graph/nodes/observation_capture.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/expected_outcome.py` |
| acceptance / finish gate | `phone_agent/graph/nodes/acceptance.py`, `phone_agent/graph/goal_evaluator.py`, `phone_agent/graph/goal.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/predicates.py`, `phone_agent/graph/goal_evidence.py`, `phone_agent/graph/nodes/observation_capture.py`, `phone_agent/graph/compatibility_adapters.py` |
| trajectory liveness (P0 #13b) | `phone_agent/graph/context.py` (`trajectory_liveness`), `phone_agent/graph/edges.py`, `phone_agent/config/policy.py` |
| checkpoint / goal resume | `phone_agent/checkpoint/goal_resume.py`, `phone_agent/checkpoint/serde.py` |
| context | `phone_agent/graph/context.py`, `phone_agent/graph/nodes/plan.py` |
| eval/trace | `evals/run_eval.py`, `phone_agent/graph/trace.py`, `phone_agent/agent.py` |

Acceptance / finish-gate signals to surface in the report (all from the
`acceptance` node, not reflect):

- `goal_not_satisfied` — finish claim rejected by `GoalEvaluator`; replan.
- `acceptance_no_contract` — verification attempted with no compiled contract.
  Fail-closed rejection, so the root cause is in the **goal** layer, not here.
- `acceptance_hard_veto` — programmatic signals contradicted the finish claim
  outright; trust the programmatic side.
- `pure_evaluation_degraded` — a criterion was unobservable, so the kept verdict
  differs from the pure evaluation; report both statuses.
- `matched_terminal_evidence` — criteria the model named as satisfied.
- `missing_terminal_evidence` — required criteria the model failed to name
  (hard gate; never auto-upgrade to success).
- `needs_recompile` — mid-task contract swap requested (has no writer today; only
  via `configurable["task_goal_contract_override"]`).
- `soft_match_accepted` — finish relied on the detail-only soft match (evidence
  relaxation without content confirmation); verify the opened page manually.
- `programmatic_contradiction_override` — programmatic signals overrode a
  `vlm_judge` self-attestation; trust the programmatic side.
- `typed_fact_not_yet_collected` — if a criterion stays here, the predicate and
  the fact provider disagree; check `value_domain` alignment (see below).
- `verifier_status` / `verifier_evidence.matched_postconditions` /
  `verifier_evidence.missing_postconditions` / `weak_signals` /
  `dynamic_change_only` / `fallback_chain` when present.

Evidence-resolution signals (per criterion, from `EvidenceAuthorityPolicy`):

- `existential_match` — one authoritative fact matched. For an existential
  predicate (per-node accessibility facts) a single hit among many non-matching
  nodes is a match; siblings that do not match contribute no evidence.
- `not_observed_in_view` — every authoritative fact was searched and none
  matched. **This is `unknown`, not a contradiction.** "Not found in the current
  viewport, this instant, via the accessibility channel" also covers offscreen
  content needing a scroll, text rendered inside an image, and truncated labels.
  Report it as "not observed yet", never as failure.
- `existential_inconclusive` — a fact had a type/value problem, so the search
  itself was unreliable.
- `same_tier_conflict` — retained for `screen_singular` / `element_scoped`
  predicates only: same-tier facts disagree about one screen-wide or
  element-scoped value.

Only a **positive counter-observation** contradicts: a device/summary source that
read the target and reported a different value (e.g. expected foreground app `A`,
observed `B`; expected toggle ON, observed OFF). A failed substring search is not
one, and before commit `3892614` it could hard-veto a genuinely completed task.

Known limitation — `element_scoped` predicates (`ui.toggle_state`,
`ui.object_rank`) resolve `unknown` on any multi-element screen because evidence
is not yet scoped to a selected element. Fail-closed and harmless, but those
criteria do not currently verify; do not report their `unknown` as a defect.

Trajectory liveness signals (`graph/context.py::trajectory_liveness`, P0 #13b):

- `advancing` — a goal criterion moved toward satisfaction.
- `exploring` — no criterion movement, but a state not visited before was
  reached. **Legitimate search lives here and must not be reported as failure.**
- `stuck` — neither, for `novelty_exhaustion_steps` consecutive steps. Oscillation
  (Back/forward loops) lands here correctly: the surface changes every step but no
  new state is added.
- `novelty_streak` / `reasons` — surface these; a rising streak with `exploring`
  is normal, a rising streak toward `stuck` is the loop signal.

Progress is **goal-relative**: only criterion movement and reaching a new state
count. Surface change, screen-hash change, new marks and `typed_text_present` are
NOT progress — they oscillate. Do not read them as advancement in the report.

Split counters (one meaning each; they used to share `retry_count`):

- `observation_retry_count` — consecutive screenshot/observation infrastructure
  failures. At its limit this routes takeover, because it is unrecoverable.
- `acceptance_round_count` — finish claims rejected by the gate, i.e. replan
  rounds. Not an error count.
- `max_steps` exhaustion is an **incomplete report, never a takeover**: the
  window ended and the budget-forced acceptance either rejected the claim
  (→ `goal_not_satisfied`), granted a continuation (trace
  `continuation_granted`; `max_steps` grew, `budget_acceptance_done` reset), or
  hit the run's absolute ceiling (`finish_source=absolute_budget_exhausted`).
  Takeover means only a human can do this (login / captcha / payment /
  structural infeasibility).

Contract adequacy signals (compile-time, from the `goal_compile_result` trace
event — **not** present in `result.json`):

- `predicate_unobservable` — no fact provider can ever emit this predicate; also
  emitted when a `raw_text` expectation cannot be node text (prose, terminal
  punctuation, screen meta-language, over-long bindings).
- `binding_never_observed` — a `raw_text` expectation has not appeared in any
  observation since the target app was entered, so it is probably a compiler
  artefact. Severity is `degraded` and diagnostic only: it must never hard-veto,
  because the literal may legitimately require scrolling.
- `predicate_domain_mismatch` — the predicate's declared `value_domain`
  (`raw_text`/`digest`/`identifier`/`scalar`/`structured`) does not match what the
  provider actually produces. Historically the compiler emitted a sha256 digest
  while the evaluator compared raw screen text, making the criterion structurally
  unsatisfiable with a green test suite (fixed in `6b133b4`).
- `task_binding_mismatch` / `required_criteria_missing` — the other two
  `STRUCTURAL_REASON_CODES` in `graph/goal_requirements.py`.

Structural rejections mean the contract could never be satisfied. Fix the
predicate binding or the fact provider; never widen the gate to get past them.
Severity is three-tier: structural → `inadequate` (takeover), semantic →
`degraded` (keep working, weaker verification), ambiguous → `needs_clarification`.

Other signals from the 9-phase rebuild (commit `7dc6946`):

- `capability_missing` / `capability_unavailable` — capability gate rejected
  dispatch; check `actions/capability.py` registry coverage before anything else.
- `unsupported_semantics` / `needs_goal_clarification` — goal contract adequacy
  rejection; a common root cause is a task verb missing from
  `TaskRequirementExtractor._OPERATIONS` in `graph/goal_requirements.py`.
- `goal_resume_*` — HMAC-bound goal resume failures; check
  `checkpoint/goal_resume.py` key consistency and serde egress collapsing.

## Report Design

Reports must be interactive HTML, not only Markdown. Use a data-dense dashboard
style:

- Primary blue `#1E40AF`, amber accent `#F59E0B`.
- Fira Code for IDs, paths, timestamps, and code symbols.
- Tabs: Overview, Timeline, Source Analysis, Recommendations, Raw Evidence.
- Filters for step, event, layer, severity, and source file.
- Overview must carry **three** separate verification cards, one per owner, so a
  finding is never attributed to the wrong layer:
  1. per-step postcondition verification (reflect),
  2. the finish gate (acceptance), including `per_criterion` status/reason per
     criterion and the contract adequacy status/`reason_codes`,
  3. trajectory liveness — state (`advancing`/`exploring`/`stuck`), `reasons`,
     `novelty_streak`, plus `observation_retry_count` and
     `acceptance_round_count` shown as the distinct counters they now are.
  A finish-gate status string alone is not enough — the reason is what points at
  the source file. Render `unknown` neutrally (not red): it means "not observed",
  and only a positive counter-observation is a contradiction.
- Include `<base target="_blank">`.
- Apply `word-break: break-all` and `overflow-wrap: break-word` to long paths/URLs.
- Never include raw screenshot base64, API keys, verification codes, or unredacted
  private text.

## Gotchas

- Do not treat `success=false` as enough. Explain what actually happened.
- Do not call a long run "stuck" until checking `status.json`, latest trace event,
  and `run_output.log`.
- Do not propose modifications without linking them to trace evidence and source
  files.
- Do not blame `nodes/reflect.py` for a finish-gate rejection. Check the
  `acceptance` node's own events first (`acceptance_result`,
  `acceptance_hard_veto`, `acceptance_no_contract`).
- Do not read a green offline suite as proof a criterion is satisfiable. A
  value-domain mismatch can make a criterion unsatisfiable at runtime while every
  test passes; the compile-time adequacy `reason_codes` are the check for that.
- **Absence of evidence is not contradiction.** Never report `unknown` /
  `not_observed_in_view` as failure. GUI observation is partially observable: the
  target may be offscreen, inside an image, or truncated. Only a positive
  counter-observation contradicts.
- Do not report `exploring` as a problem. A search-and-browse task must try
  candidates that do not pan out; that is the intended state, not a defect. Only
  `stuck` (no new state and no criterion movement) is a loop.
- Do not report `max_steps` exhaustion as a takeover, and do not report a takeover
  as "the agent gave up". Takeover means only a human can proceed. A window
  exhaustion that earned a continuation is progress, not a defect — check the
  `continuation_granted` / `continuation_denied` traces before attributing.
- `--dry-run` never enters the graph (`by_node` is just `eval`), so it validates
  the report pipeline only — never finish-gate or grounding behavior.
- Do not auto-edit business code from this skill unless the user separately asks
  for a fix.
- Prefer `.venv/bin/python`; fall back only if `.venv` is unavailable.
- Real LocateAnything needs Apple Silicon + Metal. In ZCode's sandbox, MLX may
  import but fail at runtime with `[metal::load_device] No Metal device available`;
  when `--grounding-provider hybrid|locateanything` needs real LocateAnything,
  request an escalated (sandbox-disabled) Bash rerun and record the MLX preflight
  result instead of calling it a model failure.
- Reports must surface `ExpectedOutcome`, `verifier_status`,
  `verifier_evidence.matched_postconditions`,
  `verifier_evidence.missing_postconditions`, `weak_signals`,
  `dynamic_change_only`, and provider `fallback_chain` when present.
- `PHONE_AGENT_REMOTE_GROUNDING_*` env variables no longer have any effect — the
  remote OpenAI-compatible grounding provider was reverted (commit `e0a2e4b`).
  Do not rely on `PHONE_AGENT_REMOTE_GROUNDING_BASE_URL` /
  `PHONE_AGENT_REMOTE_GROUNDING_API_KEY` /
  `PHONE_AGENT_REMOTE_GROUNDING_MODEL` /
  `PHONE_AGENT_REMOTE_GROUNDING_PROFILE` /
  `PHONE_AGENT_REMOTE_GROUNDING_REASONING_EFFORT`. They are stale entries in some
  `.env` files and will be ignored by the current grounding factory.
- `PHONE_AGENT_TRACE_UNREDACTED_PROMPT=true` is a *dangerous* local debug mode
  that records unredacted prompt text. When it is on, the report must flag it
  loudly and must still strip image payloads from prompt debug traces.