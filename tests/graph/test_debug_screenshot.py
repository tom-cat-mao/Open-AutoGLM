"""Debug-full screenshot persistence tests."""

from __future__ import annotations

import base64
import json

from phone_agent.graph.trace import JsonlTraceWriter, save_debug_screenshot

_PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
    )
).decode()


def _writer(tmp_path):
    return JsonlTraceWriter(trace_id="t-debug", trace_dir=tmp_path)


def _config(writer, debug_full):
    return {"configurable": {"trace_writer": writer, "debug_full": debug_full}}


def test_save_debug_screenshot_writes_file_and_emits_event(tmp_path):
    writer = _writer(tmp_path)
    state = {"step_count": 7}
    save_debug_screenshot(_config(writer, True), state, "observation", _PNG_1PX)
    saved = tmp_path / "screenshots" / "step_007_observation.png"
    assert saved.exists()
    assert saved.read_bytes() == base64.b64decode(_PNG_1PX)
    events = [json.loads(line) for line in writer.path.read_text().splitlines()]
    shot = [e for e in events if e["event"] == "debug_screenshot"]
    assert len(shot) == 1
    assert shot[0]["payload"]["source"] == "observation"
    assert shot[0]["payload"]["path"].endswith("step_007_observation.png")


def test_save_debug_screenshot_noop_when_flag_off(tmp_path):
    writer = _writer(tmp_path)
    save_debug_screenshot(_config(writer, False), {"step_count": 1}, "observation", _PNG_1PX)
    assert not (tmp_path / "screenshots").exists()
    if writer.path.exists():
        assert writer.path.read_text().strip() == ""


def test_save_debug_screenshot_noop_without_writer_or_payload(tmp_path):
    save_debug_screenshot(
        {"configurable": {"debug_full": True}}, {"step_count": 1}, "observation", _PNG_1PX
    )
    writer = _writer(tmp_path)
    save_debug_screenshot(_config(writer, True), {"step_count": 1}, "locate_frame", None)
    assert not (tmp_path / "screenshots").exists()
