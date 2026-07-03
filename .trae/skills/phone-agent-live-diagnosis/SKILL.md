---
name: phone-agent-live-diagnosis
description: Use when the user wants to run or monitor Open-AutoGLM PhoneAgent on a real Android device, inspect actual execution effects, correlate trace results with source code, generate code-level modification recommendations, or produce an interactive HTML diagnosis report. Trigger on 实机测试, 监看, 实际效果, 手机任务, live diagnosis, phone agent report, 源码归因, HTML 报告.
---

# Phone Agent Live Diagnosis

Run Open-AutoGLM real-device tasks, collect trace evidence, map failures back to source code, and generate an interactive HTML report.

## Scope

Use this skill for:

- Real Android device execution monitoring for `PhoneAgent`.
- One-off natural-language test targets such as "打开设置并进入 Wi-Fi 页面".
- Post-run diagnosis that must explain actual behavior, source-code cause, and modification suggestions.
- Interactive HTML reports for engineering review.

Do not use this skill for pure unit-test review, benchmark-only LocateAnything evaluation, or code modification without a real or dry-run diagnosis request.

## Default Command

From the repository root:

```bash
.venv/bin/python .trae/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "测试目标"
```

Dry run:

```bash
.venv/bin/python .trae/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py "完成一个本地 smoke 任务" --dry-run
```

## TraeCLI 2.0 Escalation Gate

This skill runs real-device and local-model diagnostics, so some failures require an escalated command rerun instead of a plain retry.

Must request escalated execution with TraeCLI tool parameters when:

- A diagnosis command touches a real Android device, ADB server, host process state, or user-level auth/config.
- `--grounding-provider hybrid|locateanything` needs real LocateAnything / MLX / Metal.
- The command fails with sandbox-shaped errors such as `Operation not permitted`, permission denied while writing outside the workspace, unavailable GPU/Metal, blocked device access, DNS/registry/network failures, or inability to write `~/.feishu-cli/token.json`.

The escalated rerun must be an actual tool call using:

```text
sandbox_permissions="require_escalated"
justification="<short user-facing question explaining why this command needs host access>"
```

Use a narrow `prefix_rule` only for repeatable non-destructive commands, for example:

```text
[".venv/bin/python", ".trae/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py"]
[".venv/bin/python", "-m", "bench.grounding.run_locateanything"]
["feishu-cli", "auth"]
```

Do not merely tell the user "this needs escalation"; request it through the tool call. Do not set `prefix_rule` for destructive commands such as `rm` or `git reset`.

Useful options:

```bash
--device-id <adb-id>
--max-steps 10
--base-url http://localhost:8000/v1
--model autoglm-phone-9b
--apikey EMPTY
--user-agent "Open-AutoGLM/0.1"
--output-mode json_schema
--context-mode inject
--grounding-provider hybrid
--accessibility-timeout 3
--accessibility-max-marks 80
--locateanything-context-max-chars 0
--locateanything-structure-mode off
--locateanything-max-visual-candidates 30
--locateanything-visual-category-budget 5
--locateanything-max-structure-calls 5
--output-dir outputs/live-diagnosis
```

Model gateway environment:

```bash
export PHONE_AGENT_BASE_URL=http://localhost:8000/v1
export PHONE_AGENT_MODEL=autoglm-phone-9b
export PHONE_AGENT_API_KEY=EMPTY
export PHONE_AGENT_HTTP_HEADERS='{"Header":"Value"}'
export PHONE_AGENT_USER_AGENT='Open-AutoGLM/0.1'
export PHONE_AGENT_CF_ACCESS_CLIENT_ID=...
export PHONE_AGENT_CF_ACCESS_CLIENT_SECRET=...
```

Grounding environment:

```bash
export PHONE_AGENT_GROUNDING_PROVIDER=hybrid
export PHONE_AGENT_ACCESSIBILITY_TIMEOUT=3.0
export PHONE_AGENT_ACCESSIBILITY_MAX_MARKS=80
export PHONE_AGENT_LOCATEANYTHING_MODEL=models/LocateAnything-3B-4bit
export PHONE_AGENT_LOCATEANYTHING_MAX_SIZE=960
export PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS=0
export PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE=off  # off | target | screen
export PHONE_AGENT_LOCATEANYTHING_MAX_VISUAL_CANDIDATES=30
export PHONE_AGENT_LOCATEANYTHING_VISUAL_CATEGORY_BUDGET=5
export PHONE_AGENT_LOCATEANYTHING_MAX_STRUCTURE_CALLS=5
```

Do not configure old remote grounding variables for the current local runtime path:
`PHONE_AGENT_REMOTE_GROUNDING_BASE_URL`, `PHONE_AGENT_REMOTE_GROUNDING_API_KEY`,
`PHONE_AGENT_REMOTE_GROUNDING_MODEL`, `PHONE_AGENT_REMOTE_GROUNDING_PROFILE`.
If they are present, the diagnosis preflight reports them as deprecated.

## Workflow

1. Confirm the user provided a concrete test target.
2. Run `scripts/run_diagnosis.py` with the target and any user-provided options.
3. Read the generated `summary.json` and `report.html` path.
4. Report the result briefly in Chinese, including:
   - verdict: success / failed / blocked / uncertain
   - report path
   - trace id/path if present
   - top source-code findings

During long real-device runs, inspect live progress instead of guessing:

```bash
.venv/bin/python .trae/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py --status outputs/live-diagnosis/<run_id>
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

When diagnosis points to a layer, inspect the corresponding files:

| Layer | Primary Files |
|---|---|
| screenshot | `phone_agent/adb/screenshot.py`, `phone_agent/graph/screenshot_status.py` |
| parse/adapter | `phone_agent/model/client.py`, `phone_agent/actions/adapter.py` |
| validation | `phone_agent/actions/validator.py`, `phone_agent/actions/repair.py` |
| grounding | `phone_agent/actions/grounding.py`, `phone_agent/grounding/`, `phone_agent/graph/observation.py`, `phone_agent/graph/marks.py` |
| safety/HITL | `phone_agent/actions/safety.py`, `phone_agent/graph/edges.py`, `phone_agent/graph/nodes/confirm.py`, `phone_agent/graph/nodes/takeover.py` |
| execution | `phone_agent/graph/nodes/execute.py`, `phone_agent/graph/tools/`, `phone_agent/adb/device.py`, `phone_agent/adb/input.py` |
| reflection/finish gate | `phone_agent/graph/goal.py`, `phone_agent/graph/goal_compiler.py`, `phone_agent/graph/goal_evaluator.py`, `phone_agent/graph/nodes/goal_node.py`, `phone_agent/graph/nodes/reflect.py`, `phone_agent/graph/verifier.py` |
| context | `phone_agent/graph/context.py`, `phone_agent/graph/nodes/plan.py` |
| eval/trace | `evals/run_eval.py`, `phone_agent/graph/trace.py`, `phone_agent/agent.py` |

## Report Design

Reports must be interactive HTML, not only Markdown. Use a data-dense dashboard style:

- Primary blue `#1E40AF`, amber accent `#F59E0B`.
- Fira Code for IDs, paths, timestamps, and code symbols.
- Tabs: Overview, Timeline, Source Analysis, Recommendations, Raw Evidence.
- Filters for step, event, layer, severity, and source file.
- Include `<base target="_blank">`.
- Apply `word-break: break-all` and `overflow-wrap: break-word` to long paths/URLs.
- Never include raw screenshot base64, API keys, verification codes, or unredacted private text.

## Gotchas

- Do not treat `success=false` as enough. Explain what actually happened.
- Do not call a long run "stuck" until checking `status.json`, latest trace event, and `run_output.log`.
- Do not propose modifications without linking them to trace evidence and source files.
- Do not auto-edit business code from this skill unless the user separately asks for a fix.
- Prefer `.venv/bin/python`; fall back only if `.venv` is unavailable.
- Real LocateAnything needs Apple Silicon + Metal. In TraeCLI sandbox, MLX may import but fail at runtime with `[metal::load_device] No Metal device available`; when `--grounding-provider hybrid|locateanything` needs real LocateAnything, request escalated command execution and record the MLX preflight result instead of calling it a model failure.
- Reports must surface `ExpectedOutcome`, `GoalContract`, `goal_contract_status`, `goal_compile_source`, `finish_validation_evidence.matched_terminal_evidence`, `finish_validation_evidence.missing_terminal_evidence`, `verifier_status`, `verifier_evidence.matched_postconditions`, `verifier_evidence.missing_postconditions`, `weak_signals`, `dynamic_change_only`, and provider `fallback_chain` when present.
- `finish` is only a claim. A run is not complete until `GoalEvaluator` validates all required `SuccessCriterion` entries named in `finish.matched_terminal_evidence`; missing/unknown evidence is a diagnosis finding, not success.
- Preflight should warn when multiple ADB devices are connected without `--device-id`, the configured device is absent, ADB Keyboard is not active, or deprecated remote grounding environment variables are set.
