"""Tests for the two-threshold auto-compact middleware (A4 §3).

All fakes — no real device, MLX, or network. A tiny scripted summariser stands in
for the memory/main model so the T2 fold path is exercised deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from phone_agent.v2.middleware.compact import (
    CompactMiddleware,
    build_compact_middleware,
    infer_context_window,
)


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
@dataclass
class FakeTaskItem:
    id: str
    content: str
    status: str = "pending"
    reason: str | None = None
    evidence_note: str | None = None


@dataclass
class FakeTaskDoc:
    goal_base: str = "打开设置并连上 WLAN"
    amendments: list[str] = field(default_factory=list)
    items: list[FakeTaskItem] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


@dataclass
class FakeSession:
    task_doc: Any = None


@dataclass
class FakeConfig:
    model_name: str = "autoglm-phone-9b"
    context_window: int | None = None
    memory_model: str | None = None
    compact_warn_ratio: float = 0.75
    compact_trigger_ratio: float = 0.92
    lang: str = "cn"


class ScriptModel:
    """A minimal chat model double: returns a canned summary, counts calls."""

    def __init__(self, text: str = "## 目标\n连 WLAN\n## 下一步\n继续", fail_times: int = 0) -> None:
        self.text = text
        self.fail_times = fail_times
        self.calls = 0
        self.last_messages: list[Any] | None = None

    def invoke(self, messages):  # noqa: ANN001
        self.calls += 1
        self.last_messages = messages
        if self.calls <= self.fail_times:
            raise RuntimeError("input too long")
        return AIMessage(content=self.text)


def _mw(session, config, model, **kw) -> CompactMiddleware:
    return CompactMiddleware(session, config, model=model, **kw)


def _big_text(n_tokens: int) -> str:
    # len//4 tokens -> repeat 4 chars per desired token.
    return "x" * (n_tokens * 4)


def _convo(n_pairs: int, tokens_each: int = 2000) -> list[Any]:
    """Build n AI(tool_call)->Tool(result) turn pairs after a Human task."""

    msgs: list[Any] = [HumanMessage(content="task", id="h0")]
    for i in range(n_pairs):
        msgs.append(
            AIMessage(
                content="",
                id=f"a{i}",
                tool_calls=[{"name": "tap", "args": {"target_mark_id": f"ax_{i}"}, "id": f"c{i}", "type": "tool_call"}],
            )
        )
        msgs.append(
            ToolMessage(content=_big_text(tokens_each), id=f"t{i}", tool_call_id=f"c{i}", name="tap")
        )
    return msgs


# --------------------------------------------------------------------------
# window inference
# --------------------------------------------------------------------------
def test_infer_window_default_and_hints():
    assert infer_context_window("autoglm-phone-9b", None) == 256_000
    assert infer_context_window("some-128k-model", None) == 128_000
    assert infer_context_window("x", 42) == 42
    assert infer_context_window(None, None) == 256_000


# --------------------------------------------------------------------------
# T1 warn
# --------------------------------------------------------------------------
def test_t1_warn_fires_once_and_no_context_change():
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=10_000)
    mw = _mw(session, config, ScriptModel())  # warn at 7500, trigger at 9200
    # ~8000 tokens across a few messages: over warn, under trigger.
    msgs = [HumanMessage(content=_big_text(8000), id="h0")]
    result = mw.before_model({"messages": msgs}, runtime=None)
    assert result is not None
    assert len(result["messages"]) == 1
    assert result["messages"][0].content.startswith("[COMPACT_WARN]")
    # One-shot: a second turn under trigger does not warn again.
    assert mw.before_model({"messages": msgs}, runtime=None) is None


def test_below_warn_is_noop():
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=100_000)
    mw = _mw(session, config, ScriptModel())
    msgs = [HumanMessage(content=_big_text(1000), id="h0")]
    assert mw.before_model({"messages": msgs}, runtime=None) is None


# --------------------------------------------------------------------------
# T2 forced compaction
# --------------------------------------------------------------------------
def test_t2_fold_rebuilds_with_summary_and_pinned():
    session = FakeSession(
        task_doc=FakeTaskDoc(items=[FakeTaskItem("1", "打开设置", status="in_progress")])
    )
    config = FakeConfig(context_window=20_000)
    model = ScriptModel()
    mw = _mw(session, config, model, keep_ratio=0.2)

    head = SystemMessage(content="系统提示", id="s0")
    taskdoc = SystemMessage(content="[TASK_DOC]\n## 目标", id="__taskdoc__abc")
    convo = _convo(8, tokens_each=3000)  # ~24k tokens -> over trigger (18400)
    msgs = [head, *convo, taskdoc]

    result = mw.before_model({"messages": msgs}, runtime=None)
    assert result is not None
    out = result["messages"]
    # Rebuild uses REMOVE_ALL_MESSAGES then the new list.
    assert isinstance(out[0], RemoveMessage)
    assert out[0].id == REMOVE_ALL_MESSAGES
    rebuilt = out[1:]
    # head preserved first.
    assert rebuilt[0] is head
    # summary block present and marked.
    summaries = [m for m in rebuilt if isinstance(m, SystemMessage) and m.content.startswith("[COMPACT_SUMMARY]")]
    assert len(summaries) == 1
    assert summaries[0].id.startswith("__compact__")
    # fresh-observation hint present.
    assert any(
        isinstance(m, SystemMessage) and m.content.startswith("[COMPACT_DONE]") for m in rebuilt
    )
    # pinned TaskDoc preserved (kept verbatim, at the tail).
    assert taskdoc is rebuilt[-1]
    assert model.calls == 1


def test_t2_tail_never_starts_on_toolmessage():
    # The recent verbatim tail must not begin with a tool_result whose tool_use
    # was folded into the summary (would dangle at the gateway).
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=20_000)
    mw = _mw(session, config, ScriptModel(), keep_ratio=0.2)
    convo = _convo(8, tokens_each=3000)
    msgs = [SystemMessage(content="sys", id="s0"), *convo]

    result = mw.before_model({"messages": msgs}, runtime=None)
    rebuilt = result["messages"][1:]
    # Find the tail (messages after the summary + before the fresh hint).
    non_meta = [
        m
        for m in rebuilt
        if not (isinstance(m, SystemMessage) and (m.content.startswith("[COMPACT_") or m.content == "sys"))
    ]
    assert non_meta, "expected a verbatim tail"
    assert not isinstance(non_meta[0], ToolMessage)


def test_t2_iterative_feeds_prior_summary_and_supersedes():
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=20_000)
    model = ScriptModel(text="new summary body")
    mw = _mw(session, config, model, keep_ratio=0.2)

    prior = SystemMessage(content="[COMPACT_SUMMARY]\nOLD BODY", id="__compact__old")
    convo = _convo(8, tokens_each=3000)
    msgs = [SystemMessage(content="sys", id="s0"), prior, *convo]

    result = mw.before_model({"messages": msgs}, runtime=None)
    rebuilt = result["messages"][1:]
    summaries = [m for m in rebuilt if isinstance(m, SystemMessage) and m.content.startswith("[COMPACT_SUMMARY]")]
    # Exactly one summary (the old one is superseded, not duplicated).
    assert len(summaries) == 1
    assert "new summary body" in summaries[0].content
    assert summaries[0].id != "__compact__old"
    # The prior summary body was fed into the summariser input.
    joined = "\n".join(
        b if isinstance(b, str) else getattr(b, "content", "")
        for b in (model.last_messages or [])
    )
    assert "OLD BODY" in joined


def test_t2_ptl_retry_then_success():
    # First summariser call fails (too long); the retry (older group dropped) wins.
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=20_000)
    model = ScriptModel(text="ok summary", fail_times=1)
    mw = _mw(session, config, model, keep_ratio=0.2)
    convo = _convo(8, tokens_each=3000)
    msgs = [SystemMessage(content="sys", id="s0"), *convo]

    result = mw.before_model({"messages": msgs}, runtime=None)
    assert result is not None
    assert model.calls == 2  # one failure + one success
    summaries = [
        m for m in result["messages"] if isinstance(m, SystemMessage) and m.content.startswith("[COMPACT_SUMMARY]")
    ]
    assert summaries and "ok summary" in summaries[0].content


def test_t2_fail_open_when_summariser_always_fails():
    # Summariser fails every retry -> fail-open: no fold, fall through to T1 warn.
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=20_000)
    model = ScriptModel(fail_times=99)
    mw = _mw(session, config, model, keep_ratio=0.2)
    convo = _convo(8, tokens_each=3000)
    msgs = [SystemMessage(content="sys", id="s0"), *convo]

    result = mw.before_model({"messages": msgs}, runtime=None)
    # No REMOVE_ALL rebuild; instead the T1 warn (context is also over warn).
    assert result is not None
    assert not any(isinstance(m, RemoveMessage) for m in result["messages"])
    assert result["messages"][0].content.startswith("[COMPACT_WARN]")
    assert model.calls == 3  # exhausted max_ptl_retries


def test_t2_skips_fold_when_too_few_ancient_messages():
    # Over trigger but almost everything is in the keep-tail -> nothing to fold.
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=10_000)
    model = ScriptModel()
    mw = _mw(session, config, model, keep_ratio=0.9, min_fold_messages=4)
    # One giant message over trigger, but only 1 message -> can't fold 4.
    msgs = [HumanMessage(content=_big_text(9500), id="h0")]
    result = mw.before_model({"messages": msgs}, runtime=None)
    # No fold; T1 warn fires instead (and summariser never called).
    assert model.calls == 0
    assert result["messages"][0].content.startswith("[COMPACT_WARN]")


def test_t2_no_model_available_fail_open():
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=20_000)
    mw = _mw(session, config, model=None, keep_ratio=0.2)  # no summariser
    convo = _convo(8, tokens_each=3000)
    msgs = [SystemMessage(content="sys", id="s0"), *convo]
    result = mw.before_model({"messages": msgs}, runtime=None)
    # Fail-open: no rebuild; T1 warn only.
    assert not any(isinstance(m, RemoveMessage) for m in result["messages"])


# --------------------------------------------------------------------------
# reset + builder
# --------------------------------------------------------------------------
def test_reset_rearms_warn():
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=10_000)
    mw = _mw(session, config, ScriptModel())
    msgs = [HumanMessage(content=_big_text(8000), id="h0")]
    assert mw.before_model({"messages": msgs}, runtime=None) is not None
    assert mw.before_model({"messages": msgs}, runtime=None) is None
    mw.reset()
    assert mw.before_model({"messages": msgs}, runtime=None) is not None


def test_builder_reads_config():
    session = FakeSession(task_doc=FakeTaskDoc())
    config = FakeConfig(context_window=50_000, compact_warn_ratio=0.7, compact_trigger_ratio=0.9)
    mw = build_compact_middleware(session, config, model=ScriptModel())
    assert mw.window == 50_000
    assert mw.warn_ratio == 0.7
    assert mw.trigger_ratio == 0.9
