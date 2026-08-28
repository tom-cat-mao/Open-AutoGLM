"""A5 local-first full-fidelity tests: screenshots on disk, unredacted evidence,
step replay, and the ``--share`` redacted copy.

Drives the *real* ``ThinPhoneAgent`` middleware stack with a scripted model + a
fake session that returns multimodal ``[OBS text + screenshot image]`` tool
results (``sys.modules`` injection, mirroring ``test_evidence_schema.py``), so
the diagnostic evidence stream + on-disk screenshots are produced exactly as on
device — then asserts the A5 semantics:

* screenshots are decoded to ``<run_dir>/screenshots/screen-<seq>.png``
  (idempotent, 0600) and the evidence ``image`` field gains a relative ``path``;
* in full-fidelity mode the stream keeps sensitive substrings UNREDACTED and
  text UNTRUNCATED, and records the model's thinking + tool calls per step;
* the analyzer builds a per-step ``replay`` the report renders as ``<img>`` refs;
* the ``--share`` path deep-redacts the summary + evidence and drops every
  screenshot ``path`` so the shared HTML leaks neither text nor screenshots.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

# A tiny valid PNG (1x1) so base64.b64decode succeeds and a real file lands.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()
_PNG_URL = f"data:image/png;base64,{_PNG_B64}"

FAKE_PHONE = "13800138000"
FAKE_KEY = "sk-abcDEF0123456789xyz"
LONG_THINK = "我要先观察屏幕，" + ("推理" * 60)  # > 64 chars, proves no trace cap


class ScriptedToolModel(BaseChatModel):
    responses: list[AIMessage]
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ANN001
        response = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"


def _tool_call(name: str, args: dict, call_id: str, content: str = "") -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


@dataclass
class FakeConfig:
    model_name: str = "scripted"
    grounding_provider: str = "none"
    lang: str = "cn"
    max_model_calls: int = 20
    trace_dir: str = ".traces"
    trace_enabled: bool = False
    taskdoc_enabled: bool = True
    taskdoc_nudge_steps: int = 5
    device_id: str | None = "emulator-5554"
    diagnostic_evidence: bool = True
    diagnostic_evidence_dir: str = ".evidence"
    diagnostic_unredacted: bool = True


@dataclass
class FakeObservation:
    screenshot_b64: str = _PNG_B64
    width: int = 1080
    height: int = 2400
    current_app: str = "com.android.settings"
    screen_seq: int = 0
    marks: dict = field(default_factory=dict)


@dataclass
class FakeSession:
    config: Any = None
    screen_seq: int = 0
    finished: bool = False
    finish_summary: str | None = None
    takeover_reason: str | None = None
    task_doc: Any = None
    seen_states: set = field(default_factory=set)
    nudged: bool = False

    def observe(self) -> FakeObservation:
        self.screen_seq += 1
        self.seen_states.add(("com.android.settings", self.screen_seq))
        return FakeObservation(screen_seq=self.screen_seq)


def _build_tools(session: FakeSession):
    from phone_agent.v2.taskdoc import TaskDoc, TaskItem

    @tool
    def read_screen() -> list:
        """Re-observe: returns a multimodal [OBS text + screenshot image] block."""
        obs = session.observe()
        text = f"[OBS] app={obs.current_app} screen#{obs.screen_seq}\nmarks (2): a; b"
        return [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {"url": _PNG_URL},
                "screen_seq": obs.screen_seq,
            },
        ]

    @tool
    def update_task_doc(items: list[dict] | None = None) -> str:
        """Maintain the task board."""
        current = session.task_doc or TaskDoc()
        candidate = TaskDoc(
            goal_base=current.goal_base,
            amendments=list(current.amendments),
            items=list(current.items),
            facts=list(current.facts),
        )
        if items is not None:
            candidate.items = [
                TaskItem(
                    id=str(it.get("id", "")),
                    content=str(it.get("content", "")),
                    status=str(it.get("status", "pending")),
                    reason=it.get("reason"),
                    evidence_note=it.get("evidence_note"),
                )
                for it in items
            ]
        error = candidate.validate()
        if error is not None:
            return f"未写入（校验失败）：{error}"
        session.task_doc = candidate
        return "已更新任务板。"

    @tool
    def type_text(text: str) -> str:
        """Type text into the focused field (arg carries a sensitive value)."""
        return f"OK. typed {text!r}\n[OBS] app=com.android.settings screen#1"

    @tool
    def finish(summary: str, evidence: list[str]) -> str:
        """Declare the task finished; return echoes a sensitive key."""
        doc = session.task_doc
        if doc is not None and doc.has_open_items():
            return f"路线仍有未完成项：{doc.open_items_summary()}。"
        session.finished = True
        session.finish_summary = summary
        return f"已记录完成声明（调试 token={FAKE_KEY}）"

    return [read_screen, update_task_doc, type_text, finish]


def _run_scripted(
    tmp_path: Path, responses: list[AIMessage], unredacted: bool = True
) -> tuple[str, FakeSession, Path]:
    """Assemble + run a ThinPhoneAgent over injected fakes; return evidence + run dir."""

    run_dir = tmp_path / "run"
    config = FakeConfig(
        diagnostic_evidence_dir=str(run_dir), diagnostic_unredacted=unredacted
    )
    session = FakeSession(config=config)
    model = ScriptedToolModel(responses=responses)

    model_mod = types.ModuleType("phone_agent.v2.model")
    model_mod.build_chat_model = lambda cfg: model
    session_mod = types.ModuleType("phone_agent.v2.session")
    session_mod.PhoneSession = lambda cfg: session
    tools_mod = types.ModuleType("phone_agent.v2.tools")
    tools_mod.build_tools = lambda sess, cfg: _build_tools(sess)
    prompts_mod = types.ModuleType("phone_agent.v2.prompts")
    prompts_mod.get_system_prompt = lambda lang="cn": "你是手机智能体。"

    injected = {
        "phone_agent.v2.model": model_mod,
        "phone_agent.v2.session": session_mod,
        "phone_agent.v2.tools": tools_mod,
        "phone_agent.v2.prompts": prompts_mod,
    }
    saved = {name: sys.modules.get(name) for name in injected}
    for name, mod in injected.items():
        sys.modules[name] = mod
    try:
        from phone_agent.v2.agent import ThinPhoneAgent

        agent = ThinPhoneAgent(config)
        agent.run("打开设置并输入手机号", hitl_handler=lambda prompt: "approve")
        return agent.evidence_path, session, run_dir
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def _default_responses() -> list[AIMessage]:
    return [
        _tool_call("read_screen", {}, "c1", content=LONG_THINK),
        _tool_call(
            "update_task_doc",
            {"items": [{"id": "s1", "content": "打开设置", "status": "completed", "evidence_note": "screen#1 可见"}]},
            "c2",
            content="屏幕已就绪，标记步骤完成",
        ),
        _tool_call("type_text", {"text": f"我的手机号是 {FAKE_PHONE}"}, "c3", content="输入手机号"),
        _tool_call("finish", {"summary": "完成", "evidence": ["设置页已打开"]}, "c4"),
        AIMessage(content="任务完成"),
    ]


@pytest.fixture
def run(tmp_path):
    path, session, run_dir = _run_scripted(tmp_path, _default_responses())
    assert path is not None and Path(path).exists()
    events = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return path, events, session, run_dir


# --------------------------------------------------------------------------
# screenshots on disk
# --------------------------------------------------------------------------
def test_screenshots_written_to_disk_with_relative_path(run):
    _, events, _, run_dir = run
    shots = run_dir / "screenshots"
    assert shots.is_dir(), "screenshots dir was not created"
    pngs = sorted(shots.glob("screen-*.png"))
    assert pngs, "no screenshot files were written"
    # the decoded file is the real PNG bytes (base64 decoded), not the b64 text.
    assert pngs[0].read_bytes() == _PNG_BYTES
    # file perms are 0600 (owner-only, local-first privacy).
    assert stat.S_IMODE(pngs[0].stat().st_mode) == 0o600
    # every image-bearing observation records a relative path pointing at the file.
    img_obs = [e for e in events if e["event"] == "tool_observation" and (e.get("image") or {}).get("present")]
    assert img_obs, "no image-bearing tool observation recorded"
    for obs in img_obs:
        path = obs["image"].get("path")
        assert path and path.startswith("screenshots/screen-")
        assert (run_dir / path).exists()


def test_screenshot_write_is_idempotent_per_seq(tmp_path):
    # Writing the same screen_seq twice must overwrite (one file), not accumulate.
    from phone_agent.v2.middleware.diagnostic import DiagnosticEvidenceMiddleware

    mw = DiagnosticEvidenceMiddleware("r", evidence_dir=str(tmp_path), enabled=True)
    rel1 = mw._write_screenshot(4, _PNG_URL)
    rel2 = mw._write_screenshot(4, _PNG_URL)  # same seq again
    assert rel1 == rel2 == "screenshots/screen-4.png"
    pngs = list((tmp_path / "screenshots").glob("*.png"))
    assert len(pngs) == 1, "same seq must overwrite, not create a second file"
    assert (tmp_path / rel1).read_bytes() == _PNG_BYTES
    # a non-data url / undecodable payload writes nothing and returns None.
    assert mw._write_screenshot(9, "not-a-data-url") is None
    assert not (tmp_path / "screenshots" / "screen-9.png").exists()


def test_no_base64_in_stream_even_with_screenshots(run):
    path, events, _, _ = run
    raw = Path(path).read_text(encoding="utf-8")
    assert _PNG_B64 not in raw, "screenshot base64 leaked into the evidence stream"
    assert "data:image" not in raw, "a data: URL leaked into the evidence stream"


# --------------------------------------------------------------------------
# full-fidelity (unredacted) evidence
# --------------------------------------------------------------------------
def test_unredacted_keeps_sensitive_and_full_text(run):
    _, events, _, _ = run
    # arg with a phone number is kept verbatim (NOT redacted) in full-fidelity.
    invoke = next(e for e in events if e["event"] == "tool_invoke" and e.get("tool") == "type_text")
    assert FAKE_PHONE in invoke["args"]["text"]
    assert "<redacted>" not in invoke["args"]["text"]
    # finish return echoing an sk- key is kept verbatim too.
    finish_obs = next(e for e in events if e["event"] == "tool_observation" and e.get("tool") == "finish")
    assert FAKE_KEY in finish_obs["result_text"]
    # model thinking > 64 chars is recorded full (no trace cap, no truncation marker).
    resp = next(e for e in events if e["event"] == "model_response" and e.get("thinking"))
    assert len(resp["thinking"]) > 64
    assert isinstance(resp["thinking"], str)


def test_model_response_records_thinking_and_tool_calls(run):
    _, events, _, _ = run
    responses = [e for e in events if e["event"] == "model_response"]
    assert responses, "no model_response events recorded"
    # the first turn calls read_screen and carries the model's thinking.
    first = responses[0]
    assert first["tool_calls"][0]["name"] == "read_screen"
    assert first["thinking"] == LONG_THINK
    # config digest records the unredacted flag + device id.
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["config_digest"]["unredacted"] is True
    assert run_start["config_digest"]["device_id"] == "emulator-5554"


def test_redacted_mode_still_redacts(tmp_path):
    # When unredacted=False (the --share evidence policy at capture time), the
    # stream falls back to redaction — parity with the pre-A5 behavior.
    path, _, _ = _run_scripted(tmp_path, _default_responses(), unredacted=False)
    events = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    invoke = next(e for e in events if e["event"] == "tool_invoke" and e.get("tool") == "type_text")
    assert FAKE_PHONE not in invoke["args"]["text"]
    assert "<redacted>" in invoke["args"]["text"]


# --------------------------------------------------------------------------
# analyzer replay
# --------------------------------------------------------------------------
def test_analyzer_builds_step_replay_with_image_path(run):
    _, events, _, _ = run
    from analyze import build_summary
    from evidence import EvidenceView

    view = EvidenceView.from_events(events)
    summary = build_summary(
        {"finished": True, "reason": "finished"},
        view,
        run_id="t1",
        created_at="t",
        target="打开设置",
    )
    replay = summary["replay"]
    assert replay, "analyzer produced no replay"
    # step 1 carries the model thinking + a tool call whose observation has an
    # on-disk screenshot path.
    step1 = next(s for s in replay if s["step"] == 1)
    assert step1["thinking"] == LONG_THINK
    tc = step1["tool_calls"][0]
    assert tc["tool"] == "read_screen"
    assert tc["image"]["path"].startswith("screenshots/screen-")


# --------------------------------------------------------------------------
# --share redacted, screenshot-free copy
# --------------------------------------------------------------------------
def test_share_copy_is_redacted_and_screenshot_free(tmp_path):
    import run_diagnosis

    rc = run_diagnosis.main(
        ["run", "--dry-run", "--output-dir", str(tmp_path), "--quiet", "--share", "分享冒烟"]
    )
    assert rc == 0
    run_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    share = run_dir / "report-share.html"
    full = run_dir / "report.html"
    assert share.exists() and full.exists()
    # share copy carries no screenshot file reference (only the static template
    # placeholder ``screen-<seq>.png`` may appear, never a concrete screen-N.png).
    share_html = share.read_text(encoding="utf-8")
    assert 'src="screenshots/' not in share_html
    assert "screenshots/screen-1.png" not in share_html
    # a redacted share summary is written alongside.
    assert (run_dir / "summary-share.json").exists()
    # both reports are base64-free and 0600.
    assert "data:image" not in share_html
    assert stat.S_IMODE(share.stat().st_mode) == 0o600


def test_share_copy_redacts_sensitive_text(tmp_path):
    # Build a summary that contains a sensitive value, write it, then run the
    # ``report --share`` path and assert the value is gone from the share HTML.
    import run_diagnosis
    from report import render_html

    summary = {
        "run_id": "t1", "created_at": "t", "target": f"给 {FAKE_PHONE} 发消息",
        "verdict": "success", "run_dir": str(tmp_path), "command": [], "duration_sec": 1,
        "steps": 1, "evidence_stream": None, "trace": None, "artifacts": {},
        "terminal": {"finished": True, "finish_summary": f"token {FAKE_KEY}", "takeover_reason": None,
                     "reason": None, "returncode": 0},
        "finish_gate": {"attempted": True, "accepted": True, "blocked_by_open_items": False,
                        "open_items_at_finish": [], "rejections": []},
        "taskdoc_final": {"goal_base": f"给 {FAKE_PHONE} 发消息", "amendments": [], "items": [],
                          "facts": [], "counts": {"total": 0, "completed": 0, "in_progress": 0, "pending": 0, "blocked": 0},
                          "open_item_count": 0, "terminal_state": "no_board"},
        "stagnation": {}, "context": {}, "hitl": {"decisions": []}, "tool_health": {"by_tool": {}},
        "grounding": {}, "visual": {}, "model": {}, "replay": [], "findings": [], "recommendations": [],
    }
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    # sanity: the full report DOES carry the sensitive text (local-first fidelity).
    assert FAKE_PHONE in render_html(summary, [])

    out = tmp_path / "report-share.html"
    rc = run_diagnosis.main(["report", str(summary_path), "--share", "--output", str(out)])
    assert rc == 0
    share_html = out.read_text(encoding="utf-8")
    assert FAKE_PHONE not in share_html
    assert FAKE_KEY not in share_html
    # the redaction marker survives (angle brackets are \u-escaped in the JSON
    # island, so match the escaped form / the bare word).
    assert "redacted" in share_html
