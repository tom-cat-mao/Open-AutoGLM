from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from phone_agent.graph.context import default_context_budget, default_screen_belief


@dataclass
class FakeScreenshot:
    width: int = 1000
    height: int = 2000
    base64_data: str = "fake-image"


class FakeDeviceFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def get_screenshot(self, device_id: str | None = None) -> FakeScreenshot:
        self._record("get_screenshot", device_id)
        return FakeScreenshot()

    def get_current_app(self, device_id: str | None = None) -> str:
        self._record("get_current_app", device_id)
        return "FakeApp"

    def tap(self, x: int, y: int, device_id: str | None = None) -> None:
        self._record("tap", x, y, device_id)

    def double_tap(self, x: int, y: int, device_id: str | None = None) -> None:
        self._record("double_tap", x, y, device_id)

    def long_press(self, x: int, y: int, device_id: str | None = None) -> None:
        self._record("long_press", x, y, device_id)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        device_id: str | None = None,
    ) -> None:
        self._record("swipe", start_x, start_y, end_x, end_y, device_id)

    def back(self, device_id: str | None = None) -> None:
        self._record("back", device_id)

    def home(self, device_id: str | None = None) -> None:
        self._record("home", device_id)

    def launch_app(self, app: str, device_id: str | None = None) -> bool:
        self._record("launch_app", app, device_id)
        return app != "missing"

    def detect_and_set_adb_keyboard(self, device_id: str | None = None) -> str:
        self._record("detect_and_set_adb_keyboard", device_id)
        return "original-ime"

    def clear_text(self, device_id: str | None = None) -> None:
        self._record("clear_text", device_id)

    def type_text(self, text: str, device_id: str | None = None) -> None:
        self._record("type_text", text, device_id)

    def restore_keyboard(self, ime: str, device_id: str | None = None) -> None:
        self._record("restore_keyboard", ime, device_id)

    def get_focused_window_or_app(self, device_id: str | None = None) -> str | None:
        self._record("get_focused_window_or_app", device_id)
        return None

    def get_top_activity(self, device_id: str | None = None) -> str | None:
        self._record("get_top_activity", device_id)
        return None

    def is_keyboard_visible(self, device_id: str | None = None) -> bool:
        self._record("is_keyboard_visible", device_id)
        return False


@pytest.fixture
def fake_device() -> FakeDeviceFactory:
    return FakeDeviceFactory()


@pytest.fixture
def base_state() -> dict[str, Any]:
    return {
        "task": "测试任务",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "old"},
                    {"type": "image_url", "image_url": {"url": "data"}},
                ],
            }
        ],
        "step_count": 1,
        "max_steps": 5,
        "lang": "cn",
        "screen_width": 1000,
        "screen_height": 2000,
        "screenshot_b64": "fake-image",
        "current_app": "FakeApp",
        "screen_id": "screen-1",
        "screen_hash": "screen-1",
        "observation": None,
        "mark_registry": None,
        "screen_structure": None,
        "object_registry": None,
        "screen_structure_summary": None,
        "object_registry_summary": None,
        "object_registry_binding": None,
        "object_set_version": None,
        "structure_topology_digest": None,
        "object_trace_summary": None,
        "thinking": "thought",
        "action_raw": '{"_metadata":"do","action":"Tap","element":[500,500]}',
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
        "intent_raw": None,
        "grounding_error": None,
        "grounding_result": None,
        "grounding_provider": None,
        "grounding_latency_ms": None,
        "grounding_failure_code": None,
        "grounding_screen_hash": None,
        "grounding_observation": None,
        "grounding_candidates": [],
        "grounding_candidate_count": 0,
        "selected_grounding_candidate_id": None,
        "expected_outcome": None,
        "action_result": None,
        "reflection": None,
        "action_succeeded": True,
        "reflection_verdict": None,
        "failure_cause": None,
        "suggested_strategy": None,
        "retry_count": 0,
        "context_mode": "inject",
        "screen_belief": default_screen_belief(),
        "action_outcome_summary": None,
        "failure_memory": [],
        "summarized_history": "",
        "context_budget": default_context_budget(),
        "context_truncated": False,
        "context_block_chars": 0,
        "failure_memory_hit_count": 0,
        "repeated_failure_count": 0,
        "gui_memory": {
            "visited_screens": [],
            "tried_actions": [],
            "scroll_memory": {},
            "task_progress": {},
        },
        "verifier_result": None,
        "verifier_status": None,
        "verifier_failure_cause": None,
        "verifier_evidence": None,
        "pending_interrupt": None,
        "interrupt_message": None,
        "interrupt_result": None,
        "pending_execute": False,
        "action_confirmed": False,
        "hitl_count": 0,
        "finished": False,
        "error": None,
        "device_id": "device-1",
    }
