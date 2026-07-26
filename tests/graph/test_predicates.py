import pickle

import pytest

from phone_agent.graph.expected_outcome import ExpectedOutcome
from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    AuthorityRule,
    EvidenceAuthorityPolicy,
    EvidenceReference,
    Matcher,
    ObservedFact,
    PredicateCatalog,
    PredicateDefinition,
    PrivacyProjection,
)
from phone_agent.graph.runtime_observation import RuntimeObservationContext


def _fact(
    predicate_id: str,
    value,
    *,
    source="accessibility",
    confidence: float = 0.95,
) -> ObservedFact:
    reference = EvidenceReference(
        source_kind=source,
        reference_id="ref-1",
        screen_id="screen-1",
        observation_epoch=7,
    )
    return ObservedFact(
        predicate_id=predicate_id,
        observed_value=value,
        confidence=confidence,
        source=source,
        evidence_reference=reference,
        contract_id="contract-1",
        screen_id="screen-1",
        observation_epoch=7,
        provider_version="provider-v1",
    )


def test_core_catalog_requires_typed_values_and_closed_predicate_ids() -> None:
    assert CORE_PREDICATE_CATALOG.create_spec("ui.focused", True).expected_value is True
    with pytest.raises(ValueError, match="schema"):
        CORE_PREDICATE_CATALOG.create_spec("ui.focused", "yes")
    with pytest.raises(ValueError, match="unknown predicate"):
        CORE_PREDICATE_CATALOG.create_spec("plugin.declared_success", True)


def test_matcher_rejects_semantic_mismatch_without_substring_guessing() -> None:
    spec = CORE_PREDICATE_CATALOG.create_spec(
        "semantic.entity_matches", "Monica Silverstone"
    )
    fact = _fact(
        "semantic.entity_matches",
        "Monica Singapore",
        source="visual_region",
    )
    CORE_PREDICATE_CATALOG.validate_fact(fact)

    result = Matcher.match(spec, fact)

    assert result.status == "contradicted"
    assert result.reason_code == "values_conflict"


def test_collection_matchers_use_subset_and_exclusion_semantics() -> None:
    contains = CORE_PREDICATE_CATALOG.create_spec(
        "ui.collection_contains", ["alpha", "beta"]
    )
    not_contains = CORE_PREDICATE_CATALOG.create_spec(
        "ui.collection_not_contains", ["blocked"]
    )

    assert (
        Matcher.match(
            contains,
            _fact("ui.collection_contains", ["alpha", "beta", "gamma"]),
        ).status
        == "matched"
    )
    assert (
        Matcher.match(
            not_contains,
            _fact("ui.collection_not_contains", ["alpha", "beta"]),
        ).status
        == "matched"
    )


def test_observed_fact_is_neutral_and_private_trace_projection_has_no_value() -> None:
    fact = _fact("ui.text_equals", "phone 13800138000")
    CORE_PREDICATE_CATALOG.validate_fact(fact)

    projection = fact.trace_projection(CORE_PREDICATE_CATALOG)

    assert "match" not in fact.__dataclass_fields__
    assert "13800138000" not in str(projection)
    assert projection["observed_value"] == {"redacted": True, "length": 17}


def test_evidence_binding_and_visual_region_bounds_fail_closed() -> None:
    with pytest.raises(ValueError, match="relative coordinates"):
        EvidenceReference(
            source_kind="visual_region",
            reference_id="region-1",
            screen_id="screen-1",
            observation_epoch=1,
            bbox=(100, 100, 1001, 500),
        )
    with pytest.raises(ValueError, match="trace-safe"):
        EvidenceReference(
            source_kind="accessibility",
            reference_id="raw private phone 13800138000",
            screen_id="screen-1",
            observation_epoch=1,
        )
    with pytest.raises(ValueError, match="epochs"):
        ObservedFact(
            predicate_id="ui.focused",
            observed_value=True,
            confidence=0.9,
            source="accessibility",
            evidence_reference=EvidenceReference(
                source_kind="accessibility",
                reference_id="node-1",
                screen_id="screen-1",
                observation_epoch=1,
            ),
            contract_id="contract-1",
            screen_id="screen-1",
            observation_epoch=2,
            provider_version="v1",
        )


def test_whole_screen_source_requires_core_allowlist() -> None:
    fact = _fact("ui.focused", True, source="whole_screen")
    with pytest.raises(ValueError, match="not allowed"):
        CORE_PREDICATE_CATALOG.validate_fact(fact)


def test_authority_policy_filters_low_confidence_and_keeps_highest_tier() -> None:
    low = _fact("ui.focused", True, confidence=0.7)
    high = _fact("ui.focused", False, confidence=0.95, source="screen_object")
    policy = EvidenceAuthorityPolicy(
        (
            AuthorityRule("ui.focused", "accessibility", 2, 0.8),
            AuthorityRule("ui.focused", "screen_object", 1, 0.8),
        )
    )

    assert policy.highest_authority((low, high)) == (high,)


def test_authority_resolution_rejects_stale_and_same_tier_conflicts() -> None:
    spec = CORE_PREDICATE_CATALOG.create_spec("ui.focused", True)
    matched = _fact("ui.focused", True)
    contradicted = _fact("ui.focused", False)
    stale = ObservedFact(
        **{
            **matched.__dict__,
            "observation_epoch": 6,
            "evidence_reference": EvidenceReference(
                source_kind="accessibility",
                reference_id="ref-stale",
                screen_id="screen-1",
                observation_epoch=6,
            ),
        }
    )
    policy = EvidenceAuthorityPolicy(
        (AuthorityRule("ui.focused", "accessibility", 2, 0.8),)
    )

    conflict = policy.resolve(
        spec,
        (matched, contradicted, stale),
        contract_id="contract-1",
        screen_id="screen-1",
        observation_epoch=7,
    )

    assert conflict.status == "unknown"
    assert conflict.reason_code == "same_tier_conflict"
    assert conflict.source_count == 2


@pytest.mark.parametrize(
    ("predicate_id", "value", "source"),
    [
        ("ui.focused", True, "accessibility"),
        ("ui.toggle_state", True, "screen_object"),
        ("ui.collection_contains", ["item-a"], "screen_object"),
        ("ui.dialog_open", True, "accessibility"),
        ("app.foreground_package", "com.example.app", "device"),
        ("screen.content_changed", True, "screen_object"),
    ],
)
def test_generic_predicate_semantics_match_without_domain_vocabulary(
    predicate_id, value, source
) -> None:
    spec = CORE_PREDICATE_CATALOG.create_spec(predicate_id, value)
    fact = _fact(predicate_id, value, source=source)
    CORE_PREDICATE_CATALOG.validate_fact(fact)

    assert Matcher.match(spec, fact).status == "matched"


def test_every_predicate_requires_explicit_privacy_projection() -> None:
    with pytest.raises(TypeError):
        PredicateDefinition(  # type: ignore[call-arg]
            predicate_id="test.value",
            value_kind="string",
            allowed_sources=frozenset({"accessibility"}),
            matcher_id="exact",
        )
    with pytest.raises(ValueError, match="runtime-only"):
        PrivacyProjection(
            privacy_class="private",
            state="metadata",
            trace="metadata",
            checkpoint="metadata",
            persistence="runtime_only",
        )


def test_catalog_rejects_arbitrary_matcher_tokens() -> None:
    with pytest.raises(ValueError, match="unknown matcher"):
        PredicateCatalog(
            (
                PredicateDefinition(
                    predicate_id="test.value",
                    value_kind="string",
                    allowed_sources=frozenset({"accessibility"}),
                    matcher_id="opaque_plugin_token",
                    projection=PrivacyProjection(
                        privacy_class="public",
                        state="full",
                        trace="full",
                        checkpoint="full",
                        persistence="checkpoint_safe",
                    ),
                ),
            )
        )


def test_legacy_expected_outcome_translates_to_typed_shadow_contract() -> None:
    transition = ExpectedOutcome(kind="input_focused").to_expected_transition()

    assert transition.predicates[0].predicate_id == "ui.focused"
    assert transition.predicates[0].expected_value is True
    assert transition.compatibility_source == "legacy_expected_outcome:input_focused"
    assert "True" not in str(transition.trace_projection())


def test_plan_runtime_keeps_private_expected_value_but_history_does_not(
    base_state, fake_device
) -> None:
    from types import SimpleNamespace

    from phone_agent.graph.nodes.plan import plan_node

    response = SimpleNamespace(
        thinking="think",
        action=(
            '{"action":{"type":"do","action":"wait","duration":"1 seconds"},'
            '"expected_outcome":{"kind":"text_present",'
            '"must_observe":["private@example.com"]}}'
        ),
        parse_metadata=None,
    )

    class Model:
        def request(self, messages, **kwargs):
            return response

    result = plan_node(
        base_state,
        {"configurable": {"model_client": Model(), "device_factory": fake_device}},
    )

    assert result["expected_outcome"]["must_observe"] == ["private@example.com"]
    assert "private@example.com" not in result["action_raw"]
    assert result["expected_transition"]["schema_version"] == "expected_transition_v1"


def test_runtime_observation_context_is_bound_invalidatable_and_not_serializable() -> (
    None
):
    class Snapshot:
        screen_id = "screen-1"
        observation_epoch = 3

    class ObservationStub:
        snapshot = Snapshot()

    context = RuntimeObservationContext(
        screenshot=object(),
        observation=ObservationStub(),  # type: ignore[arg-type]
        screen_id="screen-1",
        observation_epoch=3,
    )
    context.require_current(screen_id="screen-1", observation_epoch=3)
    with pytest.raises(TypeError, match="node-local"):
        pickle.dumps(context)
    context.invalidate()
    with pytest.raises(RuntimeError, match="stale"):
        context.require_current(screen_id="screen-1", observation_epoch=3)
