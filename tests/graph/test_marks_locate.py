"""F1 locate tool: MarkRegistry.with_extra_marks merge semantics."""

from phone_agent.graph.marks import (
    Mark,
    MarkRegistry,
    build_mark_set_version,
    compute_raw_screenshot_hash,
)


def _registry(**overrides) -> MarkRegistry:
    base = MarkRegistry(
        screen_id="screen-1",
        marks={
            "m1": Mark(
                mark_id="m1",
                screen_id="screen-1",
                bbox=(0, 0, 100, 100),
                center=(50, 50),
                source="accessibility",
                role="TextView",
                text_summary="首页",
            )
        },
        semantic_screen_id="semantic-1",
        observation_epoch=3,
        mark_set_version="v1",
        perceptual_hash="p1",
        raw_screenshot_hash=compute_raw_screenshot_hash("fake-image"),
    )
    return base


def _locate_mark(mark_id: str = "locate_1") -> Mark:
    return Mark(
        mark_id=mark_id,
        screen_id="screen-1",
        bbox=(400, 400, 600, 600),
        center=(500, 500),
        source="locateanything_mlx",
        confidence=1.0,
        role=None,
        text_summary="10月1日",
    )


def test_with_extra_marks_keeps_screen_binding_and_recomputes_version() -> None:
    registry = _registry()
    merged = registry.with_extra_marks([_locate_mark()])

    assert merged.screen_id == "screen-1"
    assert merged.raw_screenshot_hash == registry.raw_screenshot_hash
    assert merged.perceptual_hash == registry.perceptual_hash
    assert merged.observation_epoch == 3
    assert merged.semantic_screen_id == "semantic-1"
    assert set(merged.marks) == {"m1", "locate_1"}
    locate_mark = merged.marks["locate_1"]
    assert locate_mark.role is None
    assert locate_mark.source == "locateanything_mlx"
    assert locate_mark.confidence == 1.0
    # Only mark_set_version is recomputed (P0 #9 hash binding untouched).
    assert merged.mark_set_version != registry.mark_set_version
    assert merged.mark_set_version == build_mark_set_version(merged.marks)


def test_with_extra_marks_is_idempotent_and_append_only() -> None:
    registry = _registry()
    once = registry.with_extra_marks([_locate_mark()])
    twice = once.with_extra_marks([_locate_mark("locate_2")])

    assert set(twice.marks) == {"m1", "locate_1", "locate_2"}
    assert twice.marks["locate_1"] is once.marks["locate_1"]
    # Merging the same mark id again overwrites rather than duplicates.
    again = registry.with_extra_marks([_locate_mark(), _locate_mark()])
    assert len(again.marks) == 2


def test_with_extra_marks_drops_marks_bound_to_other_screens() -> None:
    registry = _registry()
    foreign = Mark(
        mark_id="locate_9",
        screen_id="other-screen",
        bbox=(0, 0, 10, 10),
        center=(5, 5),
        source="locateanything_mlx",
    )
    merged = registry.with_extra_marks([foreign, _locate_mark()])

    assert "locate_9" not in merged.marks
    assert "locate_1" in merged.marks


def test_with_extra_marks_rejects_invalid_marks_fail_closed() -> None:
    registry = _registry()
    merged = registry.with_extra_marks([{"mark_id": "bad id!"}, _locate_mark()])

    assert "bad id!" not in merged.marks
    assert "locate_1" in merged.marks


def test_with_extra_marks_empty_is_noop() -> None:
    registry = _registry()
    assert registry.with_extra_marks([]) is registry


def test_locate_mark_roundtrips_through_state_dict() -> None:
    registry = _registry().with_extra_marks([_locate_mark()])
    restored = MarkRegistry.from_dict(registry.to_dict())

    assert restored is not None
    assert restored.screen_id == "screen-1"
    assert restored.raw_screenshot_hash == registry.raw_screenshot_hash
    assert set(restored.marks) == {"m1", "locate_1"}
