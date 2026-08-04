"""R1-R4 grounding usability tightening for the fallback provider.

Covers the plan in ``docs/grounding-usability-fix-plan.md``:
- R1 ``_tokenize_hint`` purification (numeric garbage, single chars, CJK bigrams)
- R2 significant-term matching in ``_result_usability``
- R3 ``_is_target_like_mark`` container / pure-display label exclusion
- R4 no-words / all-purged fallback behavior
- hybrid integration: accessibility (calendar) not usable -> LocateAnything runs
  and its marks are merged into the final result.
"""

from __future__ import annotations

import pytest

from phone_agent.grounding.accessibility import AccessibilityTreeProvider
from phone_agent.grounding.fallback import (
    FallbackMarkProvider,
    _hint_terms,
    _is_target_like_mark,
    _result_is_usable,
    _result_usability,
    _significant_hint_terms,
    _tokenize_hint,
)
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProviderHint,
    MarkProviderResult,
    ScreenBinding,
)

CALENDAR_HINT = "帮我订2026年10月1日从北京到吉隆坡的机票"

CALENDAR_XML = """<hierarchy>
  <node text="" class="android.widget.FrameLayout" clickable="true" enabled="true" bounds="[0,100][1080,500]" />
  <node text="" class="android.widget.FrameLayout" clickable="true" enabled="true" bounds="[0,500][1080,900]" />
  <node text="2026年10月" class="android.widget.TextView" clickable="false" enabled="true" bounds="[300,60][780,100]" />
  <node text="返回" class="android.widget.Button" clickable="true" enabled="true" bounds="[20,60][100,100]" />
</hierarchy>"""


class Screenshot:
    base64_data = "fake-image"
    width = 1000
    height = 2000


def binding() -> ScreenBinding:
    return ScreenBinding(
        screen_id="screen-1", raw_screenshot_hash="hash-1", width=1000, height=2000
    )


def result_with_mark(
    *,
    text: str | None = None,
    role: str | None = None,
    bbox: list[int] | None = None,
    source: str = "test",
) -> MarkProviderResult:
    box = bbox or [100, 100, 200, 200]
    mark = MarkCandidate(
        mark_id="m1",
        bbox=list(box),
        center=[150, 150],
        source=source,
        role=role,
        text_summary=text,
    )
    return MarkProviderResult(
        success=True,
        provider="test",
        screen_id="screen-1",
        raw_screenshot_hash="hash-1",
        marks=[mark],
        candidates=[mark],
        candidate_count=1,
    )


class FakeLocateAnythingProvider:
    """Mirrors the D3 LocateAnything mark shape: neutral source label only."""

    name = "locateanything_mlx"
    version = "test"
    allow_raw_hints = True

    def __init__(self) -> None:
        self.calls = 0

    def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
        self.calls += 1
        hint = (hints or [MarkProviderHint(text="")])[0]
        mark = MarkCandidate(
            mark_id="la_1_1",
            bbox=[400, 300, 450, 350],
            center=[425, 325],
            source=self.name,
            role=hint.role,
            text_summary="visual-match",
        )
        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash="la-hash",
            marks=[mark],
            candidates=[mark],
            candidate_count=1,
        )


# ---------------------------------------------------------------------------
# R1 tokenize purification
# ---------------------------------------------------------------------------


def test_tokenize_hint_removes_date_garbage_tokens() -> None:
    tokens = _tokenize_hint("2026年10月1日")

    assert "2026年10月1日" in tokens
    assert "2026" in tokens
    for garbage in ("20", "02", "26", "6年", "年1", "10", "0月", "月1", "1", "日"):
        assert garbage not in tokens


def test_tokenize_hint_keeps_valid_words_and_single_chars_are_dropped() -> None:
    assert _tokenize_hint("吉隆坡") == ["吉隆坡", "吉隆", "隆坡"]
    assert _tokenize_hint("2026") == ["2026"]
    assert _tokenize_hint("chester117") == ["chester117"]
    assert _tokenize_hint("携程") == ["携程"]
    assert _tokenize_hint("wi-fi") == ["wi", "fi"]
    assert _tokenize_hint("1") == []
    assert _tokenize_hint("a") == []
    assert _tokenize_hint("12") == []


def test_tokenize_hint_cjk_bigrams_only_within_cjk_runs() -> None:
    tokens = _tokenize_hint("2026年10月")
    assert "6年" not in tokens
    assert "0月" not in tokens
    assert "年1" not in tokens


def test_tokenize_hint_keeps_long_cjk_phrase() -> None:
    tokens = _tokenize_hint(CALENDAR_HINT)

    assert "日从北京到吉隆坡的机票" in tokens
    assert "2026" in tokens
    assert "吉隆" in tokens
    assert "隆坡" in tokens
    assert "吉隆坡" not in tokens  # only a standalone raw token stays whole


def test_hint_terms_dedupe_and_bound() -> None:
    terms = _hint_terms([MarkProviderHint(text="吉隆坡 吉隆坡")])

    assert terms == ["吉隆坡", "吉隆", "隆坡"]
    many = _hint_terms([MarkProviderHint(text="吉隆坡")] * 6)
    assert len(many) <= 12
    assert many == terms  # dedupe across hints, then cap applies


# ---------------------------------------------------------------------------
# R2 significant terms
# ---------------------------------------------------------------------------


def test_significant_hint_terms_definition() -> None:
    assert _significant_hint_terms([MarkProviderHint(text="10月1日")]) == ["10月1日"]
    assert _significant_hint_terms([MarkProviderHint(text="2026")]) == ["2026"]
    assert _significant_hint_terms([MarkProviderHint(text="chester117")]) == [
        "chester117"
    ]
    assert _significant_hint_terms([MarkProviderHint(text="打开设置")]) == ["打开设置"]
    assert _significant_hint_terms([MarkProviderHint(text="吉隆坡")]) == []
    assert _significant_hint_terms([MarkProviderHint(text="携程")]) == []


def test_significant_hint_terms_extract_long_runs_from_sentence() -> None:
    terms = _significant_hint_terms([MarkProviderHint(text=CALENDAR_HINT)])

    assert "2026" in terms
    assert "日从北京到吉隆坡的机票" in terms
    assert "帮我订" not in terms
    assert "吉隆" not in terms


# ---------------------------------------------------------------------------
# R3 target-like mark
# ---------------------------------------------------------------------------


def test_is_target_like_mark_excludes_full_width_containers() -> None:
    assert not _is_target_like_mark(
        MarkCandidate(mark_id="c", bbox=[0, 0, 1000, 100], center=[500, 50], role="FrameLayout")
    )
    assert not _is_target_like_mark(
        MarkCandidate(mark_id="c", bbox=[0, 0, 988, 500], center=[494, 250], role="RecyclerView")
    )
    assert _is_target_like_mark(
        MarkCandidate(mark_id="c", bbox=[0, 0, 500, 100], center=[250, 50], role="FrameLayout")
    )
    assert _is_target_like_mark(
        MarkCandidate(mark_id="c", bbox=[0, 0, 1000, 100], center=[500, 50], role="Button")
    )
    assert _is_target_like_mark(
        MarkCandidate(mark_id="c", bbox=[0, 0, 1000, 100], center=[500, 50], role=None)
    )


def test_is_target_like_mark_excludes_pure_display_textview() -> None:
    assert not _is_target_like_mark(
        MarkCandidate(mark_id="t", bbox=[300, 60, 700, 100], center=[500, 80], role="TextView")
    )


def test_is_target_like_mark_keeps_interactive_textview() -> None:
    interactive = MarkCandidate(
        mark_id="t", bbox=[20, 260, 980, 420], center=[500, 340], role="TextView", confidence=1.0
    )
    assert _is_target_like_mark(interactive)


# ---------------------------------------------------------------------------
# R2/R3/R4 usable matrix
# ---------------------------------------------------------------------------


def test_calendar_marks_are_not_usable_for_date_hint() -> None:
    result = MarkProviderResult(
        success=True,
        provider="accessibility_tree",
        screen_id="screen-1",
        raw_screenshot_hash="hash-1",
        marks=[
            MarkCandidate(mark_id="row1", bbox=[0, 50, 1000, 250], center=[500, 150], role="FrameLayout"),
            MarkCandidate(mark_id="row2", bbox=[0, 250, 1000, 450], center=[500, 350], role="FrameLayout"),
            MarkCandidate(mark_id="month", bbox=[278, 30, 722, 50], center=[500, 40], role="TextView", text_summary="2026年10月"),
        ],
        candidates=[],
        candidate_count=0,
    )
    hints = [MarkProviderHint(text=CALENDAR_HINT)]

    usable, reason = _result_usability(result, hints)
    assert usable is False
    assert reason == "significant_miss"


def test_button_containing_kuala_lumpur_is_usable() -> None:
    result = result_with_mark(text="吉隆坡", role="Button")

    usable, reason = _result_usability(result, [MarkProviderHint(text="吉隆坡")])
    assert usable is True
    assert reason == "fallback_token_hit"


def test_wide_container_with_significant_text_is_not_usable() -> None:
    result = result_with_mark(
        text="2026年10月", role="FrameLayout", bbox=[0, 0, 1000, 100]
    )

    usable, reason = _result_usability(result, [MarkProviderHint(text="2026年10月1日")])
    assert usable is False
    assert reason == "significant_miss"


def test_pure_display_textview_label_hit_is_not_usable() -> None:
    result = result_with_mark(
        text="2026年10月", role="TextView", bbox=[278, 30, 722, 50]
    )

    usable, reason = _result_usability(result, [MarkProviderHint(text="2026年10月1日")])
    assert usable is False
    assert reason == "significant_miss"


def test_interactive_textview_row_hit_is_usable() -> None:
    box = [20, 260, 980, 420]
    mark = MarkCandidate(
        mark_id="m1",
        bbox=box,
        center=[500, 340],
        source="accessibility_tree",
        role="TextView",
        confidence=1.0,
        text_summary="视频标题一",
    )
    result = MarkProviderResult(
        success=True,
        provider="accessibility_tree",
        screen_id="screen-1",
        raw_screenshot_hash="hash-1",
        marks=[mark],
        candidates=[mark],
        candidate_count=1,
    )

    usable, reason = _result_usability(result, [MarkProviderHint(text="打开第一个视频")])
    assert usable is True
    assert reason == "fallback_token_hit"


def test_target_like_mark_with_significant_hit_is_usable() -> None:
    result = result_with_mark(text="2026年10月1日", role="Button")

    usable, reason = _result_usability(result, [MarkProviderHint(text="2026年10月1日")])
    assert usable is True
    assert reason == "significant_hit"


def test_no_hint_keeps_legacy_any_marks_usable() -> None:
    result = result_with_mark(text="anything", role="TextView")

    usable, reason = _result_usability(result, [])
    assert usable is True
    assert reason == "no_hint_words"
    assert _result_is_usable(result, []) is True


def test_all_purged_garbage_never_becomes_unconditional_usable() -> None:
    result = result_with_mark(text="1", role="Button")

    for hint_text in ("1", "12", "a", "a b"):
        usable, reason = _result_usability(result, [MarkProviderHint(text=hint_text)])
        assert usable is False, hint_text
        assert reason == "all_tokens_purged"


def test_fallback_token_miss_is_not_usable() -> None:
    result = result_with_mark(text="东京", role="Button")

    usable, reason = _result_usability(result, [MarkProviderHint(text="吉隆坡")])
    assert usable is False
    assert reason == "fallback_token_miss"


def test_fallback_token_hit_respects_target_like_gate() -> None:
    container = result_with_mark(text="吉隆坡", role="FrameLayout", bbox=[0, 0, 1000, 100])
    usable, _ = _result_usability(container, [MarkProviderHint(text="吉隆坡")])
    assert usable is False


# ---------------------------------------------------------------------------
# Hybrid chain integration
# ---------------------------------------------------------------------------


def test_calendar_chain_invokes_locateanything_and_merges_marks() -> None:
    tree = AccessibilityTreeProvider(lambda timeout=None: CALENDAR_XML)
    la = FakeLocateAnythingProvider()
    result = FallbackMarkProvider([tree, la]).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text=CALENDAR_HINT)],
    )

    assert la.calls == 1
    assert result.success is True
    mark_ids = [mark.mark_id for mark in result.marks]
    assert "la_1_1" in mark_ids
    assert any(mark_id.startswith("ax_") for mark_id in mark_ids)
    chain = result.metadata["fallback_chain"]
    assert [row["usable"] for row in chain] == [False, True]
    assert chain[0]["usable_reason"] == "significant_miss"
    # D3: the query-conditioned visual provider is usable by construction — the
    # hint text no longer echoes back into its mark labels.
    assert chain[1]["usable_reason"] == "provider_query_matched"


def test_calendar_chain_without_visual_provider_fails_closed() -> None:
    tree = AccessibilityTreeProvider(lambda timeout=None: CALENDAR_XML)
    result = FallbackMarkProvider([tree]).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text=CALENDAR_HINT)],
    )

    assert result.success is False
    assert result.failure_code == "grounding_no_usable_candidate"
    assert result.marks == []
    assert any(candidate.mark_id.startswith("ax_") for candidate in result.candidates)
    assert result.metadata["fallback_chain"][0]["usable"] is False


def test_no_hint_browsing_step_short_circuits_without_visual_provider() -> None:
    class ShouldNotRunProvider:
        name = "locateanything_mlx"
        version = "test"
        allow_raw_hints = True

        def provide_marks(self, *args, **kwargs):
            raise AssertionError("no-hint step must short-circuit before LA")

    tree = AccessibilityTreeProvider(lambda timeout=None: CALENDAR_XML)
    result = FallbackMarkProvider([tree, ShouldNotRunProvider()]).provide_marks(
        Screenshot(), binding()
    )

    assert result.success is True
    assert [row["usable"] for row in result.metadata["fallback_chain"]] == [True]


def test_realistic_long_task_hint_keeps_meaningful_bigrams() -> None:
    """Acceptance regression: the legacy 12-term cap dropped '吉隆' from the
    realistic 34-char task hint, so even a perfect '吉隆坡' button missed and
    the visual provider would have fired on every screen."""
    from phone_agent.grounding.fallback import (
        _hint_terms,
        _result_usability,
        MarkProviderHint,
    )

    task = "在携程帮我找2026年10月1日上午从上海飞往吉隆坡的单程最便宜机票"
    assert "吉隆" in _hint_terms([MarkProviderHint(text=task)])


def test_long_task_hint_matches_city_button(fake_marks=None) -> None:
    from phone_agent.grounding.fallback import (
        _result_usability,
        MarkProviderHint,
    )

    class _Mark:
        role = "Button"
        text_summary = "吉隆坡"
        source = "accessibility_tree"
        confidence = 1.0
        bbox = [100, 200, 300, 260]

    class _Res:
        success = True
        marks = [_Mark()]

    task = "在携程帮我找2026年10月1日上午从上海飞往吉隆坡的单程最便宜机票"
    usable, reason = _result_usability(_Res(), [MarkProviderHint(text=task)])
    assert usable, reason
