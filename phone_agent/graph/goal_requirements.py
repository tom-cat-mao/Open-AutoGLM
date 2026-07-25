"""Independent task requirement extraction and contract adequacy validation."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import unicodedata
from typing import Any, Literal

from phone_agent.config.apps import DEFAULT_APP_REGISTRY
from phone_agent.graph.goal import GoalContract
from phone_agent.graph.goal_binding import compute_task_binding, normalize_task_binding

OperationKind = Literal[
    "launch", "search", "select", "input", "toggle", "external", "unknown"
]

# Single source of truth for Chinese ordinal numerals. Both the parser
# (parse_chinese_ordinal) and the entity-span stripper (extract_entity_spans)
# read these, so the two can never support different numeral ranges — a
# divergence previously turned 第二十个视频 into the entity span 十个视频.
# Toggle vocabulary, partitioned by the state the task asks for. The
# operation-detection tuple below is derived from these three groups so the
# classifier and parse_toggle_intent can never recognise different terms.
# "切换/toggle" names a flip rather than a target state, so it stays neutral.
_TOGGLE_ON_TERMS: tuple[str, ...] = ("开启", "enable")
_TOGGLE_OFF_TERMS: tuple[str, ...] = ("关闭", "disable")
_TOGGLE_NEUTRAL_TERMS: tuple[str, ...] = ("切换", "toggle")
_TOGGLE_TERMS: tuple[str, ...] = (
    _TOGGLE_ON_TERMS + _TOGGLE_OFF_TERMS + _TOGGLE_NEUTRAL_TERMS
)

_CJK_DIGITS: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CJK_DIGIT_CLASS = "".join(_CJK_DIGITS)
_ORDINAL_COUNTER = r"(?:\s*(?:个|条|项|部|集))?"
_ORDINAL_COMPOUND_PATTERN = re.compile(
    rf"第\s*([{_CJK_DIGIT_CLASS}]?)十([{_CJK_DIGIT_CLASS}]?)"
)
_ORDINAL_SIMPLE_PATTERN = re.compile(rf"第\s*([{_CJK_DIGIT_CLASS}]){_ORDINAL_COUNTER}")
# Matches any ordinal span for removal: compound (第二十个), simple (第六个),
# and numeric (第20个) forms.
_ORDINAL_SPAN_PATTERN = re.compile(
    rf"第\s*(?:[{_CJK_DIGIT_CLASS}]?十[{_CJK_DIGIT_CLASS}]?"
    rf"|[{_CJK_DIGIT_CLASS}]|[1-9]\d*){_ORDINAL_COUNTER}"
)


@dataclass(frozen=True)
class TaskRequirementSet:
    """Requirements extracted from the raw task, never from a candidate contract."""

    task_hash: str
    operation_kind: OperationKind
    target_entity_hashes: tuple[str, ...]
    target_app_identity: str | None
    ordinal: int | None
    required_terminal_state: str
    constraint_hashes: tuple[str, ...] = ()
    source_span_count: int = 0
    confidence: float = 0.0
    ambiguities: tuple[str, ...] = ()
    extractor_version: str = "task_requirements_v1"

    def safe_projection(self) -> dict[str, Any]:
        """Return a state/trace-safe projection without raw task entities."""

        return {
            "operation_kind": self.operation_kind,
            "target_entity_count": len(self.target_entity_hashes),
            "target_app_identity": self.target_app_identity,
            "ordinal": self.ordinal,
            "required_terminal_state": self.required_terminal_state,
            "constraint_count": len(self.constraint_hashes),
            "source_span_count": self.source_span_count,
            "confidence_bucket": _confidence_bucket(self.confidence),
            "ambiguities": list(self.ambiguities),
            "extractor_version": self.extractor_version,
        }

    @classmethod
    def from_safe_projection(cls, value: dict[str, Any]) -> "TaskRequirementSet":
        """Rehydrate only a trusted caller-supplied requirement projection."""

        confidence = {"high": 0.95, "medium": 0.7, "low": 0.3}.get(
            str(value.get("confidence_bucket")), 0.0
        )
        return cls(
            task_hash=str(value.get("task_hash") or ""),
            operation_kind=str(value.get("operation_kind") or "unknown"),  # type: ignore[arg-type]
            target_entity_hashes=tuple(
                str(item) for item in value.get("target_entity_hashes") or []
            ),
            target_app_identity=value.get("target_app_identity"),
            ordinal=(
                value.get("ordinal") if isinstance(value.get("ordinal"), int) else None
            ),
            required_terminal_state=str(
                value.get("required_terminal_state") or "task_state_observed"
            ),
            constraint_hashes=tuple(
                str(item) for item in value.get("constraint_hashes") or []
            ),
            source_span_count=int(value.get("source_span_count") or 0),
            confidence=confidence,
            ambiguities=tuple(str(item) for item in value.get("ambiguities") or []),
            extractor_version=str(value.get("extractor_version") or "unknown"),
        )


class TaskRequirementExtractor:
    """Deterministic raw-task parser independent of GoalContract compilation."""

    _OPERATIONS: tuple[tuple[OperationKind, tuple[str, ...]], ...] = (
        ("search", ("搜索", "查找", "search", "find")),
        ("input", ("输入", "填写", "type", "enter")),
        ("toggle", _TOGGLE_TERMS),
        ("select", ("打开第", "选择", "播放", "看", "select", "play", "open the")),
        ("launch", ("打开", "启动", "open", "launch")),
    )

    def extract(self, task: str) -> TaskRequirementSet:
        text = unicodedata.normalize("NFKC", str(task or "")).strip()
        lowered = text.casefold()
        operation = next(
            (
                kind
                for kind, terms in self._OPERATIONS
                if any(term.casefold() in lowered for term in terms)
            ),
            "unknown",
        )
        app_resolution = DEFAULT_APP_REGISTRY.resolve_text(text)
        app_identity = (
            app_resolution.identity.canonical_id
            if app_resolution.status == "resolved" and app_resolution.identity
            else None
        )
        ordinal_match = re.search(r"第\s*([1-9]\d*)", text)
        ordinal = int(ordinal_match.group(1)) if ordinal_match else parse_chinese_ordinal(text)
        matched_alias = None
        if app_resolution.identity is not None:
            matched_alias = next(
                (
                    alias
                    for alias in sorted(
                        app_resolution.identity.aliases, key=len, reverse=True
                    )
                    if alias.casefold() in lowered
                ),
                None,
            )
        spans = extract_entity_spans(text, matched_alias, operation)
        constraints = constraint_spans(text)
        ambiguity = []
        if app_resolution.status == "ambiguous":
            ambiguity.append("app_ambiguous")
        # operation_unknown is NOT an ambiguity: an unrecognized verb should
        # fall through to the LLM compiler + vlm_judge fallback rather than
        # terminating the task at the adequacy gate. It only lowers confidence.
        confidence = 0.95 if operation != "unknown" and not ambiguity else 0.45
        return TaskRequirementSet(
            task_hash=compute_task_binding(text),
            operation_kind=operation,
            target_entity_hashes=tuple(_digest(span) for span in spans[:6]),
            target_app_identity=app_identity,
            ordinal=ordinal,
            required_terminal_state=_terminal_state(operation),
            constraint_hashes=tuple(_digest(item) for item in constraints[:6]),
            source_span_count=len(spans),
            confidence=confidence,
            ambiguities=tuple(ambiguity),
        )


@dataclass(frozen=True)
class AdequacyResult:
    status: Literal["adequate", "degraded", "inadequate", "needs_clarification"]
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


# Defects that make a contract structurally unusable: no amount of runtime
# evidence can satisfy it, so proceeding would guarantee a false verdict.
STRUCTURAL_REASON_CODES: frozenset[str] = frozenset(
    {
        "task_binding_mismatch",
        "required_criteria_missing",
        "predicate_unobservable",
        "predicate_domain_mismatch",
    }
)


class ContractAdequacyValidator:
    """Compare independently extracted requirements with a candidate contract.

    Two severities, because the two failure modes are not comparable:

    * **structural** — the contract cannot be satisfied by any observation
      (unbound task, no required criterion, a predicate no provider emits, or
      an expectation in a different value domain than the provider output).
      These are defects in the contract itself and block compilation.
    * **semantic** — a keyword-derived requirement looks uncovered. The
      requirement extractor is a vocabulary heuristic, so this is a *suspicion*,
      not a fact; it degrades the contract (recorded, weaker verification)
      rather than terminating the task. Terminal judgement happens at the
      finish gate, where the screen and device truth are actually available.
    """

    def validate(
        self, requirements: TaskRequirementSet, contract: GoalContract
    ) -> AdequacyResult:
        reasons: list[str] = []
        if requirements.task_hash != contract.task_hash:
            reasons.append("task_binding_mismatch")
        required = [item for item in contract.success_criteria if item.required]
        if not required:
            reasons.append("required_criteria_missing")
        reasons.extend(_predicate_structural_defects(contract))
        if (
            requirements.target_app_identity
            and requirements.target_app_identity != contract.target_app_hint
        ):
            reasons.append("target_app_uncovered")
        if (
            requirements.ordinal is not None
            and contract.ordinal != requirements.ordinal
        ):
            reasons.append("ordinal_uncovered")
        if requirements.target_entity_hashes and not set(
            requirements.target_entity_hashes
        ).intersection(contract.entities_sha):
            reasons.append("target_entities_uncovered")
        if requirements.target_entity_hashes and not (
            any(
                item.predicate is not None
                and item.predicate.predicate_id == "semantic.entity_matches"
                for item in required
            )
            or any(item.verification == "vlm_judge" for item in required)
        ):
            reasons.append("semantic_criterion_missing")
        predicate_ids = {
            item.predicate.predicate_id
            for item in required
            if item.predicate is not None
        }
        has_vlm_judge = any(item.verification == "vlm_judge" for item in required)
        if not _terminal_state_is_covered(
            requirements.required_terminal_state, predicate_ids, has_vlm_judge
        ):
            reasons.append("terminal_state_uncovered")
        if requirements.constraint_hashes:
            contract_constraint_hashes = {
                _digest(item) for item in contract.constraints if str(item).strip()
            }
            if not set(requirements.constraint_hashes).issubset(
                contract_constraint_hashes
            ):
                reasons.append("constraints_uncovered")

        codes = tuple(sorted(set(reasons)))
        # Ambiguity is a property of the task itself, so it outranks defects in
        # the derived contract: when the input is ambiguous everything compiled
        # from it is unreliable, and clarifying the task is the actionable fix.
        if requirements.ambiguities:
            return AdequacyResult(
                "needs_clarification",
                tuple(sorted(set(requirements.ambiguities + codes))),
            )
        if any(code in STRUCTURAL_REASON_CODES for code in codes):
            return AdequacyResult("inadequate", codes)
        if codes:
            return AdequacyResult("degraded", codes)
        return AdequacyResult("adequate")


def _predicate_structural_defects(contract: GoalContract) -> list[str]:
    """Reject predicates that no provider can observe or compare.

    Catches the two impossibilities that previously surfaced only as a finish
    gate that never opened: a predicate with no producer, and an expectation
    whose value domain differs from the provider's output domain (a digest
    expectation can never equal raw screen text).
    """
    from phone_agent.graph.fact_providers import predicate_is_observable
    from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

    defects: list[str] = []
    for criterion in contract.success_criteria:
        predicate = criterion.predicate
        if predicate is None or predicate.expected_value is None:
            continue
        if not predicate_is_observable(predicate.predicate_id):
            defects.append("predicate_unobservable")
            continue
        definition = CORE_PREDICATE_CATALOG.get(predicate.predicate_id)
        if not _expected_value_in_domain(
            definition.value_domain, predicate.expected_value
        ):
            defects.append("predicate_domain_mismatch")
    return defects


def _expected_value_in_domain(domain: str, value: Any) -> bool:
    """Whether an expected value plausibly belongs to the declared domain."""

    if domain == "digest":
        return isinstance(value, str) and bool(
            re.fullmatch(r"[0-9a-f]{8,64}", value.casefold())
        )
    if domain == "raw_text":
        # Raw screen text is never a bare digest: that combination is exactly
        # the semantic.entity_matches regression this check exists to catch.
        values = value if isinstance(value, (list, tuple)) else [value]
        return not any(
            isinstance(item, str)
            and re.fullmatch(r"[0-9a-f]{12,64}", item.casefold())
            for item in values
        )
    return True


def extract_entity_spans(
    text: str, app_alias: str | None, operation: OperationKind
) -> list[str]:
    """Single source of truth for entity extraction from a raw task string.

    Used by BOTH TaskRequirementExtractor (requirements side) and the goal
    compilers (contract side) so that requirement entity hashes and contract
    entities_sha are computed over the same spans — divergent extraction was
    the root cause of spurious `target_entities_uncovered` rejections.
    """
    cleaned = text
    for constraint in constraint_spans(text):
        cleaned = cleaned.replace(constraint, " ")
    if app_alias:
        cleaned = re.sub(re.escape(app_alias), " ", cleaned, flags=re.IGNORECASE)
    # Strip ALL operation vocabularies, not just the detected operation's.
    # Compound tasks ("搜索…并播放…") contain verbs from multiple operation
    # kinds; stripping only the primary kind leaves foreign verbs glued to
    # entity spans and breaks requirement/contract hash agreement.
    for _kind, terms in TaskRequirementExtractor._OPERATIONS:
        for term in terms:
            cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    # Ordinal spans (第一个 / 第二十个 / 第20个) — one pattern shared with
    # parse_chinese_ordinal so numeral vocabularies cannot drift apart.
    cleaned = _ORDINAL_SPAN_PATTERN.sub(" ", cleaned)
    spans = []
    for item in re.split(r"[\s,，。.!！?？/\\]+", cleaned):
        span = _trim_span(item)
        if len(span) >= 2:
            spans.append(span)
    return spans


# Conjunctions/particles that survive verb stripping and stay glued to an
# entity span ("搜索猫咪视频并播放" -> "猫咪视频并"). They are only ever
# trailing/leading residue, so they are trimmed at the edges rather than
# split on: 和/并 occur INSIDE real app names (和平精英, 并读新闻), so
# splitting would corrupt legitimate entities.
_SPAN_EDGE_PARTICLES = ("并且", "并", "然后", "接着", "以及", "和", "还有", "再", "的")
_SPAN_EDGE_PUNCTUATION = "'\"“”‘’《》<>（）()[]【】、:：;；-—_"


def _trim_span(value: str) -> str:
    """Strip surrounding punctuation and trailing conjunction residue."""

    span = value.strip(_SPAN_EDGE_PUNCTUATION).strip()
    changed = True
    while changed and span:
        changed = False
        for particle in _SPAN_EDGE_PARTICLES:
            # Only trim when a real entity remains — 和平精英 must survive.
            if span.endswith(particle) and len(span) > len(particle) + 1:
                span = span[: -len(particle)].strip(_SPAN_EDGE_PUNCTUATION).strip()
                changed = True
    return span


def _terminal_state(operation: OperationKind) -> str:
    return {
        "launch": "target_app_foreground",
        "search": "search_results_visible",
        "select": "selected_target_visible",
        "input": "input_value_visible",
        "toggle": "toggle_state_visible",
        "external": "external_effect_confirmed",
        "unknown": "task_state_observed",
    }[operation]


def _terminal_state_is_covered(
    terminal_state: str, predicate_ids: set[str], has_vlm_judge: bool = False
) -> bool:
    accepted = {
        "target_app_foreground": {
            "app.foreground_package",
            "app.foreground_activity",
            "app.foreground_identity",
        },
        "search_results_visible": {
            "ui.text_equals",
            "ui.text_hash_present",
            "ui.collection_contains",
            "semantic.entity_matches",
        },
        "selected_target_visible": {
            "ui.object_selected",
            "ui.object_rank",
            "semantic.entity_matches",
        },
        "input_value_visible": {
            "ui.focused",
            "ui.value_equals",
            "ui.text_equals",
        },
        "toggle_state_visible": {"ui.toggle_state"},
        "external_effect_confirmed": {"external.effect_confirmed"},
        "task_state_observed": set(),
    }
    # vlm_judge is the sanctioned fallback for semantic terminal states that
    # no programmatic predicate can cover (AGENTS.md P0-13a). It never
    # satisfies toggle/external states, which require programmatic signals.
    vlm_judge_coverable = {
        "search_results_visible",
        "selected_target_visible",
        "input_value_visible",
    }
    if terminal_state in vlm_judge_coverable and has_vlm_judge:
        return True
    required = accepted.get(terminal_state, set())
    return terminal_state == "task_state_observed" or bool(
        required.intersection(predicate_ids)
    )


def constraint_spans(text: str) -> list[str]:
    """Constraint clauses in a raw task ("不要加糖", "only use wifi").

    Shared by TaskRequirementExtractor and the goal compilers so requirement
    constraint hashes and contract constraints are computed over identical
    spans — divergence made every constrained task permanently inadequate.
    """
    markers = (
        "不要",
        "不得",
        "只能",
        "仅限",
        "do not",
        "must not",
        "without",
        "only",
    )
    spans: list[str] = []
    for clause in re.split(r"[，,。.!！;；]+", text):
        normalized = clause.strip()
        if normalized and any(marker in normalized.casefold() for marker in markers):
            spans.append(normalized)
    return spans


def parse_toggle_intent(text: str) -> bool | None:
    """Return the desired toggle state for a toggle task, or None if unclear.

    "切换/toggle" states a flip, not a target, so it yields None and the
    contract falls through to semantic judgement instead of asserting a
    state the task never specified.
    """
    lowered = unicodedata.normalize("NFKC", str(text or "")).casefold()
    for term in _TOGGLE_ON_TERMS:
        if term.casefold() in lowered:
            return True
    for term in _TOGGLE_OFF_TERMS:
        if term.casefold() in lowered:
            return False
    return None


def parse_chinese_ordinal(text: str) -> int | None:
    """Parse 第N个/第N ordinals including compound numerals (十二/二十/二十一).

    Substring matching ("第{word}" in text) misreads 第二十个 as 2; this
    parses the full numeral span first, then falls back to single words.
    """
    compound = _ORDINAL_COMPOUND_PATTERN.search(text)
    if compound:
        tens = _CJK_DIGITS.get(compound.group(1), 1) * 10
        ones = _CJK_DIGITS.get(compound.group(2), 0)
        return tens + ones
    simple = _ORDINAL_SIMPLE_PATTERN.search(text)
    if simple:
        return _CJK_DIGITS[simple.group(1)]
    return None


def _digest(value: str, length: int = 12) -> str:
    return hashlib.sha256(normalize_task_binding(value).encode("utf-8")).hexdigest()[
        :length
    ]


def _confidence_bucket(value: float) -> str:
    if value >= 0.9:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"
