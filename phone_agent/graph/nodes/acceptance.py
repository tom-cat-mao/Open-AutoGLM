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
from phone_agent.config.prompts_en import ACCEPTANCE_JUDGE_PROMPT_EN
from phone_agent.config.prompts_zh import ACCEPTANCE_JUDGE_PROMPT_ZH
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
    GoalContract,
    build_goal_prompt_block,
    ensure_goal_contract,
    goal_runtime_reference,
    goal_trace_payload,
)
from phone_agent.graph.goal_evaluator import (
    GoalEvaluation,
    _is_self_observable,
    _normalize_criterion_name,
    evaluation_from_acceptance_fold,
    fold_acceptance_verdicts,
    resolve_programmatic_criteria,
)
from phone_agent.graph.goal_evidence import (
    append_evaluation_entries,
    append_screen_text_digest,
    criterion_semantic_key,
    criterion_stage_map,
    revoke_seals_on_contradiction,
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

# Stage-Sealing judge prompts (L3). Single source of truth lives in
# config/prompts_zh.py / prompts_en.py (CN/EN must stay in lockstep — see the
# pairing test). The old names are retained as aliases for callers/tests that
# predate the Stage-Sealing contract change.
ACCEPTANCE_SYSTEM_PROMPT_CN = ACCEPTANCE_JUDGE_PROMPT_ZH
ACCEPTANCE_SYSTEM_PROMPT_EN = ACCEPTANCE_JUDGE_PROMPT_EN

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


def parse_acceptance_verdicts(raw: str) -> list[dict] | None:
    """Parse the Stage-Sealing judge contract: a ``verdicts`` list.

    None means the model produced no usable verdicts field (the caller falls
    back to the legacy ``completed``/``named_evidence`` contract); an empty
    list means "judge ran but had nothing satisfied to report". Verdict
    statuses are validated against the tri-state.
    """
    try:
        data = json.loads(str(raw or "").strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_verdicts = data.get("verdicts")
    if not isinstance(raw_verdicts, list):
        return None
    verdicts: list[dict] = []
    for item in raw_verdicts:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status not in {"satisfied", "unknown", "contradicted"}:
            continue
        entry: dict[str, object] = {
            "criterion": str(item.get("criterion", ""))[:128],
            "status": status,
        }
        if "observed_value" in item:
            entry["observed_value"] = item.get("observed_value")
        if "screen_reference" in item:
            entry["screen_reference"] = str(item.get("screen_reference"))[:128]
        if "evidence_step" in item:
            entry["evidence_step"] = item.get("evidence_step")
        verdicts.append(entry)
    return verdicts


def _hard_veto(
    collected: dict[str, dict] | None, goal_contract
) -> list[str]:
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


def _ledger_digest_for_judge(
    ledger: list[dict], *, contract_id: str, lang: str, task_context: str | None
) -> str:
    """Bounded L1+L2+seal summary handed to the L3 judge (Phase C §5).

    All text inside was regex-redacted on write; the rendered digest is
    re-sanitized before egress so raw literals never leak into the prompt
    consumer beyond the redacted form.
    """
    from phone_agent.graph.goal_evidence import l1_digest_screen_window

    window = l1_digest_screen_window()
    lines: list[str] = []
    if lang == "en":
        lines.append(
            "Evidence ledger digest (program-extracted screen-text records; "
            "authoritative mechanical facts — trust them directly):"
        )
    else:
        lines.append(
            "证据账本摘要（程序从无障碍树机械提取的屏幕文本记录，属已确证事实，可直接采信）："
        )
    digests = [e for e in ledger if isinstance(e, dict) and e.get("kind") == "screen_text_digest"]
    if not digests:
        lines.append("  (no screen-text records yet)" if lang == "en" else "  （暂无屏幕文本记录）")
    for entry in digests[-window:]:
        texts = [
            str(item.get("text") or "")[:120]
            for item in (entry.get("texts") or [])[:12]
        ]
        if not texts:
            continue
        screen = str(entry.get("screen_id") or "unknown")[:64]
        joined = " | ".join(texts)
        lines.append(f"  screen[{screen}]: {joined}")
    events = [e for e in ledger if isinstance(e, dict) and e.get("kind") == "effect_event"]
    for entry in events[-8:]:
        lines.append(
            f"  action[{entry.get('action') or ''}]@{entry.get('step') or 0}"
            f" screen[{str(entry.get('screen_id') or '')[:48]}]"
        )
    seals = [e for e in ledger if isinstance(e, dict) and e.get("kind") == "stage_seal"]
    for entry in seals[-8:]:
        criteria = ", ".join(str(c) for c in (entry.get("criteria_sealed") or []))
        lines.append(f"  sealed stage[{entry.get('stage_id') or ''}] criteria: {criteria}")
    digest_text = "\n".join(lines)
    safe = sanitize_context_payload(
        digest_text, consumer="reflect_prompt", task_context=task_context
    )
    return str(safe or "")[:2000]


def _trajectory_summary_for_judge(
    ledger: list[dict], *, contract_id: str, lang: str, task_context: str | None
) -> str:
    """Bounded (~12-step) trajectory summary for the L3 judge (S3).

    Code builds the FORM from ledger records — per step: action type →
    reflect verdict (from effect events), and the model screen readings
    (criterion=value from ``model_observation``). The judge uses this to
    attribute causality (this-run behavior vs residual screen state); code
    never interprets the values. All text was redacted on ledger write and is
    re-sanitized before egress.
    """

    buckets: dict[int, dict[str, Any]] = {}
    for entry in ledger:
        if not isinstance(entry, dict) or entry.get("contract_id") != contract_id:
            continue
        kind = entry.get("kind")
        try:
            step = int(entry.get("step") or 0)
        except (TypeError, ValueError):
            continue
        bucket = buckets.setdefault(step, {"actions": [], "observations": []})
        if kind == "effect_event":
            action = str(entry.get("action") or "") or "?"
            observed_after = str(entry.get("observed_after") or "")
            verdict = ""
            for part in observed_after.split():
                if part.startswith("verdict="):
                    verdict = part[len("verdict="):]
                    break
            bucket["actions"].append(
                f"{action} -> {verdict or '?'}"
            )
        elif kind == "model_observation":
            status = str(entry.get("status") or "")
            if status != "observed":
                continue
            criterion = str(entry.get("criterion") or "")[:128]
            value = str(entry.get("observed_value") or "")[:80]
            bucket["observations"].append(
                f"{criterion}={value}" if value else criterion
            )
    if not buckets:
        return ""
    lines: list[str] = []
    for step in sorted(buckets)[-12:]:
        bucket = buckets[step]
        action_text = "; ".join(bucket["actions"]) if bucket["actions"] else "?"
        line = f"s{step}: {action_text}"
        if bucket["observations"]:
            line += f"; \u89c2\u5bdf: {', '.join(bucket['observations'])}"
        lines.append(line)
    prefix = (
        "Trajectory summary (action -> reflection verdict; observation = model screen reads; "
        "your only source for causality):"
        if lang == "en"
        else "轨迹摘要（动作 -> 反思结论；观察 = 模型读屏结果；判断因果的唯一来源）："
    )
    summary_text = prefix + "\n" + "\n".join(lines)
    safe = sanitize_context_payload(
        summary_text, consumer="reflect_prompt", task_context=task_context
    )
    return str(safe or "")[:2000]



def _stage_hint(stage_id: str, *, lang: str) -> str:
    if lang == "en":
        return (
            f"This criterion belongs to stage {stage_id}; its evidence was not "
            "recorded — return to the relevant screen so the evidence can be observed."
        )
    return f"该判据属于阶段 {stage_id}，其证据未入账——请回到对应页面让证据可被观察。"


def _confirmed_stage_hint(stage_id: str, *, lang: str) -> str:
    """Rejection hint for an unsatisfied CONFIRMED criterion (provenance).

    Confirmed criteria (query parameters) must be read on the control itself;
    the hint guides the agent back to the parameter panel instead of letting
    it re-assert the same finish claim from the result list.
    """

    if lang == "en":
        return (
            f"This criterion belongs to stage {stage_id} and needs a confirmed "
            "parameter read — open the filter/parameter panel so the value is "
            "readable on its control (inferring it from the result list does "
            "not count)."
        )
    return (
        f"该判据属于阶段 {stage_id}，需要确认参数值——请打开筛选/参数面板，"
        "让参数值在控件上可被读取（从结果列表推断不算数）。"
    )


def _confirmed_terminal_hint(*, lang: str) -> str:
    if lang == "en":
        return (
            "This confirmed parameter criterion has no recorded control read — "
            "open the parameter panel so the value can be read on its control "
            "(inferring it from the result list does not count)."
        )
    return (
        "该确认参数判据暂无控件读值入账——请打开参数面板让值在控件上可被读取"
        "（从结果列表推断不算数）。"
    )


def _terminal_hint(*, lang: str) -> str:
    if lang == "en":
        return (
            "No evidence was recorded for this terminal criterion — return to the "
            "relevant screen so it can be observed."
        )
    return "该终局判据暂无证据入账——请回到相关页面让其可被观察。"


def _missing_feedback(
    contract, unknown_names: list[str], *, lang: str
) -> dict:
    """Structured rejection feedback: per-unknown-criterion stage + neutral hint
    (Phase C §7). Only criterion names / stage ids / hints — never raw screen
    text — so it is safe for the plan feedback channel. Confirmed criteria get
    a control-read hint (L2 provenance): the agent must open the parameter
    panel, not re-derive the value from the result list.
    """
    stage_map = criterion_stage_map(contract)
    criteria_by_name = {
        criterion.name: criterion for criterion in contract.success_criteria
    }
    missing: list[dict] = []
    for name in sorted(unknown_names):
        stage_id = stage_map.get(name)
        criterion = criteria_by_name.get(name)
        confirmed = getattr(criterion, "provenance", "state") == "confirmed"
        if confirmed:
            hint = (
                _confirmed_stage_hint(stage_id, lang=lang)
                if stage_id
                else _confirmed_terminal_hint(lang=lang)
            )
        else:
            hint = (
                _stage_hint(stage_id, lang=lang)
                if stage_id
                else _terminal_hint(lang=lang)
            )
        missing.append(
            {"criterion": name, "stage_id": stage_id, "hint": hint}
        )
    return {"missing": missing}


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
    # The ledger is trustworthy whenever the state carries a usable contract
    # (runtime-bound dict or a direct GoalContract object); only legacy/trace
    # payload states (no runtime reference) start from an empty ledger.
    if isinstance(state_contract, (dict, GoalContract)):
        ledger = list(state.get("goal_evidence_ledger") or [])
    else:
        ledger = []

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
    semantic_keys = {
        criterion.name: criterion_semantic_key(criterion.description)
        for criterion in goal_contract.success_criteria
    }
    if facts:
        ledger = append_evaluation_entries(
            ledger,
            evaluation={"evidence": {"per_criterion": collected}},
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            predicate_ids=facts["predicate_ids"],
            target_app_entered=in_target_app,
            semantic_keys=semantic_keys,
        )
    # L1: the terminal observation's mechanical text digest joins the ledger
    # (the final screen is part of the trajectory too).
    ledger = append_screen_text_digest(
        ledger,
        contract_id=runtime_contract_id,
        screen_id=after_observation.snapshot.screen_id,
        observation_epoch=after_observation.snapshot.observation_epoch,
        marks=after_observation.mark_registry.marks.values(),
        target_app_entered=in_target_app,
    )
    # Positive counter-observation on a sealed criterion revokes the seal
    # (P0 #13a: revocation only on contradiction, never on absence).
    if facts:
        contradicted_facts = {
            name
            for name, result in collected.items()
            if isinstance(result, dict) and result.get("status") == "contradicted"
        }
        ledger = revoke_seals_on_contradiction(
            ledger,
            contract=goal_contract,
            contract_id=runtime_contract_id,
            contradicted_criteria=contradicted_facts,
            screen_id=after_observation.snapshot.screen_id,
            step=state.get("step_count"),
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

    # --- Stage-Sealing fold: per-criterion tri-state (Phase C) ---
    # Self-observable criteria without a typed predicate (legacy
    # object_rank_match / object_hash_match / app_or_activity_match /
    # focus_or_keyboard / accessibility_text_match contracts) have no provider
    # fact, so the only mechanical truth for them is the acceptance
    # observation's verifier signals. Resolve them here and write the results
    # into the ledger (current screen/epoch) so the fold's programmatic tier
    # can settle them against the same trust rules as typed facts.
    programmatic_results = resolve_programmatic_criteria(
        goal_contract=goal_contract,
        verifier_evidence=verifier_result.evidence,
        after_observation=after_verifier_observation,
        device_signals=device_signals,
        goal_probes=configurable.get("goal_probes"),
    )
    if programmatic_results:
        ledger = append_evaluation_entries(
            ledger,
            evaluation={"evidence": {"per_criterion": programmatic_results}},
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            predicate_ids={name: None for name in programmatic_results},
            semantic_keys=semantic_keys,
        )
    fold = fold_acceptance_verdicts(
        contract=goal_contract,
        ledger=ledger,
        contract_id=runtime_contract_id,
        screen_id=after_observation.snapshot.screen_id,
        observation_epoch=after_observation.snapshot.observation_epoch,
        finish_claim_matched=finish_claim_matched,
        current_step=state.get("step_count"),
    )
    # --- layer 3: semantic judgement, only for judge-eligible unknowns ---
    criteria_by_name = {
        criterion.name: criterion for criterion in goal_contract.success_criteria
    }
    judge_eligible_unknowns = [
        name for name in fold["unknown"] if not _is_self_observable(criteria_by_name[name])
    ]
    model_message = ""
    if judge_eligible_unknowns:
        digest = _ledger_digest_for_judge(
            ledger, contract_id=runtime_contract_id, lang=lang, task_context=task
        )
        verdicts, named_evidence, model_message = _run_semantic_judge(
            state=state,
            config=config,
            lang=lang,
            task=task,
            current_app=current_app,
            screenshot=screenshot,
            after_observation_summary=sanitize_verifier_observation_payload(
                after_verifier_observation, task_context=task
            ),
            ledger_digest=digest,
            trajectory_summary=_trajectory_summary_for_judge(
                ledger,
                contract_id=runtime_contract_id,
                lang=lang,
                task_context=task,
            ),
        )
        merged_verdicts: list[dict] = []
        if verdicts is not None:
            merged_verdicts = list(verdicts)
        if named_evidence:
            # Legacy contract: each grounded named_evidence item is a
            # satisfied verdict (W1-A whitelist preserved), read on the final
            # screen (S3: evidence_step="final_screen").
            for item in named_evidence:
                merged_verdicts.append(
                    {
                        "criterion": item.get("criterion"),
                        "status": "satisfied",
                        "observed_value": item.get("observed_value"),
                        "screen_reference": item.get("screen_reference"),
                        "evidence_step": "final_screen",
                    }
                )
        if merged_verdicts:
            claimed = set()
            for item in merged_verdicts:
                if not isinstance(item, dict) or not item.get("criterion"):
                    continue
                normalized = _normalize_criterion_name(item.get("criterion"))
                if normalized:
                    claimed.add(normalized)
            finish_claim_matched = sorted(set(finish_claim_matched) | claimed)
        fold = fold_acceptance_verdicts(
            contract=goal_contract,
            ledger=ledger,
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            finish_claim_matched=finish_claim_matched,
            judge_verdicts=merged_verdicts or None,
            current_step=state.get("step_count"),
        )
    elif fold["overall"] == "unknown" and _needs_semantic_judgement(goal_contract):
        emit_trace(
            config,
            state,
            "acceptance",
            "acceptance_judge_skipped",
            {"unknown_criteria": fold["unknown"], "reason": "programmatic_only"},
        )

    evaluation = evaluation_from_acceptance_fold(
        fold, finish_claim_matched=finish_claim_matched
    )
    fold_per_criterion = {
        name: {
            "status": (
                "matched"
                if result.get("status") == "satisfied"
                else (
                    "contradicted"
                    if result.get("status") == "contradicted"
                    else "unknown"
                )
            ),
            "reason": result.get("reason"),
        }
        for name, result in fold["per_criterion"].items()
    }
    ledger = append_evaluation_entries(
        ledger,
        evaluation={"evidence": {"per_criterion": fold_per_criterion}},
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
        semantic_keys=semantic_keys,
    )
    if facts:
        # Re-append provider facts last. The fold reads the newest entry per
        # criterion, so collected evidence must settle after the
        # model-informed pass — otherwise testimony would silently outrank the
        # device truth this node is built to trust.
        ledger = append_evaluation_entries(
            ledger,
            evaluation={"evidence": {"per_criterion": collected}},
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            predicate_ids=facts["predicate_ids"],
            semantic_keys=semantic_keys,
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
            "fold_overall": fold["overall"],
            "seal_count": len(fold["seals"] or []),
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
        feedback = None
        if fold["overall"] == "unknown" and fold["unknown"]:
            feedback = _missing_feedback(
                goal_contract, fold["unknown"], lang=lang
            )
            emit_trace(
                config,
                state,
                "acceptance",
                "acceptance_rejection_feedback",
                sanitize_context_payload(
                    feedback, consumer="trace_payload", task_context=task
                ),
            )
        return _rejected(
            state,
            context_mode=context_mode,
            evaluation=evaluation,
            message=model_message or "finish claim rejected: goal evidence missing",
            ledger=ledger,
            observation=after_observation,
            current_app=current_app,
            feedback=feedback,
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
    feedback: dict | None = None,
    extra_update: dict | None = None,
) -> dict:
    """Reject a finish claim and send the run back to planning."""

    acceptance_round_count = int(state.get("acceptance_round_count") or 0) + 1
    round_limit = int(DEFAULT_VERIFICATION_POLICY.value("acceptance_round_limit"))
    limit_reached = acceptance_round_count >= round_limit
    task_context = state.get("task") if isinstance(state.get("task"), str) else None
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
    if feedback is not None:
        update["acceptance_rejection_feedback"] = sanitize_context_payload(
            feedback, "acceptance_rejection_feedback", consumer="inject", task_context=task_context
        )
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
    ledger_digest: str = "",
    trajectory_summary: str = "",
) -> tuple[list[dict] | None, list[dict] | None, str]:
    """Ask the model whether the raw-text criteria are satisfied (layer 3).

    Returns ``(verdicts, named_evidence, message)``:
    * ``verdicts`` — the Stage-Sealing tri-state contract (None when the
      model replied with the legacy ``completed``/``named_evidence`` format);
    * ``named_evidence`` — the legacy parse (None when unparseable);
    * ``message`` — the judge's message.
    """

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
    digest_block = ledger_digest if ledger_digest else (
        "(no evidence ledger records)" if lang == "en" else "（无证据账本记录）"
    )
    if lang == "en":
        body = (
            f"Original task: {task_for_prompt}\n"
            f"Current app: {current_app}\n"
            f"After-observation summary: {after_observation_summary}\n"
            f"Evidence ledger digest: {digest_block}\n"
            f"Trajectory summary: {trajectory_summary or '(no trajectory records yet)'}\n"
            "Is the whole task complete? Judge only the [judge] criteria; "
            "the ledger digest is mechanically extracted fact you may trust directly, "
            "and the trajectory summary is your only causality source."
        )
    else:
        body = (
            f"原始任务：{task_for_prompt}\n"
            f"当前应用：{current_app}\n"
            f"当前屏幕摘要：{after_observation_summary}\n"
            f"证据账本摘要：{digest_block}\n"
            f"轨迹摘要：{trajectory_summary or '（暂无轨迹记录）'}\n"
            "整个任务是否已完成？只判断 [judge] 标准；账本摘要是程序提取的事实，可直接采信；"
            "轨迹摘要是判断因果的唯一来源。"
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
        verdicts = parse_acceptance_verdicts(response.action)
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
                    "verdicts": [
                        {
                            "criterion": str(item.get("criterion", ""))[:128],
                            "status": str(item.get("status", ""))[:32],
                            "observed_value": str(
                                item.get("observed_value", "")
                            )[:200],
                        }
                        for item in (verdicts or [])
                    ],
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
        return verdicts, named_evidence, message
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
        # No testimony collected. Returning None keeps fail-closed semantics:
        # unknown blocks.
        return None, None, message
