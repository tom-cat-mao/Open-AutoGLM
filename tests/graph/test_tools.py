from __future__ import annotations

import importlib

from phone_agent.graph.tools import dispatch_tool, get_all_tools


def _schema(tool) -> dict:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        if hasattr(args_schema, "model_json_schema"):
            return args_schema.model_json_schema()
        if hasattr(args_schema, "schema"):
            return args_schema.schema()
    return getattr(tool, "args", {})


def test_tool_schema_hides_device_factory() -> None:
    for tool in get_all_tools():
        assert "device_factory" not in str(_schema(tool))


def test_dispatch_tool_injects_fake_device_for_touch_navigation_and_launch(
    fake_device,
) -> None:
    cases = [
        ({"_metadata": "do", "action": "Tap", "element": [500, 250]}, "tap"),
        (
            {
                "_metadata": "do",
                "action": "Swipe",
                "start": [0, 0],
                "end": [1000, 1000],
            },
            "swipe",
        ),
        ({"_metadata": "do", "action": "Back"}, "back"),
        ({"_metadata": "do", "action": "Home"}, "home"),
        ({"_metadata": "do", "action": "Launch", "app": "Chrome"}, "launch_app"),
        (
            {"_metadata": "do", "action": "Double Tap", "element": [500, 500]},
            "double_tap",
        ),
        (
            {"_metadata": "do", "action": "Long Press", "element": [500, 500]},
            "long_press",
        ),
    ]

    for action, expected_call in cases:
        result = dispatch_tool(
            action, 1000, 2000, "device-1", device_factory=fake_device
        )
        assert result.success is True
        assert fake_device.calls[-1][0] == expected_call


def test_dispatch_tool_converts_relative_coordinates_in_tool_layer(fake_device) -> None:
    dispatch_tool(
        {"_metadata": "do", "action": "Tap", "element": [500, 250]},
        1080,
        2400,
        "device-1",
        device_factory=fake_device,
    )

    assert fake_device.calls[-1] == ("tap", (540, 600, "device-1"), {})


def test_dispatch_type_uses_injected_keyboard_flow(fake_device, monkeypatch) -> None:
    type_text_module = importlib.import_module("phone_agent.graph.tools.type_text")
    monkeypatch.setattr(type_text_module.time, "sleep", lambda _: None)

    result = dispatch_tool(
        {"_metadata": "do", "action": "Type", "text": "hello"},
        1000,
        2000,
        "device-1",
        device_factory=fake_device,
    )

    assert result.success is True
    assert [call[0] for call in fake_device.calls] == [
        "detect_and_set_adb_keyboard",
        "clear_text",
        "type_text",
        "restore_keyboard",
    ]


def test_dispatch_wait_and_misc_without_device(monkeypatch) -> None:
    wait_module = importlib.import_module("phone_agent.graph.tools.wait")
    monkeypatch.setattr(wait_module.time, "sleep", lambda _: None)

    for action in (
        {"_metadata": "do", "action": "Wait", "duration": "0 seconds"},
        {"_metadata": "do", "action": "Note", "message": "note"},
        {"_metadata": "do", "action": "Call_API", "message": "api"},
        {"_metadata": "do", "action": "Interact", "message": "need user"},
    ):
        result = dispatch_tool(action, 1000, 2000, device_factory=object())
        assert result.success is True
