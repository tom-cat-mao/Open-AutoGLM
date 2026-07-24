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

Inspect a long-running job:

```bash
.venv/bin/python .agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py --status outputs/live-diagnosis/<run_id>
tail -f outputs/live-diagnosis/<run_id>/run_output.log
tail -f outputs/live-diagnosis/<run_id>/traces/*.jsonl
```

> The legacy copy under `.trae/skills/phone-agent-live-diagnosis/` is preserved for
> TraeCLI. ZCode workflows should prefer the `.agents/skills/...` path above.

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

Most flags fall back to `PHONE_AGENT_*` environment variables (loaded from the
project `.env`); only override what you need.

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
| goal contract | `phone_agent/graph/nodes/goal_node.py`, `phone_agent/graph/goal_requirements.py`, `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal.py`, `phone_agent/graph/goal_binding.py` |
| reflection / finish gate | `phone_agent/graph/goal.py`, `phone_agent/graph/goal_evaluator.py`, `phone_agent/graph/nodes/reflect.py`, `phone_agent/graph/verifier.py`, `phone_agent/graph/fact_providers.py`, `phone_agent/graph/predicates.py`, `phone_agent/graph/goal_evidence.py`, `phone_agent/graph/compatibility_adapters.py` |
| checkpoint / goal resume | `phone_agent/checkpoint/goal_resume.py`, `phone_agent/checkpoint/serde.py` |
| context | `phone_agent/graph/context.py`, `phone_agent/graph/nodes/plan.py` |
| eval/trace | `evals/run_eval.py`, `phone_agent/graph/trace.py`, `phone_agent/agent.py` |

Key reflection/finish-gate signals to surface in the report:

- `goal_not_satisfied` — finish claim rejected by `GoalEvaluator`; replan.
- `matched_terminal_evidence` — criteria the model named as satisfied.
- `missing_terminal_evidence` — required criteria the model failed to name
  (hard gate; never auto-upgrade to success).
- `needs_recompile` — mid-task contract swap requested (has no writer today; only
  via `configurable["task_goal_contract_override"]`).
- `soft_match_accepted` — finish relied on the detail-only soft match (evidence
  relaxation without content-hash confirmation); verify the opened page manually.
- `programmatic_contradiction_override` — programmatic signals overrode a
  `vlm_judge` self-attestation; trust the programmatic side.
- `verifier_status` / `verifier_evidence.matched_postconditions` /
  `verifier_evidence.missing_postconditions` / `weak_signals` /
  `dynamic_change_only` / `fallback_chain` when present.

Signals from the 9-phase rebuild (commit `7dc6946`) to route to the new layers:

- `capability_missing` / `capability_unavailable` — capability gate rejected
  dispatch; check `actions/capability.py` registry coverage before anything else.
- `unsupported_semantics` / `needs_goal_clarification` — goal contract adequacy
  rejection; the usual root cause is a task verb missing from
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