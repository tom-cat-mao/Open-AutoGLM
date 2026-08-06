"""Stage-Sealing Phase A: L1 screen_text_digest, L2 effect_event, bounded crop,
semantic keys. Acceptance behavior is unchanged in this phase."""

from phone_agent.graph.goal import SuccessCriterion
from phone_agent.graph.goal_evidence import (
    L1_DIGEST_TEXT_LIMIT,
    append_effect_event,
    append_evaluation_entries,
    append_screen_text_digest,
    criterion_semantic_key,
    effect_event_entry,
    l1_digest_screen_window,
    normalize_semantic_text,
    screen_text_digest_entry,
    should_record_effect_event,
    stage_semantic_key,
)


def _ax_mark(mark_id: str, text: str) -> dict:
    return {
        "mark_id": mark_id,
        "source": "accessibility_tree",
        "text_summary": text,
    }


# ----------------------------------------------------------------------
# L1: digest entry construction
# ----------------------------------------------------------------------


def test_digest_extracts_ax_texts_only() -> None:
    """LocateAnything / locate_* / la_* marks never enter L1 (noise)."""
    marks = [
        _ax_mark("ax_1", "2026年10月"),
        _ax_mark("ax_2", "10月1日"),
        {"mark_id": "locate_7", "source": "locateanything", "text_summary": "噪声"},
        {"mark_id": "la_3", "source": "locateanything", "text_summary": "噪声2"},
        {"mark_id": "m_1", "source": "mock", "text_summary": "非ax噪声"},
    ]
    entry = screen_text_digest_entry(
        contract_id="c1",
        screen_id="s1",
        observation_epoch=1,
        marks=marks,
        text_limit=40,
    )
    assert entry is not None
    texts = [item["text"] for item in entry["texts"]]
    assert texts == ["2026年10月", "10月1日"]
    assert entry["count"] == 2
    assert entry["kind"] == "screen_text_digest"
    assert entry["contract_id"] == "c1"


def test_digest_dedupes_texts_and_bounds_per_screen() -> None:
    marks = [_ax_mark(f"ax_{i}", f"text-{i % 3}") for i in range(120)]
    entry = screen_text_digest_entry(
        contract_id="c1",
        screen_id="s1",
        observation_epoch=1,
        marks=marks,
        text_limit=L1_DIGEST_TEXT_LIMIT,
    )
    assert entry is not None
    # dedup to 3 unique texts, bounded by the text limit
    assert len(entry["texts"]) == 3
    assert entry["count"] == 3


def test_digest_redacts_private_text_on_write() -> None:
    marks = [_ax_mark("ax_1", "联系人 13800138000")] + [
        _ax_mark(f"ax_{i}", "普通文本") for i in range(2, 20)
    ]
    entry = screen_text_digest_entry(
        contract_id="c1",
        screen_id="s1",
        observation_epoch=1,
        marks=marks,
        text_limit=40,
    )
    payload = str(entry)
    assert "13800138000" not in payload
    assert "<redacted>" in payload


def test_digest_empty_screen_returns_none() -> None:
    assert (
        screen_text_digest_entry(
            contract_id="c1", screen_id="s1", observation_epoch=1, marks=[]
        )
        is None
    )


def test_append_screen_text_digest_is_bounded_to_recent_screens() -> None:
    ledger: list[dict] = []
    for epoch in range(1, l1_digest_screen_window() + 5):
        ledger = append_screen_text_digest(
            ledger,
            contract_id="c1",
            screen_id=f"s{epoch}",
            observation_epoch=epoch,
            marks=[_ax_mark("ax_1", f"screen-{epoch}")],
        )
    digests = [e for e in ledger if e.get("kind") == "screen_text_digest"]
    assert len(digests) == l1_digest_screen_window()
    assert digests[0]["screen_id"] == "s5"
    assert digests[-1]["screen_id"] == "s34"


# ----------------------------------------------------------------------
# L2: effect events
# ----------------------------------------------------------------------


def test_effect_event_gate_only_succeeded_or_partial() -> None:
    assert should_record_effect_event(verdict="succeeded", hard_failure=False)
    assert should_record_effect_event(verdict="partial", hard_failure=False)
    assert not should_record_effect_event(verdict="failed", hard_failure=False)
    assert not should_record_effect_event(verdict="succeeded", hard_failure=True)
    assert not should_record_effect_event(verdict="unknown", hard_failure=False)


def test_append_effect_event_is_idempotent_per_step() -> None:
    ledger: list[dict] = []
    kwargs = dict(
        contract_id="c1",
        action="Tap",
        target="ax_3",
        observed_after="verdict=succeeded",
        screen_id="s1",
        step=4,
        named_evidence=[
            {"criterion": "date", "screen_reference": "ax_3", "observed_value": "x"}
        ],
    )
    ledger = append_effect_event(ledger, **kwargs)
    ledger = append_effect_event(ledger, **kwargs)
    events = [e for e in ledger if e.get("kind") == "effect_event"]
    assert len(events) == 1
    assert events[0]["step"] == 4
    assert events[0]["action"] == "Tap"
    assert events[0]["named_evidence"][0]["criterion"] == "date"


def test_effect_event_is_bounded() -> None:
    ledger: list[dict] = []
    for step in range(40):
        ledger = append_effect_event(
            ledger,
            contract_id="c1",
            action="Tap",
            target=None,
            observed_after="ok",
            screen_id="s1",
            step=step,
        )
    events = [e for e in ledger if e.get("kind") == "effect_event"]
    assert len(events) == 24
    assert events[-1]["step"] == 39


def test_effect_event_redacts_observed_value() -> None:
    entry = effect_event_entry(
        contract_id="c1",
        action="Tap",
        target="ax_1",
        observed_after="ok",
        screen_id="s1",
        step=1,
        named_evidence=[
            {"criterion": "c", "screen_reference": "ax_1", "observed_value": "13800138000"}
        ],
    )
    assert "13800138000" not in str(entry)
    assert entry["named_evidence"][0]["observed_value"] == "<redacted>"


# ----------------------------------------------------------------------
# Bounded cross-kind crop
# ----------------------------------------------------------------------


def test_bounded_ledger_keeps_anchored_criterion_matches() -> None:
    ledger: list[dict] = []
    for epoch in range(1, 100):
        ledger = append_evaluation_entries(
            ledger,
            evaluation={
                "evidence": {
                    "per_criterion": {
                        "target_app_visible": {
                            "status": "matched" if epoch == 2 else "unknown",
                            "reason": "r",
                            "source": "accessibility",
                        }
                    }
                }
            },
            contract_id="c1",
            screen_id=f"s{epoch}",
            observation_epoch=epoch,
            predicate_ids={"target_app_visible": "app.foreground_identity"},
            target_app_entered=True,
        )
        ledger = append_screen_text_digest(
            ledger,
            contract_id="c1",
            screen_id=f"s{epoch}",
            observation_epoch=epoch,
            marks=[_ax_mark("ax_1", f"t{epoch}")],
        )
    # The epoch-2 matched anchor survives the crop; unknown entries are trimmed.
    entries = [e for e in ledger if "criterion_id" in e]
    assert any(e["status"] == "matched" and e["observation_epoch"] == 2 for e in entries)
    assert len(entries) <= 64
    digests = [e for e in ledger if e.get("kind") == "screen_text_digest"]
    assert len(digests) == l1_digest_screen_window()


# ----------------------------------------------------------------------
# Semantic keys: name-independent, stable under renames
# ----------------------------------------------------------------------


def test_criterion_semantic_key_is_normalized_and_stable() -> None:
    a = criterion_semantic_key("departure date 2026年10月1日")
    b = criterion_semantic_key("departure date   2026年10月1日 ")
    c = criterion_semantic_key("DEPARTURE date ２０２６年10月1日")
    d = criterion_semantic_key("完全不同的判据")
    assert a == b == c
    assert a != d
    assert len(a) == 12


def test_stage_semantic_key_ignores_names() -> None:
    criteria = {
        "date_a": SuccessCriterion(
            "date_a", "departure date 2026年10月1日", "vlm_judge"
        ),
        "time_a": SuccessCriterion(
            "time_a", "departure window 06:00-12:00", "vlm_judge"
        ),
    }
    from phone_agent.graph.goal import TaskStage

    stage_a = TaskStage("S1", "select date", ("date_a", "time_a"), "", 0)
    stage_b = TaskStage("S1", "select date", ("date_b", "time_b"), "", 0)
    renamed = {
        "date_b": SuccessCriterion(
            "date_b", "departure date 2026年10月1日", "vlm_judge"
        ),
        "time_b": SuccessCriterion(
            "time_b", "departure window 06:00-12:00", "vlm_judge"
        ),
    }
    assert stage_semantic_key(stage_a, criteria) == stage_semantic_key(stage_b, renamed)
    assert normalize_semantic_text("  A  B\t") == "a b"
