"""DiagnosticEvidenceMiddleware end-to-end schema + redaction tests (§5.2).

Drives the *real* ``ThinPhoneAgent`` middleware stack with a scripted model and a
fake session (``sys.modules`` injection, mirroring ``tests/v2/test_agent_loop.py``)
so the diagnostic evidence stream is produced exactly as it is on device — then
asserts the three guarantees that must never bend:

* the stream is valid JSONL with the §1 event vocabulary;
* **no** field anywhere contains screenshot base64 (image blocks are reduced to
  ``{present, screen_seq, bytes}``);
* OBS ``result_text`` is kept **full** (not truncated to 64 chars — that is the
  trace policy, not the diagnostic policy); and sensitive substrings (a phone
  number in a tool arg, an ``sk-`` key in a tool return) are redacted.
"""

from __future__ import annotations

import json
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

# A recognizable base64 payload: if any byte of it reaches the stream the
# base64-drop guarantee is broken. 400 chars > the 120-char redact threshold too.
B64_SENTINEL = "QUJD" * 100
FAKE_PHONE = "13800138000"
FAKE_KEY = "sk-abcDEF0123456789xyz"
# A long OBS line (> 64 chars) to prove the diagnostic stream does not truncate.
LONG_MARK_DIGEST = "; ".join(f"m{i}:按钮{i}" for i in range(12))


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


def _tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
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
    diagnostic_evidence: bool = True
    diagnostic_evidence_dir: str = ".evidence"


@dataclass
class FakeObservation:
    screenshot_b64: str = B64_SENTINEL
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
        text = (
            f"[OBS] app={obs.current_app} screen#{obs.screen_seq}\n"
            f"marks (12): {LONG_MARK_DIGEST}"
        )
        return [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{B64_SENTINEL}"},
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


def _run_scripted(tmp_path: Path, responses: list[AIMessage]) -> tuple[str, FakeSession]:
    """Assemble + run a ThinPhoneAgent over the injected fakes; return evidence path."""

    config = FakeConfig(diagnostic_evidence_dir=str(tmp_path / ".evidence"))
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
        return agent.evidence_path, session
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


@pytest.fixture
def evidence(tmp_path):
    responses = [
        _tool_call("read_screen", {}, "c1"),
        _tool_call(
            "update_task_doc",
            {"items": [{"id": "s1", "content": "打开设置", "status": "completed", "evidence_note": "screen#1 可见"}]},
            "c2",
        ),
        _tool_call("type_text", {"text": f"我的手机号是 {FAKE_PHONE}"}, "c3"),
        _tool_call("finish", {"summary": "完成", "evidence": ["设置页已打开"]}, "c4"),
        AIMessage(content="任务完成"),
    ]
    path, session = _run_scripted(tmp_path, responses)
    assert path is not None and Path(path).exists()
    events = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return path, events, session


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------
def test_stream_is_valid_jsonl_with_known_events(evidence):
    _, events, _ = evidence
    assert events, "no evidence events were written"
    known = {
        "run_start",
        "model_request",
        "taskdoc_snapshot",
        "tool_invoke",
        "tool_observation",
        "hitl_decision",
        "stagnation_nudge",
        "run_end",
    }
    for ev in events:
        assert ev.get("event") in known, f"unknown event: {ev.get('event')}"
        assert "ts" in ev
    kinds = {ev["event"] for ev in events}
    # The header + at least one model_request + tool cycle + terminal must exist.
    assert "run_start" in kinds
    assert "model_request" in kinds
    assert "tool_invoke" in kinds
    assert "tool_observation" in kinds
    assert "run_end" in kinds


def test_run_start_and_taskdoc_snapshot_shape(evidence):
    _, events, _ = evidence
    run_start = next(e for e in events if e["event"] == "run_start")
    assert "config_digest" in run_start
    assert run_start["config_digest"]["taskdoc_enabled"] is True
    # goal_base was harness-seeded from the task text.
    assert "打开设置" in run_start["task_goal_base"]
    snaps = [e for e in events if e["event"] == "taskdoc_snapshot"]
    assert snaps, "no taskdoc snapshot recorded"
    # The first snapshot is the harness-seeded (empty-route) board; the latest
    # reflects the model's update_task_doc write.
    latest = snaps[-1]
    assert isinstance(latest["items"], list)
    assert latest["items"][0]["status"] == "completed"


# --------------------------------------------------------------------------
# base64-drop guarantee
# --------------------------------------------------------------------------
def test_no_base64_anywhere(evidence):
    path, events, _ = evidence
    raw = Path(path).read_text(encoding="utf-8")
    assert B64_SENTINEL not in raw, "screenshot base64 leaked into the evidence stream"
    assert "data:image" not in raw, "a data: URL leaked into the evidence stream"
    # The image block must be reduced to a summary with a byte count, not a payload.
    obs = [e for e in events if e["event"] == "tool_observation" and (e.get("image") or {}).get("present")]
    assert obs, "the multimodal read_screen observation recorded no image"
    img = obs[0]["image"]
    assert img["present"] is True
    assert img["screen_seq"] is not None
    assert isinstance(img["bytes"], int) and img["bytes"] > 0


# --------------------------------------------------------------------------
# full-text (not 64-char truncated) + redaction
# --------------------------------------------------------------------------
def test_obs_result_text_not_truncated_to_64(evidence):
    _, events, _ = evidence
    read_obs = next(
        e for e in events
        if e["event"] == "tool_observation" and e.get("tool") == "read_screen"
    )
    text = read_obs["result_text"]
    assert isinstance(text, str), "OBS text within DIAG_MAX_TEXT should stay a plain string"
    assert len(text) > 64, "diagnostic stream must NOT apply the 64-char trace cap"
    assert "[OBS] app=com.android.settings" in text
    # obs block parsed from the text.
    assert read_obs["obs"]["current_app"] == "com.android.settings"
    assert read_obs["obs"]["mark_count"] == 12


def test_sensitive_arg_is_redacted(evidence):
    _, events, _ = evidence
    invoke = next(
        e for e in events if e["event"] == "tool_invoke" and e.get("tool") == "type_text"
    )
    arg_text = invoke["args"]["text"]
    assert FAKE_PHONE not in arg_text
    assert "<redacted>" in arg_text


def test_sensitive_return_is_redacted(evidence):
    _, events, _ = evidence
    finish_obs = next(
        e for e in events if e["event"] == "tool_observation" and e.get("tool") == "finish"
    )
    text = finish_obs["result_text"]
    assert FAKE_KEY not in text
    assert "<redacted>" in text
