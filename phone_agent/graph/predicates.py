"""Typed predicates, neutral facts, matching, and evidence authority contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping


ValueKind = Literal["string", "boolean", "integer", "string_list", "mapping"]
SourceKind = Literal[
    "accessibility",
    "screen_object",
    "mark",
    "visual_region",
    "whole_screen",
    "external_probe",
    "device",
]
PrivacyClass = Literal["public", "private", "sensitive"]
ProjectionKind = Literal["full", "redacted", "metadata", "omit"]
PersistencePolicy = Literal["runtime_only", "checkpoint_safe"]
MatchStatus = Literal["matched", "contradicted", "unknown"]


@dataclass(frozen=True)
class PrivacyProjection:
    """Required value projection rules for each storage boundary."""

    privacy_class: PrivacyClass
    state: ProjectionKind
    trace: ProjectionKind
    checkpoint: ProjectionKind
    persistence: PersistencePolicy

    def __post_init__(self) -> None:
        if self.persistence == "runtime_only" and self.checkpoint != "omit":
            raise ValueError("runtime-only predicates must be omitted from checkpoints")
        if self.privacy_class != "public" and "full" in {
            self.state,
            self.trace,
            self.checkpoint,
        }:
            raise ValueError("private predicate values cannot cross runtime boundaries")


@dataclass(frozen=True)
class PredicateDefinition:
    """Closed schema and policy declaration for one predicate identifier."""

    predicate_id: str
    value_kind: ValueKind
    allowed_sources: frozenset[SourceKind]
    matcher_id: str
    projection: PrivacyProjection
    whole_screen_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.predicate_id or "." not in self.predicate_id:
            raise ValueError("predicate_id must be a namespaced identifier")
        if not self.allowed_sources:
            raise ValueError("predicate must declare at least one evidence source")
        if self.matcher_id not in Matcher.SUPPORTED_MATCHERS:
            raise ValueError(f"unknown matcher: {self.matcher_id}")
        if self.whole_screen_allowed and "whole_screen" not in self.allowed_sources:
            raise ValueError(
                "whole-screen policy requires whole_screen as an allowed source"
            )


@dataclass(frozen=True)
class PredicateSpec:
    """One typed expected assertion bound to a catalog definition."""

    predicate_id: str
    expected_value: Any
    matcher_id: str
    privacy_class: PrivacyClass


@dataclass(frozen=True)
class ExpectedTransition:
    """Typed postconditions scoped to one action."""

    predicates: tuple[PredicateSpec, ...]
    compatibility_source: str | None = None
    schema_version: str = "expected_transition_v1"

    def trace_projection(self) -> dict[str, Any]:
        """Return IDs and policy metadata without runtime expected values."""

        return {
            "schema_version": self.schema_version,
            "compatibility_source": self.compatibility_source,
            "predicates": [
                {
                    "predicate_id": item.predicate_id,
                    "matcher_id": item.matcher_id,
                    "privacy_class": item.privacy_class,
                    "expected_value": "<runtime-only>",
                }
                for item in self.predicates
            ],
        }


@dataclass(frozen=True)
class EvidenceReference:
    """Evidence-only reference; it never grants execution authority."""

    source_kind: SourceKind
    reference_id: str
    screen_id: str | None
    observation_epoch: int
    bbox: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.reference_id):
            raise ValueError("evidence reference_id must be a trace-safe identifier")
        if self.screen_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", self.screen_id
        ):
            raise ValueError("evidence screen_id must be a trace-safe identifier")
        if self.observation_epoch < 0:
            raise ValueError("observation_epoch must be non-negative")
        if self.bbox is not None:
            if self.source_kind != "visual_region":
                raise ValueError("only visual-region evidence may carry a bbox")
            left, top, right, bottom = self.bbox
            if (
                min(self.bbox) < 0
                or max(self.bbox) > 1000
                or left >= right
                or top >= bottom
            ):
                raise ValueError(
                    "visual-region bbox must be valid relative coordinates"
                )

    def trace_projection(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "reference_id": self.reference_id,
            "screen_id": self.screen_id,
            "observation_epoch": self.observation_epoch,
            "bbox": list(self.bbox) if self.bbox is not None else None,
        }


@dataclass(frozen=True)
class ObservedFact:
    """Neutral current-observation fact; match polarity is intentionally absent."""

    predicate_id: str
    observed_value: Any
    confidence: float
    source: SourceKind
    evidence_reference: EvidenceReference
    contract_id: str
    screen_id: str
    observation_epoch: int
    provider_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.evidence_reference.source_kind != self.source:
            raise ValueError("fact source and evidence source must match")
        if self.evidence_reference.observation_epoch != self.observation_epoch:
            raise ValueError("fact and evidence epochs must match")
        if self.evidence_reference.screen_id not in {None, self.screen_id}:
            raise ValueError("fact and evidence screen bindings must match")
        for label, value in (
            ("contract_id", self.contract_id),
            ("screen_id", self.screen_id),
            ("provider_version", self.provider_version),
        ):
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
                raise ValueError(f"fact {label} must be a trace-safe identifier")

    def validate_binding(
        self, *, contract_id: str, screen_id: str, observation_epoch: int
    ) -> None:
        """Reject stale or cross-contract facts before matching."""

        if self.contract_id != contract_id:
            raise ValueError("fact contract binding mismatch")
        if self.screen_id != screen_id or self.observation_epoch != observation_epoch:
            raise ValueError("fact observation binding is stale")

    def trace_projection(self, catalog: "PredicateCatalog") -> dict[str, Any]:
        definition = catalog.get(self.predicate_id)
        return {
            "predicate_id": self.predicate_id,
            "observed_value": _project_value(
                self.observed_value, definition.projection.trace
            ),
            "confidence_bucket": _confidence_bucket(self.confidence),
            "source": self.source,
            "evidence_reference": self.evidence_reference.trace_projection(),
            "contract_id": self.contract_id,
            "screen_id": self.screen_id,
            "observation_epoch": self.observation_epoch,
            "provider_version": self.provider_version,
        }


@dataclass(frozen=True)
class MatchResult:
    status: MatchStatus
    matcher_id: str
    reason_code: str


class Matcher:
    """The only component allowed to derive expected/observed polarity."""

    SUPPORTED_MATCHERS = frozenset(
        {
            "exact",
            "casefold_exact",
            "contains",
            "collection_contains",
            "collection_not_contains",
        }
    )

    @classmethod
    def match(cls, spec: PredicateSpec, fact: ObservedFact) -> MatchResult:
        if spec.predicate_id != fact.predicate_id:
            return MatchResult("unknown", spec.matcher_id, "predicate_mismatch")
        if spec.matcher_id not in cls.SUPPORTED_MATCHERS:
            return MatchResult("unknown", spec.matcher_id, "matcher_unknown")
        expected = spec.expected_value
        observed = fact.observed_value
        if expected is None or observed is None:
            return MatchResult("unknown", spec.matcher_id, "value_missing")
        if spec.matcher_id == "exact":
            matched = type(expected) is type(observed) and expected == observed
        elif spec.matcher_id == "casefold_exact":
            if not isinstance(expected, str) or not isinstance(observed, str):
                return MatchResult("unknown", spec.matcher_id, "value_type_invalid")
            matched = expected.casefold().strip() == observed.casefold().strip()
        elif spec.matcher_id == "contains":
            if not isinstance(expected, str) or not isinstance(observed, str):
                return MatchResult("unknown", spec.matcher_id, "value_type_invalid")
            matched = expected.casefold().strip() in observed.casefold()
        else:
            if not isinstance(expected, (list, tuple, set)) or not isinstance(
                observed, (list, tuple, set)
            ):
                return MatchResult("unknown", spec.matcher_id, "value_type_invalid")
            expected_values = set(expected)
            observed_values = set(observed)
            if spec.matcher_id == "collection_contains":
                matched = expected_values.issubset(observed_values)
            else:
                matched = expected_values.isdisjoint(observed_values)
        return MatchResult(
            "matched" if matched else "contradicted",
            spec.matcher_id,
            "values_match" if matched else "values_conflict",
        )


@dataclass(frozen=True)
class AuthorityRule:
    predicate_id: str
    source: SourceKind
    tier: int
    minimum_confidence: float

    def __post_init__(self) -> None:
        if self.tier < 0 or not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("invalid evidence authority rule")


@dataclass(frozen=True)
class AuthorityResolution:
    status: MatchStatus
    reason_code: str
    source_count: int


class EvidenceAuthorityPolicy:
    """Core-owned source authority; plugins cannot mutate registered rules."""

    def __init__(self, rules: tuple[AuthorityRule, ...]) -> None:
        self._rules = {(rule.predicate_id, rule.source): rule for rule in rules}
        if len(self._rules) != len(rules):
            raise ValueError("duplicate evidence authority rule")

    def authority_for(self, fact: ObservedFact) -> AuthorityRule | None:
        rule = self._rules.get((fact.predicate_id, fact.source))
        if rule is None or fact.confidence < rule.minimum_confidence:
            return None
        return rule

    def highest_authority(
        self, facts: tuple[ObservedFact, ...]
    ) -> tuple[ObservedFact, ...]:
        accepted = [(fact, self.authority_for(fact)) for fact in facts]
        accepted = [(fact, rule) for fact, rule in accepted if rule is not None]
        if not accepted:
            return ()
        highest = max(rule.tier for _, rule in accepted)
        return tuple(fact for fact, rule in accepted if rule.tier == highest)

    def resolve(
        self,
        spec: PredicateSpec,
        facts: tuple[ObservedFact, ...],
        *,
        contract_id: str,
        screen_id: str,
        observation_epoch: int,
    ) -> AuthorityResolution:
        """Resolve current highest-tier facts without allowing weak overrides."""

        current: list[ObservedFact] = []
        for fact in facts:
            try:
                fact.validate_binding(
                    contract_id=contract_id,
                    screen_id=screen_id,
                    observation_epoch=observation_epoch,
                )
            except ValueError:
                continue
            current.append(fact)
        authoritative = self.highest_authority(tuple(current))
        if not authoritative:
            return AuthorityResolution("unknown", "no_authoritative_fact", 0)
        statuses = {Matcher.match(spec, fact).status for fact in authoritative}
        if "unknown" in statuses or len(statuses) != 1:
            return AuthorityResolution(
                "unknown", "same_tier_conflict", len(authoritative)
            )
        return AuthorityResolution(
            statuses.pop(), "authority_resolved", len(authoritative)
        )


class PredicateCatalog:
    """Closed, versioned registry for predicate schemas and privacy policy."""

    def __init__(self, definitions: tuple[PredicateDefinition, ...]) -> None:
        self._definitions = {item.predicate_id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("duplicate predicate_id")

    def get(self, predicate_id: str) -> PredicateDefinition:
        try:
            return self._definitions[predicate_id]
        except KeyError as exc:
            raise ValueError(f"unknown predicate: {predicate_id}") from exc

    def create_spec(self, predicate_id: str, expected_value: Any) -> PredicateSpec:
        definition = self.get(predicate_id)
        _validate_value(definition.value_kind, expected_value)
        return PredicateSpec(
            predicate_id=predicate_id,
            expected_value=expected_value,
            matcher_id=definition.matcher_id,
            privacy_class=definition.projection.privacy_class,
        )

    def validate_fact(self, fact: ObservedFact) -> None:
        definition = self.get(fact.predicate_id)
        if fact.source not in definition.allowed_sources:
            raise ValueError("evidence source is not allowed for predicate")
        if fact.source == "whole_screen" and not definition.whole_screen_allowed:
            raise ValueError("whole-screen evidence is not allowlisted")
        _validate_value(definition.value_kind, fact.observed_value)

    @property
    def definitions(self) -> Mapping[str, PredicateDefinition]:
        return self._definitions


PUBLIC_CHECKPOINT = PrivacyProjection(
    privacy_class="public",
    state="full",
    trace="full",
    checkpoint="full",
    persistence="checkpoint_safe",
)
PRIVATE_RUNTIME = PrivacyProjection(
    privacy_class="private",
    state="metadata",
    trace="metadata",
    checkpoint="omit",
    persistence="runtime_only",
)


def _definition(
    predicate_id: str,
    value_kind: ValueKind,
    sources: set[SourceKind],
    *,
    matcher_id: str = "exact",
    projection: PrivacyProjection = PUBLIC_CHECKPOINT,
    whole_screen_allowed: bool = False,
) -> PredicateDefinition:
    return PredicateDefinition(
        predicate_id=predicate_id,
        value_kind=value_kind,
        allowed_sources=frozenset(sources),
        matcher_id=matcher_id,
        projection=projection,
        whole_screen_allowed=whole_screen_allowed,
    )


CORE_PREDICATE_CATALOG = PredicateCatalog(
    (
        _definition(
            "app.foreground_package", "string", {"device"}, matcher_id="casefold_exact"
        ),
        _definition(
            "app.foreground_activity", "string", {"device"}, matcher_id="casefold_exact"
        ),
        _definition(
            "app.foreground_identity", "string", {"device"}, matcher_id="casefold_exact"
        ),
        _definition("ui.focused", "boolean", {"accessibility", "screen_object"}),
        _definition("ui.keyboard_visible", "boolean", {"accessibility", "device"}),
        _definition(
            "ui.text_equals",
            "string",
            {"accessibility", "visual_region"},
            matcher_id="casefold_exact",
            projection=PRIVATE_RUNTIME,
        ),
        _definition("ui.text_hash_present", "string", {"accessibility"}),
        _definition(
            "ui.reference_text_changed", "boolean", {"accessibility", "visual_region"}
        ),
        _definition("ui.object_present", "string", {"screen_object", "mark"}),
        _definition("ui.object_absent", "string", {"screen_object", "mark"}),
        _definition("ui.object_selected", "string", {"screen_object", "accessibility"}),
        _definition("ui.object_rank", "integer", {"screen_object", "accessibility"}),
        _definition(
            "ui.value_equals",
            "string",
            {"accessibility", "screen_object"},
            matcher_id="casefold_exact",
            projection=PRIVATE_RUNTIME,
        ),
        _definition("ui.value_changed", "boolean", {"accessibility", "screen_object"}),
        _definition("ui.toggle_state", "boolean", {"accessibility", "screen_object"}),
        _definition(
            "ui.collection_contains",
            "string_list",
            {"accessibility", "screen_object"},
            matcher_id="collection_contains",
            projection=PRIVATE_RUNTIME,
        ),
        _definition(
            "ui.collection_not_contains",
            "string_list",
            {"accessibility", "screen_object"},
            matcher_id="collection_not_contains",
            projection=PRIVATE_RUNTIME,
        ),
        _definition(
            "ui.collection_count_changed", "boolean", {"accessibility", "screen_object"}
        ),
        _definition(
            "ui.collection_order",
            "string_list",
            {"accessibility", "screen_object"},
            projection=PRIVATE_RUNTIME,
        ),
        _definition("ui.dialog_open", "boolean", {"accessibility", "screen_object"}),
        _definition("ui.dialog_closed", "boolean", {"accessibility", "screen_object"}),
        _definition("screen.structure_changed", "boolean", {"screen_object"}),
        _definition(
            "screen.content_changed", "boolean", {"screen_object", "visual_region"}
        ),
        _definition(
            "screen.loading_state",
            "string",
            {"accessibility", "screen_object", "visual_region"},
        ),
        _definition(
            "semantic.entity_matches",
            "string",
            {"accessibility", "visual_region", "whole_screen"},
            matcher_id="casefold_exact",
            projection=PRIVATE_RUNTIME,
            whole_screen_allowed=True,
        ),
        _definition(
            "external.effect_confirmed",
            "mapping",
            {"external_probe"},
            projection=PRIVATE_RUNTIME,
        ),
    )
)


def _validate_value(kind: ValueKind, value: Any) -> None:
    valid = {
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "string_list": isinstance(value, (list, tuple))
        and all(isinstance(item, str) for item in value),
        "mapping": isinstance(value, Mapping),
    }[kind]
    if not valid:
        raise ValueError(f"value does not match predicate schema: {kind}")


def _project_value(value: Any, projection: ProjectionKind) -> Any:
    if projection == "full":
        return value
    if projection == "redacted":
        return "<redacted>"
    if projection == "metadata":
        if isinstance(value, str):
            return {"redacted": True, "length": len(value)}
        if isinstance(value, (list, tuple, set, Mapping)):
            return {"redacted": True, "count": len(value)}
        return {"redacted": True, "type": type(value).__name__}
    return None


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.6:
        return "medium"
    return "low"
