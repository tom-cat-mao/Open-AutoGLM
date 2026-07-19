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
        ("toggle", ("开启", "关闭", "切换", "enable", "disable", "toggle")),
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
        ordinal = int(ordinal_match.group(1)) if ordinal_match else _word_ordinal(text)
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
        spans = _semantic_spans(text, matched_alias, operation)
        constraints = _constraint_spans(text)
        ambiguity = []
        if operation == "unknown":
            ambiguity.append("operation_unknown")
        if app_resolution.status == "ambiguous":
            ambiguity.append("app_ambiguous")
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
    status: Literal["adequate", "inadequate", "needs_clarification"]
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


class ContractAdequacyValidator:
    """Compare independently extracted requirements with a candidate contract."""

    def validate(
        self, requirements: TaskRequirementSet, contract: GoalContract
    ) -> AdequacyResult:
        reasons: list[str] = []
        if requirements.task_hash != contract.task_hash:
            reasons.append("task_binding_mismatch")
        required = [item for item in contract.success_criteria if item.required]
        if not required:
            reasons.append("required_criteria_missing")
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
        if requirements.target_entity_hashes and not any(
            item.predicate is not None
            and item.predicate.predicate_id == "semantic.entity_matches"
            for item in required
        ):
            reasons.append("semantic_criterion_missing")
        predicate_ids = {
            item.predicate.predicate_id
            for item in required
            if item.predicate is not None
        }
        if not _terminal_state_is_covered(
            requirements.required_terminal_state, predicate_ids
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
        if requirements.ambiguities:
            return AdequacyResult(
                "needs_clarification",
                tuple(sorted(set(requirements.ambiguities + tuple(reasons)))),
            )
        if reasons:
            return AdequacyResult("inadequate", tuple(sorted(set(reasons))))
        return AdequacyResult("adequate")


def _semantic_spans(
    text: str, app_alias: str | None, operation: OperationKind
) -> list[str]:
    cleaned = text
    for constraint in _constraint_spans(text):
        cleaned = cleaned.replace(constraint, " ")
    if app_alias:
        cleaned = re.sub(re.escape(app_alias), " ", cleaned, flags=re.IGNORECASE)
    for kind, terms in TaskRequirementExtractor._OPERATIONS:
        if kind == operation:
            for term in terms:
                cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"第\s*[1-9]\d*\s*(?:个|条|项|部|集)?", " ", cleaned)
    return [
        item.strip("'\"“”‘’《》<>（）()[]【】")
        for item in re.split(r"[\s,，。.!！?？/\\]+", cleaned)
        if len(item.strip("'\"“”‘’《》<>（）()[]【】")) >= 2
    ]


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


def _terminal_state_is_covered(terminal_state: str, predicate_ids: set[str]) -> bool:
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
    required = accepted.get(terminal_state, set())
    return terminal_state == "task_state_observed" or bool(
        required.intersection(predicate_ids)
    )


def _constraint_spans(text: str) -> list[str]:
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


def _word_ordinal(text: str) -> int | None:
    for word, value in (("一", 1), ("二", 2), ("三", 3), ("四", 4), ("五", 5)):
        if f"第{word}" in text:
            return value
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
