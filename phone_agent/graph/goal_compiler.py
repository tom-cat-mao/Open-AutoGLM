"""Goal contract compilers: External > LLM > Heuristic fallback chain.

The compiler is invoked once at step 0 (by goal_node) and produces a
declarative GoalContract.  On LLM failure, it falls back to a heuristic
weak contract (vlm_judge_at_finish) rather than terminating the task.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Protocol

from phone_agent.config.apps import DEFAULT_APP_REGISTRY, get_app_registry_summary
from phone_agent.graph.goal import (
    GoalContract,
    SuccessCriterion,
    VALID_VERIFICATIONS,
    compute_task_hash,
    redact_objective,
)
from phone_agent.graph.goal_requirements import (
    ContractAdequacyValidator,
    TaskRequirementExtractor,
    TaskRequirementSet,
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

ORDINAL_PATTERNS: tuple[tuple[str, int], ...] = (
    ("第一个", 1),
    ("第1个", 1),
    ("第一", 1),
    ("第二个", 2),
    ("第2个", 2),
    ("第二", 2),
    ("第三个", 3),
    ("第3个", 3),
    ("第三", 3),
    ("第四个", 4),
    ("第4个", 4),
    ("第四", 4),
    ("第五个", 5),
    ("第5个", 5),
    ("第五", 5),
)

MAX_ENTITY_COUNT = 4


def _detect_app_hint(text: str) -> str | None:
    resolution = DEFAULT_APP_REGISTRY.resolve_text(text)
    if resolution.status != "resolved" or resolution.identity is None:
        return None
    return resolution.identity.canonical_id


def _detect_ordinal(text: str) -> int | None:
    for pattern, value in ORDINAL_PATTERNS:
        if pattern in text:
            return value
    match = re.search(r"第\s*([1-9]\d*)\s*(?:个|条|项|部|集)?", text)
    if match:
        return int(match.group(1))
    return None


def _extract_entities_sha(text: str) -> list[str]:
    normalized = _remove_known_goal_terms(text)
    candidates = []
    for chunk in re.split(r"[\s,，。.!！?？/\\]+", normalized):
        cleaned = chunk.strip("'\"“”‘’《》<>（）()[]【】")
        if len(cleaned) < 2:
            continue
        if re.fullmatch(r"第?\d+个?", cleaned):
            continue
        candidates.append(cleaned)
    return [
        hashlib.sha256(item.encode("utf-8")).hexdigest()[:12]
        for item in candidates[:MAX_ENTITY_COUNT]
    ]


def _remove_known_goal_terms(text: str) -> str:
    cleaned = str(text or "")
    resolution = DEFAULT_APP_REGISTRY.resolve_text(cleaned)
    if resolution.status == "resolved" and resolution.identity is not None:
        for term in sorted(resolution.identity.aliases, key=len, reverse=True):
            cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    for pattern, _value in ORDINAL_PATTERNS:
        cleaned = cleaned.replace(pattern, " ")
    for term in (
        "去",
        "看",
        "打开",
        "搜索",
        "查找",
        "播放",
        "进入",
        "视频",
        "watch",
        "video",
        "play",
        "open",
        "search",
    ):
        cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


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
            constraints=[],
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

        return replace(
            self._contract,
            compile_status="user_override",
            compile_source="external",
        )


# ----------------------------------------------------------------------
# LLM compiler — structured output, one call, retry once on parse failure
# ----------------------------------------------------------------------


GOAL_COMPILER_SYSTEM_PROMPT_CN = """你是一个任务目标编译器。你的职责是把用户的自然语言任务编译成一个声明式目标契约（JSON），包含可验证的成功标准、约束和非目标。

你必须只输出一个 JSON 对象，不要 Markdown 或多余文本：
{
  "objective": "用户目标的脱敏重述（去除手机号/邮箱等隐私）",
  "success_criteria": [
    {"name": "criterion_id", "description": "可观察的终态条件描述", "verification": "accessibility_text_match|object_hash_match|object_rank_match|app_or_activity_match|focus_or_keyboard|vlm_judge|external_probe", "required": true}
  ],
  "constraints": ["约束1", "约束2"],
  "non_goals": ["非目标1"],
  "target_app_hint": "canonical AppRegistry identity or null",
  "ordinal": null
}

verification 枚举说明：
- accessibility_text_match: 屏幕上可观察到特定文本（在 description 里用 sha256:xxxxxxxxxxxx 形式给出 hash stub）
- object_hash_match: 选中的 UI 对象 hash 匹配
- object_rank_match: 选中第 ordinal 个列表项
- app_or_activity_match: 目标 app 或 activity 在前台
- focus_or_keyboard: 输入框聚焦或键盘可见
- vlm_judge: 需要视觉判断（必须在 finish 时点名该 criterion 并引用屏幕证据）
- external_probe: 外部程序化探针

规则：
- 至少 1 条 required criterion
- criterion name 必须唯一、是合法标识符（字母数字下划线）
- 隐私信息（手机号、邮箱、密钥）不要写进 objective/description，用 <redacted> 替代
- 如果任务涉及"第N个"结果，设 ordinal 并用 object_rank_match
"""

GOAL_COMPILER_SYSTEM_PROMPT_EN = """You are a task goal compiler. Convert the user's natural-language task into a declarative goal contract (JSON) with verifiable success criteria, constraints, and non-goals.

Output exactly one JSON object, no Markdown:
{
  "objective": "privacy-redacted restatement of the user goal",
  "success_criteria": [
    {"name": "criterion_id", "description": "observable terminal condition", "verification": "accessibility_text_match|object_hash_match|object_rank_match|app_or_activity_match|focus_or_keyboard|vlm_judge|external_probe", "required": true}
  ],
  "constraints": ["constraint1"],
  "non_goals": ["non_goal1"],
  "target_app_hint": "canonical AppRegistry identity or null",
  "ordinal": null
}

Rules:
- At least 1 required criterion; criterion names must be unique identifiers
- Replace private info (phone/email/keys) with <redacted>
- Use object_rank_match with ordinal for "the Nth result" tasks
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

        criteria = _attach_core_predicates(
            criteria,
            target_app_hint=target_app_hint,
            ordinal=ordinal,
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
            entities_sha=_extract_entities_sha(task),
            verification_strategy="hybrid",
            stop_conditions={},
            compile_status="compiled",
            compile_source="llm",
            compile_attempts=0,
        )


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
        elif criterion.verification == "accessibility_text_match":
            match = re.search(r"sha256:([a-fA-F0-9]{8,64})", criterion.description)
            if match:
                predicate = CORE_PREDICATE_CATALOG.create_spec(
                    "ui.text_hash_present", match.group(1).casefold()
                )
        migrated.append(replace(criterion, predicate=predicate))
    return migrated


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
