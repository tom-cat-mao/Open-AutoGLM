#!/usr/bin/env python3
"""Run PhoneAgent live diagnosis and render an interactive HTML report."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]


def resolve_repo_root() -> Path:
    """Resolve the Open-AutoGLM repo root.

    The skill ships under ``<repo>/.agents/skills/phone-agent-live-diagnosis/scripts``
    so ``parents[4]`` is the repo root. ``PHONE_AGENT_REPO_ROOT`` overrides this for
    callers that invoke the script through symlinks or from a copied tree.
    """

    override = os.getenv("PHONE_AGENT_REPO_ROOT")
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_dir():
            return path
    return Path(__file__).resolve().parents[4]


ROOT = resolve_repo_root()
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "live-diagnosis"


SOURCE_RULES = [
    {
        "signals": {
            "screenshot_unavailable",
            "secure_screenshot_blocked",
            "adb_screencap_failed",
            "screenshot_pull_failed",
            "invalid_screenshot",
        },
        "layer": "grounding",
        "severity": "P0",
        "title": "截图不可用或安全截图被阻断",
        "files": [
            "phone_agent/adb/screenshot.py",
            "phone_agent/graph/screenshot_status.py",
            "phone_agent/graph/nodes/plan.py",
            "phone_agent/graph/nodes/reflect.py",
        ],
        "suggestion": "确认无效截图在进入模型前 fail-closed，并在报告中记录真实 failure_code；如涉及多分辨率设备，补充真实设备尺寸采集。",
        "verify": "在安全页、黑屏页和普通页各运行一次 diagnosis，检查 plan 阶段是否停止且未把占位图继续送入模型。",
    },
    {
        "signals": {"invalid_json", "parse_error", "unsupported_tool_call", "model_request_failed"},
        "layer": "parse",
        "severity": "P1",
        "title": "模型输出或结构化解析失败",
        "files": [
            "phone_agent/model/client.py",
            "phone_agent/actions/adapter.py",
            "phone_agent/graph/nodes/plan.py",
        ],
        "suggestion": "核对 output_mode、response_format/tool_calls 聚合与 adapter 白名单；失败时只允许格式重试，不应伪装为 finish。",
        "verify": "用 json_schema/tool_calls/auto 分别运行同一目标，比较 parse_metadata 与 parse_retry_success。",
    },
    {
        "signals": {
            "mark_required",
            "unknown_mark",
            "mark_unavailable",
            "stale_mark",
            "stale_screen",
            "hash_mismatch",
            "grounding_no_candidate",
            "grounding_no_usable_candidate",
            "grounding_ambiguous",
            "low_confidence",
            "bad_bbox",
            "provider_unavailable",
            "missing_provider_hash",
        },
        "layer": "grounding",
        "severity": "P0",
        "title": "Grounding/MarkRegistry 链路失败",
        "files": [
            "phone_agent/actions/grounding.py",
            "phone_agent/grounding/fallback.py",
            "phone_agent/grounding/accessibility.py",
            "phone_agent/grounding/locateanything.py",
            "phone_agent/grounding/factory.py",
            "phone_agent/graph/observation.py",
            "phone_agent/graph/marks.py",
        ],
        "suggestion": "检查 hybrid provider 是否记录 fallback_chain、是否只在 hint 可用时停止 fallback；失败不得回退到主 VLM 坐标。",
        "verify": "分别跑 native 设置页和 WebView/自绘页，确认 accessibility 与 LocateAnything fallback 行为符合预期。",
    },
    {
        "signals": {"unknown_app", "unknown_action", "missing_field", "unsafe_value", "invalid_metadata"},
        "layer": "validation",
        "severity": "P1",
        "title": "ActionIR 校验失败",
        "files": [
            "phone_agent/actions/validator.py",
            "phone_agent/actions/repair.py",
            "phone_agent/config/apps.py",
        ],
        "suggestion": "核对 canonical action schema、Launch registry 和 repair 范围；Repair 只能修别名/大小写，不应猜坐标或隐私文本。",
        "verify": "构造未知 app、错误 action、越界坐标 case，确认均 fail-closed 并记录稳定 error_code。",
    },
    {
        "signals": {"action_safety_rejected", "confirmation_required", "sensitive_tap_requires_confirmation"},
        "layer": "safety",
        "severity": "P0",
        "title": "Safety/HITL 路由异常或需要人工确认",
        "files": [
            "phone_agent/actions/safety.py",
            "phone_agent/config/policy.py",
            "phone_agent/graph/edges.py",
            "phone_agent/graph/nodes/confirm.py",
            "phone_agent/graph/nodes/takeover.py",
            "phone_agent/graph/nodes/execute.py",
        ],
        "suggestion": "检查 terminal guard、pending_execute、action_confirmed 和 confirm/takeover 路由顺序，避免 stale interrupt 把终止状态误路由；HITL 误触发/漏触发时核对 config/policy.py 的 SafetyPolicyRegistry 分类（policy_match / uncertain_fail_closed）。",
        "verify": "运行敏感 Tap、登录/验证码和用户取消确认 case，确认路由和 hitl_count 准确。",
    },
    {
        "signals": {"dispatch_failed", "execution_failed", "missing_action"},
        "layer": "execution",
        "severity": "P1",
        "title": "设备动作执行失败",
        "files": [
            "phone_agent/graph/nodes/execute.py",
            "phone_agent/actions/receipt.py",
            "phone_agent/graph/tools/coords.py",
            "phone_agent/graph/tools/tap.py",
            "phone_agent/graph/tools/swipe.py",
            "phone_agent/graph/tools/type_text.py",
            "phone_agent/graph/tools/launch.py",
            "phone_agent/adb/device.py",
            "phone_agent/adb/input.py",
        ],
        "suggestion": "检查 0-1000 坐标到像素转换、ADB returncode/stderr、输入法切换和 launch component 解析；ActionReceipt 只描述 dispatch，不代表 UI 已跳转，别把 receipt 当作成功证据。",
        "verify": "在不同分辨率设备上运行 Tap/Swipe/Type/Launch case，并记录 ADB stderr。",
    },
    {
        "signals": {
            "model_reflection_failed",
            "repeated_action",
            "network_or_loading",
            # Emitted from the shared observation capture used by
            # plan/reflect/acceptance, so it is not acceptance-specific.
            "context_lost",
            "postcondition_unverified",
            "after_observation_unavailable",
            "dynamic_change_only",
            "missing_postconditions",
            "verifier_unknown",
            "verifier_failure",
            "focused_editable_or_keyboard_visible",
        },
        "layer": "reflection",
        "severity": "P2",
        "title": "单步反思或后置条件校验不稳定",
        "files": [
            "phone_agent/graph/nodes/reflect.py",
            "phone_agent/graph/nodes/observation_capture.py",
            "phone_agent/graph/verifier.py",
            "phone_agent/graph/expected_outcome.py",
            "phone_agent/graph/fact_providers.py",
            "phone_agent/graph/predicates.py",
        ],
        "suggestion": "reflect 只回答“这一步动作生效了吗”，不再决定任务是否完成（finish gate 已移入 acceptance 节点）。检查 ExpectedOutcome、matched/missing postconditions、weak_signals 与模型反思的合并优先级；动态区域变化不能单独证明成功。after-observation 由 nodes/observation_capture.py 统一采集并被 reflect/acceptance 共享——若两者对“当前屏幕”判断不一致，先查这里而不是各自节点。",
        "verify": "运行搜索框点击、输入、视频打开和动态首页变化 case，对比 expected_outcome、verifier_evidence 与 reflection_verdict。",
    },
    {
        "signals": {
            "goal_not_satisfied",
            "finish_validation_unknown",
            "finish_validation_failure",
            "needs_recompile",
            # matched_terminal_evidence is intentionally NOT a trigger: it fires
            # on a clean pass too, and would make every success a P1 finding.
            "missing_terminal_evidence",
            "soft_match_accepted",
            "soft_matched_criteria",
            "programmatic_contradiction_override",
            "acceptance_no_contract",
            "acceptance_hard_veto",
            "acceptance_error",
            "pure_evaluation_degraded",
            "typed_fact_not_yet_collected",
        },
        "layer": "acceptance",
        "severity": "P1",
        "title": "Finish gate（验收）拒绝或判定不稳定",
        "files": [
            "phone_agent/graph/nodes/acceptance.py",
            "phone_agent/graph/goal_evaluator.py",
            "phone_agent/graph/goal.py",
            "phone_agent/graph/verifier.py",
            "phone_agent/graph/goal_evidence.py",
            "phone_agent/graph/fact_providers.py",
            "phone_agent/graph/predicates.py",
            "phone_agent/graph/nodes/observation_capture.py",
            "phone_agent/graph/compatibility_adapters.py",
            "phone_agent/graph/runtime_goal.py",
        ],
        "suggestion": "finish gate 住在 nodes/acceptance.py（不是 reflect.py），只在模型声明完成时触发，权威顺序固定为 hard veto > hard confirm > semantic judgement，全程 fail-closed。vlm_judge 标准未在 matched_terminal_evidence 点名即视为 missing（硬门）。acceptance_no_contract 表示没有已编译契约就想验收，属 fail-closed 拒绝，查 goal 层而非此处。acceptance_hard_veto 表示程序信号直接否决完成声明，应信程序侧。needs_recompile 当前无 writer，mid-task 合约切换仅通过 configurable[\"task_goal_contract_override\"]。若 per_criterion 长期停在 typed_fact_not_yet_collected，检查 fact_providers/predicates 的 typed predicate 与 evidence 对齐，并确认 predicate 的 value_domain 与 provider 实际产出同域（编译端曾发过 sha256 而比较端拿 raw text，导致条件恒不可满足）。soft_match_accepted 表示依赖 detail-only 软匹配，需人工确认打开的是正确详情页。",
        "verify": "跑一个会声明完成的任务（如“打开设置并进入 Wi-Fi 页面”），确认 trace 出现 acceptance 节点的 acceptance_result 事件，且 finish_validation.evidence.per_criterion 中每条 required 标准都有 matched/missing 判定与具体 reason。",
    },
    {
        "signals": {
            "capability_missing",
            "capability_unavailable",
            "capability_rejected",
        },
        "layer": "capability",
        "severity": "P0",
        "title": "Capability 闸门拒绝动作分发",
        "files": [
            "phone_agent/actions/capability.py",
            "phone_agent/actions/receipt.py",
            "phone_agent/graph/nodes/execute.py",
        ],
        "suggestion": "capability_missing 表示动作没有 ToolCapability 声明（新动作类型未注册）；capability_unavailable 表示声明存在但 implementation_status=unavailable（stub 动作不再报假成功，fail-closed）。检查 CAPABILITY_REGISTRY 是否覆盖该动作名，以及 ActionReceipt 的 dispatch_status；不要为绕过闸门把 stub 标成 implemented。",
        "verify": "用同一会话分别触发已注册动作和未注册动作名，确认 trace 出现 capability_rejected 事件且 receipt.side_effect_receipt.reason_code 正确。",
    },
    {
        "signals": {
            "unsupported_semantics",
            "needs_goal_clarification",
            "goal_contract_invalid",
            "goal_approval_replacement_inadequate",
            "runtime_goal_binding_invalid",
            "runtime_goal_binding_unavailable",
            "runtime_goal_context_missing",
            "task_binding_mismatch",
            "required_criteria_missing",
            "predicate_unobservable",
            "predicate_domain_mismatch",
            "contract_adequacy_inadequate",
            "contract_adequacy_needs_clarification",
            "contract_adequacy_degraded",
        },
        "layer": "goal",
        "severity": "P1",
        "title": "Goal 契约编译或 adequacy 校验失败",
        "files": [
            "phone_agent/graph/nodes/goal_node.py",
            "phone_agent/graph/goal_requirements.py",
            "phone_agent/graph/goal_compiler.py",
            "phone_agent/graph/goal.py",
            "phone_agent/graph/predicates.py",
            "phone_agent/graph/fact_providers.py",
            "phone_agent/graph/goal_binding.py",
        ],
        "suggestion": "先分清 adequacy 的三档严重度：structural（inadequate → takeover）、semantic（degraded → 继续跑但验证更弱）、ambiguous（needs_clarification）。STRUCTURAL_REASON_CODES = {task_binding_mismatch, required_criteria_missing, predicate_unobservable, predicate_domain_mismatch}（见 goal_requirements.py）。predicate_unobservable 表示该 predicate 没有任何 fact provider 能产出，predicate_domain_mismatch 表示 predicate 声明的 value_domain（raw_text/digest/identifier/scalar/structured）与 provider 实际产出不同域——这两条是把“契约永不可满足”从运行时潜伏提前到编译期暴露的机制，不要通过放宽 gate 绕过，应修 predicate 绑定或 provider。另一常见根因：任务动词不在 TaskRequirementExtractor._OPERATIONS 关键词表（中英双语有限集合），导致 operation_kind=unknown。先核对 trace 中 task_requirement_set.safe_projection 的 operation_kind/ambiguities；unsupported_semantics 走 takeover，不要降级为静默通过。",
        "verify": "分别用含表中动词（打开/搜索/播放）和表外动词的任务跑 diagnosis，比较 trace 中 goal_compile_result 的 contract_adequacy status/reason_codes 与 state 的 contract_adequacy_status（注意 result.json 不含该字段，只能从 trace 取）。",
    },
    {
        "signals": {
            "goal_resume_hmac_mismatch",
            "goal_resume_rehydration_failed",
            "goal_resume_untrusted",
            "trusted_goal_resume_invalid",
        },
        "layer": "checkpoint",
        "severity": "P1",
        "title": "可信 Goal 恢复（HMAC 绑定）失败",
        "files": [
            "phone_agent/checkpoint/goal_resume.py",
            "phone_agent/checkpoint/serde.py",
        ],
        "suggestion": "checkpoint 恢复采用 HMAC 绑定 + fail-closed 重水合；goal_evidence_ledger 在 checkpoint 出口被清空，进度只能从 trusted_goal_resume 投影恢复。若恢复后目标状态丢失，优先核对 HMAC key 是否一致、serde 塌缩是否误删了必要投影。",
        "verify": "在有 checkpointer 的配置下中断后恢复同一任务，确认 goal_resume 投影可重水合且 evidence ledger 不依赖 checkpoint 内容。",
    },
]

# Keyed by rule["layer"] so inserting or reordering SOURCE_RULES cannot silently
# repoint a layer fallback at the wrong rule (positional indices used to do this).
_RULES_BY_LAYER = {rule["layer"]: rule for rule in SOURCE_RULES}

DECISION_LOOP_RULE = {
    "signals": {
        "repeated_action_detected",
        "avoid_repeating_ignored",
        "budget_exhausted_no_finish",
        "liveness_stuck",
        "repeated_failure_count",
    },
    "layer": "decision",
    "severity": "P1",
    "title": "决策层重复循环：agent 在同一目标/页面上反复动作直至预算耗尽",
    "files": [
        "phone_agent/graph/context.py",
        "phone_agent/graph/edges.py",
        "phone_agent/graph/nodes/execute.py",
        "phone_agent/graph/nodes/plan.py",
    ],
    "suggestion": "优先核对 execute 层重复目标守卫（repeated_target_loop）是否生效、trajectory_liveness 是否把语义振荡判为 stuck、avoid_repeating 提示是否被模型持续无视；此类失败不是 grounding/执行层故障，不要归因为 reflection。",
    "verify": "用触发 repeated_action_detected / liveness_stuck 的 trace 重跑诊断，确认 decision finding 取代 reflection 误归因，且 signal_steps 覆盖率达到 confirmed。",
}

LAYER_FALLBACKS = {
    "parse": _RULES_BY_LAYER["parse"],
    "adapter": _RULES_BY_LAYER["parse"],
    "validation": _RULES_BY_LAYER["validation"],
    "grounding": _RULES_BY_LAYER["grounding"],
    "safety": _RULES_BY_LAYER["safety"],
    "execution": _RULES_BY_LAYER["execution"],
    "reflection": _RULES_BY_LAYER["reflection"],
    "acceptance": _RULES_BY_LAYER["acceptance"],
    "capability": _RULES_BY_LAYER["capability"],
    "goal": _RULES_BY_LAYER["goal"],
    "checkpoint": _RULES_BY_LAYER["checkpoint"],
    "context": {
        "layer": "context",
        "severity": "P2",
        "title": "Context harness 指标或注入异常",
        "files": [
            "phone_agent/graph/context.py",
            "phone_agent/graph/nodes/plan.py",
            "phone_agent/graph/trace.py",
        ],
        "suggestion": "确认 compact_messages_for_request 只影响请求消息，不改写 state，并验证敏感信息脱敏。",
        "verify": "分别用 off/observe/inject 跑同一目标，比较 selected_sections 与消息统计。",
    },
}


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration: float


def main() -> int:
    load_project_env()
    args = parse_args()
    if getattr(args, "status", None):
        print(json.dumps(read_status(Path(args.status)), ensure_ascii=False, indent=2))
        return 0
    run_id = build_run_id(args.target)
    run_dir = Path(args.output_dir).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    task_path = run_dir / "task.json"
    trace_dir = run_dir / "traces"
    task_record = {
        "id": slugify(args.target)[:48] or "live_task",
        "task": args.target,
        "category": "live" if not args.dry_run else "smoke",
        "expected_app": args.expected_app,
        "max_steps": args.max_steps,
    }
    write_json(task_path, [task_record])

    preflight = collect_preflight(args)
    write_json(run_dir / "preflight.json", preflight)

    cmd = build_eval_command(args, task_path, trace_dir)
    command_result = run_command(cmd, env=build_env(args), run_dir=run_dir, trace_dir=trace_dir)

    result = parse_eval_result(command_result.stdout, command_result.stderr, command_result.returncode)
    write_json(run_dir / "result.json", result)

    record = first_result_record(result)
    trace_path = resolve_trace_path(record, trace_dir)
    trace_events = read_jsonl(trace_path) if trace_path else []
    if trace_path and trace_path.exists():
        shutil.copy2(trace_path, run_dir / "trace.jsonl")

    trace_summary = summarize_trace(trace_events)
    write_json(run_dir / "trace_summary.json", trace_summary)

    code_findings = build_code_findings(record, trace_summary)
    recommendations = build_recommendations(code_findings, record)
    write_json(run_dir / "code_findings.json", code_findings)
    write_json(run_dir / "recommendations.json", recommendations)

    summary = build_summary(
        args=args,
        run_id=run_id,
        run_dir=run_dir,
        command=cmd,
        command_result=command_result,
        result=result,
        record=record,
        trace_summary=trace_summary,
        code_findings=code_findings,
        recommendations=recommendations,
    )
    write_json(run_dir / "summary.json", summary)

    html_report = render_html_report(summary, trace_events)
    (run_dir / "report.html").write_text(html_report, encoding="utf-8")

    print(json.dumps({
        "run_id": run_id,
        "verdict": summary["verdict"],
        "report_path": str(run_dir / "report.html"),
        "summary_path": str(run_dir / "summary.json"),
        "trace_path": record.get("trace_path"),
        "top_findings": [item["title"] for item in code_findings[:3]],
    }, ensure_ascii=False, indent=2))
    return 0 if command_result.returncode == 0 else command_result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Open-AutoGLM live diagnosis")
    parser.add_argument("target", nargs="?", help="Natural-language phone-agent test target")
    parser.add_argument("--status", help="Read a run directory or status.json and print current runtime status")
    parser.add_argument("--dry-run", action="store_true", help="Use eval dry-run without model/device")
    parser.add_argument("--device-id", default=os.getenv("PHONE_AGENT_DEVICE_ID"))
    parser.add_argument("--expected-app", default=None)
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("PHONE_AGENT_MAX_STEPS", "10")))
    parser.add_argument("--base-url", default=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--model", default=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"))
    parser.add_argument("--apikey", default=os.getenv("PHONE_AGENT_API_KEY", "EMPTY"))
    parser.add_argument("--output-mode", choices=["json_schema", "tool_calls", "auto"], default=os.getenv("PHONE_AGENT_OUTPUT_MODE", "json_schema"))
    parser.add_argument("--model-timeout", type=float, default=float(os.getenv("PHONE_AGENT_MODEL_TIMEOUT", "60")))
    parser.add_argument("--model-max-retries", type=int, default=int(os.getenv("PHONE_AGENT_MODEL_MAX_RETRIES", "2")))
    parser.add_argument("--stream", action="store_true", default=parse_bool(os.getenv("PHONE_AGENT_STREAM"), False))
    parser.add_argument("--model-extra-body", default=os.getenv("PHONE_AGENT_MODEL_EXTRA_BODY"))
    parser.add_argument("--thinking-mode", choices=["auto", "on", "off"], default=os.getenv("PHONE_AGENT_THINKING_MODE", "auto"))
    parser.add_argument("--thinking-param", choices=["enable_thinking", "chat_template_kwargs"], default=os.getenv("PHONE_AGENT_THINKING_PARAM", "enable_thinking"))
    parser.add_argument("--context-mode", choices=["off", "observe", "inject"], default=os.getenv("PHONE_AGENT_CONTEXT_MODE", "inject"))
    parser.add_argument("--grounding-provider", default=os.getenv("PHONE_AGENT_GROUNDING_PROVIDER", "hybrid"))
    parser.add_argument("--accessibility-timeout", type=float, default=float(os.getenv("PHONE_AGENT_ACCESSIBILITY_TIMEOUT", "3.0")))
    parser.add_argument("--accessibility-max-marks", type=int, default=int(os.getenv("PHONE_AGENT_ACCESSIBILITY_MAX_MARKS", "80")))
    parser.add_argument("--locateanything-context-max-chars", type=int, default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS", "0")))
    parser.add_argument(
        "--locateanything-structure-mode",
        choices=["off", "target", "screen"],
        default=os.getenv("PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE", "off"),
        help="Optional LocateAnything visual structure mode (off | target | screen)",
    )
    parser.add_argument(
        "--locateanything-max-visual-candidates",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_VISUAL_CANDIDATES", "20")),
        help="Maximum visual sidecar candidates emitted by LocateAnything structure mode",
    )
    parser.add_argument(
        "--locateanything-visual-category-budget",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_VISUAL_CATEGORY_BUDGET", "8")),
        help="Maximum bounded visual categories queried in screen structure mode",
    )
    parser.add_argument(
        "--locateanything-max-structure-calls",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_STRUCTURE_CALLS", "3")),
        help="Maximum LocateAnything calls used for screen structure sidecar generation",
    )
    parser.add_argument(
        "--trace-raw-model-response",
        action="store_true",
        default=parse_bool(os.getenv("PHONE_AGENT_TRACE_RAW_MODEL_RESPONSE"), False),
        help="Write raw model response text into local trace/report metadata for debugging",
    )
    parser.add_argument(
        "--trace-request-messages",
        action="store_true",
        default=parse_bool(os.getenv("PHONE_AGENT_TRACE_REQUEST_MESSAGES"), False),
        help="Write final model request messages into local trace/report metadata for debugging",
    )
    parser.add_argument(
        "--trace-prompt-blocks",
        action="store_true",
        default=parse_bool(os.getenv("PHONE_AGENT_TRACE_PROMPT_BLOCKS"), False),
        help="Write prompt construction blocks into local trace/report metadata for debugging",
    )
    parser.add_argument(
        "--trace-unredacted-prompt",
        action="store_true",
        default=parse_bool(os.getenv("PHONE_AGENT_TRACE_UNREDACTED_PROMPT"), False),
        help="Dangerous local debug mode: do not redact traced request messages or prompt blocks",
    )
    parser.add_argument("--lang", choices=["cn", "en"], default=os.getenv("PHONE_AGENT_LANG", "cn"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if not args.status and not args.target:
        parser.error("target is required unless --status is used")
    return args


def read_status(path: Path) -> dict[str, Any]:
    target = path
    if target.is_dir():
        target = target / "status.json"
    if target.exists():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"state": "invalid_status_json", "path": str(target)}
    run_dir = path if path.is_dir() else path.parent
    traces = run_dir / "traces"
    return {
        "state": "status_missing",
        "path": str(target),
        "latest_trace": latest_trace_status(traces) if traces.exists() else {},
    }


def build_run_id(target: str) -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + slugify(target)[:36]


def slugify(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip()).strip("-")
    if not text:
        return uuid.uuid4().hex[:8]
    return text[:80]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def load_project_env() -> None:
    """Load PHONE_AGENT_* defaults from the project .env without overriding shell values."""

    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("PHONE_AGENT_") or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def collect_preflight(args: argparse.Namespace) -> dict[str, Any]:
    adb_path = shutil.which("adb")
    python_path = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
    data = {
        "repo": str(ROOT),
        "python": python_path,
        "adb_path": adb_path,
        "dry_run": args.dry_run,
        "device_id": args.device_id,
        "output_mode": args.output_mode,
        "context_mode": args.context_mode,
        "grounding_provider": args.grounding_provider,
        "checks": {},
    }
    data["checks"]["python_version"] = safe_cmd([python_path, "--version"])
    if args.grounding_provider.lower() in {
        "hybrid",
        "accessibility_locateanything",
        "uiautomator_locateanything",
        "locateanything",
        "locateanything_mlx",
        "mlx",
    }:
        data["checks"]["mlx_metal"] = check_mlx_metal(python_path)
    if adb_path:
        data["checks"]["adb_version"] = safe_cmd([adb_path, "version"])
        data["checks"]["adb_devices"] = safe_cmd([adb_path, "devices", "-l"])
        if args.device_id:
            prefix = [adb_path, "-s", args.device_id]
        else:
            prefix = [adb_path]
        data["checks"]["wm_size"] = safe_cmd(prefix + ["shell", "wm", "size"])
        data["checks"]["current_focus"] = safe_cmd(prefix + ["shell", "dumpsys", "window"])
    return data


def check_mlx_metal(python_path: str) -> dict[str, Any]:
    script = "\n".join(
        [
            "import platform, json",
            "payload={'platform': platform.system(), 'machine': platform.machine()}",
            "try:",
            "    import mlx.core as mx",
            "    payload['import_ok']=True",
            "    payload['default_device']=str(mx.default_device())",
            "    payload['sum']=int(mx.sum(mx.array([1,2,3])).item())",
            "    payload['metal_ok']=payload['sum']==6",
            "except Exception as exc:",
            "    payload['import_ok']=False",
            "    payload['metal_ok']=False",
            "    payload['error_type']=type(exc).__name__",
            "    payload['error']=str(exc)[:500]",
            "print(json.dumps(payload, ensure_ascii=False))",
        ]
    )
    result = safe_cmd([python_path, "-c", script], timeout=12)
    payload = {}
    if result.get("stdout"):
        try:
            payload = json.loads(str(result["stdout"]).splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            payload = {}
    return {**result, "parsed": payload}


def safe_cmd(cmd: list[str], timeout: int = 8) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": trim(result.stdout, 4000),
            "stderr": trim(result.stderr, 4000),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": type(exc).__name__,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def build_eval_command(args: argparse.Namespace, task_path: Path, trace_dir: Path) -> list[str]:
    python_bin = ROOT / ".venv" / "bin" / "python"
    python = str(python_bin if python_bin.exists() else Path(sys.executable))
    cmd = [
        python,
        "evals/run_eval.py",
        "--tasks",
        str(task_path),
        "--trace-dir",
        str(trace_dir),
        "--context-mode",
        args.context_mode,
        "--output-mode",
        args.output_mode,
        "--grounding-provider",
        args.grounding_provider,
        "--accessibility-timeout",
        str(args.accessibility_timeout),
        "--accessibility-max-marks",
        str(args.accessibility_max_marks),
        "--locateanything-context-max-chars",
        str(args.locateanything_context_max_chars),
        "--locateanything-structure-mode",
        args.locateanything_structure_mode,
        "--locateanything-max-visual-candidates",
        str(args.locateanything_max_visual_candidates),
        "--locateanything-visual-category-budget",
        str(args.locateanything_visual_category_budget),
        "--locateanything-max-structure-calls",
        str(args.locateanything_max_structure_calls),
        "--lang",
        args.lang,
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--apikey",
        args.apikey,
        "--model-timeout",
        str(args.model_timeout),
        "--model-max-retries",
        str(args.model_max_retries),
        "--thinking-mode",
        args.thinking_mode,
        "--thinking-param",
        args.thinking_param,
    ]
    if args.trace_raw_model_response:
        cmd.append("--trace-raw-model-response")
    if args.trace_request_messages:
        cmd.append("--trace-request-messages")
    if args.trace_prompt_blocks:
        cmd.append("--trace-prompt-blocks")
    if args.trace_unredacted_prompt:
        cmd.append("--trace-unredacted-prompt")
    if args.stream:
        cmd.append("--stream")
    if args.model_extra_body:
        cmd.extend(["--model-extra-body", args.model_extra_body])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.device_id:
        cmd.extend(["--device-id", args.device_id])
    if args.quiet:
        cmd.append("--quiet")
    return cmd


def build_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env["PHONE_AGENT_OUTPUT_MODE"] = args.output_mode
    env["PHONE_AGENT_MODEL_TIMEOUT"] = str(args.model_timeout)
    env["PHONE_AGENT_MODEL_MAX_RETRIES"] = str(args.model_max_retries)
    env["PHONE_AGENT_STREAM"] = "true" if args.stream else "false"
    env["PHONE_AGENT_THINKING_MODE"] = args.thinking_mode
    env["PHONE_AGENT_THINKING_PARAM"] = args.thinking_param
    env["PHONE_AGENT_TRACE_RAW_MODEL_RESPONSE"] = "true" if args.trace_raw_model_response else "false"
    env["PHONE_AGENT_TRACE_REQUEST_MESSAGES"] = "true" if args.trace_request_messages else "false"
    env["PHONE_AGENT_TRACE_PROMPT_BLOCKS"] = "true" if args.trace_prompt_blocks else "false"
    env["PHONE_AGENT_TRACE_UNREDACTED_PROMPT"] = "true" if args.trace_unredacted_prompt else "false"
    if args.model_extra_body:
        env["PHONE_AGENT_MODEL_EXTRA_BODY"] = args.model_extra_body
    env["PHONE_AGENT_CONTEXT_MODE"] = args.context_mode
    env["PHONE_AGENT_GROUNDING_PROVIDER"] = args.grounding_provider
    env["PHONE_AGENT_ACCESSIBILITY_TIMEOUT"] = str(args.accessibility_timeout)
    env["PHONE_AGENT_ACCESSIBILITY_MAX_MARKS"] = str(args.accessibility_max_marks)
    env["PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS"] = str(args.locateanything_context_max_chars)
    env["PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE"] = args.locateanything_structure_mode
    env["PHONE_AGENT_LOCATEANYTHING_MAX_VISUAL_CANDIDATES"] = str(args.locateanything_max_visual_candidates)
    env["PHONE_AGENT_LOCATEANYTHING_VISUAL_CATEGORY_BUDGET"] = str(args.locateanything_visual_category_budget)
    env["PHONE_AGENT_LOCATEANYTHING_MAX_STRUCTURE_CALLS"] = str(args.locateanything_max_structure_calls)
    if args.device_id:
        env["PHONE_AGENT_DEVICE_ID"] = args.device_id
    return env


def run_command(cmd: list[str], env: dict[str, str], *, run_dir: Path, trace_dir: Path) -> CommandResult:
    started = time.perf_counter()
    stdout_path = run_dir / "run_output.log"
    stderr_path = run_dir / "run_error.log"
    status_path = run_dir / "status.json"
    env = {**env, "PYTHONUNBUFFERED": "1"}
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            env=env,
        )
        write_runtime_status(status_path, cmd, proc.pid, None, started, trace_dir)
        try:
            while proc.poll() is None:
                write_runtime_status(status_path, cmd, proc.pid, None, started, trace_dir)
                time.sleep(2)
        except KeyboardInterrupt:
            write_runtime_status(status_path, cmd, proc.pid, None, started, trace_dir, interrupted=True)
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            raise
        returncode = proc.returncode
    write_runtime_status(status_path, cmd, None, returncode, started, trace_dir)
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    return CommandResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=time.perf_counter() - started,
    )


def write_runtime_status(
    path: Path,
    cmd: list[str],
    pid: int | None,
    returncode: int | None,
    started: float,
    trace_dir: Path,
    *,
    interrupted: bool = False,
) -> None:
    payload = {
        "state": "interrupted" if interrupted else ("running" if returncode is None else "completed"),
        "pid": pid,
        "returncode": returncode,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "command": redact_command(cmd),
        "latest_trace": latest_trace_status(trace_dir),
    }
    write_json(path, payload)


def latest_trace_status(trace_dir: Path) -> dict[str, Any]:
    files = sorted(trace_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return {}
    path = files[0]
    last_event: dict[str, Any] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
                last_event = {
                    "step_id": value.get("step_id"),
                    "node": value.get("node"),
                    "event": value.get("event"),
                    "timestamp": value.get("timestamp"),
                    "action": payload.get("action"),
                    "failure_cause": payload.get("failure_cause"),
                    "reflection_verdict": payload.get("reflection_verdict"),
                    "suggested_strategy": payload.get("suggested_strategy"),
                    "finish_validation_status": payload.get("finish_validation_status"),
                    "current_app": payload.get("current_app"),
                }
    except Exception as exc:
        last_event = {"read_error": type(exc).__name__}
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        "last_event": last_event,
    }


def parse_eval_result(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    stripped = stdout.strip()
    if stripped:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"(\{\s*\"summary\".*\})\s*$", stripped, flags=re.S)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
    return {
        "summary": {
            "total": 1,
            "success": 0,
            "success_rate": 0,
            "dry_run": False,
            "parse_error": "eval_output_not_json",
        },
        "results": [
            {
                "success": False,
                "finished": True,
                "error": "eval output was not valid JSON",
                "final_message": trim(stdout or stderr, 2000),
                "returncode": returncode,
            }
        ],
    }


def first_result_record(result: dict[str, Any]) -> dict[str, Any]:
    rows = result.get("results") if isinstance(result, dict) else None
    if isinstance(rows, list) and rows:
        return rows[0] if isinstance(rows[0], dict) else {}
    return {}


def resolve_trace_path(record: dict[str, Any], trace_dir: Path) -> Path | None:
    explicit = record.get("trace_path")
    if explicit:
        path = Path(str(explicit))
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return path
    trace_id = record.get("trace_id")
    if trace_id:
        candidate = trace_dir / f"{trace_id}.jsonl"
        if candidate.exists():
            return candidate
    files = sorted(trace_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_event: dict[str, int] = {}
    by_node: dict[str, int] = {}
    steps: dict[str, list[dict[str, Any]]] = {}
    errors = []
    grounding = []
    verifier = []
    expected_outcomes = []
    finish_validations = []
    acceptance = []
    goal_compiles = []
    fallback_chains = []
    timeline = []
    for event in events:
        name = str(event.get("event") or "unknown")
        node = str(event.get("node") or "unknown")
        step = str(event.get("step_id") if event.get("step_id") is not None else "none")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        by_event[name] = by_event.get(name, 0) + 1
        by_node[node] = by_node.get(node, 0) + 1
        compact = {
            "step_id": step,
            "node": node,
            "event": name,
            "timestamp": event.get("timestamp"),
            "payload": payload,
        }
        timeline.append(compact)
        steps.setdefault(step, []).append(compact)
        # Routine per-step reflection failures are outcomes, not errors: every
        # reflect_result carries failure_cause, so including them here makes
        # every run look error-full. Only real error events and execution /
        # infrastructure failure codes belong in the errors bucket.
        if "error" in name or payload.get("error_code") or payload.get("grounding_error_code"):
            errors.append(compact)
        elif payload.get("failure_cause") and name != "reflect_result":
            errors.append(compact)
        if payload.get("grounding_error_code") or payload.get("grounding_observation") or payload.get("mark_provider_observation"):
            grounding.append(compact)
            fallback_chains.extend(extract_fallback_chains(payload))
        if payload.get("expected_outcome") or payload.get("parse_metadata", {}).get("expected_outcome_present"):
            expected_outcomes.append(compact)
        if payload.get("finish_validation") or payload.get("finish_validation_status"):
            finish_validations.append(compact)
        # The finish gate lives in its own `acceptance` node (not reflect), and
        # some of its fail-closed events carry no error_code/failure_cause at
        # all (acceptance_no_contract, acceptance_hard_veto). Collect them by
        # node/event name so a rejected finish is never invisible.
        if node == "acceptance" or name.startswith("acceptance_"):
            acceptance.append(compact)
        # contract_adequacy.reason_codes (structural rejection codes such as
        # predicate_domain_mismatch) only ever appear on this event.
        if payload.get("contract_adequacy") or payload.get("requirement_set"):
            goal_compiles.append(compact)
        if (
            payload.get("verifier_result")
            or payload.get("verifier_status")
            or payload.get("verifier_evidence")
            or payload.get("finish_validation")
            or payload.get("finish_validation_evidence")
        ):
            verifier.append(compact)
    return {
        "event_count": len(events),
        "by_event": by_event,
        "by_node": by_node,
        "step_count": len([key for key in steps if key != "none"]),
        "errors": errors[:50],
        "grounding": grounding[:50],
        "fallback_chains": fallback_chains[:50],
        "expected_outcomes": expected_outcomes[:50],
        "finish_validations": finish_validations[:50],
        "acceptance": acceptance[:50],
        "goal_compiles": goal_compiles[:50],
        "verifier": verifier[:50],
        "timeline": timeline[:200],
    }


def extract_fallback_chains(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chains = []
    for key in ("grounding_observation", "mark_provider_observation"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        providers = value.get("providers")
        if isinstance(providers, list):
            for provider in providers:
                if not isinstance(provider, dict):
                    continue
                metadata = provider.get("metadata")
                if isinstance(metadata, dict) and isinstance(metadata.get("fallback_chain"), list):
                    chains.extend(row for row in metadata["fallback_chain"] if isinstance(row, dict))
        metadata = value.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("fallback_chain"), list):
            chains.extend(row for row in metadata["fallback_chain"] if isinstance(row, dict))
    return chains


def build_code_findings(record: dict[str, Any], trace_summary: dict[str, Any]) -> list[dict[str, Any]]:
    signals = collect_signals(record, trace_summary)
    step_count = int(trace_summary.get("step_count") or 0)
    signal_steps = collect_signal_steps(trace_summary)
    findings = []
    matched_titles = set()
    for rule in SOURCE_RULES:
        matched = sorted(rule["signals"] & signals)
        if not matched:
            continue
        confidence = grade_confidence(matched, signal_steps, step_count)
        finding = finding_from_rule(rule, matched, confidence)
        finding["signal_steps"] = {
            signal: signal_steps[signal] for signal in matched if signal in signal_steps
        }
        findings.append(finding)
        matched_titles.add(rule["title"])
    loop_signals = decision_loop_signals(record, signal_steps)
    loop_matched = sorted(DECISION_LOOP_RULE["signals"] & loop_signals)
    if loop_matched:
        confidence = grade_confidence(loop_matched, signal_steps, step_count)
        finding = finding_from_rule(DECISION_LOOP_RULE, loop_matched, confidence)
        finding["signal_steps"] = {
            signal: signal_steps[signal] for signal in loop_matched if signal in signal_steps
        }
        findings.insert(0, finding)
        matched_titles.add(DECISION_LOOP_RULE["title"])
    _cap_weak_verifier_confidence(findings)
    layer = str(record.get("error_layer") or "")
    if layer and layer in LAYER_FALLBACKS:
        fallback = LAYER_FALLBACKS[layer]
        if fallback["title"] not in matched_titles:
            findings.append(finding_from_rule(fallback, [layer], "likely"))
    if not findings:
        if record.get("success"):
            findings.append({
                "severity": "Info",
                "layer": "success",
                "title": "未发现明确源码异常",
                "confidence": "confirmed",
                "matched_signals": [],
                "files": add_line_numbers(["evals/run_eval.py", "phone_agent/agent.py", "phone_agent/graph/trace.py"]),
                "suggestion": "本次运行未暴露明确失败，建议保留 trace 作为回归基线，并继续扩大真实 App 和边界场景覆盖。",
                "verify": "将本次目标加入批量 case，后续比较 success_rate、steps、grounding latency 和 failure histogram。",
            })
        else:
            findings.append({
                "severity": "P2",
                "layer": "unknown",
                "title": "失败原因不明确，需要人工复核 trace",
                "confidence": "needs-repro",
                "matched_signals": sorted(signals),
                "files": add_line_numbers(["evals/run_eval.py", "phone_agent/agent.py", "phone_agent/graph/trace.py"]),
                "suggestion": "补充 trace 事件、stdout/stderr 和设备状态；若 trace 缺失，优先检查 trace writer 与 eval 输出。",
                "verify": "用 --dry-run 验证报告链路，再用真实设备复跑相同目标。",
            })
    return findings


def finding_from_rule(rule: dict[str, Any], matched: list[str], confidence: str) -> dict[str, Any]:
    return {
        "severity": rule["severity"],
        "layer": rule["layer"],
        "title": rule["title"],
        "confidence": confidence,
        "matched_signals": matched,
        "files": add_line_numbers(rule["files"]),
        "suggestion": rule["suggestion"],
        "verify": rule["verify"],
    }


def add_line_numbers(files: list[str]) -> list[dict[str, Any]]:
    result = []
    for rel in files:
        path = ROOT / rel
        result.append({
            "path": rel,
            "exists": path.exists(),
            "anchors": find_anchors(path) if path.exists() else [],
        })
    return result


def find_anchors(path: Path) -> list[dict[str, Any]]:
    anchors = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return anchors
    pattern = re.compile(r"^\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for index, line in enumerate(lines, start=1):
        match = pattern.match(line)
        if match:
            anchors.append({"line": index, "symbol": match.group(2)})
        if len(anchors) >= 8:
            break
    return anchors


def _collect_finish_validation_signals(
    finish_validation: Any, signals: set[str]
) -> None:
    """Extract signals from a GoalEvaluation dict (record- or trace-level).

    Beyond status/matched/missing, this inspects per-criterion reasons so a
    finish accepted via the detail-only soft-match fallback (evidence-strength
    relaxation from the mark-based tap fix) is visible to the report instead
    of being indistinguishable from a fully-evidenced finish.
    """
    if not isinstance(finish_validation, dict):
        return
    status = finish_validation.get("status")
    if status:
        signals.add(f"finish_validation_{status}")
    if finish_validation.get("needs_recompile"):
        signals.add("needs_recompile")
    for item_value in finish_validation.get("missing_terminal_evidence") or []:
        signals.add(str(item_value))
    matched = finish_validation.get("matched_terminal_evidence")
    if isinstance(matched, list) and matched:
        signals.add("matched_terminal_evidence")
    missing = finish_validation.get("missing_terminal_evidence")
    if isinstance(missing, list) and missing:
        signals.add("missing_terminal_evidence")
    soft_matched = finish_validation.get("soft_matched")
    if isinstance(soft_matched, list) and soft_matched:
        signals.add("soft_matched_criteria")
    evidence = finish_validation.get("evidence")
    if not isinstance(evidence, dict):
        return
    per_criterion = evidence.get("per_criterion")
    if not isinstance(per_criterion, dict):
        return
    for criterion_result in per_criterion.values():
        if not isinstance(criterion_result, dict):
            continue
        reason = str(criterion_result.get("reason") or "")
        if "soft_match" in reason:
            signals.add("soft_match_accepted")
        # A criterion parked on typed_fact_not_yet_collected forever usually
        # means the predicate and the fact provider disagree (unobservable
        # predicate, or the same fact in two different value domains).
        if reason == "typed_fact_not_yet_collected":
            signals.add("typed_fact_not_yet_collected")
        if criterion_result.get("override_reason") == "programmatic_contradiction":
            signals.add("programmatic_contradiction_override")


def collect_signals(record: dict[str, Any], trace_summary: dict[str, Any]) -> set[str]:
    signals = set()
    for key in ("error_layer", "error_code", "failure_cause", "grounding_failure_code", "verifier_failure_cause"):
        value = record.get(key)
        if value:
            signals.add(str(value))
    finish_status = record.get("finish_validation_status")
    if finish_status:
        signals.add(f"finish_validation_{finish_status}")
    verifier_status = record.get("verifier_status")
    if verifier_status:
        signals.add(f"verifier_{verifier_status}")
    evidence = record.get("verifier_evidence")
    if isinstance(evidence, dict):
        if evidence.get("missing_postconditions"):
            signals.add("missing_postconditions")
            for item in evidence.get("missing_postconditions") or []:
                signals.add(str(item))
        if evidence.get("dynamic_change_only"):
            signals.add("dynamic_change_only")
    _collect_finish_validation_signals(record.get("finish_validation_evidence"), signals)
    for item in trace_summary.get("errors", []):
        payload = item.get("payload") or {}
        for key in ("error_layer", "error_code", "failure_cause", "grounding_error_code", "parse_error_code", "validation_error_code"):
            value = payload.get(key)
            if value:
                signals.add(str(value))
    for item in trace_summary.get("verifier", []):
        payload = item.get("payload") or {}
        status = payload.get("verifier_status")
        if status:
            signals.add(f"verifier_{status}")
        verifier_result = payload.get("verifier_result")
        if isinstance(verifier_result, dict):
            status = verifier_result.get("status")
            if status:
                signals.add(f"verifier_{status}")
            evidence = verifier_result.get("evidence")
            if isinstance(evidence, dict):
                if evidence.get("missing_postconditions"):
                    signals.add("missing_postconditions")
                    for item_value in evidence.get("missing_postconditions") or []:
                        signals.add(str(item_value))
                if evidence.get("dynamic_change_only"):
                    signals.add("dynamic_change_only")
        evidence = payload.get("verifier_evidence")
        if isinstance(evidence, dict):
            if evidence.get("missing_postconditions"):
                signals.add("missing_postconditions")
                for item_value in evidence.get("missing_postconditions") or []:
                    signals.add(str(item_value))
            if evidence.get("dynamic_change_only"):
                signals.add("dynamic_change_only")
        finish_validation = payload.get("finish_validation") or payload.get("finish_validation_evidence")
        _collect_finish_validation_signals(finish_validation, signals)
    # Acceptance-node events are the finish gate's own record. Several of them
    # (acceptance_no_contract, acceptance_hard_veto, pure_evaluation_degraded)
    # are fail-closed rejections that carry no error_code, so signal on the
    # event name itself rather than waiting for an error field.
    for item in trace_summary.get("acceptance", []):
        name = str(item.get("event") or "")
        # acceptance_result fires on every acceptance run including a clean pass,
        # so it is not a problem signal — only the fail-closed variants are.
        if name in {
            "acceptance_no_contract",
            "acceptance_hard_veto",
            "acceptance_error",
            "pure_evaluation_degraded",
        }:
            signals.add(name)
        payload = item.get("payload") or {}
        if payload.get("contradicted_criteria"):
            signals.add("acceptance_hard_veto")
            for item_value in payload.get("contradicted_criteria") or []:
                signals.add(str(item_value))
        _collect_finish_validation_signals(payload.get("finish_validation"), signals)
    # Adequacy reason codes are compile-time proof that a contract could never
    # be satisfied (e.g. predicate_domain_mismatch). They exist only on the
    # goal_compile_result event, never on result.json.
    for item in trace_summary.get("goal_compiles", []):
        payload = item.get("payload") or {}
        adequacy = payload.get("contract_adequacy")
        if not isinstance(adequacy, dict):
            continue
        status = adequacy.get("status")
        if status and status != "adequate":
            signals.add(f"contract_adequacy_{status}")
        for code in adequacy.get("reason_codes") or []:
            signals.add(str(code))
    for item in trace_summary.get("grounding", []):
        payload = item.get("payload") or {}
        obs = payload.get("grounding_observation")
        if isinstance(obs, dict):
            for key in ("failure_code", "status", "provider"):
                value = obs.get(key)
                if value:
                    signals.add(str(value))
            metadata = obs.get("metadata")
            if isinstance(metadata, dict):
                chain = metadata.get("fallback_chain")
                if isinstance(chain, list):
                    for row in chain:
                        if isinstance(row, dict) and row.get("failure_code"):
                            signals.add(str(row["failure_code"]))
    return signals


def collect_signal_steps(trace_summary: dict[str, Any]) -> dict[str, list[str]]:
    """Map each decision-loop signal to the step ids where it was observed."""

    mapping: dict[str, set[str]] = {}
    total_steps = max(int(trace_summary.get("step_count") or 0), 1)

    def _add(signal: str, step: Any) -> None:
        mapping.setdefault(signal, set()).add(str(step if step is not None else "none"))

    for compact in trace_summary.get("timeline", []):
        step = compact.get("step_id")
        payload = compact.get("payload") or {}
        if compact.get("event") != "reflect_result":
            continue
        if payload.get("repeated_action_detected"):
            _add("repeated_action_detected", step)
        repeat_count = payload.get("repeat_count")
        if isinstance(repeat_count, int) and repeat_count >= 3:
            _add("avoid_repeating_ignored", step)
        liveness = payload.get("trajectory_liveness")
        state = liveness.get("state") if isinstance(liveness, dict) else liveness
        if state == "stuck":
            _add("liveness_stuck", step)
    return {signal: sorted(steps) for signal, steps in mapping.items()} if total_steps else {}


def decision_loop_signals(
    record: dict[str, Any], signal_steps: dict[str, list[str]]
) -> set[str]:
    """Decision-loop signals from the run record and per-step reflect payloads."""

    signals = set(signal_steps)
    steps = record.get("steps")
    max_steps = record.get("max_steps")
    if (
        isinstance(steps, int)
        and isinstance(max_steps, int)
        and max_steps > 0
        and steps >= max_steps
        and not record.get("acceptance_round_count")
        and record.get("finish_validation_status") is None
    ):
        signals.add("budget_exhausted_no_finish")
    repeated_failures = record.get("repeated_failure_count")
    if isinstance(repeated_failures, int) and repeated_failures >= 3:
        signals.add("repeated_failure_count")
    return signals


def grade_confidence(matched: list[str], signal_steps: dict[str, list[str]], step_count: int) -> str:
    """Grade by how much of the run the matched signals actually cover."""

    if step_count <= 0:
        return "needs-repro"
    covered = set()
    for signal in matched:
        covered.update(signal_steps.get(signal) or [])
    if not covered:
        # Record-level signals (budget exhaustion, counters) and reflect
        # verifier aggregates describe the whole run rather than one step, so
        # absence of per-step evidence is neutral, not weak.
        return "likely"
    coverage = len(covered) / step_count
    if coverage >= 0.5:
        return "confirmed"
    if coverage >= 0.2:
        return "likely"
    return "needs-repro"


_VERIFIER_WEAK_SIGNALS = {"dynamic_change_only", "missing_postconditions", "verifier_unknown"}


def _cap_weak_verifier_confidence(findings: list[dict[str, Any]]) -> None:
    """A reflection finding built only from sporadic weak verifier signals is
    never 'confirmed' — those fire on ordinary dynamic screens too."""
    for finding in findings:
        if finding.get("layer") != "reflection":
            continue
        matched = set(finding.get("matched_signals") or [])
        if matched and matched <= _VERIFIER_WEAK_SIGNALS and finding.get("confidence") == "confirmed":
            finding["confidence"] = "likely"


def build_recommendations(findings: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations = []
    for index, finding in enumerate(findings, start=1):
        recommendations.append({
            "id": f"R{index:02d}",
            "priority": finding["severity"],
            "confidence": finding["confidence"],
            "title": finding["title"],
            "target_files": finding["files"],
            "recommendation": finding["suggestion"],
            "verification": finding["verify"],
            "source": {
                "error_layer": record.get("error_layer"),
                "error_code": record.get("error_code"),
                "failure_cause": record.get("failure_cause"),
                "grounding_failure_code": record.get("grounding_failure_code"),
                "verifier_status": record.get("verifier_status"),
                "verifier_failure_cause": record.get("verifier_failure_cause"),
                "finish_validation_status": record.get("finish_validation_status"),
            },
        })
    return recommendations


LAYER_LABELS = {
    "grounding": "视觉定位（Grounding）",
    "parse": "模型输出解析",
    "validation": "动作校验",
    "safety": "安全/人工确认",
    "capability": "能力闸门",
    "execution": "设备执行",
    "reflection": "单步反思",
    "acceptance": "验收 / Finish Gate",
    "goal": "目标契约编译",
    "checkpoint": "检查点恢复",
    "context": "上下文管理",
    "decision": "决策层循环",
    "success": "无异常",
    "unknown": "未定位",
}


def _first_anchor(files: list[dict[str, Any]]) -> str:
    """Pick a human-readable `path:line` for the first file with anchors."""
    for file in files:
        anchors = file.get("anchors") or []
        if anchors:
            return f"{file.get('path')}:{anchors[0].get('line')}"
    for file in files:
        if file.get("path"):
            return str(file.get("path"))
    return ""


def build_root_causes(
    verdict: str,
    record: dict[str, Any],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn raw findings into a plain-language root-cause narrative.

    Each entry answers three questions in Chinese: what happened (what),
    why it happened (why, with source location), and what to do next
    (action). This is the primary reading path of the report — findings
    remain available as structured detail underneath.
    """
    causes = []
    if verdict == "success":
        causes.append({
            "what": "任务按预期完成，运行未暴露明确失败。",
            "why": "各层（定位、执行、反思、目标验证）均未产生错误信号。",
            "action": "将本次 trace 保留为回归基线，继续扩大真实 App 与边界场景覆盖。",
            "severity": "Info",
            "layer": "success",
            "source": "",
        })
        return causes

    if verdict == "blocked" and record.get("hitl_count"):
        causes.append({
            "what": f"任务在人工确认/接管处暂停（HITL 触发 {record.get('hitl_count')} 次）。",
            "why": "安全策略（SafetyPolicyRegistry）将某个动作分类为需要人工介入，例如支付、登录或验证码。",
            "action": "确认是否为预期的敏感动作；若是误触发，检查 config/policy.py 的词汇表与 Safety Gate 分类。",
            "severity": "P0",
            "layer": "safety",
            "source": "phone_agent/config/policy.py",
        })

    for finding in findings:
        if finding.get("layer") in {"success", "unknown"}:
            continue
        layer_label = LAYER_LABELS.get(finding.get("layer", ""), finding.get("layer", ""))
        signals = finding.get("matched_signals") or []
        signal_text = "、".join(f"`{s}`" for s in signals[:4]) or "无"
        source = _first_anchor(finding.get("files") or [])
        causes.append({
            "what": f"{layer_label}层出现异常：{finding.get('title')}。",
            "why": f"捕获信号 {signal_text}。{finding.get('suggestion', '')}",
            "action": finding.get("verify", ""),
            "severity": finding.get("severity", "P2"),
            "layer": finding.get("layer", ""),
            "source": source,
            "confidence": finding.get("confidence", ""),
        })

    if not causes:
        causes.append({
            "what": "任务失败但错误信号未匹配到任何已知模式。",
            "why": f"error={record.get('error') or '-'}；failure_cause={record.get('failure_cause') or '-'}；可能是新故障模式或 trace 不完整。",
            "action": "人工复核 trace.jsonl 与 run_output.log；若确认是新故障模式，应把对应信号补充进 SOURCE_RULES。",
            "severity": "P2",
            "layer": "unknown",
            "source": "",
        })
    return causes


def build_summary(
    *,
    args: argparse.Namespace,
    run_id: str,
    run_dir: Path,
    command: list[str],
    command_result: CommandResult,
    result: dict[str, Any],
    record: dict[str, Any],
    trace_summary: dict[str, Any],
    code_findings: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
) -> dict[str, Any]:
    verdict = classify_verdict(record, command_result.returncode)
    root_causes = build_root_causes(verdict, record, code_findings)
    return {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target": args.target,
        "verdict": verdict,
        "root_causes": root_causes,
        "run_dir": str(run_dir),
        "command": redact_command(command),
        "dangerous_debug": {
            "trace_raw_model_response": bool(args.trace_raw_model_response),
            "trace_request_messages": bool(args.trace_request_messages),
            "trace_prompt_blocks": bool(args.trace_prompt_blocks),
            "trace_unredacted_prompt": bool(args.trace_unredacted_prompt),
            "warning": "Prompt/request debug may include sensitive local UI text. Image payloads are stripped from prompt debug traces by default.",
        },
        "preflight": preflight_summary(run_dir / "preflight.json"),
        "returncode": command_result.returncode,
        "duration_sec": round(command_result.duration, 3),
        "result_summary": result.get("summary", {}),
        "result": record,
        "trace_summary": trace_summary,
        "code_findings": code_findings,
        "recommendations": recommendations,
        "artifacts": {
            "task": str(run_dir / "task.json"),
            "preflight": str(run_dir / "preflight.json"),
            "result": str(run_dir / "result.json"),
            "trace": str(run_dir / "trace.jsonl"),
            "trace_summary": str(run_dir / "trace_summary.json"),
            "code_findings": str(run_dir / "code_findings.json"),
            "recommendations": str(run_dir / "recommendations.json"),
            "report": str(run_dir / "report.html"),
        },
    }


def classify_verdict(record: dict[str, Any], returncode: int) -> str:
    if record.get("success"):
        return "success"
    if record.get("hitl_count"):
        return "blocked"
    if returncode != 0:
        return "failed"
    if record.get("error") or record.get("failure_cause") or record.get("error_code"):
        return "failed"
    return "uncertain"


def preflight_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    mlx = checks.get("mlx_metal") if isinstance(checks.get("mlx_metal"), dict) else None
    if isinstance(mlx, dict):
        parsed = mlx.get("parsed") if isinstance(mlx.get("parsed"), dict) else {}
        payload["mlx_metal_ok"] = bool(parsed.get("metal_ok"))
        payload["mlx_metal_error"] = parsed.get("error")
    return payload


def redact_command(cmd: list[str]) -> list[str]:
    redacted = []
    skip_next = False
    for item in cmd:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--apikey"}:
            skip_next = True
    return redacted


def render_html_report(summary: dict[str, Any], trace_events: list[dict[str, Any]]) -> str:
    payload = json.dumps({"summary": summary, "trace_events": trace_events}, ensure_ascii=False)
    escaped_payload = (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("</", "<\\/")
    )
    return HTML_TEMPLATE.replace("__REPORT_DATA__", escaped_payload)


def trim(value: str, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[:limit] + "\n...<truncated>"


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <base target="_blank">
  <title>Phone Agent Live Diagnosis</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');
    :root {
      --primary: #1E40AF;
      --primary-soft: #DBEAFE;
      --accent: #F59E0B;
      --bg: #F1F5F9;
      --panel: #FFFFFF;
      --ink: #0F172A;
      --muted: #64748B;
      --line: #E2E8F0;
      --success: #15803D;
      --failed: #B91C1C;
      --blocked: #B45309;
      --radius: 10px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Fira Sans", system-ui, sans-serif;
      letter-spacing: 0;
    }
    header {
      background: linear-gradient(135deg, #0F172A 0%, #1E293B 60%, #1E3A8A 100%);
      color: white;
      padding: 26px 32px 22px;
      border-bottom: 4px solid var(--accent);
    }
    .header-row {
      display: flex; align-items: flex-start; justify-content: space-between;
      gap: 16px; flex-wrap: wrap; max-width: 1400px;
    }
    h1 { margin: 0 0 10px; font-size: 24px; line-height: 1.2; }
    h2 { margin: 0 0 12px; font-size: 17px; display: flex; align-items: center; gap: 8px; }
    h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--accent); }
    h3 { margin: 0 0 8px; font-size: 14px; }
    .mono, code, pre { font-family: "Fira Code", ui-monospace, monospace; }
    .wrap { word-break: break-all; overflow-wrap: break-word; }
    .subtitle { color: #CBD5E1; max-width: 1000px; font-size: 14px; line-height: 1.5; }
    .verdict-chip {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 8px 16px; border-radius: 999px;
      font: 700 14px "Fira Code"; white-space: nowrap;
      background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.25);
    }
    .verdict-chip.success { background: rgba(21,128,61,.35); border-color: #4ADE80; color: #BBF7D0; }
    .verdict-chip.failed { background: rgba(185,28,28,.35); border-color: #F87171; color: #FECACA; }
    .verdict-chip.blocked { background: rgba(180,83,9,.4); border-color: #FCD34D; color: #FDE68A; }
    .shell { padding: 20px 32px 40px; max-width: 1400px; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px 16px;
      box-shadow: 0 1px 3px rgba(15, 23, 42, .06);
    }
    .kpi-label { color: var(--muted); font-size: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: .04em; }
    .kpi-value { font-size: 18px; font-weight: 700; }
    .badge {
      display: inline-flex; align-items: center; min-height: 22px;
      border-radius: 999px; padding: 2px 9px;
      font: 600 12px "Fira Code";
      background: #E0E7FF; color: var(--primary); border: 1px solid #BFDBFE;
    }
    .badge.success { background: #DCFCE7; color: var(--success); border-color: #86EFAC; }
    .badge.failed { background: #FEE2E2; color: var(--failed); border-color: #FCA5A5; }
    .badge.blocked { background: #FEF3C7; color: var(--blocked); border-color: #FCD34D; }
    .alert {
      border-radius: 8px; padding: 10px 14px; margin: 0 0 12px;
      font-weight: 700; word-break: break-all; overflow-wrap: break-word;
    }
    .alert.danger { background: #FEE2E2; color: var(--failed); border: 1px solid #FCA5A5; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 14px 0; }
    button, input, select {
      border: 1px solid var(--line); background: white; color: var(--ink);
      border-radius: 8px; padding: 8px 12px; font: 500 13px "Fira Sans";
    }
    button { cursor: pointer; transition: all .15s; }
    button:hover { border-color: var(--primary); color: var(--primary); }
    button.active { background: var(--primary); border-color: var(--primary); color: white; }
    input { min-width: 260px; }
    .tab { display: none; }
    .tab.active { display: block; animation: fadeIn .18s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
    .grid-2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 10px; vertical-align: top; text-align: left; }
    th { color: #334155; background: #F8FAFC; position: sticky; top: 0; z-index: 1; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
    tr:last-child td { border-bottom: none; }
    /* Root cause cards */
    .cause-card {
      background: var(--panel); border: 1px solid var(--line);
      border-left: 5px solid var(--muted);
      border-radius: var(--radius); padding: 16px 18px; margin-bottom: 12px;
      box-shadow: 0 1px 3px rgba(15,23,42,.06);
    }
    .cause-card.sev-P0 { border-left-color: var(--failed); }
    .cause-card.sev-P1 { border-left-color: var(--accent); }
    .cause-card.sev-P2 { border-left-color: var(--primary); }
    .cause-card.sev-Info { border-left-color: var(--success); }
    .cause-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
    .cause-what { font-size: 15px; font-weight: 700; line-height: 1.45; margin: 6px 0 10px; }
    .cause-block { display: grid; grid-template-columns: 72px minmax(0,1fr); gap: 6px 12px; font-size: 13.5px; line-height: 1.6; }
    .cause-label { color: var(--muted); font-weight: 600; white-space: nowrap; }
    .cause-src { margin-top: 10px; padding: 8px 10px; background: #F8FAFC; border: 1px dashed var(--line); border-radius: 6px; font-size: 12.5px; }
    /* Timeline */
    .timeline { display: grid; gap: 8px; }
    .event {
      border-left: 4px solid var(--primary); padding: 10px 14px;
      background: white; border-radius: 8px;
      border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line);
    }
    .event.is-error { border-left-color: var(--failed); background: #FFF7F7; }
    .event-head { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: var(--primary); font-weight: 700; }
    pre {
      margin: 8px 0 0; padding: 12px; background: #0B1220; color: #E2E8F0;
      border-radius: 8px; overflow: auto; max-height: 360px; font-size: 12px;
      white-space: pre-wrap; word-break: break-all;
    }
    .severity-P0 { color: #B91C1C; font-weight: 700; }
    .severity-P1 { color: #B45309; font-weight: 700; }
    .severity-P2 { color: #1E40AF; font-weight: 700; }
    .severity-Info { color: #15803D; font-weight: 700; }
    .muted { color: var(--muted); }
    a { color: var(--primary); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 1100px) {
      .grid-2 { grid-template-columns: 1fr; }
    }
    @media (max-width: 520px) {
      header, .shell { padding-left: 16px; padding-right: 16px; }
      input { min-width: 100%; }
    }
  </style>
</head>
<body>
<script id="report-data" type="application/json">__REPORT_DATA__</script>
<header>
  <div class="header-row">
    <div>
      <h1>Phone Agent 实机诊断报告</h1>
      <div class="subtitle wrap" id="subtitle"></div>
    </div>
    <div id="verdictChip"></div>
  </div>
</header>
<main class="shell">
  <section class="kpis" id="kpis"></section>
  <nav class="toolbar" id="tabs"></nav>
  <section class="toolbar">
    <input id="search" placeholder="搜索 step / event / file / error code">
    <select id="layerFilter"><option value="">全部层级</option></select>
    <select id="severityFilter"><option value="">全部优先级</option></select>
  </section>
  <section id="overview" class="tab active"></section>
  <section id="timeline" class="tab"></section>
  <section id="source" class="tab"></section>
  <section id="recommendations" class="tab"></section>
  <section id="raw" class="tab"></section>
</main>
<script>
const data = JSON.parse(document.getElementById('report-data').textContent);
const summary = data.summary;
const traceEvents = data.trace_events || [];
const state = { tab: 'overview', query: '', layer: '', severity: '' };
const tabs = [
  ['overview', '概览与根因'],
  ['timeline', '时间线'],
  ['source', '源码归因'],
  ['recommendations', '修改建议'],
  ['raw', '原始证据'],
];
const VERDICT_LABEL = { success: '成功', failed: '失败', blocked: '人工阻断', uncertain: '不确定' };
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function json(v) { return esc(JSON.stringify(v, null, 2)); }
function badge(text, cls='') { return `<span class="badge ${cls}">${esc(text)}</span>`; }
function verdictClass(v) { return v === 'success' ? 'success' : v === 'blocked' ? 'blocked' : v === 'failed' ? 'failed' : ''; }
function render() {
  document.getElementById('subtitle').innerHTML =
    `<strong>${esc(summary.target)}</strong><br><span class="mono wrap">${esc(summary.run_id)} · ${esc(summary.created_at || '')}</span>`;
  document.getElementById('verdictChip').innerHTML =
    `<span class="verdict-chip ${verdictClass(summary.verdict)}">${esc(VERDICT_LABEL[summary.verdict] || summary.verdict)}</span>`;
  renderKpis();
  renderTabs();
  renderFilters();
  renderOverview();
  renderTimeline();
  renderSource();
  renderRecommendations();
  renderRaw();
}
function renderKpis() {
  const r = summary.result || {};
  const rs = summary.result_summary || {};
  const items = [
    ['成功率', esc(Math.round((rs.success_rate ?? (r.success ? 1 : 0)) * 100)) + '%'],
    ['步数', esc(r.steps ?? rs.avg_steps ?? '-')],
    ['耗时', esc((r.duration ?? summary.duration_sec ?? 0).toFixed ? (r.duration ?? summary.duration_sec).toFixed(2) + 's' : '-')],
    ['错误层', badge(r.error_layer || '无')],
    ['错误码', badge(r.error_code || '无')],
    ['Grounding', badge(r.grounding_provider || 'unknown')],
    ['Verifier', badge(r.verifier_status || 'unknown')],
  ];
  document.getElementById('kpis').innerHTML = items.map(([k, v]) =>
    `<div class="card"><div class="kpi-label">${k}</div><div class="kpi-value wrap">${v}</div></div>`).join('');
}
function renderTabs() {
  document.getElementById('tabs').innerHTML = tabs.map(([id, label]) =>
    `<button class="${state.tab === id ? 'active' : ''}" onclick="state.tab='${id}'; selectTab()">${label}</button>`).join('');
}
function selectTab() {
  for (const [id] of tabs) document.getElementById(id).classList.toggle('active', state.tab === id);
  renderTabs();
}
function renderFilters() {
  const layers = [...new Set((summary.code_findings || []).map(f => f.layer).filter(Boolean))];
  const severities = [...new Set((summary.code_findings || []).map(f => f.severity).filter(Boolean))];
  const layer = document.getElementById('layerFilter');
  const severity = document.getElementById('severityFilter');
  if (layer.options.length <= 1) layer.innerHTML += layers.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (severity.options.length <= 1) severity.innerHTML += severities.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
}
function matches(text) { return !state.query || String(text).toLowerCase().includes(state.query.toLowerCase()); }
function row(k, v) { return `<tr><td class="mono">${esc(k)}</td><td class="wrap">${esc(v ?? '-')}</td></tr>`; }

function renderRootCauses() {
  const causes = (summary.root_causes || []).filter(c =>
    matches(JSON.stringify(c)) && (!state.layer || c.layer === state.layer) && (!state.severity || c.severity === state.severity));
  if (!causes.length) return '<div class="card">当前筛选条件下没有根因条目。</div>';
  return causes.map(c => `
    <div class="cause-card sev-${esc(c.severity)}">
      <div class="cause-head">
        ${badge(c.severity || 'P2')}
        ${badge(c.layer || '')}
        ${c.confidence ? `<span class="muted mono">${esc(c.confidence)}</span>` : ''}
      </div>
      <div class="cause-what">${esc(c.what)}</div>
      <div class="cause-block">
        <span class="cause-label">为什么</span><span class="wrap">${esc(c.why)}</span>
        <span class="cause-label">怎么办</span><span class="wrap">${esc(c.action)}</span>
      </div>
      ${c.source ? `<div class="cause-src mono wrap">📍 ${esc(c.source)}</div>` : ''}
    </div>`).join('');
}

function renderOverview() {
  const r = summary.result || {};
  const artifacts = summary.artifacts || {};
  const preflight = summary.preflight || {};
  const debug = summary.dangerous_debug || {};
  const latestExpected = (summary.trace_summary?.expected_outcomes || []).slice(-1)[0]?.payload?.expected_outcome || r.expected_outcome || {};
  const latestVerifier = r.verifier_evidence || (summary.trace_summary?.verifier || []).slice(-1)[0]?.payload?.verifier_evidence || {};
  const fallbackRows = summary.trace_summary?.fallback_chains || [];
  const finishEvidence = r.finish_validation_evidence || {};
  const softMatched = finishEvidence.soft_matched || [];
  // The finish gate is the `acceptance` node, not reflect. Prefer its own
  // trace event, falling back to the result record.
  const acceptanceRows = summary.trace_summary?.acceptance || [];
  const lastAcceptance = acceptanceRows.slice(-1)[0] || {};
  const finishFromTrace = (acceptanceRows.map(e => (e.payload || {}).finish_validation).filter(Boolean).slice(-1)[0]) || {};
  const finishGate = Object.keys(finishEvidence).length ? finishEvidence : finishFromTrace;
  const perCriterion = (finishGate.evidence || {}).per_criterion || {};
  const lastGoalCompile = (summary.trace_summary?.goal_compiles || []).slice(-1)[0] || {};
  const adequacy = (lastGoalCompile.payload || {}).contract_adequacy || {};
  document.getElementById('overview').innerHTML = `
    <h2 style="margin-bottom:12px">根因分析</h2>
    <div style="margin-bottom:18px">${renderRootCauses()}</div>
    <div class="grid-2">
      <div class="card">
        <h2>运行结论</h2>
        ${debug.trace_unredacted_prompt ? '<div class="alert danger">危险调试已启用：trace-unredacted-prompt 会记录未脱敏 prompt 文本；截图 image_url 已从 prompt debug 中剥离。</div>' : ''}
        <table>
          <tr><th>字段</th><th>值</th></tr>
          ${row('目标', summary.target)}
          ${row('结论', VERDICT_LABEL[summary.verdict] || summary.verdict)}
          ${row('错误', r.error)}
          ${row('失败原因', r.failure_cause)}
          ${row('错误码', r.error_code)}
          ${row('Finish Validation', r.finish_validation_status)}
          ${row('软匹配标准', softMatched.length ? JSON.stringify(softMatched) : '无')}
          ${row('Trace ID', r.trace_id)}
          ${row('命令', (summary.command || []).join(' '))}
        </table>
      </div>
      <div class="card">
        <h2>产物</h2>
        <table><tr><th>名称</th><th>路径</th></tr>
        ${Object.entries(artifacts).map(([k,v]) => row(k, v)).join('')}
        </table>
      </div>
      <div class="card">
        <h2>环境预检</h2>
        <table>
          ${row('Device ID', preflight.device_id)}
          ${row('ADB', preflight.adb_path)}
          ${row('Grounding Provider', preflight.grounding_provider)}
          ${row('MLX Metal OK', preflight.mlx_metal_ok)}
          ${row('MLX Metal Error', preflight.mlx_metal_error)}
        </table>
      </div>
      <div class="card">
        <h2>后置条件验证（单步 reflect）</h2>
        <table>
          ${row('ExpectedOutcome', JSON.stringify(latestExpected))}
          ${row('Matched', JSON.stringify(latestVerifier.matched_postconditions || []))}
          ${row('Missing', JSON.stringify(latestVerifier.missing_postconditions || []))}
          ${row('Dynamic Only', latestVerifier.dynamic_change_only)}
        </table>
      </div>
      <div class="card">
        <h2>Finish Gate（acceptance 节点）</h2>
        ${adequacy.status && adequacy.status !== 'adequate' ? `<div class="alert danger">契约 adequacy = ${esc(adequacy.status)}：${esc((adequacy.reason_codes || []).join(', ') || '无 reason_code')}。structural 拒绝（predicate_unobservable / predicate_domain_mismatch / task_binding_mismatch / required_criteria_missing）意味着该契约在编译期即不可满足，应修 predicate 绑定或 fact provider，不要放宽 gate。</div>` : ''}
        ${lastAcceptance.event === 'acceptance_no_contract' ? '<div class="alert danger">acceptance_no_contract：没有已编译契约就进入验收，已 fail-closed 拒绝——根因在 goal 层。</div>' : ''}
        ${lastAcceptance.event === 'acceptance_hard_veto' ? '<div class="alert danger">acceptance_hard_veto：程序信号直接否决了模型的完成声明，应信程序侧。</div>' : ''}
        <table>
          ${row('验收状态', finishGate.status || r.finish_validation_status || '未触发（模型未声明完成）')}
          ${row('最后 acceptance 事件', lastAcceptance.event)}
          ${row('契约 adequacy', adequacy.status ? `${adequacy.status} ${JSON.stringify(adequacy.reason_codes || [])}` : '-')}
          ${row('已满足标准', JSON.stringify(finishGate.matched_terminal_evidence || []))}
          ${row('未满足标准', JSON.stringify(finishGate.missing_terminal_evidence || []))}
          ${row('软匹配标准', (finishGate.soft_matched || softMatched).length ? JSON.stringify(finishGate.soft_matched || softMatched) : '无')}
        </table>
        <table><tr><th>标准</th><th>判定</th><th>原因</th></tr>
          ${Object.keys(perCriterion).length ? Object.entries(perCriterion).map(([name, res]) => `<tr>
            <td class="mono wrap">${esc(name)}</td>
            <td class="mono">${badge(esc((res || {}).status || '-'), (res || {}).status === 'matched' ? '' : 'failed')}</td>
            <td class="mono wrap">${esc((res || {}).override_reason ? `${(res || {}).reason} (override: ${(res || {}).override_reason})` : ((res || {}).reason || '-'))}</td>
          </tr>`).join('') : '<tr><td colspan="3">无 per_criterion 记录（finish gate 未触发或契约未编译）</td></tr>'}
        </table>
      </div>
      <div class="card">
        <h2>Grounding Fallback Chain</h2>
        <table><tr><th>Provider</th><th>Status</th><th>Failure</th><th>Marks</th></tr>
          ${fallbackRows.length ? fallbackRows.map(item => `<tr>
            <td class="mono">${esc(item.provider || '-')}</td>
            <td class="mono">${esc(item.status || item.success || '-')}</td>
            <td class="mono wrap">${esc(item.failure_code || item.message || '-')}</td>
            <td class="mono">${esc(item.mark_count ?? item.candidate_count ?? '-')}</td>
          </tr>`).join('') : '<tr><td colspan="4">无 fallback_chain 记录</td></tr>'}
        </table>
      </div>
    </div>`;
}
function renderTimeline() {
  const rows = (summary.trace_summary?.timeline || []).filter(e => matches(`${e.step_id} ${e.node} ${e.event} ${JSON.stringify(e.payload || {})}`));
  const isErr = e => e.event.includes('error') || (e.payload || {}).error_code || (e.payload || {}).failure_cause;
  document.getElementById('timeline').innerHTML = `<div class="timeline">${rows.map(e => `
    <article class="event ${isErr(e) ? 'is-error' : ''}">
      <div class="event-head">
        <div>${badge('step ' + e.step_id)} ${badge(e.node)} ${badge(e.event, isErr(e) ? 'failed' : '')}</div>
        <span class="mono muted">${esc(e.timestamp || '')}</span>
      </div>
      <details><summary>payload</summary><pre>${json(e.payload || {})}</pre></details>
    </article>`).join('') || '<div class="card">没有匹配的事件。</div>'}
</div>`;
}
function renderSource() {
  const rows = filteredFindings();
  document.getElementById('source').innerHTML = `<div class="card"><h2>源码归因</h2><table>
    <tr><th>优先级</th><th>层级</th><th>现象</th><th>信号</th><th>源码位置</th><th>建议</th></tr>
    ${rows.map(f => `<tr>
      <td class="severity-${esc(f.severity)}">${esc(f.severity)}</td>
      <td class="mono">${esc(f.layer)}</td>
      <td>${esc(f.title)}<br><span class="muted mono">${esc(f.confidence)}</span></td>
      <td class="mono wrap">${esc((f.matched_signals || []).join(', ') || '-')}</td>
      <td>${renderFiles(f.files || [])}</td>
      <td class="wrap">${esc(f.suggestion)}<br><span class="muted">${esc(f.verify)}</span></td>
    </tr>`).join('')}
  </table></div>`;
}
function renderFiles(files) {
  return files.map(file => {
    const anchors = (file.anchors || []).slice(0, 3).map(a => `${a.symbol}:${a.line}`).join(', ');
    return `<div class="mono wrap">${esc(file.path)}${anchors ? '<br><span class="muted">' + esc(anchors) + '</span>' : ''}</div>`;
  }).join('');
}
function filteredFindings() {
  return (summary.code_findings || []).filter(f => {
    const text = JSON.stringify(f);
    return (!state.layer || f.layer === state.layer) && (!state.severity || f.severity === state.severity) && matches(text);
  });
}
function renderRecommendations() {
  const rows = (summary.recommendations || []).filter(r => matches(JSON.stringify(r)) && (!state.severity || r.priority === state.severity));
  document.getElementById('recommendations').innerHTML = `<div class="card"><h2>修改建议</h2><table>
    <tr><th>ID</th><th>优先级</th><th>建议</th><th>目标文件</th><th>验证方式</th></tr>
    ${rows.map(r => `<tr>
      <td class="mono">${esc(r.id)}</td>
      <td class="severity-${esc(r.priority)}">${esc(r.priority)}</td>
      <td class="wrap"><strong>${esc(r.title)}</strong><br>${esc(r.recommendation)}<br><span class="muted mono">${esc(r.confidence)}</span></td>
      <td>${renderFiles(r.target_files || [])}</td>
      <td class="wrap">${esc(r.verification)}</td>
    </tr>`).join('')}
  </table></div>`;
}
function renderRaw() {
  document.getElementById('raw').innerHTML = `<div class="grid-2">
    <div class="card"><h2>Summary JSON</h2><pre>${json(summary)}</pre></div>
    <div class="card"><h2>Trace Events</h2><pre>${json(traceEvents.slice(0, 200))}</pre></div>
  </div>`;
}
document.getElementById('search').addEventListener('input', e => { state.query = e.target.value; renderOverview(); renderTimeline(); renderSource(); renderRecommendations(); });
document.getElementById('layerFilter').addEventListener('change', e => { state.layer = e.target.value; renderOverview(); renderSource(); });
document.getElementById('severityFilter').addEventListener('change', e => { state.severity = e.target.value; renderOverview(); renderSource(); renderRecommendations(); });
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
