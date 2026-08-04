"""D1/D2/D3 + A-lite regression tests: screen identity decoupling.

Covers ``docs/execution-screen-identity-decoupling.md``:
- D1: final screen_id topology uses base (ax) marks only — provider/locate
  marks never enter screen identity.
- D2: locate inheritance gate is relaxed for the same physical page (semantic
  equal AND (ax structure digest equal OR p-hash distance <= threshold)),
  inherited marks are re-bound to the new screen_id; execution-side grounding
  validation is untouched.
- D3: la_* marks carry a neutral source label, never the query text.
- A-lite: after a successful Locate, the next observation skips the automatic
  LocateAnything provider; the explicit locate tool stays available.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from io import BytesIO

from PIL import Image

from phone_agent.config.policy import LOCATE_INHERIT_PHASH_MAX_DISTANCE
from phone_agent.graph.marks import (
    Mark,
    MarkRegistry,
    build_mark_set_version,
    build_mark_topology_digest,
    build_screen_id,
    hash_hamming_distance,
)
from phone_agent.graph.observation import build_observation
from phone_agent.grounding.locateanything import (
    LOCATEANYTHING_NEUTRAL_MARK_LABEL,
    LocateAnythingMLXProvider,
)
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProviderHint,
    MarkProviderResult,
    ScreenBinding,
)


class FakeScreenshot:
    base64_data: str
    width: int
    height: int

    def __init__(self, base64_data: str = "screen", width: int = 1000, height: int = 2000):
        self.base64_data = base64_data
        self.width = width
        self.height = height


def _png_b64(color: tuple[int, int, int] = (255, 255, 255)) -> str:
    image = Image.new("RGB", (8, 8), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _two_tone_b64(vertical: bool) -> str:
    """8x8 image split white/black; mean-hash differs by split orientation."""
    image = Image.new("RGB", (8, 8), (255, 255, 255))
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            if (vertical and x >= 4) or (not vertical and y >= 4):
                pixels[x, y] = (0, 0, 0)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class ChurnProvider:
    """Mirrors D3 LocateAnything: la marks carry a neutral label; bbox varies."""

    name = "locateanything_mlx"
    version = "test"
    allow_raw_hints = True

    def __init__(self, bbox: list[int] | None = None) -> None:
        self.bbox = bbox or [400, 400, 600, 600]

    def provide_marks(
        self, screenshot, screen_binding, hints=None, timeout=None
    ) -> MarkProviderResult:
        mark = MarkCandidate(
            mark_id="la_1_1",
            bbox=list(self.bbox),
            center=[500, 500],
            source=self.name,
            text_summary=LOCATEANYTHING_NEUTRAL_MARK_LABEL,
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


def _ax_mark_dict(prefix: str = "ax", n: int = 2) -> list[dict]:
    return [
        {
            "mark_id": f"{prefix}_{i}",
            "bbox": [i * 100, 100, i * 100 + 200, 300],
            "role": "Button",
            "source": "uiautomator",
        }
        for i in range(1, n + 1)
    ]


def _ax_mark(mid: str, bbox: tuple, screen_id: str) -> Mark:
    return Mark(
        mark_id=mid,
        screen_id=screen_id,
        bbox=bbox,
        center=((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2),
        source="accessibility_tree",
    )


def _locate_mark(screen_id: str, mark_id: str = "locate_1") -> Mark:
    return Mark(
        mark_id=mark_id,
        screen_id=screen_id,
        bbox=(400, 400, 600, 600),
        center=(500, 500),
        source="locateanything_mlx",
        confidence=1.0,
        text_summary="visual-match",
    )


def _mk(
    marks: dict[str, Mark],
    screen_id: str,
    *,
    semantic: str = "sem-1",
    p_hash: str = "1111111111111111",
    epoch: int = 1,
) -> MarkRegistry:
    return MarkRegistry(
        screen_id=screen_id,
        marks=marks,
        semantic_screen_id=semantic,
        observation_epoch=epoch,
        mark_set_version=build_mark_topology_digest(list(marks.values())),
        perceptual_hash=p_hash,
        raw_screenshot_hash="raw-1",
    )


# ---------------------------------------------------------------------------
# D1: screen identity decoupled from provider/locate marks
# ---------------------------------------------------------------------------


def test_d1_screen_id_stable_across_provider_mark_churn() -> None:
    base = _ax_mark_dict()
    first = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=base,
        mark_providers=[ChurnProvider([400, 400, 600, 600])],
    )
    second = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=base,
        mark_providers=[ChurnProvider([100, 100, 200, 200])],
    )

    assert first.snapshot.screen_id == second.snapshot.screen_id
    # the churning la marks still landed in the registries (execution side)
    assert first.mark_registry.marks["la_1_1"].bbox == (400.0, 400.0, 600.0, 600.0)
    assert second.mark_registry.marks["la_1_1"].bbox == (100.0, 100.0, 200.0, 200.0)


def test_d1_screen_id_topology_ignores_provider_marks() -> None:
    base = _ax_mark_dict()
    plain = build_observation(
        screenshot=FakeScreenshot(), current_app="Calendar", marks=base
    )
    with_la = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=base,
        mark_providers=[ChurnProvider()],
    )

    assert plain.snapshot.screen_id == with_la.snapshot.screen_id
    assert "la_1_1" in with_la.mark_registry.marks


def test_d1_final_screen_id_matches_base_only_digest() -> None:
    base = _ax_mark_dict()
    obs = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=base,
        mark_providers=[ChurnProvider()],
    )
    expected = build_screen_id(
        current_app="Calendar",
        screenshot_b64="screen",
        width=1000,
        height=2000,
        marks=base,
    )
    assert obs.snapshot.screen_id == expected


def test_d1_screen_id_changes_on_real_navigation() -> None:
    base = _ax_mark_dict(n=2)
    changed = _ax_mark_dict(n=3)
    a = build_observation(screenshot=FakeScreenshot(), current_app="Calendar", marks=base)
    b = build_observation(screenshot=FakeScreenshot(), current_app="Calendar", marks=changed)
    c = build_observation(screenshot=FakeScreenshot(), current_app="Other", marks=base)

    assert a.snapshot.screen_id != b.snapshot.screen_id
    assert a.snapshot.screen_id != c.snapshot.screen_id


def test_d1_true_screen_change_flips_digest_via_pixels() -> None:
    base = _ax_mark_dict(n=2)
    vertical = build_observation(
        screenshot=FakeScreenshot(base64_data=_two_tone_b64(vertical=True)),
        current_app="Calendar",
        marks=base,
    )
    horizontal = build_observation(
        screenshot=FakeScreenshot(base64_data=_two_tone_b64(vertical=False)),
        current_app="Calendar",
        marks=base,
    )

    assert vertical.snapshot.perceptual_hash != horizontal.snapshot.perceptual_hash
    assert vertical.snapshot.screen_id != horizontal.snapshot.screen_id


def _hybrid_ax_mark_dict(n: int = 2) -> list[dict]:
    return [
        {
            "mark_id": f"ax_{i}",
            "bbox": [i * 100, 100, i * 100 + 200, 300],
            "role": None,
            "source": "accessibility_tree",
        }
        for i in range(1, n + 1)
    ]


class AxTreeProvider:
    """Hybrid-mode ax provider: marks enter all_marks as accessibility_tree."""

    name = "accessibility_tree"
    version = "test"
    allow_raw_hints = False

    def __init__(self, n: int = 2, bbox_shift: int = 0, id_prefix: str = "ax") -> None:
        self.n = n
        self.bbox_shift = bbox_shift
        self.id_prefix = id_prefix

    def provide_marks(
        self, screenshot, screen_binding, hints=None, timeout=None
    ) -> MarkProviderResult:
        marks = [
            MarkCandidate(
                mark_id=f"{self.id_prefix}_{i}",
                bbox=[i * 100 + self.bbox_shift, 100, i * 100 + 200 + self.bbox_shift, 300],
                center=[i * 100 + 100 + self.bbox_shift, 200],
                source=self.name,
                text_summary=f"ax-{i}",
            )
            for i in range(1, self.n + 1)
        ]
        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash="ax-hash",
            marks=marks,
            candidates=marks,
            candidate_count=len(marks),
        )


# ---------------------------------------------------------------------------
# D1 hybrid mode: ax structure enters all_marks via the accessibility_tree
# provider (base marks empty, like the default hybrid grounding path). The
# final screen_id topology must still fold those marks in while keeping
# la_*/locate_* marks out.
# ---------------------------------------------------------------------------


def test_d1_hybrid_screen_id_stable_across_la_churn() -> None:
    first = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=None,
        mark_providers=[AxTreeProvider(n=2), ChurnProvider([400, 400, 600, 600])],
    )
    second = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=None,
        mark_providers=[AxTreeProvider(n=2), ChurnProvider([100, 100, 200, 200])],
    )

    assert first.snapshot.screen_id == second.snapshot.screen_id
    # la churn still lands in the registries (execution side untouched)
    assert first.mark_registry.marks["la_1_1"].bbox == (400.0, 400.0, 600.0, 600.0)
    assert second.mark_registry.marks["la_1_1"].bbox == (100.0, 100.0, 200.0, 200.0)
    # the ax provider marks drive the topology: id equals the base-only-digest
    # prediction over the ax marks, and differs from the degenerate empty-topology id
    expected = build_screen_id(
        current_app="Calendar",
        screenshot_b64="screen",
        width=1000,
        height=2000,
        marks=_hybrid_ax_mark_dict(n=2),
    )
    degenerate = build_screen_id(
        current_app="Calendar",
        screenshot_b64="screen",
        width=1000,
        height=2000,
        marks=None,
    )
    assert first.snapshot.screen_id == expected
    assert first.snapshot.screen_id != degenerate


def test_d1_hybrid_ax_structure_change_flips_screen_id() -> None:
    before = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=None,
        mark_providers=[AxTreeProvider(n=2), ChurnProvider()],
    )
    after = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=None,
        mark_providers=[AxTreeProvider(n=3), ChurnProvider()],
    )

    assert before.snapshot.screen_id != after.snapshot.screen_id


def test_d1_hybrid_app_switch_flips_screen_id() -> None:
    calendar = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=None,
        mark_providers=[AxTreeProvider(n=2)],
    )
    other = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Other",
        marks=None,
        mark_providers=[AxTreeProvider(n=2)],
    )

    assert calendar.snapshot.screen_id != other.snapshot.screen_id


def test_d1_hybrid_base_and_provider_ax_merge_into_topology() -> None:
    base = _ax_mark_dict(n=2)  # uiautomator source via base marks
    obs = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=base,
        mark_providers=[AxTreeProvider(n=2, id_prefix="px")],
    )
    provider_marks = [
        {**mark, "mark_id": f"px_{i}"}
        for i, mark in enumerate(_hybrid_ax_mark_dict(n=2), start=1)
    ]
    expected = build_screen_id(
        current_app="Calendar",
        screenshot_b64="screen",
        width=1000,
        height=2000,
        marks=_ax_mark_dict(n=2) + provider_marks,
    )

    assert obs.snapshot.screen_id == expected
    # the merged registry still holds both ax origins
    sources = {mark.source for mark in obs.mark_registry.marks.values()}
    assert sources == {"uiautomator", "accessibility_tree"}


# ---------------------------------------------------------------------------
# D2: relaxed locate-inheritance gate
# ---------------------------------------------------------------------------


def _jitter_pair(prev_ax: int, new_ax: int) -> tuple[MarkRegistry, MarkRegistry, Mark]:
    prev_marks = {
        f"ax_{i}": _ax_mark(f"ax_{i}", (i * 100, 100, i * 100 + 200, 300), "old-screen")
        for i in range(1, prev_ax + 1)
    }
    new_marks = {
        f"ax_{i}": _ax_mark(f"ax_{i}", (i * 100, 100, i * 100 + 200, 300), "new-screen")
        for i in range(1, new_ax + 1)
    }
    return _mk(prev_marks, "old-screen"), _mk(new_marks, "new-screen"), _locate_mark("old-screen")


def build_ax_digests_differ(previous: MarkRegistry, new: MarkRegistry) -> bool:
    from phone_agent.graph.marks import build_ax_mark_digest

    return build_ax_mark_digest(previous.marks) != build_ax_mark_digest(new.marks)


def test_d2_locate_survives_ax_jitter_via_close_p_hash() -> None:
    previous, new, locate = _jitter_pair(prev_ax=3, new_ax=2)
    # same physical pixels -> identical p-hash despite the tree jitter
    previous = replace(previous, perceptual_hash="a1b2c3d4e5f60718")
    new = replace(new, perceptual_hash="a1b2c3d4e5f60718")
    assert build_ax_digests_differ(previous, new)

    merged = new.with_inherited_locate_marks([locate], previous=previous)

    assert "locate_1" in merged.marks
    assert merged.marks["locate_1"].screen_id == "new-screen"
    assert merged.mark_set_version == build_mark_set_version(merged.marks)


def test_d2_locate_survives_identical_ax_digest_with_distant_p_hash() -> None:
    previous, new, locate = _jitter_pair(prev_ax=2, new_ax=2)
    previous = replace(previous, perceptual_hash="0000000000000000")
    new = replace(new, perceptual_hash="ffffffffffffffff")

    merged = new.with_inherited_locate_marks([locate], previous=previous)

    assert "locate_1" in merged.marks
    assert merged.marks["locate_1"].screen_id == "new-screen"


def test_d2_locate_dropped_when_semantic_screen_differs() -> None:
    previous, new, locate = _jitter_pair(prev_ax=2, new_ax=2)
    new = replace(new, semantic_screen_id="sem-2")

    merged = new.with_inherited_locate_marks([locate], previous=previous)

    assert "locate_1" not in merged.marks


def test_d2_locate_dropped_when_structure_differs_and_p_hash_distant() -> None:
    previous, new, locate = _jitter_pair(prev_ax=3, new_ax=2)
    previous = replace(previous, perceptual_hash="0000000000000000")
    new = replace(new, perceptual_hash="ffffffffffffffff")

    merged = new.with_inherited_locate_marks([locate], previous=previous)

    assert "locate_1" not in merged.marks


def test_d2_gate_fail_closed_without_ax_marks() -> None:
    previous = _mk({"locate_1": _locate_mark("old-screen")}, "old-screen", p_hash="0000000000000000")
    new = _mk({}, "new-screen", p_hash="ffffffffffffffff")

    merged = new.with_inherited_locate_marks(
        [previous.marks["locate_1"]], previous=previous
    )

    assert "locate_1" not in merged.marks


def test_d2_p_hash_threshold_boundary() -> None:
    previous, new, locate = _jitter_pair(prev_ax=3, new_ax=2)
    previous = replace(previous, perceptual_hash="0000000000000000")
    # distance 8 == threshold -> accepted; distance 16 > threshold -> dropped
    at_boundary = replace(new, perceptual_hash="ff00000000000000")
    over_boundary = replace(new, perceptual_hash="ffff000000000000")
    assert (
        hash_hamming_distance("ff00000000000000", "0000000000000000")
        == LOCATE_INHERIT_PHASH_MAX_DISTANCE
    )

    merged_at = at_boundary.with_inherited_locate_marks([locate], previous=previous)
    merged_over = over_boundary.with_inherited_locate_marks([locate], previous=previous)
    assert "locate_1" in merged_at.marks
    assert "locate_1" not in merged_over.marks


def test_d2_exact_same_screen_keeps_legacy_merge() -> None:
    previous, _, locate = _jitter_pair(prev_ax=2, new_ax=2)
    merged = previous.with_inherited_locate_marks([locate], previous=previous)

    assert "locate_1" in merged.marks
    assert merged.marks["locate_1"].screen_id == "old-screen"


def test_d2_observation_inherit_across_ax_jitter_keeps_grounding() -> None:
    """Integration: ax jitter (node count change) between rounds flips the D1
    screen_id; the relaxed gate still inherits and re-binds locate_N, and the
    execution-side grounding validation accepts it unchanged."""
    from phone_agent.actions.grounding import (
        _validate_mark_binding,
        ground_intent_to_action,
    )

    screenshot = FakeScreenshot(base64_data=_png_b64((255, 255, 255)))
    first = build_observation(
        screenshot=screenshot,
        current_app="Calendar",
        marks=_ax_mark_dict(n=3),
    )
    previous_registry = first.mark_registry.with_extra_marks(
        [{"mark_id": "locate_1", "screen_id": first.snapshot.screen_id,
          "bbox": [400, 400, 600, 600], "center": [500, 500],
          "source": "locateanything_mlx", "confidence": 1.0}]
    )
    rebuilt = build_observation(
        screenshot=screenshot,
        current_app="Calendar",
        marks=_ax_mark_dict(n=2),  # one ax node dropped
        previous_registry=previous_registry,
    )

    assert rebuilt.snapshot.screen_id != first.snapshot.screen_id
    assert "locate_1" in rebuilt.mark_registry.marks
    assert rebuilt.mark_registry.marks["locate_1"].screen_id == rebuilt.snapshot.screen_id
    # execution-side triple validation accepts the re-bound mark (shortcut)
    _validate_mark_binding(
        rebuilt.mark_registry,
        screen_id=rebuilt.snapshot.screen_id,
        screen_binding=None,
        grounding_metadata={},
    )
    grounded = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "target_mark_id": "locate_1"},
        mark_registry=rebuilt.mark_registry,
        screen_id=rebuilt.snapshot.screen_id,
    )
    assert grounded["_metadata"] == "do"
    assert grounded["element"] == [500.0, 500.0]


def build_ax_digests_differ(previous: MarkRegistry, new: MarkRegistry) -> bool:
    from phone_agent.graph.marks import build_ax_mark_digest

    return build_ax_mark_digest(previous.marks) != build_ax_mark_digest(new.marks)


# ---------------------------------------------------------------------------
# D3: la_* marks never echo the query text
# ---------------------------------------------------------------------------


def _stub_la_provider() -> LocateAnythingMLXProvider:
    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit")
    return provider


def test_d3_locateanything_marks_carry_neutral_label(monkeypatch) -> None:
    provider = _stub_la_provider()
    monkeypatch.setattr(
        "phone_agent.grounding.locateanything.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "phone_agent.grounding.locateanything.platform.machine", lambda: "arm64"
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(provider, "_prepare_image", lambda screenshot: (object(), "input-hash"))
    monkeypatch.setattr(
        provider,
        "_run_model",
        lambda image, description, timeout=None: "<box>100 200 300 400</box>",
    )

    result = provider.provide_marks(
        FakeScreenshot(),
        ScreenBinding(
            screen_id="screen-1",
            raw_screenshot_hash="hash-1",
            width=1000,
            height=2000,
        ),
        hints=[MarkProviderHint(text="2026年10月2日日期")],
    )

    assert result.success is True
    assert result.marks[0].text_summary == LOCATEANYTHING_NEUTRAL_MARK_LABEL
    assert "2026年10月2日日期" not in str(result.to_dict())


def test_d3_observation_marks_block_renders_neutral_label(monkeypatch) -> None:
    provider = _stub_la_provider()
    monkeypatch.setattr(
        "phone_agent.grounding.locateanything.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "phone_agent.grounding.locateanything.platform.machine", lambda: "arm64"
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(provider, "_prepare_image", lambda screenshot: (object(), "input-hash"))
    monkeypatch.setattr(
        provider,
        "_run_model",
        lambda image, description, timeout=None: "<box>100 200 300 400</box>",
    )
    observation = build_observation(
        screenshot=FakeScreenshot(),
        current_app="Calendar",
        marks=_ax_mark_dict(),
        mark_providers=[provider],
        provider_hints=[MarkProviderHint(text="2026年10月2日日期")],
    )

    block = observation.mark_registry.prompt_block(lang="cn")
    assert "la_1_1" in block
    assert LOCATEANYTHING_NEUTRAL_MARK_LABEL in block
    assert "2026年10月2日日期" not in block


# ---------------------------------------------------------------------------
# A-lite: skip automatic LocateAnything after a successful locate
# ---------------------------------------------------------------------------


def test_a_lite_previous_successful_locate_detection() -> None:
    from phone_agent.graph.nodes.plan import _previous_step_was_successful_locate

    assert (
        _previous_step_was_successful_locate(
            {
                "action_parsed": {"action": "Locate", "target_text_hint": "10月1日"},
                "action_result": {"success": True},
            }
        )
        is True
    )
    assert (
        _previous_step_was_successful_locate(
            {
                "action_parsed": {"action": "Locate", "target_text_hint": "10月1日"},
                "action_result": {"success": False},
            }
        )
        is False
    )
    assert (
        _previous_step_was_successful_locate(
            {"action_parsed": {"action": "Tap"}, "action_result": {"success": True}}
        )
        is False
    )
    assert _previous_step_was_successful_locate({}) is False


def test_a_lite_factory_skips_locateanything_for_observation_only() -> None:
    from phone_agent.grounding.factory import (
        build_locate_provider,
        build_mark_provider,
        build_mark_providers,
    )

    common = {
        "grounding_provider_name": "hybrid",
        "accessibility_tree_dump": lambda timeout=None: "<hierarchy />",
        "grounding_model_path": "models/LocateAnything-3B-4bit",
    }
    skipped = build_mark_providers({**common, "skip_locateanything": True})
    assert [provider.name for provider in skipped[0].providers] == [
        "accessibility_tree"
    ]

    full = build_mark_providers(common)
    assert [provider.name for provider in full[0].providers] == [
        "accessibility_tree",
        "locateanything_mlx",
    ]

    # single-provider observation mode honors the skip too
    assert (
        build_mark_provider(
            {"grounding_provider_name": "locateanything", "skip_locateanything": True}
        )
        is None
    )
    # the explicit locate tool is NOT affected by the observation-time skip
    locate = build_locate_provider(
        {
            "grounding_provider_name": "hybrid",
            "skip_locateanything": True,
            "grounding_model_path": "models/LocateAnything-3B-4bit",
        }
    )
    assert locate is not None
    assert locate.name == "locateanything_mlx"
