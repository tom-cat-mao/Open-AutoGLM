from dataclasses import dataclass

from phone_agent.graph.nodes.plan import plan_node
from phone_agent.graph.nodes.reflect import parse_reflection_action, reflect_node


@dataclass
class FakeModelResponse:
    thinking: str
    action: str


class FakeModelClient:
    def __init__(self, response: FakeModelResponse) -> None:
        self.response = response
        self.messages = None

    def request(self, messages):
        self.messages = messages
        return self.response


def test_plan_node_returns_only_new_messages_and_resets_action_confirmed(
    base_state, fake_device
) -> None:
    base_state["messages"] = []
    base_state["step_count"] = 0
    base_state["action_confirmed"] = True
    model = FakeModelClient(
        FakeModelResponse("think", 'do(action="Wait", duration="1 seconds")')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "system_prompt": "sys",
            }
        },
    )

    assert len(result["messages"]) == 2
    assert result["messages"][0]["role"] == "system"
    assert result["action_confirmed"] is False
    assert result["action_parsed"]["action"] == "Wait"


def test_reflect_node_cn_and_en_task_finished_detection(
    base_state, fake_device
) -> None:
    for lang, action in (
        ("cn", 'continue(message="任务已完成")'),
        ("en", 'continue(message="Task completed")'),
    ):
        base_state["lang"] = lang
        model = FakeModelClient(FakeModelResponse("ok", action))

        result = reflect_node(
            base_state,
            {
                "configurable": {
                    "model_client": model,
                    "device_factory": fake_device,
                    "verbose": False,
                }
            },
        )

        assert result["action_succeeded"] is True
        assert result["finished"] is True


def test_parse_reflection_action_structured_and_legacy() -> None:
    structured = parse_reflection_action(
        'reflection(verdict="failed", failure_cause="wrong_page", suggested_strategy="go_back", message="页面不对")'
    )

    assert structured.verdict == "failed"
    assert structured.failure_cause == "wrong_page"
    assert structured.suggested_strategy == "go_back"
    assert structured.message == "页面不对"
    assert parse_reflection_action('continue(message="ok")').verdict == "succeeded"
    retry = parse_reflection_action('reflection(verdict="bad", failure_cause="bad", suggested_strategy="bad")')
    assert retry.verdict == "failed"
    assert retry.failure_cause == "unknown"
    assert retry.suggested_strategy == "retry"
    malformed = parse_reflection_action('reflection(verdict="failed"')
    assert malformed.failure_cause == "unknown"


def test_reflect_node_returns_structured_failure(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "点击后仍停留在错误页面",
            'reflection(verdict="failed", failure_cause="wrong_page", suggested_strategy="go_back", message="页面不对")',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
            }
        },
    )

    assert result["action_succeeded"] is False
    assert result["reflection_verdict"] == "failed"
    assert result["failure_cause"] == "wrong_page"
    assert result["suggested_strategy"] == "go_back"
    assert result["retry_count"] == 1


def test_plan_node_includes_structured_reflection_context(base_state, fake_device) -> None:
    base_state["reflection"] = "上一步失败"
    base_state["reflection_verdict"] = "failed"
    base_state["failure_cause"] = "element_not_found"
    base_state["suggested_strategy"] = "swipe_to_find"
    model = FakeModelClient(
        FakeModelResponse("think", 'do(action="Wait", duration="1 seconds")')
    )

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "system_prompt": "sys",
            }
        },
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "failure_cause: element_not_found" in text
    assert "suggested_strategy: swipe_to_find" in text


def test_plan_node_observe_mode_does_not_inject_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "observe"
    base_state["screen_belief"] = {"summary": "should-not-appear"}
    model = FakeModelClient(FakeModelResponse("think", 'do(action="Wait", duration="1 seconds")'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "observe"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "短期上下文" not in text
    assert "should-not-appear" not in text


def test_plan_node_inject_mode_adds_bounded_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "inject"
    base_state["screen_belief"] = {"summary": "safe summary", "current_app": "FakeApp"}
    base_state["failure_memory"] = [{"failure_cause": "wrong_page", "action": "Tap"}]
    model = FakeModelClient(FakeModelResponse("think", 'do(action="Wait", duration="1 seconds")'))

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "短期上下文" in text
    assert "safe summary" not in text
    assert "redacted" in text
    assert result["context_block_chars"] <= 1500


def test_plan_node_inject_mode_redacts_sensitive_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "inject"
    base_state["screen_belief"] = {"summary": "张三", "visible_text": "13800138000"}
    base_state["summarized_history"] = "sk-secret 明天三点见"
    model = FakeModelClient(FakeModelResponse("think", 'do(action="Wait", duration="1 seconds")'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "张三" not in text
    assert "13800138000" not in text
    assert "sk-secret" not in text


def test_reflect_node_updates_context_memory(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "仍在错误页面",
            'reflection(verdict="failed", failure_cause="context_lost", suggested_strategy="retry", message="找不到目标")',
        )
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["failure_cause"] == "context_lost"
    assert result["screen_belief"]["current_app"] == "FakeApp"
    assert result["failure_memory"][-1]["failure_cause"] == "context_lost"
    assert "context_lost" in result["summarized_history"]


def test_reflection_context_redacts_raw_reflection(base_state, fake_device) -> None:
    base_state["reflection"] = "张三 13800138000"
    model = FakeModelClient(FakeModelResponse("think", 'do(action="Wait", duration="1 seconds")'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "张三" not in text
    assert "13800138000" not in text
