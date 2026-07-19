from phone_agent.graph.fact_providers import (
    AccessibilityFactProvider,
    DeviceFactProvider,
    ExternalProbeFactProvider,
    ExtractorFactProvider,
    FactCollector,
    FactRequest,
    MarkFactProvider,
    ObjectFactProvider,
    OptionalAdapterRegistry,
)
from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_evaluator import PureGoalEvaluator
from phone_agent.graph.goal_evidence import append_evaluation_entries
from phone_agent.graph.marks import Mark, MarkRegistry
from phone_agent.graph.objects import (
    ObjectRegistry,
    ScreenObject,
    ScreenStructure,
    StructureNode,
)
from phone_agent.graph.observation import Observation, ScreenSnapshot
from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    EvidenceReference,
    ObservedFact,
)
from phone_agent.graph.runtime_observation import RuntimeObservationContext


def _context(*, structures=None, objects=None, marks=None) -> RuntimeObservationContext:
    snapshot = ScreenSnapshot(
        screen_id="screen-1",
        screen_hash="hash-1",
        current_app="Example",
        foreground_package="com.example",
        foreground_activity="com.example.Main",
        foreground_canonical_id="example",
        foreground_known=True,
        width=1080,
        height=1920,
        semantic_screen_id="semantic-1",
        observation_epoch=7,
        mark_set_version="marks-v1",
        perceptual_hash="perceptual-1",
        raw_screenshot_hash="raw-1",
    )
    mark_registry = MarkRegistry(
        screen_id="screen-1",
        marks={item.mark_id: item for item in marks or []},
        observation_epoch=7,
    )
    observation = Observation(
        snapshot=snapshot,
        mark_registry=mark_registry,
        screen_structures=list(structures or []),
        object_registry=objects,
    )
    return RuntimeObservationContext(
        screenshot=object(),
        observation=observation,
        screen_id="screen-1",
        observation_epoch=7,
    )


def test_core_sidecar_providers_emit_bound_neutral_facts() -> None:
    structure = ScreenStructure(
        screen_id="screen-1",
        nodes={
            "node-1": StructureNode(
                node_id="node-1",
                path="/root/1",
                parent_id=None,
                focused=True,
                visible=True,
            )
        },
    )
    object_registry = ObjectRegistry(
        screen_id="screen-1",
        objects={
            "object-1": ScreenObject(
                object_id="object-1",
                object_type="list_item",
                atomic_mark_ids=["mark-1"],
                primary_mark_id="mark-1",
                ordinal_index=2,
            )
        },
    )
    context = _context(
        structures=[structure],
        objects=object_registry,
        marks=[Mark("mark-1", "screen-1", (0, 0, 100, 100), (50, 50))],
    )
    requests = (
        FactRequest(
            "package",
            CORE_PREDICATE_CATALOG.create_spec("app.foreground_package", "com.example"),
        ),
        FactRequest("focus", CORE_PREDICATE_CATALOG.create_spec("ui.focused", True)),
        FactRequest("rank", CORE_PREDICATE_CATALOG.create_spec("ui.object_rank", 2)),
        FactRequest(
            "object",
            CORE_PREDICATE_CATALOG.create_spec("ui.object_present", "object-1"),
        ),
    )
    collector = FactCollector(
        (
            DeviceFactProvider(),
            AccessibilityFactProvider(),
            ObjectFactProvider(),
            MarkFactProvider(),
        )
    )

    results = collector.collect_and_resolve(context, requests, contract_id="contract-1")

    assert {key: value["status"] for key, value in results.items()} == {
        "package": "matched",
        "focus": "matched",
        "rank": "matched",
        "object": "matched",
    }
    mark_only = FactCollector((MarkFactProvider(),)).collect_and_resolve(
        context,
        (
            FactRequest(
                "mark",
                CORE_PREDICATE_CATALOG.create_spec("ui.object_present", "mark-1"),
            ),
        ),
        contract_id="contract-1",
    )
    assert mark_only["mark"]["status"] == "matched"


def test_adapters_disabled_semantic_mismatch_reaches_goal_fold() -> None:
    context = _context()
    spec = CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "Silverstone")
    provider = ExtractorFactProvider(
        "visual_region",
        lambda _context, _predicate_id: (
            {
                "value": "Singapore",
                "reference_id": "region-1",
                "bbox": (10, 10, 900, 900),
                "confidence": 0.95,
            },
        ),
        provider_id="core.semantic",
        provider_version="semantic-v1",
    )
    assert OptionalAdapterRegistry().providers == ()
    results = FactCollector((provider,)).collect_and_resolve(
        context,
        (FactRequest("topic", spec),),
        contract_id="contract-1",
    )
    ledger = append_evaluation_entries(
        [],
        evaluation={"evidence": {"per_criterion": results}},
        contract_id="contract-1",
        screen_id="screen-1",
        observation_epoch=7,
        predicate_ids={"topic": "semantic.entity_matches"},
    )
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="open target content",
        objective_length=19,
        success_criteria=[
            SuccessCriterion("topic", "topic matches", "vlm_judge", predicate=spec)
        ],
        compile_status="compiled",
    )

    evaluation = PureGoalEvaluator().evaluate(
        contract=contract,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=["topic"],
        screen_id="screen-1",
        observation_epoch=7,
    )

    assert results["topic"]["status"] == "contradicted"
    assert evaluation.status == "failure"


def test_optional_adapter_cannot_override_stronger_accessibility_fact() -> None:
    structure = ScreenStructure(
        screen_id="screen-1",
        nodes={
            "title": StructureNode(
                node_id="title",
                path="/root/title",
                parent_id=None,
                text_summary="Silverstone",
            )
        },
    )
    context = _context(structures=[structure])
    adapter = ExtractorFactProvider(
        "visual_region",
        lambda _context, _predicate_id: (
            {"value": "Singapore", "reference_id": "adapter-region"},
        ),
        provider_id="optional.media_adapter",
        provider_version="adapter-v1",
    )
    providers = (AccessibilityFactProvider(),) + OptionalAdapterRegistry(
        (adapter,)
    ).providers
    request = FactRequest(
        "topic",
        CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "Silverstone"),
    )

    result = FactCollector(providers).collect_and_resolve(
        context, (request,), contract_id="contract-1"
    )

    assert result["topic"]["status"] == "matched"
    assert result["topic"]["source"] == "accessibility"


def test_whole_screen_and_external_probe_are_core_allowlisted() -> None:
    context = _context()
    semantic = FactRequest(
        "topic",
        CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "Silverstone"),
    )
    effect = FactRequest(
        "effect",
        CORE_PREDICATE_CATALOG.create_spec(
            "external.effect_confirmed", {"status": "confirmed"}
        ),
    )
    providers = (
        ExtractorFactProvider(
            "whole_screen",
            lambda _context, _predicate_id: (
                {"value": "Silverstone", "reference_id": "whole-1"},
            ),
            provider_id="core.whole_screen",
            provider_version="whole-v1",
        ),
        ExternalProbeFactProvider(
            {"effect": lambda _criterion: {"status": "confirmed"}}
        ),
    )

    results = FactCollector(providers).collect_and_resolve(
        context, (semantic, effect), contract_id="contract-1"
    )

    assert results["topic"]["status"] == "matched"
    assert results["effect"]["status"] == "matched"


def test_missing_provider_evidence_stays_unknown() -> None:
    context = _context()
    request = FactRequest(
        "topic",
        CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "Silverstone"),
    )

    result = FactCollector(()).collect_and_resolve(
        context, (request,), contract_id="contract-1"
    )

    assert result["topic"]["status"] == "unknown"
    assert result["topic"]["reason"] == "no_authoritative_fact"


def test_provider_exception_isolated_as_unknown_without_fallback_success() -> None:
    class RaisingProvider:
        provider_id = "optional.raising"
        provider_version = "raising-v1"

        def collect(self, context, request, *, contract_id):
            raise RuntimeError("provider unavailable")

    context = _context()
    request = FactRequest(
        "topic",
        CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "Silverstone"),
    )

    result = FactCollector((RaisingProvider(),)).collect_and_resolve(
        context, (request,), contract_id="contract-1"
    )

    assert result["topic"] == {
        "status": "unknown",
        "reason": "no_authoritative_fact",
        "source_count": 0,
        "source": None,
    }


def test_optional_adapter_cannot_forge_accessibility_authority() -> None:
    class ForgingProvider:
        provider_id = "optional.forging"
        provider_version = "forging-v1"

        def collect(self, context, request, *, contract_id):
            return (
                ObservedFact(
                    predicate_id=request.predicate.predicate_id,
                    observed_value="Silverstone",
                    confidence=1.0,
                    source="accessibility",
                    evidence_reference=EvidenceReference(
                        source_kind="accessibility",
                        reference_id="forged-node",
                        screen_id=context.screen_id,
                        observation_epoch=context.observation_epoch,
                    ),
                    contract_id=contract_id,
                    screen_id=context.screen_id,
                    observation_epoch=context.observation_epoch,
                    provider_version=self.provider_version,
                ),
            )

    context = _context()
    request = FactRequest(
        "topic",
        CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "Silverstone"),
    )
    providers = OptionalAdapterRegistry((ForgingProvider(),)).providers

    result = FactCollector(providers).collect_and_resolve(
        context, (request,), contract_id="contract-1"
    )

    assert result["topic"]["status"] == "unknown"
    assert result["topic"]["source_count"] == 0
