"""Goal contract compilers: External > LLM > Heuristic fallback chain.

The compiler is invoked once at step 0 (by goal_node) and produces a
declarative GoalContract.  On LLM failure, it falls back to a heuristic
weak contract (vlm_judge_at_finish) rather than terminating the task.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Protocol

from phone_agent.config.apps import DEFAULT_APP_REGISTRY, get_app_registry_summary
from phone_agent.graph.goal import (
    LEGACY_SHA256_STUB_PATTERN,
    GoalContract,
    SuccessCriterion,
    TaskStage,
    VALID_VERIFICATIONS,
    compute_task_hash,
    redact_objective,
    task_plan_validation_errors,
)
from phone_agent.graph.goal_requirements import (
    ContractAdequacyValidator,
    TaskRequirementExtractor,
    TaskRequirementSet,
    constraint_spans,
    extract_entity_spans,
    parse_chinese_ordinal,
    parse_toggle_intent,
    raw_text_binding_is_observable,
    _digest as _requirement_digest,
)
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG


class GoalCompilationError(ValueError):
    """Fail-closed goal compilation error with a stable reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------
# Heuristic extraction (ported from old task_goal.py — app/ordinal/entity only)
# ----------------------------------------------------------------------

MAX_ENTITY_COUNT = 4


def _detect_app_hint(text: str) -> str | None:
    resolution = DEFAULT_APP_REGISTRY.resolve_text(text)
    if resolution.status != "resolved" or resolution.identity is None:
        return None
    return resolution.identity.canonical_id


def _detect_ordinal(text: str) -> int | None:
    match = re.search(r"第\s*([1-9]\d*)\s*(?:个|条|项|部|集)?", text)
    if match:
        return int(match.group(1))
    return parse_chinese_ordinal(text)


def _extract_entity_spans(text: str) -> list[str]:
    """Raw entity spans over the SAME spans the requirement extractor produces.

    Replicates TaskRequirementExtractor's operation + app-alias resolution
    inline (extractor does not expose them) and delegates span extraction to
    extract_entity_spans, so requirement entity hashes and contract
    entities_sha always intersect for the same task (divergent stripping
    vocabularies previously caused target_entities_uncovered).
    """
    lowered = str(text or "").casefold()
    operation = next(
        (
            kind
            for kind, terms in TaskRequirementExtractor._OPERATIONS
            if any(term.casefold() in lowered for term in terms)
        ),
        "unknown",
    )
    matched_alias = None
    resolution = DEFAULT_APP_REGISTRY.resolve_text(text)
    if resolution.identity is not None:
        matched_alias = next(
            (
                alias
                for alias in sorted(
                    resolution.identity.aliases, key=len, reverse=True
                )
                if alias.casefold() in lowered
            ),
            None,
        )
    return extract_entity_spans(text, matched_alias, operation)[:MAX_ENTITY_COUNT]


def _extract_entities_sha(text: str) -> list[str]:
    """Digests of the entity spans, used for adequacy intersection only.

    These hashes are a *binding* device (requirement side vs contract side),
    never a match target.
    """
    return [_requirement_digest(item) for item in _extract_entity_spans(text)]


# ----------------------------------------------------------------------
# Compiler protocol
# ----------------------------------------------------------------------


class GoalContractCompiler(Protocol):
    """Compile a task string into a GoalContract."""

    def compile(
        self, *, task: str, hints: dict[str, Any] | None = None
    ) -> GoalContract: ...


# ----------------------------------------------------------------------
# Heuristic compiler — always succeeds, produces weak vlm_judge contract
# ----------------------------------------------------------------------


class HeuristicGoalCompiler:
    """Fallback compiler: extracts app/ordinal/entities but uses vlm_judge.

    Never fails.  Produces a contract with ``verification_strategy=
    "vlm_judge_at_finish"`` and all criteria as ``vlm_judge``.  This does
    NOT drive automatic success — the GoalEvaluator still requires explicit
    criterion naming and grounded evidence.
    """

    def compile(
        self, *, task: str, hints: dict[str, Any] | None = None
    ) -> GoalContract:
        text = unicodedata.normalize("NFKC", str(task or "")).strip()
        app_hint = _detect_app_hint(text)
        ordinal = _detect_ordinal(text)
        entities_sha = _extract_entities_sha(text)
        toggle_state = parse_toggle_intent(text)
        redacted_obj = redact_objective(text)

        criteria: list[SuccessCriterion] = []
        if app_hint:
            criteria.append(
                SuccessCriterion(
                    name="target_app_visible",
                    description=f"Target app '{app_hint}' is in foreground",
                    verification="app_or_activity_match",
                    required=True,
                    predicate=CORE_PREDICATE_CATALOG.create_spec(
                        "app.foreground_identity", app_hint
                    ),
                )
            )
        if ordinal is not None:
            criteria.append(
                SuccessCriterion(
                    name="selected_object_rank",
                    description=f"Selected object has rank {ordinal}",
                    verification="object_rank_match",
                    required=True,
                    predicate=CORE_PREDICATE_CATALOG.create_spec(
                        "ui.object_rank", ordinal
                    ),
                )
            )
        if toggle_state is not None:
            criteria.append(
                SuccessCriterion(
                    name="toggle_state_reached",
                    description=(
                        f"Target toggle is {'on' if toggle_state else 'off'}"
                    ),
                    verification="toggle_state_match",
                    required=True,
                    predicate=CORE_PREDICATE_CATALOG.create_spec(
                        "ui.toggle_state", toggle_state
                    ),
                )
            )
        if not criteria or entities_sha:
            criteria.insert(
                0,
                SuccessCriterion(
                    name="task_completed",
                    description=redacted_obj
                    or "Task objective visible on final screen",
                    verification="vlm_judge",
                    required=True,
                ),
            )
        return GoalContract(
            task_hash=compute_task_hash(text),
            redacted_objective=redacted_obj,
            objective_length=len(text),
            success_criteria=criteria,
            # Carry the constraint clauses the requirement extractor found, via
            # the same function it uses, so the two sides agree by construction.
            # Leaving this empty made every task containing 不要/只能/only
            # permanently inadequate.
            constraints=constraint_spans(text),
            non_goals=[],
            target_app_hint=app_hint,
            target_activity_hint=None,
            ordinal=ordinal,
            entities_sha=entities_sha,
            verification_strategy="vlm_judge_at_finish",
            stop_conditions={},
            compile_status="compiled",
            compile_source="heuristic",
            compile_attempts=0,
            # W2 T2: the heuristic fallback has no task plan — the whole
            # contract IS the path. ``None`` is the legal degraded state that
            # keeps every existing behavior unchanged.
            task_plan=None,
        )


# ----------------------------------------------------------------------
# External compiler — benchmark/eval injection
# ----------------------------------------------------------------------


class ExternalGoalCompiler:
    """Return a pre-built contract (benchmark or user-provided)."""

    def __init__(self, contract: GoalContract) -> None:
        self._contract = contract

    def compile(
        self, *, task: str, hints: dict[str, Any] | None = None
    ) -> GoalContract:
        from dataclasses import replace

        task_plan = self._contract.task_plan
        if task_plan is not None:
            errors = task_plan_validation_errors(
                task_plan,
                criterion_names=[item.name for item in self._contract.success_criteria],
                criteria={
                    item.name: item for item in self._contract.success_criteria
                },
            )
            if errors:
                # W2 T2: an external plan is adopted only when it validates;
                # otherwise it degrades to ``None`` (plan is belief-only and
                # must never fail an otherwise usable contract).
                task_plan = None
        return replace(
            self._contract,
            compile_status="user_override",
            compile_source="external",
            task_plan=task_plan,
        )


# ----------------------------------------------------------------------
# LLM compiler — structured output, one call, retry once on parse failure
# ----------------------------------------------------------------------


GOAL_COMPILER_SYSTEM_PROMPT_CN = """你是一个任务目标编译器。你的职责是把用户的自然语言任务编译成一个声明式目标契约（JSON），包含可验证的成功标准、约束、非目标和一个任务阶段规划（task_plan）。

你必须只输出一个 JSON 对象，不要 Markdown 或多余文本：
{
  "objective": "用户目标的脱敏重述（去除手机号/邮箱等隐私）",
  "success_criteria": [
    {"name": "criterion_id", "description": "可观察的终态条件描述", "verification": "accessibility_text_match|object_hash_match|object_rank_match|app_or_activity_match|focus_or_keyboard|toggle_state_match|vlm_judge|external_probe", "required": true}
  ],
  "constraints": ["约束1", "约束2"],
  "non_goals": ["非目标1"],
  "target_app_hint": "canonical AppRegistry identity or null",
  "ordinal": null,
  "task_plan": [
    {"objective": "页面级目标（一句话）", "done_criteria": ["契约中 success_criteria 的名字"], "fallback": "该阶段卡住时的兜底策略（一句话）"}
  ]
}

verification 枚举说明：
- accessibility_text_match: 屏幕上可观察到特定文本（在 description 里逐字写出预期可见文本）
- object_hash_match: 选中的 UI 对象 hash 匹配
- object_rank_match: 选中第 ordinal 个列表项
- app_or_activity_match: 目标 app 或 activity 在前台
- focus_or_keyboard: 输入框聚焦或键盘可见
- toggle_state_match: 目标开关处于指定状态（开启/关闭）
- vlm_judge: 需要视觉判断（必须在 finish 时点名该 criterion 并引用屏幕证据）
- external_probe: 外部程序化探针

规则：
- 至少 1 条 required criterion
- criterion name 必须唯一、是合法标识符（字母数字下划线）
- 隐私信息（手机号、邮箱、密钥）不要写进 objective/description，用 <redacted> 替代
- 如果任务涉及"第N个"结果，设 ordinal 并用 object_rank_match
- 如果任务是把某个开关打开或关闭，用 toggle_state_match 描述目标开关状态
- 如果任务的完成条件需要语义判断（某个目标内容出现/生效，而非仅 app 前台或列表序号），
  必须额外给出一条 required 的 vlm_judge criterion 描述该终态；不要只用 app_or_activity_match 代替
- vlm_judge 标准的 description 必须描述**屏幕上可观察的具体内容**，不能只写抽象状态（如“任务完成”“目标已达成”）。
  写法示例：“出现含‘银石’字样的卡片”“设置页显示‘已开启’开关”。描述中给出具体的屏幕文本或元素，
  验收模型才能据实点名该标准并引用屏幕证据
task_plan 规则：
- 产出 3-6 个阶段，按执行顺序排列；阶段目标必须是**页面级**表述（如“进入某 UP 主主页”“找到目标视频并打开”），
  禁止控件/坐标级描述（一屏就碎）
- done_criteria 里的每个名字必须逐字等于本契约 success_criteria 中某条 criterion 的 name，禁止新造名字
- 每阶段至少包含一条**非恒真**的完成信号：不能只用 app_or_activity_match / app 前台类标准构成全部 done_criteria
  （否则阶段会在任务毫无进展时虚推进）；vlm_judge、accessibility_text_match 等真实观察类标准都可以
- fallback 写该阶段卡住时的一句兜底策略（如“返回上一页重试”“先滚动寻找目标”）；不写则留空字符串
"""

GOAL_COMPILER_SYSTEM_PROMPT_EN = """You are a task goal compiler. Convert the user's natural-language task into a declarative goal contract (JSON) with verifiable success criteria, constraints, non-goals, and a task-stage plan (task_plan).

Output exactly one JSON object, no Markdown:
{
  "objective": "privacy-redacted restatement of the user goal",
  "success_criteria": [
    {"name": "criterion_id", "description": "observable terminal condition", "verification": "accessibility_text_match|object_hash_match|object_rank_match|app_or_activity_match|focus_or_keyboard|toggle_state_match|vlm_judge|external_probe", "required": true}
  ],
  "constraints": ["constraint1"],
  "non_goals": ["non_goal1"],
  "target_app_hint": "canonical AppRegistry identity or null",
  "ordinal": null,
  "task_plan": [
    {"objective": "page-level goal (one sentence)", "done_criteria": ["a success_criteria name from this contract"], "fallback": "one-sentence recovery strategy if this stage stalls"}
  ]
}

Rules:
- At least 1 required criterion; criterion names must be unique identifiers
- Replace private info (phone/email/keys) with <redacted>
- Use object_rank_match with ordinal for "the Nth result" tasks
- Use toggle_state_match when the task turns a switch on or off
- When completion requires semantic judgement (some target content present/effective,
  not merely app foreground or a list rank), add a required vlm_judge criterion
  describing that terminal state; do NOT substitute app_or_activity_match for it
- vlm_judge descriptions MUST name concrete on-screen content, not an abstract
  status ("task complete" / "goal achieved" is not enough). Example: "a card
  containing the text 'Silverstone' appears", "the settings page shows an 'Enabled'
  toggle". Name the specific screen text or element so the acceptance model can
  cite real screen evidence for it
task_plan rules:
- Produce 3-6 stages in execution order; each objective MUST be page-level
  (e.g. "reach the UP's home page", "find and open the target video") — never
  control- or coordinate-level descriptions
- Every name in done_criteria MUST equal, verbatim, the name of one criterion in
  this contract's success_criteria; never invent names
- Each stage needs at least one NON-always-true done signal: never build a stage
  whose done_criteria are only app_or_activity_match / app-foreground checks
  (that would let the stage advance with zero task progress); vlm_judge,
  accessibility_text_match and other real-observation criteria are fine
- fallback is a one-sentence recovery strategy for when this stage stalls
  (e.g. "go back and retry", "scroll to find the target"); empty string if none
"""


class LLMGoalCompiler:
    """Compile a goal contract via one structured-output LLM call.

    Retries once on parse failure.  Returns ``compile_status="failed"`` if
    both attempts fail; the caller (goal_node) will then fall back to
    HeuristicGoalCompiler.
    """

    def __init__(
        self,
        model_client: Any,
        lang: str = "cn",
        retry_limit: int = 1,
    ) -> None:
        self._model = model_client
        self._lang = lang
        self._retry_limit = max(0, retry_limit)

    def compile(
        self, *, task: str, hints: dict[str, Any] | None = None
    ) -> GoalContract:
        import json

        from phone_agent.model.client import MessageBuilder, ModelParseError

        text = str(task or "").strip()
        system_prompt = (
            GOAL_COMPILER_SYSTEM_PROMPT_EN
            if self._lang == "en"
            else GOAL_COMPILER_SYSTEM_PROMPT_CN
        )
        system_prompt = "\n\n".join(
            [system_prompt, get_app_registry_summary(lang=self._lang)]
        )
        messages = [
            MessageBuilder.create_system_message(system_prompt),
            MessageBuilder.create_user_message(text=f"Task: {text}"),
        ]

        attempts = 0
        while attempts <= self._retry_limit:
            attempts += 1
            try:
                response = self._model.request(
                    messages, output_mode="json_schema", validate_action=False
                )
                raw = response.action.strip()
                data = json.loads(raw)
                contract = self._parse_compiled_contract(data, task=text)
                contract = _with_compile_meta(contract, source="llm", attempts=attempts)
                return contract
            except (
                json.JSONDecodeError,
                ValueError,
                ModelParseError,
                TypeError,
            ):
                continue
            except Exception:
                continue

        # Both attempts failed — return a failed sentinel; goal_node falls back.
        return GoalContract(
            task_hash=compute_task_hash(text),
            redacted_objective=redact_objective(text),
            objective_length=len(text),
            success_criteria=[],
            verification_strategy="vlm_judge_at_finish",
            compile_status="failed",
            compile_source="llm",
            compile_attempts=attempts,
        )

    def _parse_compiled_contract(
        self, data: dict[str, Any], *, task: str
    ) -> GoalContract:
        if not isinstance(data, dict):
            raise ValueError("compiler output is not a dict")

        raw_criteria = data.get("success_criteria")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            raise ValueError("success_criteria must be a non-empty list")

        seen_names: set[str] = set()
        criteria: list[SuccessCriterion] = []
        for item in raw_criteria:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid criterion name: {name!r}")
            if name in seen_names:
                raise ValueError(f"duplicate criterion name: {name}")
            seen_names.add(name)

            verification = str(item.get("verification") or "vlm_judge")
            if verification not in VALID_VERIFICATIONS:
                raise ValueError(f"invalid verification: {verification!r}")

            description = str(item.get("description") or "")
            # Redact description inline for prompt/trace safety
            from phone_agent.graph.context import redact_context_text

            description = redact_context_text(description)[:300]

            criteria.append(
                SuccessCriterion(
                    name=name,
                    description=description,
                    verification=verification,  # type: ignore[arg-type]
                    required=bool(item.get("required", True)),
                    probe_id=item.get("probe_id"),
                )
            )

        if not criteria:
            raise ValueError("no valid criteria parsed")

        ordinal = data.get("ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool):
            ordinal = None

        target_app_hint = None
        raw_app_hint = data.get("target_app_hint")
        if isinstance(raw_app_hint, str) and raw_app_hint.strip():
            app_resolution = DEFAULT_APP_REGISTRY.resolve_term(raw_app_hint)
            if app_resolution.status != "resolved" or app_resolution.identity is None:
                raise ValueError(
                    f"unresolvable target_app_hint: {app_resolution.status}"
                )
            target_app_hint = app_resolution.identity.canonical_id

        entities_sha = _extract_entities_sha(task)
        entity_spans = _extract_entity_spans(task)
        entity_span = entity_spans[0] if entity_spans else None
        toggle_state = parse_toggle_intent(task)
        if toggle_state is not None and not any(
            item.verification == "toggle_state_match" for item in criteria
        ):
            # Toggle tasks need a programmatic state criterion; models routinely
            # emit only app-foreground criteria, which would let "设置页在前台"
            # stand in for "开关已关闭".
            criteria.append(
                SuccessCriterion(
                    name="toggle_state_reached",
                    description=(
                        f"Target toggle is {'on' if toggle_state else 'off'}"
                    ),
                    verification="toggle_state_match",
                    required=True,
                )
            )
        if entities_sha and not any(
            item.verification == "vlm_judge" and item.required for item in criteria
        ):
            # Entity-bearing tasks need a semantic terminal criterion; models
            # frequently emit only app-foreground/rank criteria. Synthesize
            # the vlm_judge fallback so adequacy does not reject an otherwise
            # reasonable contract (mirrors HeuristicGoalCompiler).
            from phone_agent.graph.goal import redact_objective as _redact

            criteria.append(
                SuccessCriterion(
                    name="task_objective_achieved",
                    description=_redact(task)[:300]
                    or "Task objective visible on final screen",
                    verification="vlm_judge",
                    required=True,
                )
            )
        criteria = _attach_core_predicates(
            criteria,
            target_app_hint=target_app_hint,
            ordinal=ordinal,
            entity_span=entity_span,
            toggle_state=toggle_state,
        )

        task_plan = _parse_task_plan(
            data.get("task_plan"),
            criterion_names=[item.name for item in criteria],
            criteria={item.name: item for item in criteria},
        )

        return GoalContract(
            task_hash=compute_task_hash(task),
            redacted_objective=redact_objective(str(data.get("objective") or task)),
            objective_length=len(str(task)),
            success_criteria=criteria,
            constraints=[
                str(c) for c in (data.get("constraints") or []) if isinstance(c, str)
            ][:8],
            non_goals=[
                str(ng) for ng in (data.get("non_goals") or []) if isinstance(ng, str)
            ][:8],
            target_app_hint=target_app_hint,
            target_activity_hint=None,
            ordinal=ordinal,
            entities_sha=entities_sha,
            verification_strategy="hybrid",
            stop_conditions={},
            compile_status="compiled",
            compile_source="llm",
            compile_attempts=0,
            task_plan=task_plan,
        )


def _parse_task_plan(
    value: Any,
    *,
    criterion_names: list[str],
    criteria: dict[str, SuccessCriterion],
) -> tuple[TaskStage, ...] | None:
    """Parse + validate the LLM's ``task_plan`` section (W2 T2).

    A missing or empty section is legal (plan is optional; ``None`` degrades
    gracefully). An ill-formed section raises ``ValueError`` so the compile
    fails and the chain falls back to Heuristic (``task_plan=None``): unknown
    criterion names, duplicate ids/indices, or a stage whose done criteria are
    all always-true auto standards (trivial-only) are all rejected.
    """

    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("task_plan must be a list")
    if not value:
        return None
    if len(value) < 3 or len(value) > 6:
        raise ValueError(f"task_plan stage count out of range: {len(value)}")
    stages: list[TaskStage] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"task_plan stage {index} is not an object")
        stage = TaskStage(
            stage_id=str(item.get("stage_id") or f"stage_{index + 1}"),
            objective=str(item.get("objective") or ""),
            done_criteria=tuple(
                str(name) for name in (item.get("done_criteria") or []) if name
            ),
            fallback=str(item.get("fallback") or ""),
            index=index,
        )
        stages.append(stage)
    errors = task_plan_validation_errors(
        tuple(stages),
        criterion_names=criterion_names,
        criteria=criteria,
    )
    if errors:
        raise ValueError(f"invalid task_plan: {','.join(errors)}")
    return tuple(stages)


def _with_compile_meta(
    contract: GoalContract, *, source: str, attempts: int
) -> GoalContract:
    from dataclasses import replace

    return replace(contract, compile_source=source, compile_attempts=attempts)


def _attach_core_predicates(
    criteria: list[SuccessCriterion],
    *,
    target_app_hint: str | None,
    ordinal: int | None,
    entity_span: str | None = None,
    toggle_state: bool | None = None,
) -> list[SuccessCriterion]:
    """Explicitly migrate deterministic legacy compiler criteria to predicates."""

    from dataclasses import replace

    migrated: list[SuccessCriterion] = []
    for criterion in criteria:
        if criterion.predicate is not None:
            migrated.append(criterion)
            continue
        predicate = None
        if criterion.verification == "app_or_activity_match" and target_app_hint:
            predicate = CORE_PREDICATE_CATALOG.create_spec(
                "app.foreground_identity", target_app_hint
            )
        elif criterion.verification == "object_rank_match" and ordinal is not None:
            predicate = CORE_PREDICATE_CATALOG.create_spec("ui.object_rank", ordinal)
        elif criterion.verification == "focus_or_keyboard":
            predicate = CORE_PREDICATE_CATALOG.create_spec("ui.focused", True)
        elif criterion.verification == "toggle_state_match" and toggle_state is not None:
            predicate = CORE_PREDICATE_CATALOG.create_spec(
                "ui.toggle_state", toggle_state
            )
        elif criterion.verification == "accessibility_text_match":
            match = LEGACY_SHA256_STUB_PATTERN.search(criterion.description)
            if match:
                predicate = CORE_PREDICATE_CATALOG.create_spec(
                    "ui.text_hash_present", match.group(1).casefold()
                )
            else:
                expected_text = _quoted_span(criterion.description)
                if expected_text is None and raw_text_binding_is_observable(entity_span):
                    expected_text = entity_span
                if expected_text:
                    predicate = CORE_PREDICATE_CATALOG.create_spec(
                        "semantic.entity_matches", expected_text
                    )
        migrated.append(replace(criterion, predicate=predicate))
    return migrated


def _quoted_span(description: str) -> str | None:
    """Extract the most specific non-empty literal from supported quote pairs."""

    candidates: list[str] = []
    for left, right in (("“", "”"), ('"', '"'), ("《", "》"), ("「", "」")):
        candidates.extend(
            item.strip()
            for item in re.findall(
                rf"{re.escape(left)}([^\n]*?){re.escape(right)}", description
            )
            if item.strip()
        )
    return min(candidates, key=len) if candidates else None


# ----------------------------------------------------------------------
# Compilation chain entry point
# ----------------------------------------------------------------------


def compile_goal_contract(
    state: dict[str, Any], config: dict[str, Any]
) -> GoalContract:
    """Run the compilation chain: External > LLM > Heuristic.

    Returns a GoalContract with ``compile_status`` in {"compiled", "user_override"}.
    Never returns ``compile_status="failed"`` — falls back to Heuristic on LLM failure.
    """
    configurable = config.get("configurable", {}) if config else {}
    task = str(state.get("task") or "")
    lang = str(state.get("lang") or "cn")
    retry_limit = int(configurable.get("goal_compile_retry", 1) or 1)

    # 1. External override (benchmark/eval)
    override = configurable.get("task_goal_contract_override")
    requirement_override = configurable.get("task_requirement_set_override")
    if isinstance(override, GoalContract):
        contract = ExternalGoalCompiler(override).compile(task=task)
        return _validate_external_override(
            contract, task, requirement_override, configurable
        )
    if isinstance(override, dict):
        try:
            contract = GoalContract.from_dict(override)
        except Exception as exc:
            raise GoalCompilationError(
                "external_goal_invalid", "external goal override is invalid"
            ) from exc
        return _validate_external_override(
            ExternalGoalCompiler(contract).compile(task=task),
            task,
            requirement_override,
            configurable,
        )

    # 2. LLM compiler (if model_client available)
    model_client = configurable.get("model_client")
    if model_client is not None:
        llm_compiler = LLMGoalCompiler(model_client, lang=lang, retry_limit=retry_limit)
        contract = llm_compiler.compile(task=task)
        if contract.compile_status == "compiled":
            return contract
        # LLM failed — fall through to heuristic fallback
        heuristic = HeuristicGoalCompiler().compile(task=task)
        from dataclasses import replace

        return replace(heuristic, compile_source="heuristic_fallback")

    # 3. No model client — use heuristic directly (not a "fallback")
    return HeuristicGoalCompiler().compile(task=task)


def _validate_external_override(
    contract: GoalContract,
    task: str,
    requirement_override: Any,
    configurable: dict[str, Any],
) -> GoalContract:
    if configurable.get("allow_legacy_goal_override_for_tests") is True:
        return contract
    if isinstance(requirement_override, TaskRequirementSet):
        requirements = requirement_override
    elif isinstance(requirement_override, dict):
        requirements = TaskRequirementSet.from_safe_projection(requirement_override)
    else:
        raise GoalCompilationError(
            "external_goal_requirements_missing",
            "external goal override requires an independently supplied requirement set",
        )
    extracted = TaskRequirementExtractor().extract(task)
    if requirements.task_hash != extracted.task_hash:
        raise GoalCompilationError(
            "external_requirement_binding_mismatch",
            "external requirement set is not bound to the current raw task",
        )
    adequacy = ContractAdequacyValidator().validate(requirements, contract)
    if adequacy.status != "adequate":
        raise GoalCompilationError(
            "external_goal_inadequate",
            f"external goal does not cover task requirements: {','.join(adequacy.reason_codes)}",
        )
    return contract
