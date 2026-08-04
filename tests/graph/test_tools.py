from __future__ import annotations

import importlib
import subprocess

import pytest

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


@pytest.mark.parametrize(
    ("relative", "width", "height", "expected"),
    [
        # exact origin
        ([0, 0], 1080, 2400, (0, 0)),
        # exact max boundary maps to full pixels
        ([1000, 1000], 1080, 2400, (1080, 2400)),
        # mid-range square mapping
        ([500, 500], 1080, 2400, (540, 1200)),
        ([500, 250], 1080, 2400, (540, 600)),
        # int truncation, not rounding: 333/1000*1080 = 359.64
        ([333, 667], 1080, 2400, (359, 1600)),
        # out-of-range inputs map proportionally (no clamp): the validator
        # rejects >1000/<0 upstream, so convert is only reached with 0-1000
        ([1500, -100], 1080, 2400, (1620, -240)),
        # non-square aspect: x scales by width, y scales by height
        ([100, 900], 720, 1280, (72, 1152)),
        ([333, 667], 720, 1280, (239, 853)),
    ],
)
def test_convert_relative_to_absolute_boundaries(
    relative: list[int], width: int, height: int, expected: tuple[int, int]
) -> None:
    from phone_agent.graph.tools.coords import convert_relative_to_absolute

    assert convert_relative_to_absolute(relative, width, height) == expected


def test_gesture_compiler_keeps_relative_coordinates() -> None:
    from phone_agent.actions.gesture import compile_action_to_gesture

    gesture = compile_action_to_gesture({"_metadata": "do", "action": "Tap", "element": [500, 250]})

    assert gesture.kind == "tap"
    assert gesture.params["element"] == [500, 250]
    assert "screen_width" not in gesture.params


def test_gesture_trace_sanitizes_typed_text(base_state, fake_device, tmp_path) -> None:
    import json

    from phone_agent.graph.nodes.execute import execute_node
    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(trace_id="gesture-private", trace_dir=tmp_path, redact=False)
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "13800138000"}

    execute_node(
        base_state,
        {"configurable": {"device_factory": fake_device, "trace_writer": writer, "verbose": False}},
    )

    raw = writer.path.read_text(encoding="utf-8")
    assert "13800138000" not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    gesture = next(record for record in records if record["event"] == "gesture_compiled")
    assert gesture["payload"]["gesture"]["params"]["text"] == "<redacted>"


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

    wait_result = dispatch_tool(
        {"_metadata": "do", "action": "Wait", "duration": "0 seconds"},
        1000,
        2000,
        device_factory=object(),
    )
    assert wait_result.success is True

    for action in (
        {"_metadata": "do", "action": "Note", "message": "note"},
        {"_metadata": "do", "action": "Call_API", "message": "api"},
        {"_metadata": "do", "action": "Interact", "message": "need user"},
    ):
        result = dispatch_tool(action, 1000, 2000, device_factory=object())
        assert result.success is False


def test_adb_launch_app_uses_am_start_not_monkey(monkeypatch) -> None:
    device_module = importlib.import_module("phone_agent.adb.device")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "resolve-activity" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="com.android.settings/.Settings\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="Starting: Intent\n", stderr="")

    monkeypatch.setattr(device_module.subprocess, "run", fake_run)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    assert device_module.launch_app("Settings", "device-1") is True

    assert any(
        "am" in cmd
        and "start" in cmd
        and "-n" in cmd
        and "com.android.settings/.Settings" in cmd
        for cmd in calls
    )
    assert not any("monkey" in cmd for cmd in calls)


def test_adb_launch_app_falls_back_when_launcher_component_missing(monkeypatch) -> None:
    device_module = importlib.import_module("phone_agent.adb.device")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "resolve-activity" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="No activity found\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="Starting: Intent\n", stderr="")

    monkeypatch.setattr(device_module.subprocess, "run", fake_run)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    assert device_module.launch_app("Settings", "device-1") is True

    assert any(
        "am" in cmd
        and "start" in cmd
        and "-p" in cmd
        and "com.android.settings" in cmd
        for cmd in calls
    )
    assert not any("monkey" in cmd for cmd in calls)


def test_adb_launch_app_returns_false_on_am_start_error(monkeypatch) -> None:
    device_module = importlib.import_module("phone_agent.adb.device")

    def fake_run(cmd, **kwargs):
        if "resolve-activity" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="com.android.settings/.Settings\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="Error: Activity not started\n", stderr="")

    monkeypatch.setattr(device_module.subprocess, "run", fake_run)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    assert device_module.launch_app("Settings", "device-1") is False


def test_adb_home_falls_back_when_input_hits_inject_events(monkeypatch) -> None:
    device_module = importlib.import_module("phone_agent.adb.device")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "input" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=b"java.lang.SecurityException: inject events permission denied",
                stderr=b"",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(device_module.subprocess, "run", fake_run)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    device_module.home("device-1")

    assert any("input" in cmd and "KEYCODE_HOME" in cmd for cmd in calls)
    assert any("am" in cmd and "android.intent.category.HOME" in cmd for cmd in calls)
