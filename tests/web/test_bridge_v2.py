"""V2 bridge features: screenshot history, step details, soft stop, usage."""

from __future__ import annotations

import time
from types import SimpleNamespace

from phone_agent.v2.agent import RunResult
from phone_agent.web.bridge import WebRunBridge


class _FakeSession:
    def __init__(self) -> None:
        from phone_agent.v2.usage import UsageLedger

        self.takeover_reason = None
        self.usage_ledger = UsageLedger()
        self.app_store = None
        self.task_doc = None


class _FakeAgent:
    def __init__(self, config, extra_middleware=None, **_kwargs):
        self.run_id = "fake-run"
        self.trace_path = None
        self.session = _FakeSession()
        self._middleware = (extra_middleware or [None])[0]

    def run(self, task, hitl_handler=None):
        mw = self._middleware
        for index in range(2):
            mw.before_model({"messages": []}, None)
            if mw.stop_requested:
                break
            mw.wrap_model_call(SimpleNamespace(), lambda _r: SimpleNamespace(result=[]))
            mw.wrap_tool_call(
                SimpleNamespace(
                    tool_call={
                        "name": "tap",
                        "args": {"intent": f"步骤{index}", "target_mark_id": "ax_1@e1"},
                        "id": f"c{index}",
                    }
                ),
                lambda _r: SimpleNamespace(
                    content=[
                        {"type": "text", "text": f"[OBS] app=设置 screen#{index + 1}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,frame{index}"},
                            "screen_seq": index + 1,
                        },
                    ],
                    status="success",
                ),
            )
            time.sleep(0.05)
        return RunResult(True, "done", 2, None)


def _bridge() -> WebRunBridge:
    return WebRunBridge(
        {},
        config_factory=lambda _o: SimpleNamespace(memory_dir="memory"),
        agent_factory=_FakeAgent,
    )


def test_snapshot_exposes_screens_steps_usage():
    bridge = _bridge()
    bridge.start("测试任务")
    assert bridge.wait(timeout=10)
    state = bridge.snapshot()
    assert state["status"] == "succeeded"
    assert len(state["screens"]) == 2
    assert state["screens"][0]["seq"] == 1
    assert len(state["steps"]) == 2
    assert state["steps"][0]["args"]["target_mark_id"] == "ax_1@e1"
    assert "usage" in state


def test_soft_stop_sets_takeover_channel():
    bridge = _bridge()

    class _StopAgent(_FakeAgent):
        def run(self, task, hitl_handler=None):
            mw = self._middleware
            mw.request_stop()
            for _ in range(3):
                update = mw.before_model({"messages": []}, None)
                if update and update.get("jump_to") == "end":
                    break
            return RunResult(False, "用户从 Web 控制台停止", 0, None)

    bridge._agent_factory = _StopAgent
    bridge.start("停止测试")
    assert bridge.wait(timeout=10)
    state = bridge.snapshot()
    assert state["status"] == "takeover"


def test_request_stop_without_run_is_noop():
    bridge = _bridge()
    assert bridge.request_stop() is False


def test_kb_entries_empty_without_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = _bridge()
    assert bridge.kb_entries() == []
    assert bridge.run_dream()["status"] == "skipped"
