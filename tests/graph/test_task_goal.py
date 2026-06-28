from phone_agent.graph.task_goal import build_task_goal_contract, validate_finish_claim


def test_task_goal_contract_extracts_ranked_video_without_raw_entity_in_trace() -> None:
    contract = build_task_goal_contract("去b站看逗比的雀巢的第二个视频")

    trace = contract.to_trace_payload()

    assert trace["target_app_hint"] == "bilibili"
    assert trace["goal_type"] == "open_or_watch_ranked_content"
    assert trace["ordinal"] == 2
    assert "selected_rank=2" in trace["terminal_evidence"]
    assert "逗比" not in str(trace)
    assert "雀巢" not in str(trace)


def test_finish_claim_fails_when_only_search_results_are_visible() -> None:
    contract = build_task_goal_contract("去b站看逗比的雀巢的第二个视频")

    result = validate_finish_claim(
        contract=contract,
        verifier_status="unknown",
        verifier_evidence={},
        after_observation={
            "marks": [
                {"role": "TextView", "text_summary": "搜索结果 综合 视频"},
                {"role": "TextView", "text_summary": "UP主 逗比的雀巢"},
            ]
        },
        finish_claim="已搜索到UP主",
    )

    assert result["status"] in {"failure", "unknown"}
    assert "detail_or_player_visible" in result["missing_terminal_evidence"]
    assert "selected_rank=2" in result["missing_terminal_evidence"]


def test_finish_claim_passes_with_player_and_selected_object_match() -> None:
    contract = build_task_goal_contract("去b站看逗比的雀巢的第二个视频")

    result = validate_finish_claim(
        contract=contract,
        verifier_status="success",
        verifier_evidence={"selected_object_signals": {"selected_object_match": True, "selected_object_expected_rank": 2}},
        after_observation={"marks": [{"role": "TextView", "text_summary": "播放器 暂停 弹幕 评论"}]},
        finish_claim="已打开第二个视频",
    )

    assert result["status"] == "success"
    assert "detail_or_player_visible" in result["matched_terminal_evidence"]
    assert "selected_rank=2" in result["matched_terminal_evidence"]


def test_finish_claim_allows_search_task_when_results_visible() -> None:
    contract = build_task_goal_contract("搜索蓝牙耳机")

    result = validate_finish_claim(
        contract=contract,
        verifier_status="unknown",
        verifier_evidence={},
        after_observation={"marks": [{"role": "TextView", "text_summary": "搜索结果 综合 蓝牙耳机"}]},
        finish_claim="已完成搜索",
    )

    assert result["status"] == "success"
    assert result["matched_terminal_evidence"] == ["search_results_visible"]


def test_finish_claim_allows_open_app_task_with_verifier_success() -> None:
    contract = build_task_goal_contract("打开设置")

    result = validate_finish_claim(
        contract=contract,
        verifier_status="success",
        verifier_evidence={},
        after_observation={"snapshot": {"current_app": "com.android.settings"}},
        finish_claim="已打开设置",
    )

    assert result["status"] == "success"


def test_task_goal_contract_extracts_creator_alias_from_continuous_chinese_task() -> None:
    contract = build_task_goal_contract("去b站看逗比的雀巢的第二个视频")
    trace = contract.to_trace_payload()

    assert trace["entities"]
    assert trace["entities"][0]["alias"] == "<matches_task_entity:1>"
    assert trace["entities"][0]["length"] > 0
    assert "逗比" not in str(trace)
    assert "雀巢" not in str(trace)


def test_finish_claim_summary_never_contains_raw_private_text() -> None:
    result = validate_finish_claim(
        contract=build_task_goal_contract("去b站看逗比的雀巢的第二个视频"),
        verifier_status="unknown",
        verifier_evidence={},
        after_observation={},
        finish_claim="已搜索到逗比的雀巢 13800138000 sk-secret",
    )

    claim = result["finish_claim_summary"]
    assert claim["length"] > 0
    assert claim["sha256"]
    assert "逗比" not in str(claim)
    assert "13800138000" not in str(claim)
    assert "sk-secret" not in str(claim)
