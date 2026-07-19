from phone_agent.checkpoint.goal_resume import TrustedGoalResumeBinder
from phone_agent.checkpoint.serde import _redact_for_checkpoint
from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_requirements import TaskRequirementExtractor
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

SECRET = b"0123456789abcdef0123456789abcdef"


def _contract(semantic_value: str = "Silverstone") -> GoalContract:
    return GoalContract(
        task_hash="ordinary-low-entropy-task-hash",
        redacted_objective="open target content",
        objective_length=19,
        success_criteria=[
            SuccessCriterion(
                "app_progress",
                "app visited",
                "app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "example"
                ),
                scope="trajectory",
                freshness="trajectory",
            ),
            SuccessCriterion(
                "terminal_app",
                "app visible now",
                "app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "example"
                ),
            ),
            SuccessCriterion(
                "private_topic",
                "topic observed",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", semantic_value
                ),
                scope="trajectory",
                freshness="trajectory",
            ),
        ],
        entities_sha=["low-entropy-entity-hash"],
        compile_status="compiled",
    )


def _ledger() -> list[dict]:
    common = {
        "status": "matched",
        "reason_code": "values_match",
        "source_kind": "device",
        "confidence_bucket": "high",
        "contract_id": "ordinary-low-entropy-task-hash",
        "screen_id": "screen-1",
        "observation_epoch": 4,
    }
    return [
        {
            **common,
            "criterion_id": "app_progress",
            "predicate_id": "app.foreground_identity",
        },
        {
            **common,
            "criterion_id": "terminal_app",
            "predicate_id": "app.foreground_identity",
        },
        {
            **common,
            "criterion_id": "private_topic",
            "predicate_id": "semantic.entity_matches",
            "source_kind": "visual_region",
        },
    ]


def test_trusted_resume_restores_only_checkpoint_safe_trajectory_progress() -> None:
    requirements = TaskRequirementExtractor().extract("打开 example")
    contract = _contract()
    binder = TrustedGoalResumeBinder(SECRET)

    projection = binder.build_projection(
        requirements=requirements,
        contract=contract,
        evidence_ledger=_ledger(),
    )
    result = binder.rehydrate(
        projection,
        requirements=requirements,
        contract=contract,
    )

    assert [item["criterion_id"] for item in projection.progress] == ["app_progress"]
    assert result.status == "trusted"
    assert result.requires_reobservation is True
    assert [item["criterion_id"] for item in result.evidence_ledger] == ["app_progress"]


def test_equal_shape_different_semantic_value_changes_binding() -> None:
    requirements = TaskRequirementExtractor().extract("打开 example")
    binder = TrustedGoalResumeBinder(SECRET)

    first = binder.binding(requirements, _contract("Silverstone"))
    second = binder.binding(requirements, _contract("Singapore"))

    assert first != second
    assert "Silverstone" not in first
    assert "Singapore" not in second


def test_wrong_key_or_contract_binding_fails_closed_without_progress() -> None:
    requirements = TaskRequirementExtractor().extract("打开 example")
    contract = _contract()
    projection = TrustedGoalResumeBinder(SECRET).build_projection(
        requirements=requirements,
        contract=contract,
        evidence_ledger=_ledger(),
    )

    result = TrustedGoalResumeBinder(b"x" * 32).rehydrate(
        projection,
        requirements=requirements,
        contract=contract,
    )

    assert result.status == "goal_contract_invalid"
    assert result.evidence_ledger == ()
    assert result.requires_reobservation is True
    assert result.reason_code == "contract_binding_mismatch"


def test_missing_untrusted_projection_never_restores_progress() -> None:
    result = TrustedGoalResumeBinder(SECRET).rehydrate(
        None,
        requirements=TaskRequirementExtractor().extract("打开 example"),
        contract=_contract(),
    )

    assert result.status == "goal_contract_invalid"
    assert result.reason_code == "trusted_projection_missing"


def test_checkpoint_serializer_removes_unbound_goal_values_and_progress() -> None:
    requirements = TaskRequirementExtractor().extract(
        "搜索 private@example.com 订单 ORD-123"
    )
    contract = _contract("private@example.com")
    checkpoint = _redact_for_checkpoint(
        {
            "task_requirement_set": requirements.safe_projection(),
            "goal_contract": contract.to_dict(),
            "goal_evidence_ledger": _ledger(),
            "screenshot_b64": "aGVsbG8=",
        }
    )
    serialized = str(checkpoint)

    assert checkpoint["goal_evidence_ledger"] == []
    assert "target_entity_hashes" not in checkpoint["task_requirement_set"]
    assert "task_hash" not in checkpoint["goal_contract"]
    assert "entities_sha" not in checkpoint["goal_contract"]
    assert "private@example.com" not in serialized
    assert "ORD-123" not in serialized
    assert "aGVsbG8=" not in serialized


def test_checkpoint_private_stub_has_no_dictionary_enumerable_digest() -> None:
    import hashlib

    secret = "OTP 123456"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
    checkpoint = _redact_for_checkpoint(
        {"message": secret, "expected": f"sha256:{digest}", "sha256": digest}
    )
    serialized = str(checkpoint)

    assert secret not in serialized
    assert digest not in serialized
