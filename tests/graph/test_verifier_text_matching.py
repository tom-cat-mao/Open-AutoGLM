from phone_agent.graph.verifier import _match_expected_text, verify_action_outcome


def test_expected_text_contains_is_case_insensitive_for_uppercase_input() -> None:
    matched, missing = _match_expected_text(["VillageThomas"], "result: villagethomas")

    assert matched == ["VillageThomas"]
    assert missing == []


def test_sensitive_expected_text_verifies_with_raw_contains() -> None:
    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            "expected_outcome": {
                "kind": "target_appeared",
                "must_observe": ["13800138000"],
            },
        },
        after_screenshot=object(),
        after_app="Settings",
        action_result={"success": True, "message": "ok"},
        after_observation={"marks": [{"text_summary": "contact 13800138000"}]},
    )

    assert result.status == "success"
    assert result.evidence["matched_postconditions"] == ["13800138000"]
