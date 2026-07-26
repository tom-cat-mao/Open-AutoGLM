"""Closure invariants binding the predicate catalog to real producers.

Two whole task families used to die 100% of the time with a fully green suite,
because nothing checked that the pieces line up end to end:

* ``semantic.entity_matches`` expected a digest while its provider emitted raw
  screen text, so ``casefold_exact`` could never match — every entity task was
  unsatisfiable at the finish gate.
* ``ui.toggle_state`` was emitted by a provider but reachable from no
  verification kind, so every toggle task was rejected at the adequacy gate.

Both are *closure* failures: a predicate is only useful when something can
produce it, something can request it, and the two agree on the value domain.
These tests assert that closure by exercising the real providers rather than
by restating a list, so they fail when an implementation drifts.
"""

import hashlib

import pytest

from phone_agent.graph.fact_providers import (
    CORE_PROVIDER_PREDICATES,
    AccessibilityFactProvider,
    DeviceFactProvider,
    ExternalProbeFactProvider,
    FactCollector,
    FactRequest,
    MarkFactProvider,
    ObjectFactProvider,
    predicate_is_observable,
)
from phone_agent.graph.goal import VALID_VERIFICATIONS
from phone_agent.graph.goal_compiler import _attach_core_predicates
from phone_agent.graph.goal import SuccessCriterion
from phone_agent.graph.goal_requirements import _terminal_state_is_covered
from phone_agent.graph.marks import Mark, MarkRegistry
from phone_agent.graph.objects import (
    ObjectRegistry,
    ScreenObject,
    ScreenStructure,
    StructureNode,
)
from phone_agent.graph.observation import Observation, ScreenSnapshot
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG
from phone_agent.graph.runtime_observation import RuntimeObservationContext

# The text carried by the probe observation's accessibility node.
_NODE_TEXT = "Silverstone"

# Representative expected values per predicate, used to probe the providers.
_PROBE_VALUES: dict[str, object] = {
    "app.foreground_package": "com.example",
    "app.foreground_activity": "com.example.Main",
    "app.foreground_identity": "example",
    "ui.focused": True,
    "ui.toggle_state": True,
    "ui.text_equals": _NODE_TEXT,
    # The provider digests node text before emitting, so the expectation must
    # be the digest of the same text — computed, not transcribed.
    "ui.text_hash_present": hashlib.sha256(_NODE_TEXT.encode("utf-8")).hexdigest()[:12],
    "ui.dialog_open": True,
    "ui.dialog_closed": False,
    "semantic.entity_matches": "Silverstone",
    "ui.object_present": "object-1",
    "ui.object_rank": 2,
    "external.effect_confirmed": {"ok": True},
}


def _probe_context() -> RuntimeObservationContext:
    """An observation populated so every core provider has something to emit."""

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
    structure = ScreenStructure(
        screen_id="screen-1",
        nodes={
            "node-1": StructureNode(
                node_id="node-1",
                path="/root/1",
                parent_id=None,
                role="dialog",
                text_summary=_NODE_TEXT,
                focused=True,
                checkable=True,
                checked=True,
                visible=True,
            )
        },
    )
    observation = Observation(
        snapshot=snapshot,
        mark_registry=MarkRegistry(
            screen_id="screen-1",
            marks={"mark-1": Mark("mark-1", "screen-1", (0, 0, 100, 100), (50, 50))},
            observation_epoch=7,
        ),
        screen_structures=[structure],
        object_registry=ObjectRegistry(
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
        ),
    )
    return RuntimeObservationContext(
        screenshot=object(),
        observation=observation,
        screen_id="screen-1",
        observation_epoch=7,
    )


def _core_providers():
    return (
        DeviceFactProvider(),
        AccessibilityFactProvider(),
        ObjectFactProvider(),
        MarkFactProvider(),
        ExternalProbeFactProvider({"probe": lambda _criterion: {"ok": True}}),
    )


@pytest.mark.parametrize("predicate_id", sorted(CORE_PROVIDER_PREDICATES))
def test_declared_producers_actually_emit_matching_facts(predicate_id: str) -> None:
    """Every predicate claimed observable must really resolve against a live
    observation, in the same value domain the catalog declares.

    This is what makes CORE_PROVIDER_PREDICATES trustworthy as the input to the
    adequacy gate: it is verified against the providers, not just asserted.
    """
    spec = CORE_PREDICATE_CATALOG.create_spec(
        predicate_id, _PROBE_VALUES[predicate_id]
    )
    criterion_id = "probe" if predicate_id == "external.effect_confirmed" else "c"
    context = _probe_context()
    try:
        results = FactCollector(_core_providers()).collect_and_resolve(
            context, (FactRequest(criterion_id, spec),), contract_id="contract-1"
        )
    finally:
        context.invalidate()

    assert results[criterion_id]["status"] == "matched", results[criterion_id]


def test_observability_map_has_no_phantom_entries() -> None:
    """Every declared producer predicate must exist in the catalog."""
    unknown = sorted(
        predicate_id
        for predicate_id in CORE_PROVIDER_PREDICATES
        if predicate_id not in CORE_PREDICATE_CATALOG.definitions
    )
    assert unknown == []


def test_provider_predicates_declare_closed_evidence_scopes() -> None:
    scopes = {
        CORE_PREDICATE_CATALOG.get(predicate_id).evidence_scope
        for predicate_id in CORE_PROVIDER_PREDICATES
    }
    assert scopes <= {"existential", "screen_singular", "element_scoped"}
    assert CORE_PREDICATE_CATALOG.get("ui.object_rank").evidence_scope == "element_scoped"
    assert CORE_PREDICATE_CATALOG.get("ui.toggle_state").evidence_scope == "element_scoped"


def test_probe_values_cover_every_observable_predicate() -> None:
    """Guard the guard: a new producer must come with a probe value, or the
    parametrised test above would silently skip it."""
    assert set(_PROBE_VALUES) == set(CORE_PROVIDER_PREDICATES)


def _attachable_predicate_ids() -> set[str]:
    """Predicates the compiler can bind, discovered by driving it directly."""

    attachable: set[str] = set()
    for verification in sorted(VALID_VERIFICATIONS):
        criterion = SuccessCriterion(
            name="c",
            description="text sha256:0123456789ab",
            verification=verification,
        )
        migrated = _attach_core_predicates(
            [criterion],
            target_app_hint="bilibili",
            ordinal=3,
            entity_span="猫咪视频",
            toggle_state=True,
        )
        predicate = migrated[0].predicate
        if predicate is not None:
            attachable.add(predicate.predicate_id)
    return attachable


def _emittable_operations() -> set[str]:
    """Operation kinds the requirement extractor can actually produce.

    ``OperationKind`` also declares ``external``, which no vocabulary matches,
    so a compiled contract can never demand ``external_effect_confirmed``. That
    terminal state is reachable only through an externally supplied contract
    plus ``goal_probes`` (benchmark/eval), which is asserted separately below.
    """
    from phone_agent.graph.goal_requirements import TaskRequirementExtractor

    return {kind for kind, _terms in TaskRequirementExtractor._OPERATIONS} | {"unknown"}


def test_terminal_states_are_reachable_from_a_real_contract() -> None:
    """Each terminal state the extractor can demand must be satisfiable.

    Either a programmatic predicate the compiler can attach and a provider can
    emit, or the sanctioned vlm_judge fallback. `toggle_state_visible` failed
    this: it required ui.toggle_state, which no verification kind could attach
    and which vlm_judge was explicitly barred from covering.
    """
    from phone_agent.graph.goal_requirements import _terminal_state

    observable_and_attachable = {
        predicate_id
        for predicate_id in _attachable_predicate_ids()
        if predicate_is_observable(predicate_id)
    }

    unreachable = []
    for operation in sorted(_emittable_operations()):
        terminal_state = _terminal_state(operation)
        programmatic = _terminal_state_is_covered(
            terminal_state, observable_and_attachable, False
        )
        with_judge = _terminal_state_is_covered(terminal_state, set(), True)
        if not (programmatic or with_judge):
            unreachable.append((operation, terminal_state))

    assert unreachable == [], (
        f"terminal states no contract can satisfy: {unreachable}"
    )


def test_external_effect_terminal_state_is_observable_when_probes_supplied() -> None:
    """`external` is unreachable from the extractor, so its terminal state is
    only ever demanded by an injected contract. Assert the machinery behind it
    still closes: the predicate is observable, and the state accepts it."""
    assert "external" not in _emittable_operations()
    assert predicate_is_observable("external.effect_confirmed")
    assert _terminal_state_is_covered(
        "external_effect_confirmed", {"external.effect_confirmed"}, False
    )


def test_attachable_predicates_are_all_observable() -> None:
    """A predicate the compiler binds but no provider emits yields a contract
    that can never be verified."""
    unobservable = sorted(
        predicate_id
        for predicate_id in _attachable_predicate_ids()
        if not predicate_is_observable(predicate_id)
    )
    assert unobservable == []


def test_attachable_expectations_match_their_declared_domain() -> None:
    """The Phase 1 regression, asserted structurally: a bound expectation must
    live in the value domain its provider emits."""
    from phone_agent.graph.goal_requirements import _expected_value_in_domain

    mismatched = []
    for verification in sorted(VALID_VERIFICATIONS):
        criterion = SuccessCriterion(
            name="c",
            description="text sha256:0123456789ab",
            verification=verification,
        )
        migrated = _attach_core_predicates(
            [criterion],
            target_app_hint="bilibili",
            ordinal=3,
            entity_span="猫咪视频",
            toggle_state=True,
        )
        predicate = migrated[0].predicate
        if predicate is None:
            continue
        definition = CORE_PREDICATE_CATALOG.get(predicate.predicate_id)
        if not _expected_value_in_domain(
            definition.value_domain, predicate.expected_value
        ):
            mismatched.append((verification, predicate.predicate_id))

    assert mismatched == []
