"""Fakes-only tests for the web event middleware and run bridge."""

from __future__ import annotations

import queue
import sys
import time
import types
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from phone_agent.v2.agent import RunResult
from phone_agent.web.bridge import WebEventMiddleware, WebRunBridge
from tests.v2.test_agent_loop import ScriptedToolModel


def _drain(events: queue.Queue) -> list[dict]:
    collected = []
    while not events.empty():
        collected.append(events.get_nowait())
    return collected


def test_web_event_middleware_emits_scripted_model_tool_screen_and_taskdoc():
    events: queue.Queue[dict] = queue.Queue()
    middleware = WebEventMiddleware(events)
    model = ScriptedToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "tap",
                        "args": {
                            "intent": "打开 WLAN",
                            "target_mark_id": "ax_1@e1",
                        },
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            )
        ]
    )

    middleware.before_model(
        {
            "messages": [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": "打开 WLAN\n[OBS] app=系统 设置 screen#1",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,QUJA"},
                            "screen_seq": 1,
                        },
                    ]
                ),
                SystemMessage(
                    content="[TASK_DOC]\n## 目标\nbase: 打开 WLAN\n## 路线\n- [in_progress] 1: 打开设置"
                ),
            ]
        },
        runtime=None,
    )
    middleware.wrap_model_call(
        SimpleNamespace(), handler=lambda request: model.invoke([])
    )
    request = SimpleNamespace(
        tool_call={
            "name": "tap",
            "args": {"intent": "打开 WLAN", "target_mark_id": "ax_1@e1"},
        }
    )
    result = ToolMessage(
        content=[
            {"type": "text", "text": "已点击 WLAN\n[OBS] app=设置 screen#2"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,QUJD"},
                "screen_seq": 2,
            },
        ],
        tool_call_id="c1",
        name="tap",
    )
    assert middleware.wrap_tool_call(request, handler=lambda _: result) is result

    emitted = _drain(events)
    assert [event["event"] for event in emitted] == [
        "taskdoc_snapshot",
        "screen",
        "model_call",
        "tool_call",
        "tool_result",
        "screen",
    ]
    assert emitted[1]["current_app"] == "系统 设置"
    assert emitted[1]["screen_seq"] == 1
    assert emitted[2]["step"] == 1
    assert emitted[2]["tokens_total"] == 15
    assert emitted[3]["args"]["intent"] == "打开 WLAN"
    assert emitted[4]["ok"] is True
    assert emitted[5] == {
        "event": "screen",
        "step": 1,
        "image": "data:image/png;base64,QUJD",
        "current_app": "设置",
        "screen_seq": 2,
        "ts": emitted[5]["ts"],
    }


def test_web_event_middleware_marks_warning_as_failed_and_emits_warning():
    events: queue.Queue[dict] = queue.Queue()
    middleware = WebEventMiddleware(events)
    request = SimpleNamespace(tool_call={"name": "tap", "args": {"intent": "确认付款"}})
    warning = ToolMessage(
        content="⚠️ 已拦截（未执行）：tap\n带 confirm_irreversible=true 重试",
        tool_call_id="c1",
        name="tap",
        status="error",
    )

    middleware.wrap_tool_call(request, handler=lambda _: warning)
    emitted = _drain(events)

    assert emitted[1]["event"] == "tool_result"
    assert emitted[1]["ok"] is False
    assert emitted[2]["event"] == "safety_warning"


def test_thin_agent_appends_web_middleware_via_extension_point(monkeypatch, tmp_path):
    captured = {}

    def fake_create_agent(model, *, tools, middleware, checkpointer):  # noqa: ANN001
        captured["middleware"] = middleware
        return SimpleNamespace()

    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    modules = {
        "phone_agent.v2.model": {"build_chat_model": lambda config: SimpleNamespace()},
        "phone_agent.v2.session": {"PhoneSession": lambda config: SimpleNamespace()},
        "phone_agent.v2.tools": {"build_tools": lambda session, config: []},
        "phone_agent.v2.prompts": {"get_system_prompt": lambda lang="cn": "system"},
    }
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, name, module)

    config = SimpleNamespace(
        trace_dir=str(tmp_path),
        trace_enabled=False,
        taskdoc_enabled=False,
        compact_enabled=False,
        diagnostic_evidence=False,
        safety_mode="off",
        lang="cn",
    )
    observer = WebEventMiddleware(queue.Queue())

    from phone_agent.v2.agent import ThinPhoneAgent

    ThinPhoneAgent(config, extra_middleware=[observer])

    assert captured["middleware"][-1] is observer


class _HitlFakeAgent:
    answer: str | None = None

    def __init__(self, config, *, extra_middleware):  # noqa: ANN001
        self.run_id = "fake-run"
        self.trace_path = None
        self.session = SimpleNamespace(takeover_reason=None)
        self.middleware = extra_middleware[0]

    def run(self, task: str, hitl_handler) -> RunResult:  # noqa: ANN001
        type(self).answer = hitl_handler(f"是否执行：{task}")
        return RunResult(True, "done", 1, None)


def test_bridge_hitl_blocks_until_answer_and_terminal_result_lands():
    bridge = WebRunBridge(
        config_factory=lambda overrides: SimpleNamespace(**(overrides or {})),
        agent_factory=_HitlFakeAgent,
    )
    bridge.start("打开设置")

    deadline = time.monotonic() + 2
    while bridge.snapshot()["pending_hitl_prompt"] is None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert bridge.wait(0.02) is False

    bridge.submit_hitl("approve")
    assert bridge.wait(2) is True
    state = bridge.snapshot()

    assert _HitlFakeAgent.answer == "approve"
    assert state["status"] == "succeeded"
    assert state["pending_hitl_prompt"] is None
    assert state["final_result"] == {
        "success": True,
        "reason": "done",
        "steps": 1,
        "trace_path": None,
    }


def test_bridge_maps_budget_exhaustion_to_terminal_status():
    class BudgetAgent(_HitlFakeAgent):
        def run(self, task: str, hitl_handler) -> RunResult:  # noqa: ANN001
            return RunResult(False, "token_budget_exhausted", 4, None)

    bridge = WebRunBridge(
        config_factory=lambda overrides: SimpleNamespace(), agent_factory=BudgetAgent
    )
    bridge.start("长任务")
    assert bridge.wait(2) is True
    assert bridge.snapshot()["status"] == "budget_exhausted"


def test_bridge_maps_takeover_to_terminal_status():
    class TakeoverAgent(_HitlFakeAgent):
        def run(self, task: str, hitl_handler) -> RunResult:  # noqa: ANN001
            self.session.takeover_reason = "请人工完成登录"
            return RunResult(False, "请人工完成登录", 2, None)

    bridge = WebRunBridge(
        config_factory=lambda overrides: SimpleNamespace(), agent_factory=TakeoverAgent
    )
    bridge.start("登录")
    assert bridge.wait(2) is True
    assert bridge.snapshot()["status"] == "takeover"


def test_bridge_surfaces_background_error_as_terminal_result():
    class ErrorAgent(_HitlFakeAgent):
        def run(self, task: str, hitl_handler) -> RunResult:  # noqa: ANN001
            raise RuntimeError("boom")

    bridge = WebRunBridge(
        config_factory=lambda overrides: SimpleNamespace(), agent_factory=ErrorAgent
    )
    bridge.start("触发错误")
    assert bridge.wait(2) is True
    state = bridge.snapshot()
    assert state["status"] == "error"
    assert state["final_result"]["success"] is False
    assert state["final_result"]["reason"] == "error: RuntimeError: boom"
