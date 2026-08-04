"""Acceptance node: decide whether the task itself is complete.

Split out of Reflect, which was judging two different questions with two
different time scales in one LLM call: "did this tap work?" (every step) and
"is the whole task done?" (only on a finish claim). Interleaving them made
evidence-gathering and judging feed each other — the gate evaluated once,
returned `unknown`, ran the model to collect evidence, then evaluated again.

Authority here is explicit and ordered. Later layers cannot overturn earlier
ones:

1. **Hard veto** — collected facts contradict a required criterion. Terminal;
   the model cannot argue a task complete against device truth.
2. **Hard confirm** — facts positively satisfy a criterion. No model
   testimony is requested for these, and its absence is not held against them.
3. **Semantic judgement** — only for criteria whose expectation is raw screen
   text, where deciding whether a label *means* the goal was met genuinely
   needs a model. This is a first-class path, not an exception.

Fail-closed throughout: `unknown` never becomes success.
"""

import json
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.config.policy import (
    CONTINUATION_GRANT_STEPS,
    CONTINUATION_MAX_GRANTS,
    DEFAULT_VERIFICATION_POLICY,
    absolute_max_steps,
)
from phone_agent.device_factory import ObservationCaptureError
from phone_agent.graph.context import (
    continuation_credential,
    get_context_mode,
    latched_criterion_count,
    sanitize_context_payload,
)
from phone_agent.graph.device_observation import capture_device_observation
from phone_agent.graph.fact_providers import (
    collect_goal_facts,
)
from phone_agent.graph.goal import (
    build_goal_prompt_block,
    ensure_goal_contract,
    goal_runtime_reference,
    goal_trace_payload,
)
from phone_agent.graph.goal_evaluator import (
    GoalEvaluation,
    _is_self_observable,
    _normalize_criterion_name,
    evaluate_finish_claim,
    pure_goal_evaluator,
)
from phone_agent.graph.goal_evidence import (
    append_evaluation_entries,
    target_app_entered,
    unattested_raw_text_bindings,
)
from phone_agent.graph.nodes.observation_capture import (
    build_after_observation,
    collect_device_verifier_signals,
    sanitize_verifier_observation_payload,
    screenshot_failure_update,
    state_before_observation_payload,
    verifier_observation_payload,
)
from phone_agent.graph.screenshot_status import screenshot_failure_code
from phone_agent.graph.trace import emit_trace
from phone_agent.graph.verifier import verify_action_outcome
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState

ACCEPTANCE_SYSTEM_PROMPT_CN = """你是一个手机自动化任务的终局验收员。屏幕上的动作已经执行完毕，现在要判断**整个任务**是否真的完成了。

你必须只输出一个 JSON 对象，不要 Markdown、XML、函数调用或多余文本：
{"completed":true|false,"message":"简短说明","named_evidence":[{"criterion":"标准名","screen_reference":"mark_id 或屏幕上的具体元素","observed_value":"你在该处实际看到的文字"}]}

判断标准：
- 只判断契约中标记为 [judge] 的成功标准。标记为 [auto] 的标准由系统读取设备状态自行核验，你不需要点名或回报。
- 每条证据给出：criterion（标准名）、screen_reference（mark_id 或屏幕上的具体元素，不要写"区域1"/"屏幕"这类占位）、observed_value（你在该处实际看到的原文）。
- 照实回报你看到的文字，不要猜测系统内部使用的取值。observed_value 仅用于当前 node 匹配，不写入 state/trace。
- 只有当屏幕确实显示该标准已满足时才点名它。宁可漏报，不要虚报——虚报会让任务被错误地判定为完成。
- 标准名白名单：用户消息中的"标准名白名单"列出了本任务的合法标准名。named_evidence 中每条 evidence 的 criterion 字段必须**逐字等于**白名单中的名称之一，禁止改写、翻译、大小写变化、加前后缀或拼接其他文字。
- 完整性：completed=true 时，白名单中每个 required 的 [judge] 标准都必须各有一条 criterion 逐字命中的 named_evidence，缺一不可；缺少任何一条即视为任务未完成，输出 completed=false。
- 如果任务尚未完成，输出 completed=false 并把 named_evidence 留空。
- 广告、banner、推荐流、热词或首页动态内容不能证明任务完成。
"""

ACCEPTANCE_SYSTEM_PROMPT_EN = """You are the terminal acceptance checker for a mobile automation task. The action has already executed; your job is to judge whether the **whole task** is genuinely complete.

You MUST output exactly one JSON object. No Markdown, XML, function calls, or extra text:
{"completed":true|false,"message":"brief note","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id or a concrete on-screen element","observed_value":"the text you actually see there"}]}

Judgment criteria:
- Judge only the criteria marked [judge] in the contract. Criteria marked [auto] are verified by the system from device state — do not cite or report them.
- For each evidence item give: criterion (its name), screen_reference (a mark_id or concrete on-screen element — never a placeholder like "region-1"/"screen"), and observed_value (the text you actually see there).
- Report what you see verbatim; do not guess values the system uses internally. observed_value is node-local and must not enter state or trace.
- Only name a criterion when the screen genuinely shows it satisfied. Prefer under-reporting: a false claim makes the task wrongly count as finished.
- Criterion name whitelist: the user message contains a "criterion name whitelist" listing the only legal names for this task. The criterion field of every named_evidence item MUST equal one of the whitelist names VERBATIM — no paraphrasing, translation, case changes, prefixes/suffixes, or extra text.
- Completeness: when completed=true, every required [judge] criterion in the whitelist must have exactly one named_evidence item whose criterion matches verbatim. Missing any one means the task is not complete — output completed=false.
- If the task is not complete, output completed=false and leave named_evidence empty.
- Ads, banners, recommendation feeds, trending words, and home-screen churn never prove completion.
"""

_EVIDENCE_SOURCES = frozenset(
    {
        "accessibility",
        "screen_object",
        "mark",
        "visual_region",
        "whole_screen",
        "external_probe",
        "device",
    }
)


def parse_acceptance_response(raw: str) -> tuple[bool, str, list[dict] | None]:
    """Parse the acceptance model reply into (completed, message, named_evidence).

    ``named_evidence`` is None when the model produced no usable list, which the
    evaluator distinguishes from an empty list ("asked, saw nothing").
    """
    try:
        data = json.loads(str(raw or "").strip())
    except (json.JSONDecodeError, TypeError):
        return False, "acceptance response unparseable", None
    if not isinstance(data, dict):
        return False, "acceptance response was not an object", None

    completed = data.get("completed") is True
    message = str(data.get("message") or "")[:300]
    raw_evidence = data.get("named_evidence")
    if not isinstance(raw_evidence, list):
        return completed, message, None

    evidence: list[dict] = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        entry: dict[str, object] = {
            "criterion": str(item.get("criterion", ""))[:128],
            "screen_reference": str(item.get("screen_reference", ""))[:128],
        }
        if "observed_value" in item:
            entry["observed_value"] = item.get("observed_value")
        if item.get("source") in _EVIDENCE_SOURCES:
            entry["source"] = item.get("source")
        evidence.append(entry)
    return completed, message, evidence


def _hard_veto(collected: dict[str, dict] | None, goal_contract) -> list[str]:
    """Required criteria that collected facts directly contradict (layer 1)."""

    if not collected:
        return []
    required = {
        criterion.name for criterion in goal_contract.success_criteria if criterion.required
    }
    return sorted(
        name
        for name, result in collected.items()
        if name in required
        and isinstance(result, dict)
        and result.get("status") == "contradicted"
    )


def _needs_semantic_judgement(goal_contract) -> bool:
    """Whether any criterion genuinely requires a model to interpret the screen."""

    return any(
        not _is_self_observable(criterion)
        for criterion in goal_contract.success_criteria
    )


def _judge_criterion_names(goal_contract) -> list[str]:
    """Verbatim [judge] criterion names in contract order (the whitelist).

    Only criteria the system cannot settle from device truth go to the judge;
    [auto] criteria are excluded because the judge must not cite them.
    """

    if goal_contract is None:
        return []
    return [
        criterion.name
        for criterion in goal_contract.success_criteria
        if not _is_self_observable(criterion)
    ]


def _judge_whitelist_block(names: list[str], *, lang: str) -> str:
    """Render the verbatim criterion-name whitelist for the judge prompt."""

    if not names:
        return ""
    names_block = "\n".join(f"    {name}" for name in names)
    if lang == "en":
        return (
            "Criterion name whitelist (use these names VERBATIM — no "
            "paraphrasing, translation, or case changes):\n" + names_block
        )
    return "标准名白名单（必须逐字使用，禁止改写、翻译或大小写变化）：\n" + names_block


def acceptance_node(state: "AgentState", config: RunnableConfig) -> dict:
    """Validate a pending finish claim against the goal contract.

    Two entry channels: a model finish claim (``pending_finish=True`` routed
    from ``after_execute``) and a budget-forced acceptance (``should_continue``
    routed here when the step budget is exhausted without a claim). Both run
    the same three authority layers — hard veto → hard confirm → semantic
    judgement — so a budget-forced run with an empty ``matched_terminal_evidence``
    fails closed unless the semantic judge names grounded evidence.
    """

    configurable = config.get("configurable", {})
    device_factory = configurable["device_factory"]
    device_id = state.get("device_id")
    lang = state.get("lang", "cn")
    task = state["task"]
    context_mode = get_context_mode(state, config)
    action_parsed = state.get("action_parsed")
    action_result = state.get("action_result")

    # Budget-forced entry: should_continue is a pure function and cannot write
    # state, so the acceptance node marks the run here. The flag prevents a
    # second forced acceptance; pending_finish is set so downstream state
    # consumers see an in-flight claim exactly like the model-claim channel.
    budget_forced = bool(
        state.get("goal_contract_status") == "compiled"
        and not state.get("budget_acceptance_done")
        and not state.get("pending_finish")
        and int(state.get("step_count") or 0) >= int(state.get("max_steps") or 0)
    )
    budget_update: dict = {}
    if budget_forced:
        # F2 window budget: the window boundary is either a plain budget-forced
        # acceptance or the run's absolute step ceiling (initial window * 3). At
        # the absolute ceiling no continuation can be earned, so the run ends
        # with an explicit ``absolute_budget_exhausted`` attribution.
        absolute_cap = int(
            state.get("absolute_max_steps")
            or absolute_max_steps(state.get("max_steps"))
        )
        is_absolute_exhausted = int(state.get("step_count") or 0) >= absolute_cap
        budget_update = {
            "pending_finish": True,
            "budget_acceptance_done": True,
            "finish_source": (
                "absolute_budget_exhausted"
                if is_absolute_exhausted
                else "budget_forced"
            ),
        }
        emit_trace(
            config,
            state,
            "acceptance",
            "budget_forced_acceptance",
            {
                "step_count": state.get("step_count"),
                "max_steps": state.get("max_steps"),
                "absolute_max_steps": absolute_cap,
                "absolute_budget_exhausted": is_absolute_exhausted,
                "finish_claim": None,
                "pending_finish": True,
                "matched_terminal_evidence": [],
            },
        )

    goal_contract = ensure_goal_contract(state, config)
    if goal_contract is None:
        # Fail closed: without a contract there is nothing to verify against.
        emit_trace(
            config,
            state,
            "acceptance",
            "acceptance_no_contract",
            {"reason": "goal_contract_not_compiled"},
        )
        return _rejected(
            state,
            context_mode=context_mode,
            evaluation=GoalEvaluation(
                status="failure",
                matched=[],
                missing=["goal_contract_unavailable"],
                evidence={"reason": "goal_contract_not_compiled"},
            ),
            message="finish claim rejected: goal contract unavailable",
        )

    # --- observe once; both the fact providers and the model see this frame ---
    try:
        device_capture = capture_device_observation(
            device_factory,
            device_id,
            timeout=int(configurable.get("screenshot_timeout", 10) or 10),
            max_attempts=int(configurable.get("observation_capture_attempts", 2) or 2),
        )
    except ObservationCaptureError as exc:
        return {
            "pending_finish": False,
            "finished": False,
            "finish_validation_status": "unknown",
            "reflection_verdict": "retry",
            "failure_cause": "context_lost",
            "suggested_strategy": "wait",
            "grounding_error": exc.code,
            "grounding_failure_code": exc.code,
            "observation_retry_count": int(state.get("observation_retry_count") or 0) + 1,
            "context_mode": context_mode,
            **budget_update,
        }
    screenshot = device_capture.screenshot
    current_app = device_capture.current_app
    if screenshot_failure_code(screenshot):
        return {
            **screenshot_failure_update(
                state=state,
                config=config,
                screenshot=screenshot,
                current_app=current_app,
                context_mode=context_mode,
            ),
            **budget_update,
        }

    after_observation = build_after_observation(
        state=state,
        config=config,
        screenshot=screenshot,
        current_app=current_app,
        foreground=device_capture.foreground,
        observation_epoch=device_capture.observation_epoch,
        device_factory=device_factory,
        device_id=device_id,
    )
    after_verifier_observation = verifier_observation_payload(
        after_observation, task_context=task
    )
    device_signals = collect_device_verifier_signals(
        device_factory=device_factory, device_id=device_id, config=config
    )
    if device_signals:
        after_verifier_observation = {
            **after_verifier_observation,
            "device_signals": device_signals,
        }
    verifier_result = verify_action_outcome(
        before_state={**state, "expected_outcome": state.get("expected_outcome")},
        after_screenshot=screenshot,
        after_app=current_app,
        action_result=action_result,
        before_observation=state_before_observation_payload(state, task_context=task),
        after_observation=after_verifier_observation,
        page_signal_adapter=None,
    )

    finish_claim_matched: list[str] = []
    if isinstance(action_parsed, dict):
        raw_claim = action_parsed.get("matched_terminal_evidence")
        if isinstance(raw_claim, list):
            finish_claim_matched = [item for item in raw_claim if isinstance(item, str)]

    runtime_contract_id = goal_runtime_reference(state)
    state_contract = state.get("goal_contract")
    has_runtime_binding = isinstance(state_contract, dict) and isinstance(
        state_contract.get("runtime_reference"), str
    )
    ledger = list(state.get("goal_evidence_ledger") or []) if has_runtime_binding else []

    # --- layers 1 & 2: what the system can establish on its own ---
    facts = collect_goal_facts(
        goal_contract=goal_contract,
        configurable=configurable,
        screenshot=screenshot,
        after_observation=after_observation,
        runtime_contract_id=runtime_contract_id,
    )
    collected = facts["collected"] if facts else None
    in_target_app = target_app_entered(
        goal_contract,
        collected,
        current_app=current_app,
        foreground_activity=after_observation.snapshot.foreground_activity,
    )
    if facts:
        ledger = append_evaluation_entries(
            ledger,
            evaluation={"evidence": {"per_criterion": collected}},
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            predicate_ids=facts["predicate_ids"],
            target_app_entered=in_target_app,
        )

    unattested = unattested_raw_text_bindings(
        ledger,
        goal_contract,
        contract_id=runtime_contract_id,
    )
    adequacy_update: dict = {}
    if unattested:
        adequacy_update = {
            "contract_adequacy_status": "degraded",
            "contract_adequacy_reasons": sorted(
                set(state.get("contract_adequacy_reasons") or [])
                | {"binding_never_observed"}
            ),
        }
        emit_trace(
            config,
            state,
            "acceptance",
            "binding_never_observed",
            {"criterion_ids": unattested, "status": "degraded"},
        )

    vetoed = _hard_veto(collected, goal_contract)
    if vetoed:
        emit_trace(
            config,
            state,
            "acceptance",
            "acceptance_hard_veto",
            {"contradicted_criteria": vetoed},
        )
        evaluation = GoalEvaluation(
            status="failure",
            matched=[],
            missing=vetoed,
            evidence={
                "per_criterion": collected or {},
                "authority_layer": "hard_veto",
            },
        )
        continuation_update = (
            _continuation_decision(state, config, evaluation=evaluation)
            if budget_forced
            else {}
        )
        return _rejected(
            state,
            context_mode=context_mode,
            evaluation=evaluation,
            message="finish claim rejected: device evidence contradicts the goal",
            ledger=ledger,
            observation=after_observation,
            current_app=current_app,
            extra_update={
                **adequacy_update,
                **budget_update,
                **continuation_update,
            },
        )

    # --- layer 3: semantic judgement, only where it is actually needed ---
    named_evidence: list[dict] | None = None
    model_message = ""
    if _needs_semantic_judgement(goal_contract):
        named_evidence, model_message = _run_semantic_judge(
            state=state,
            config=config,
            lang=lang,
            task=task,
            current_app=current_app,
            screenshot=screenshot,
            after_observation_summary=sanitize_verifier_observation_payload(
                after_verifier_observation, task_context=task
            ),
        )
        if named_evidence:
            claimed = set()
            for item in named_evidence:
                if not isinstance(item, dict) or not item.get("criterion"):
                    continue
                normalized = _normalize_criterion_name(item.get("criterion"))
                if normalized:
                    claimed.add(normalized)
            finish_claim_matched = sorted(set(finish_claim_matched) | claimed)

    evaluation = evaluate_finish_claim(
        contract=goal_contract,
        verifier_status=verifier_result.status,
        verifier_evidence=verifier_result.evidence,
        after_observation=after_verifier_observation,
        device_signals=device_signals,
        finish_claim_matched=finish_claim_matched,
        reflect_named_evidence=named_evidence,
        goal_probes=configurable.get("goal_probes"),
    )
    ledger = append_evaluation_entries(
        ledger,
        evaluation=evaluation.to_dict(),
        contract_id=runtime_contract_id,
        screen_id=after_observation.snapshot.screen_id,
        observation_epoch=after_observation.snapshot.observation_epoch,
        predicate_ids={
            criterion.name: (
                criterion.predicate.predicate_id
                if criterion.predicate is not None
                else None
            )
            for criterion in goal_contract.success_criteria
        },
    )
    if facts:
        # Re-append provider facts last. The typed fold reads the newest entry
        # per criterion, so collected evidence must settle after the
        # model-informed pass — otherwise testimony would silently outrank the
        # device truth this node is built to trust.
        ledger = append_evaluation_entries(
            ledger,
            evaluation={"evidence": {"per_criterion": collected}},
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            predicate_ids=facts["predicate_ids"],
        )

    # The typed fold is authoritative when every criterion carries a predicate,
    # except where it saw nothing at all: absence is not counter-evidence.
    if goal_contract.success_criteria and all(
        criterion.predicate is not None for criterion in goal_contract.success_criteria
    ):
        pure_evaluation = pure_goal_evaluator.evaluate(
            contract=goal_contract,
            contract_id=runtime_contract_id,
            evidence_ledger=ledger,
            finish_claim_matched=finish_claim_matched,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
        )
        per_criterion = (pure_evaluation.evidence or {}).get("per_criterion") or {}
        has_unobserved = any(
            isinstance(value, dict) and value.get("reason") == "criterion_unobserved"
            for value in per_criterion.values()
        )
        if not has_unobserved:
            evaluation = pure_evaluation
        else:
            emit_trace(
                config,
                state,
                "acceptance",
                "pure_evaluation_degraded",
                {
                    "reason": "criterion_unobserved",
                    "kept_status": evaluation.status,
                    "pure_status": pure_evaluation.status,
                },
            )

    emit_trace(
        config,
        state,
        "acceptance",
        "acceptance_result",
        {
            "current_app": current_app,
            "goal_contract": goal_trace_payload(state, config),
            "finish_claim_matched": sorted(finish_claim_matched),
            "finish_validation": evaluation.to_dict(),
            "device_signals": sanitize_context_payload(
                device_signals, consumer="reflect_prompt", task_context=task
            ),
        },
    )

    if evaluation.status != "success":
        continuation_update = (
            _continuation_decision(state, config, evaluation=evaluation)
            if budget_forced
            else {}
        )
        return _rejected(
            state,
            context_mode=context_mode,
            evaluation=evaluation,
            message=model_message or "finish claim rejected: goal evidence missing",
            ledger=ledger,
            observation=after_observation,
            current_app=current_app,
            extra_update={
                **adequacy_update,
                **budget_update,
                **continuation_update,
            },
        )

    return {
        **_observation_update(after_observation, current_app),
        "pending_finish": False,
        "finished": True,
        "finish_validation_status": evaluation.status,
        "finish_validation_evidence": evaluation.to_dict(),
        "goal_evidence_ledger": ledger,
        "observation_retry_count": 0,
        "action_succeeded": True,
        "reflection_verdict": "succeeded",
        "failure_cause": None,
        "suggested_strategy": "finish",
        "reflection": model_message or "task complete: goal criteria satisfied",
        "context_mode": context_mode,
        **adequacy_update,
        **budget_update,
    }

def _observation_update(after_observation, current_app: str) -> dict:
    """State fields carrying the freshly captured screen forward."""

    return {
        "screenshot_b64": None,
        "current_app": current_app,
        "screen_id": after_observation.snapshot.screen_id,
        "screen_hash": after_observation.snapshot.screen_hash,
        "observation": after_observation.to_dict(),
        "mark_registry": after_observation.mark_registry.to_dict(),
    }


def _continuation_decision(
    state: "AgentState", config: RunnableConfig, *, evaluation: GoalEvaluation
) -> dict:
    """Evaluate and apply the F2 earned-continuation at a rejected window boundary.

    The credential is a pure function (``context.continuation_credential``); the
    write happens HERE in the node — edges never write state (pi-16 pit #4).
    Telemetry is emitted before thresholds are adjusted, and grants are capped
    by ``CONTINUATION_MAX_GRANTS`` and by the absolute step ceiling.
    """

    credential = continuation_credential(
        {**state, "finish_validation_evidence": evaluation.to_dict()}
    )
    current_latch = latched_criterion_count(state)
    update = {
        "continuation_last_latch_count": current_latch,
        # W2 T4: window-boundary snapshot of the plan's current stage, consumed
        # by continuation_credential branch 4 (stage_advance).
        "continuation_last_stage_index": (state.get("task_plan_status") or {}).get(
            "current_stage_index"
        ),
    }
    absolute_cap = int(
        state.get("absolute_max_steps") or absolute_max_steps(state.get("max_steps"))
    )
    current_max = int(state.get("max_steps") or 0)
    granted = (
        credential.granted
        and int(state.get("continuation_count") or 0) < CONTINUATION_MAX_GRANTS
        and current_max + CONTINUATION_GRANT_STEPS <= absolute_cap
    )
    if granted:
        emit_trace(
            config,
            state,
            "acceptance",
            "continuation_granted",
            {
                "branches": list(credential.branches),
                "reason": credential.reason,
                "max_steps_before": current_max,
                "max_steps_after": current_max + CONTINUATION_GRANT_STEPS,
                "continuation_count": int(state.get("continuation_count") or 0) + 1,
                "absolute_max_steps": absolute_cap,
                "step_count": state.get("step_count"),
            },
        )
        update.update(
            {
                "max_steps": current_max + CONTINUATION_GRANT_STEPS,
                "continuation_count": int(state.get("continuation_count") or 0) + 1,
                "budget_acceptance_done": False,
                "finish_source": None,
                "pending_finish": False,
            }
        )
    else:
        emit_trace(
            config,
            state,
            "acceptance",
            "continuation_denied",
            {
                "branches": list(credential.branches),
                "reason": credential.reason,
                "continuation_count": int(state.get("continuation_count") or 0),
                "absolute_budget_exhausted": bool(
                    int(state.get("step_count") or 0) >= absolute_cap
                ),
            },
        )
    return update


def _rejected(
    state: "AgentState",
    *,
    context_mode: str,
    evaluation: GoalEvaluation,
    message: str,
    ledger: list[dict] | None = None,
    observation=None,
    current_app: str | None = None,
    extra_update: dict | None = None,
) -> dict:
    """Reject a finish claim and send the run back to planning."""

    acceptance_round_count = int(state.get("acceptance_round_count") or 0) + 1
    round_limit = int(DEFAULT_VERIFICATION_POLICY.value("acceptance_round_limit"))
    limit_reached = acceptance_round_count >= round_limit
    update: dict = {
        "pending_finish": False,
        "finished": False,
        "finish_validation_status": evaluation.status,
        "finish_validation_evidence": evaluation.to_dict(),
        "action_succeeded": False,
        "reflection_verdict": "failed",
        "failure_cause": "goal_not_satisfied",
        "suggested_strategy": (
            "avoid_repeated_finish_claim" if limit_reached else "continue"
        ),
        "reflection": (
            f"{message}; acceptance round limit reached, continue without repeating "
            "the same finish claim"
            if limit_reached
            else message
        ),
        "acceptance_round_count": acceptance_round_count,
        "context_mode": context_mode,
    }
    if ledger is not None:
        update["goal_evidence_ledger"] = ledger
    if observation is not None and current_app is not None:
        update.update(_observation_update(observation, current_app))
        update["observation_retry_count"] = 0
    if extra_update:
        update.update(extra_update)
    return update


def _run_semantic_judge(
    *,
    state: "AgentState",
    config: RunnableConfig,
    lang: str,
    task: str,
    current_app: str,
    screenshot,
    after_observation_summary: dict,
) -> tuple[list[dict] | None, str]:
    """Ask the model whether the raw-text criteria are satisfied (layer 3)."""

    configurable = config.get("configurable", {})
    model_client = configurable["model_client"]
    system_prompt = (
        ACCEPTANCE_SYSTEM_PROMPT_EN if lang == "en" else ACCEPTANCE_SYSTEM_PROMPT_CN
    )
    task_for_prompt = str(
        sanitize_context_payload(
            task, "task", consumer="reflect_prompt", task_context=task
        )
    )
    goal_block = build_goal_prompt_block(state, lang=lang, config=config)
    whitelist = _judge_whitelist_block(
        _judge_criterion_names(ensure_goal_contract(state, config)), lang=lang
    )
    if lang == "en":
        body = (
            f"Original task: {task_for_prompt}\n"
            f"Current app: {current_app}\n"
            f"After-observation summary: {after_observation_summary}\n"
            "Is the whole task complete? Judge only the [judge] criteria."
        )
    else:
        body = (
            f"原始任务：{task_for_prompt}\n"
            f"当前应用：{current_app}\n"
            f"当前屏幕摘要：{after_observation_summary}\n"
            "整个任务是否已完成？只判断 [judge] 标准。"
        )
    if whitelist:
        body = f"{body}\n\n{whitelist}"

    messages = [
        MessageBuilder.create_system_message(system_prompt),
        MessageBuilder.create_user_message(text=goal_block),
        MessageBuilder.create_user_message(
            text=body,
            image_base64=screenshot.base64_data,
            image_mime_type=getattr(screenshot, "mime_type", "image/png"),
        ),
    ]
    try:
        try:
            response = model_client.request(
                messages, output_mode="json_schema", validate_action=False
            )
        except TypeError as type_error:
            if "output_mode" not in str(type_error) and "validate_action" not in str(
                type_error
            ):
                raise
            response = model_client.request(messages)
        completed, message, named_evidence = parse_acceptance_response(response.action)
        # A3: the judge's raw reply is the only attribution source when the
        # gate fails — surface its key fields (redacted) in the trace. Only
        # additions here; nothing pre-existing is rewritten.
        emit_trace(
            config,
            state,
            "acceptance",
            "acceptance_judge_reply",
            sanitize_context_payload(
                {
                    "completed": completed,
                    "message": message,
                    "named_evidence": [
                        {
                            "criterion": str(item.get("criterion", ""))[:128],
                            "screen_reference": str(
                                item.get("screen_reference", "")
                            )[:128],
                            "observed_value": str(
                                item.get("observed_value", "")
                            )[:200],
                        }
                        for item in (named_evidence or [])
                    ],
                },
                consumer="trace_payload",
                task_context=task,
            ),
        )
        return named_evidence, message
    except Exception as exc:
        message = f"Acceptance judgement failed: {type(exc).__name__}"
        emit_trace(
            config,
            state,
            "acceptance",
            "acceptance_error",
            {
                "message": message,
                "parse_metadata": getattr(exc, "parse_metadata", {}) or {},
            },
        )
        # No testimony collected. Returning None (not []) keeps the evaluator's
        # "not yet consulted" semantics, which is fail-closed: unknown blocks.
        return None, message
