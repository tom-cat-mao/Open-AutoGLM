"""Concrete neutral fact providers over one node-local observation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable, Iterable, Protocol

from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY

from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    AuthorityRule,
    EvidenceAuthorityPolicy,
    EvidenceReference,
    ObservedFact,
    PredicateCatalog,
    PredicateSpec,
    SourceKind,
)
from phone_agent.graph.runtime_observation import RuntimeObservationContext


@dataclass(frozen=True)
class FactRequest:
    """One criterion-to-predicate collection request."""

    criterion_id: str
    predicate: PredicateSpec


class FactProvider(Protocol):
    """Provider boundary: emit neutral facts, never match polarity."""

    provider_id: str
    provider_version: str

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]: ...


SOURCE_AUTHORITY_TIER: dict[SourceKind, int] = {
    "device": 5,
    "external_probe": 5,
    "accessibility": 4,
    "screen_object": 3,
    "mark": 2,
    "visual_region": 2,
    "whole_screen": 1,
}


@dataclass(frozen=True)
class ProviderRegistration:
    """Core-owned limits for one provider implementation."""

    provider: FactProvider
    provider_id: str
    provider_version: str
    allowed_sources: frozenset[SourceKind]
    authority_ceiling: int

    def accepts(self, fact: ObservedFact) -> bool:
        return (
            self.provider.provider_id == self.provider_id
            and self.provider.provider_version == self.provider_version
            and fact.provider_version == re_safe_id(self.provider_version)
            and fact.source in self.allowed_sources
            and SOURCE_AUTHORITY_TIER[fact.source] <= self.authority_ceiling
        )


Extractor = Callable[[RuntimeObservationContext, str], Iterable[dict[str, Any]]]
Probe = Callable[[str], Any]


class DeviceFactProvider:
    provider_id = "core.device"
    provider_version = "device_facts_v1"

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]:
        snapshot = context.observation.snapshot
        values = {
            "app.foreground_package": snapshot.foreground_package,
            "app.foreground_activity": snapshot.foreground_activity,
            "app.foreground_identity": snapshot.foreground_canonical_id,
        }
        value = values.get(request.predicate.predicate_id)
        if value is None:
            return ()
        return (
            _fact(
                context,
                request.predicate.predicate_id,
                value,
                "device",
                "snapshot",
                contract_id,
                self.provider_version,
            ),
        )


class AccessibilityFactProvider:
    provider_id = "core.accessibility"
    provider_version = "accessibility_facts_v1"

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]:
        predicate_id = request.predicate.predicate_id
        nodes = [
            node
            for structure in context.observation.screen_structures
            if structure.structure_kind == "accessibility" and structure.status == "ok"
            for node in structure.nodes.values()
            if node.visible
        ]
        facts: list[ObservedFact] = []
        if predicate_id == "ui.focused":
            facts.append(
                self._node_fact(
                    context,
                    predicate_id,
                    any(node.focused for node in nodes),
                    "focus",
                    contract_id,
                )
            )
        elif predicate_id == "ui.toggle_state":
            for node in nodes:
                if node.checkable:
                    facts.append(
                        self._node_fact(
                            context,
                            predicate_id,
                            node.checked,
                            node.node_id,
                            contract_id,
                        )
                    )
        elif predicate_id in {
            "ui.text_equals",
            "semantic.entity_matches",
        }:
            for node in nodes:
                for value in (node.text_summary, node.content_desc_summary):
                    if value:
                        facts.append(
                            self._node_fact(
                                context, predicate_id, value, node.node_id, contract_id
                            )
                        )
        elif predicate_id == "semantic.attributes_present":
            # Multi-fragment conjunction over ONE control subtree: each node
            # emits its texts as a list, so a fragment split across two nodes
            # can never fabricate the attribute set.
            for node in nodes:
                texts = [
                    value
                    for value in (node.text_summary, node.content_desc_summary)
                    if value
                ]
                if texts:
                    facts.append(
                        self._node_fact(
                            context,
                            predicate_id,
                            list(texts),
                            node.node_id,
                            contract_id,
                        )
                    )
        elif predicate_id == "ui.text_hash_present":
            for node in nodes:
                for value in (node.text_summary, node.content_desc_summary):
                    if value:
                        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
                        facts.append(
                            self._node_fact(
                                context, predicate_id, digest, node.node_id, contract_id
                            )
                        )
        elif predicate_id in {"ui.dialog_open", "ui.dialog_closed"}:
            is_open = any((node.role or "").casefold() == "dialog" for node in nodes)
            facts.append(
                self._node_fact(
                    context,
                    predicate_id,
                    is_open if predicate_id == "ui.dialog_open" else not is_open,
                    "dialog",
                    contract_id,
                )
            )
        return tuple(facts)

    def _node_fact(
        self,
        context: RuntimeObservationContext,
        predicate_id: str,
        value: Any,
        reference_id: str,
        contract_id: str,
    ) -> ObservedFact:
        return _fact(
            context,
            predicate_id,
            value,
            "accessibility",
            reference_id,
            contract_id,
            self.provider_version,
        )


class ObjectFactProvider:
    provider_id = "core.screen_object"
    provider_version = "object_facts_v1"

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]:
        registry = context.observation.object_registry
        if registry is None:
            return ()
        predicate_id = request.predicate.predicate_id
        facts: list[ObservedFact] = []
        for item in registry.objects.values():
            value: Any = None
            if predicate_id == "ui.object_present":
                value = item.object_id
            elif predicate_id == "ui.object_rank" and item.ordinal_index is not None:
                value = item.ordinal_index
            elif predicate_id == "ui.toggle_state" and item.object_type == "toggle":
                value = "checked" in item.selector_reasons
            if value is not None:
                facts.append(
                    _fact(
                        context,
                        predicate_id,
                        value,
                        "screen_object",
                        item.object_id,
                        contract_id,
                        self.provider_version,
                        confidence=item.confidence,
                    )
                )
        return tuple(facts)


class MarkFactProvider:
    provider_id = "core.mark"
    provider_version = "mark_facts_v1"

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]:
        if request.predicate.predicate_id != "ui.object_present":
            return ()
        return tuple(
            _fact(
                context,
                request.predicate.predicate_id,
                mark.mark_id,
                "mark",
                mark.mark_id,
                contract_id,
                self.provider_version,
                confidence=mark.confidence,
            )
            for mark in context.observation.mark_registry.marks.values()
        )


class ExtractorFactProvider:
    """Core-controlled visual-region or whole-screen extractor adapter."""

    def __init__(
        self,
        source: SourceKind,
        extractor: Extractor,
        *,
        provider_id: str,
        provider_version: str,
    ) -> None:
        if source not in {"visual_region", "whole_screen"}:
            raise ValueError(
                "extractor provider source must be visual_region or whole_screen"
            )
        self.source = source
        self.extractor = extractor
        self.provider_id = provider_id
        self.provider_version = provider_version

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]:
        definition = CORE_PREDICATE_CATALOG.get(request.predicate.predicate_id)
        if self.source not in definition.allowed_sources:
            return ()
        if self.source == "whole_screen" and not definition.whole_screen_allowed:
            return ()
        facts: list[ObservedFact] = []
        for index, item in enumerate(
            self.extractor(context, request.predicate.predicate_id)
        ):
            if not isinstance(item, dict) or "value" not in item:
                continue
            bbox_value = item.get("bbox")
            bbox = (
                tuple(bbox_value)
                if isinstance(bbox_value, (list, tuple)) and len(bbox_value) == 4
                else None
            )
            facts.append(
                _fact(
                    context,
                    request.predicate.predicate_id,
                    item["value"],
                    self.source,
                    str(item.get("reference_id") or f"{self.provider_id}-{index}"),
                    contract_id,
                    self.provider_version,
                    confidence=float(item.get("confidence", 0.8)),
                    bbox=bbox,
                )
            )
        return tuple(facts)


class ExternalProbeFactProvider:
    provider_id = "core.external_probe"
    provider_version = "external_probe_facts_v1"

    def __init__(self, probes: dict[str, Probe]) -> None:
        self._probes = dict(probes)

    def collect(
        self,
        context: RuntimeObservationContext,
        request: FactRequest,
        *,
        contract_id: str,
    ) -> tuple[ObservedFact, ...]:
        if request.predicate.predicate_id != "external.effect_confirmed":
            return ()
        probe = self._probes.get(request.criterion_id)
        if probe is None:
            return ()
        return (
            _fact(
                context,
                request.predicate.predicate_id,
                probe(request.criterion_id),
                "external_probe",
                f"probe-{request.criterion_id}",
                contract_id,
                self.provider_version,
            ),
        )


class OptionalAdapterRegistry:
    """Immutable optional provider list; adapters receive no policy mutation API."""

    def __init__(self, providers: tuple[FactProvider, ...] = ()) -> None:
        self._providers = tuple(
            ProviderRegistration(
                provider=provider,
                provider_id=provider.provider_id,
                provider_version=provider.provider_version,
                allowed_sources=frozenset({"visual_region", "whole_screen"}),
                authority_ceiling=SOURCE_AUTHORITY_TIER["visual_region"],
            )
            for provider in providers
        )

    @property
    def providers(self) -> tuple[ProviderRegistration, ...]:
        return self._providers


class FactCollector:
    """Collect, validate, bind, and authority-resolve facts for criteria."""

    def __init__(
        self,
        providers: tuple[FactProvider | ProviderRegistration, ...],
        *,
        catalog: PredicateCatalog = CORE_PREDICATE_CATALOG,
        authority_policy: EvidenceAuthorityPolicy | None = None,
        max_facts: int = 128,
    ) -> None:
        self._providers = tuple(
            _provider_registration(provider) for provider in providers
        )
        self._catalog = catalog
        self._authority = authority_policy or default_evidence_authority_policy(catalog)
        self._max_facts = max(1, max_facts)

    def collect_and_resolve(
        self,
        context: RuntimeObservationContext,
        requests: tuple[FactRequest, ...],
        *,
        contract_id: str,
    ) -> dict[str, dict[str, Any]]:
        context.require_current(
            screen_id=context.screen_id, observation_epoch=context.observation_epoch
        )
        results: dict[str, dict[str, Any]] = {}
        for request in requests:
            facts: list[ObservedFact] = []
            for registration in self._providers:
                try:
                    provided = registration.provider.collect(
                        context, request, contract_id=contract_id
                    )
                except Exception:
                    provided = ()
                for fact in provided:
                    if not registration.accepts(fact):
                        continue
                    try:
                        self._catalog.validate_fact(fact)
                        fact.validate_binding(
                            contract_id=contract_id,
                            screen_id=context.screen_id,
                            observation_epoch=context.observation_epoch,
                        )
                    except ValueError:
                        continue
                    facts.append(fact)
                    if len(facts) >= self._max_facts:
                        break
                if len(facts) >= self._max_facts:
                    break
            resolution = self._authority.resolve(
                request.predicate,
                tuple(facts),
                contract_id=contract_id,
                screen_id=context.screen_id,
                observation_epoch=context.observation_epoch,
            )
            results[request.criterion_id] = {
                "status": resolution.status,
                "reason": resolution.reason_code,
                "source_count": resolution.source_count,
                "source": _highest_source(self._authority, facts),
            }
        return results


def default_core_fact_providers() -> tuple[FactProvider, ...]:
    return (
        DeviceFactProvider(),
        AccessibilityFactProvider(),
        ObjectFactProvider(),
        MarkFactProvider(),
    )


# Which predicates the core providers can actually emit facts for. A criterion
# bound to a predicate absent from this map can never be observed, so the
# contract asserting it is structurally unsatisfiable — the adequacy gate
# rejects that instead of letting it fail silently at the finish gate.
# `tests/graph/test_predicate_catalog_closure.py` drives every provider against
# a synthetic observation to prove this map matches real behaviour, so it
# cannot drift away from the implementations above.
CORE_PROVIDER_PREDICATES: frozenset[str] = frozenset(
    {
        # DeviceFactProvider
        "app.foreground_package",
        "app.foreground_activity",
        "app.foreground_identity",
        # AccessibilityFactProvider
        "ui.focused",
        "ui.toggle_state",
        "ui.text_equals",
        "ui.text_hash_present",
        "ui.dialog_open",
        "ui.dialog_closed",
        "semantic.entity_matches",
        "semantic.attributes_present",
        # ObjectFactProvider
        "ui.object_present",
        "ui.object_rank",
        # ExternalProbeFactProvider (registered when goal_probes are supplied)
        "external.effect_confirmed",
    }
)


def collect_goal_facts(
    *,
    goal_contract: Any,
    configurable: dict[str, Any],
    screenshot: Any,
    after_observation: Any,
    runtime_contract_id: str | None,
) -> dict[str, dict] | None:
    """Collect resolved neutral facts for every typed Goal criterion."""

    requests = tuple(
        FactRequest(criterion.name, criterion.predicate)
        for criterion in goal_contract.success_criteria
        if criterion.predicate is not None
        and criterion.predicate.expected_value is not None
    )
    if not requests:
        return None

    providers = list(default_core_fact_providers())
    for source, key in (
        ("visual_region", "visual_fact_extractor"),
        ("whole_screen", "whole_screen_fact_extractor"),
    ):
        extractor = configurable.get(key)
        if callable(extractor):
            providers.append(
                ExtractorFactProvider(
                    source,
                    extractor,
                    provider_id=f"core.{source}",
                    provider_version=f"{source}_v1",
                )
            )
    goal_probes = configurable.get("goal_probes")
    if isinstance(goal_probes, dict):
        providers.append(ExternalProbeFactProvider(goal_probes))
    adapter_registry = configurable.get("optional_fact_adapter_registry")
    if isinstance(adapter_registry, OptionalAdapterRegistry):
        providers.extend(adapter_registry.providers)

    runtime_context = RuntimeObservationContext(
        screenshot=screenshot,
        observation=after_observation,
        screen_id=after_observation.snapshot.screen_id,
        observation_epoch=after_observation.snapshot.observation_epoch,
    )
    try:
        collected = FactCollector(tuple(providers)).collect_and_resolve(
            runtime_context, requests, contract_id=runtime_contract_id
        )
    finally:
        runtime_context.invalidate()
    return {
        "collected": collected,
        "predicate_ids": {
            request.criterion_id: request.predicate.predicate_id
            for request in requests
        },
    }


def predicate_is_observable(predicate_id: str) -> bool:
    """Whether any core provider can emit facts for this predicate."""

    return predicate_id in CORE_PROVIDER_PREDICATES


def default_evidence_authority_policy(
    catalog: PredicateCatalog = CORE_PREDICATE_CATALOG,
) -> EvidenceAuthorityPolicy:
    tiers = SOURCE_AUTHORITY_TIER
    minimum_confidence = DEFAULT_VERIFICATION_POLICY.value("fact_min_confidence")
    rules = tuple(
        AuthorityRule(
            definition.predicate_id, source, tiers[source], minimum_confidence
        )
        for definition in catalog.definitions.values()
        for source in sorted(definition.allowed_sources)
    )
    return EvidenceAuthorityPolicy(rules)


def _fact(
    context: RuntimeObservationContext,
    predicate_id: str,
    value: Any,
    source: SourceKind,
    reference_id: str,
    contract_id: str,
    provider_version: str,
    *,
    confidence: float = 1.0,
    bbox: tuple[int, int, int, int] | None = None,
) -> ObservedFact:
    safe_reference = re_safe_id(reference_id)
    reference = EvidenceReference(
        source_kind=source,
        reference_id=safe_reference,
        screen_id=context.screen_id,
        observation_epoch=context.observation_epoch,
        bbox=bbox,
    )
    return ObservedFact(
        predicate_id=predicate_id,
        observed_value=value,
        confidence=max(0.0, min(float(confidence), 1.0)),
        source=source,
        evidence_reference=reference,
        contract_id=contract_id,
        screen_id=context.screen_id,
        observation_epoch=context.observation_epoch,
        provider_version=re_safe_id(provider_version),
    )


def _provider_registration(
    value: FactProvider | ProviderRegistration,
) -> ProviderRegistration:
    if isinstance(value, ProviderRegistration):
        return value
    source = getattr(value, "source", None)
    core_sources: dict[str, frozenset[SourceKind]] = {
        "core.device": frozenset({"device"}),
        "core.accessibility": frozenset({"accessibility"}),
        "core.screen_object": frozenset({"screen_object"}),
        "core.mark": frozenset({"mark"}),
        "core.external_probe": frozenset({"external_probe"}),
    }
    allowed = core_sources.get(value.provider_id)
    if (
        allowed is None
        and value.provider_id.startswith("core.")
        and source in SOURCE_AUTHORITY_TIER
    ):
        allowed = frozenset({source})
    if allowed is None:
        allowed = frozenset({"visual_region", "whole_screen"})
    ceiling = max(SOURCE_AUTHORITY_TIER[item] for item in allowed)
    return ProviderRegistration(
        provider=value,
        provider_id=value.provider_id,
        provider_version=value.provider_version,
        allowed_sources=allowed,
        authority_ceiling=ceiling,
    )


def re_safe_id(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in "_.:-" else "-" for char in str(value)
    )[:128]
    return safe or "fact"


def _highest_source(
    policy: EvidenceAuthorityPolicy, facts: list[ObservedFact]
) -> str | None:
    highest = policy.highest_authority(tuple(facts))
    return highest[0].source if highest else None
