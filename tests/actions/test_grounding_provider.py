import json
import pytest
import sys
import subprocess
from types import ModuleType, SimpleNamespace

from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.grounding.accessibility import (
    AccessibilityTreeProvider,
    parse_uiautomator_marks,
    parse_uiautomator_summary,
    parse_uiautomator_structure,
    visible_text_summary,
)
from phone_agent.grounding.fallback import FallbackMarkProvider
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.factory import build_mark_provider, build_mark_providers
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.parser import GroundingParseError, calibrate_bbox_from_resized_input, parse_box_response
from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, MarkProviderResult, ScreenBinding
from phone_agent.graph.marks import MarkRegistry


class Screenshot:
    base64_data = "fake-image"
    width = 1000
    height = 2000


def binding() -> ScreenBinding:
    return ScreenBinding(screen_id="screen-1", raw_screenshot_hash="hash-1", width=1000, height=2000)


def test_parse_box_response_returns_deterministic_center() -> None:
    parsed = parse_box_response("noise <box>100 200 300 400</box>")

    assert parsed.bbox == [100, 200, 300, 400]
    assert parsed.center == [200, 300]
    assert parsed.area == 40000


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("", "empty_output"),
        ("no box", "invalid_format"),
        ("<box>0 0 100 100</box><box>200 200 300 300</box>", "grounding_ambiguous"),
        ("<box>-1 0 10 10</box>", "out_of_range"),
        ("<box>1 1 1 1</box>", "too_small"),
        ("<box>0 0 1000 1000</box>", "too_large"),
    ],
)
def test_parse_box_response_failure_codes(output: str, code: str | None) -> None:
    with pytest.raises(GroundingParseError) as exc_info:
        parse_box_response(output)

    assert exc_info.value.code == code


def test_parse_box_response_normalizes_coordinate_order() -> None:
    parsed = parse_box_response("<box>300 400 100 200</box>")

    assert parsed.bbox == [100, 200, 300, 400]


def test_resize_calibration_keeps_normalized_bbox_and_rejects_bad_size() -> None:
    assert calibrate_bbox_from_resized_input([1, 2, 3, 4], original_size=(100, 200), resized_size=(50, 100)) == [1, 2, 3, 4]
    with pytest.raises(GroundingParseError) as exc_info:
        calibrate_bbox_from_resized_input([1, 2, 3, 4], original_size=(0, 200), resized_size=(50, 100))
    assert exc_info.value.code == "invalid_resize"


def test_locateanything_default_max_size_is_960() -> None:
    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit")

    assert provider.max_size == 960
    assert provider.context_max_chars == 0


def test_mark_provider_factory_passes_locateanything_max_size_from_config() -> None:
    provider = build_mark_provider(
        {
            "grounding_provider_name": "locateanything",
            "grounding_model_path": "models/LocateAnything-3B-4bit",
            "grounding_max_size": 720,
        }
    )

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.max_size == 720


def test_mark_provider_factory_prefers_locateanything_specific_max_size(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_AGENT_LOCATEANYTHING_MAX_SIZE", "720")
    monkeypatch.setenv("PHONE_AGENT_GROUNDING_MAX_SIZE", "512")

    provider = build_mark_provider(
        {
            "grounding_provider_name": "locateanything",
            "grounding_max_size": 640,
            "locateanything_max_size": 960,
        }
    )

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.max_size == 960


def test_mark_provider_factory_passes_locateanything_max_size_from_env(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_AGENT_GROUNDING_PROVIDER", "locateanything")
    monkeypatch.setenv("PHONE_AGENT_LOCATEANYTHING_MAX_SIZE", "720")

    provider = build_mark_provider()

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.max_size == 720


def test_mark_provider_factory_invalid_max_size_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_AGENT_GROUNDING_PROVIDER", "locateanything")
    monkeypatch.setenv("PHONE_AGENT_LOCATEANYTHING_MAX_SIZE", "not-an-int")

    provider = build_mark_provider()

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.max_size == 960


def test_mark_providers_default_to_hybrid(monkeypatch) -> None:
    monkeypatch.delenv("PHONE_AGENT_GROUNDING_PROVIDER", raising=False)

    providers = build_mark_providers(
        {
            "grounding_model_path": "models/LocateAnything-3B-4bit",
        }
    )

    assert len(providers) == 1
    assert isinstance(providers[0], FallbackMarkProvider)
    assert [provider.name for provider in providers[0].providers] == ["locateanything_mlx"]


def test_mark_provider_factory_passes_locateanything_context_budget() -> None:
    provider = build_mark_provider(
        {
            "grounding_provider_name": "locateanything",
            "locateanything_context_max_chars": 32,
        }
    )

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.context_max_chars == 32


def test_mark_provider_factory_locateanything_structure_mode_precedence(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE", "screen")

    provider = build_mark_provider(
        {
            "grounding_provider_name": "locateanything",
            "locateanything_structure_mode": "target",
            "locateanything_max_visual_candidates": 12,
            "locateanything_visual_category_budget": 2,
            "locateanything_max_structure_calls": 3,
        }
    )

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.structure_mode == "target"
    assert provider.max_visual_candidates == 12
    assert provider.visual_category_budget == 2
    assert provider.max_structure_calls == 3


def test_mark_provider_factory_invalid_explicit_structure_mode_raises() -> None:
    with pytest.raises(ValueError):
        build_mark_provider(
            {
                "grounding_provider_name": "locateanything",
                "locateanything_structure_mode": "bad",
            }
        )


def test_mark_provider_factory_invalid_env_structure_mode_falls_back_off(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_AGENT_GROUNDING_PROVIDER", "locateanything")
    monkeypatch.setenv("PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE", "bad")

    provider = build_mark_provider()

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.structure_mode == "off"
    assert provider.invalid_structure_mode == "bad"


def test_locateanything_rejects_non_positive_max_size() -> None:
    with pytest.raises(ValueError):
        LocateAnythingMLXProvider(max_size=0)


def test_locateanything_rejects_negative_context_budget() -> None:
    with pytest.raises(ValueError):
        LocateAnythingMLXProvider(context_max_chars=-1)


def test_locateanything_rejects_invalid_structure_mode() -> None:
    with pytest.raises(ValueError):
        LocateAnythingMLXProvider(structure_mode="bad")


def test_description_only_intent_requires_mark_id() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_text_hint": "Settings"},
            mark_registry=None,
            screen_id="screen-1",
            screen_binding=binding(),
        )

    assert exc_info.value.code == "mark_required"


def test_mark_prompt_preserves_non_sensitive_ui_text_and_geometry() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "ax_2",
                "screen_id": "screen-1",
                "bbox": [25, 53, 140, 98],
                "role": "ViewGroup",
                "text_summary": "我的",
            },
            {
                "mark_id": "ax_3",
                "screen_id": "screen-1",
                "bbox": [174, 57, 745, 95],
                "role": "LinearLayout",
                "text_summary": "搜索 食贫道",
            },
        ],
    )

    prompt = registry.prompt_block()

    assert "搜索" in prompt
    assert "食贫道" not in prompt
    assert "我的" in prompt
    assert "bbox=[174.0, 57.0, 745.0, 95.0]" in prompt
    assert "center=[459.5, 76.0]" in prompt
    assert "position=top-center-wide" in prompt
    assert "redacted" not in prompt
    assert "sha256" not in prompt


def test_mark_prompt_hides_private_content_text() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "m1",
                "screen_id": "screen-1",
                "bbox": [100, 100, 300, 200],
                "role": "TextView",
                "text_summary": "张三",
            }
        ],
    )

    prompt = registry.prompt_block()

    assert "张三" not in prompt
    assert "<private_or_content_text>" in prompt


def test_mark_prompt_still_redacts_sensitive_ui_text() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "m1",
                "screen_id": "screen-1",
                "bbox": [100, 100, 900, 180],
                "role": "TextView",
                "text_summary": "联系人 13800138000",
            }
        ],
    )

    prompt = registry.prompt_block()

    assert "13800138000" not in prompt
    assert "<redacted>" in prompt


def test_mark_grounding_rejects_target_text_mismatch() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "profile",
                "screen_id": "screen-1",
                "bbox": [25, 53, 140, 98],
                "role": "ViewGroup",
                "text_summary": "我的",
            },
            {
                "mark_id": "search",
                "screen_id": "screen-1",
                "bbox": [174, 57, 745, 95],
                "role": "LinearLayout",
                "text_summary": "搜索",
            },
        ],
    )

    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "tap",
                "target_mark_id": "profile",
                "target_text_hint": "搜索",
            },
            mark_registry=registry,
            screen_id="screen-1",
        )

    assert exc_info.value.code == "mark_semantic_mismatch"


def test_mark_grounding_allows_search_like_wide_top_target() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "search_bar",
                "screen_id": "screen-1",
                "bbox": [174, 57, 745, 95],
                "role": "LinearLayout",
                "text_summary": "推荐热词",
            }
        ],
    )

    action = ground_intent_to_action(
        {
            "_metadata": "intent",
            "action": "tap",
            "target_mark_id": "search_bar",
            "target_text_hint": "搜索",
        },
        mark_registry=registry,
        screen_id="screen-1",
    )

    assert action == {"_metadata": "do", "action": "Tap", "element": [459.5, 76.0]}


def test_fake_mark_provider_returns_mark_candidates() -> None:
    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    result = provider.provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="Settings", source="test")],
    )

    assert result.success is True
    assert result.provider == "fake"
    assert result.marks[0].mark_id == "fake_1"
    assert result.marks[0].bbox == [100, 200, 300, 400]
    assert result.marks[0].center == [200, 300]
    assert result.hints[0]["source"] == "test"


def test_fake_mark_provider_failure_is_not_executable_action() -> None:
    provider = FakeGroundingProvider(failure_code="provider_unavailable")
    result = provider.provide_marks(Screenshot(), binding(), hints=[MarkProviderHint(text="Settings")])

    assert result.success is False
    assert result.failure_code == "provider_unavailable"
    assert result.marks == []


def test_parse_uiautomator_marks_normalizes_interactive_nodes() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <hierarchy>
      <node index="0" text="Wi-Fi" resource-id="android:id/title" class="android.widget.TextView"
            package="android" content-desc="" clickable="true" enabled="true" bounds="[54,120][540,180]" />
      <node index="1" text="" resource-id="" class="android.view.View"
            package="android" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2400]" />
      <node index="2" text="Search" resource-id="com.example:id/search" class="android.widget.EditText"
            package="app" content-desc="Search settings" clickable="false" focusable="true" enabled="true" bounds="[0,200][1080,320]" />
    </hierarchy>"""

    marks = parse_uiautomator_marks(xml, screen_width=1080, screen_height=2400)

    assert [mark["mark_id"] for mark in marks] == ["ax_1", "ax_2"]
    assert marks[0]["bbox"] == [50, 50, 500, 75]
    assert marks[0]["center"] == [275, 62]
    assert marks[0]["role"] == "TextView"
    assert "Wi-Fi" in marks[0]["text_summary"]
    assert marks[1]["role"] == "EditText"


def test_parse_uiautomator_structure_preserves_topology_and_signed_bounds() -> None:
    xml = """<hierarchy>
      <node index="0" text="" resource-id="root" class="android.widget.FrameLayout"
            clickable="false" enabled="true" bounds="[-10,-20][1080,2400]">
        <node index="0" text="" resource-id="feed" class="androidx.recyclerview.widget.RecyclerView"
              scrollable="true" enabled="true" bounds="[0,200][1080,2200]">
          <node index="0" text="视频标题一" resource-id="title-13800138000" class="android.widget.TextView"
                clickable="true" enabled="true" bounds="[24,260][1000,420]" />
        </node>
      </node>
    </hierarchy>"""

    structure = parse_uiautomator_structure(xml, screen_width=1080, screen_height=2400)

    assert structure is not None
    assert structure.root_node_id == "node_1"
    root = structure.nodes["node_1"]
    list_node = structure.nodes["node_2"]
    title = structure.nodes["node_3"]
    assert root.bounds == (0, 0, 1000, 1000)
    assert list_node.parent_id == "node_1"
    assert list_node.scrollable is True
    assert title.parent_id == "node_2"
    assert title.text_summary == "视频标题一"
    assert title.resource_id_hash
    assert "13800138000" not in title.resource_id_hash


def test_parse_uiautomator_accepts_whitespace_and_clamps_out_of_range_bounds() -> None:
    xml = """<hierarchy>
      <node text="Search" class="android.widget.Button" clickable="true" enabled="true" bounds="[ -10, 20 ][ 1080, 2400 ]" />
      <node text="Zero" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,100][100,200]" />
      <node text="Bad" class="android.widget.Button" clickable="true" enabled="true" bounds="[bad]" />
    </hierarchy>"""

    marks = parse_uiautomator_marks(xml, screen_width=1080, screen_height=2400)
    summary = parse_uiautomator_summary(xml, screen_width=1080, screen_height=2400)

    assert marks[0]["bbox"] == [0, 8, 1000, 1000]
    assert summary["xml_status"] == "ok"
    assert summary["raw_node_count"] == 3
    assert summary["mark_count"] == 1
    assert summary["bounds_parse_fail_count"] == 1
    assert summary["filtered_zero_area_count"] == 1
    assert summary["interactive_candidate_count"] == 3


def test_parse_uiautomator_summary_cleans_control_chars_but_fails_closed_on_bad_xml() -> None:
    control_char_xml = """<hierarchy>
      <node text="OK\x01" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,0][100,100]" />
    </hierarchy>"""
    bad_xml = """<hierarchy><node text="A & B" class="android.widget.Button" bounds="[0,0][1,1]" /></hierarchy>"""

    assert parse_uiautomator_summary(control_char_xml, screen_width=100, screen_height=100)["mark_count"] == 1
    bad_summary = parse_uiautomator_summary(bad_xml, screen_width=100, screen_height=100)
    assert bad_summary["xml_status"] == "accessibility_xml_parse_error"
    assert bad_summary["raw_node_count"] == 0


def test_accessibility_tree_provider_returns_screen_bound_marks() -> None:
    xml = """<hierarchy>
      <node text="OK" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,200][300,400]" />
    </hierarchy>"""
    provider = AccessibilityTreeProvider(lambda timeout=None: xml)

    result = provider.provide_marks(Screenshot(), binding(), hints=[MarkProviderHint(text="OK")])

    assert result.success is True
    assert result.provider == "accessibility_tree"
    assert result.raw_screenshot_hash == "hash-1"
    assert result.provider_input_hash
    assert result.marks[0].mark_id == "ax_1"
    assert result.hints[0]["text_length"] == 2
    assert result.screen_structure is not None
    assert result.screen_structure["node_count"] == 1
    assert result.metadata["parse_summary"]["mark_count"] == 1


def test_accessibility_tree_provider_no_marks_fails_closed() -> None:
    provider = AccessibilityTreeProvider(lambda timeout=None: "<hierarchy />")

    result = provider.provide_marks(Screenshot(), binding())

    assert result.success is False
    assert result.failure_code == "accessibility_structure_missing"
    assert result.marks == []
    assert result.metadata["parse_summary"]["xml_status"] == "ok"


def test_accessibility_tree_provider_reports_parse_error_failure_code() -> None:
    provider = AccessibilityTreeProvider(lambda timeout=None: '<hierarchy><node text="A & B" /></hierarchy>')

    result = provider.provide_marks(Screenshot(), binding())

    assert result.success is False
    assert result.failure_code == "accessibility_xml_parse_error"
    assert result.metadata["parse_summary"]["xml_status"] == "accessibility_xml_parse_error"


def test_adb_dump_uiautomator_xml_extracts_closed_hierarchy(monkeypatch) -> None:
    from phone_agent.adb.device import dump_uiautomator_xml

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='UI hierchary dumped to: /dev/tty\n<?xml version="1.0"?><hierarchy><node /></hierarchy>\nDone\n',
            stderr="",
        )

    monkeypatch.setattr("phone_agent.adb.device.subprocess.run", fake_run)

    assert dump_uiautomator_xml() == '<?xml version="1.0"?><hierarchy><node /></hierarchy>'


def test_adb_dump_uiautomator_xml_timeout_is_normalized(monkeypatch) -> None:
    from phone_agent.adb.device import dump_uiautomator_xml

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=1)

    monkeypatch.setattr("phone_agent.adb.device.subprocess.run", fake_run)

    with pytest.raises(TimeoutError):
        dump_uiautomator_xml(timeout=1)


def test_visible_text_summary_extracts_bounded_unique_text() -> None:
    xml = """<hierarchy>
      <node text="Wi-Fi" class="android.widget.TextView" bounds="[0,0][1,1]" />
      <node text="Wi-Fi" class="android.widget.TextView" bounds="[0,0][1,1]" />
      <node text="" content-desc="Bluetooth" resource-id="com.example:id/bluetooth" class="android.widget.TextView" bounds="[0,0][1,1]" />
    </hierarchy>"""

    assert visible_text_summary(xml, max_chars=32) == "Wi-Fi, Bluetooth | com.example:i"


def test_fallback_provider_stops_after_first_successful_marks() -> None:
    class CountingProvider:
        name = "counting"
        version = "test"
        allow_raw_hints = False

        def __init__(self) -> None:
            self.calls = 0

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            self.calls += 1
            mark = MarkCandidate(mark_id="first", bbox=[1, 2, 3, 4], center=[2, 3], source=self.name)
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="first-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
                hints=[hint.redacted_summary() for hint in hints or []],
            )

    first = CountingProvider()
    second = CountingProvider()
    result = FallbackMarkProvider([first, second]).provide_marks(Screenshot(), binding(), hints=[])

    assert result.success is True
    assert result.provider == "counting"
    assert result.marks[0].mark_id == "first"
    assert first.calls == 1
    assert second.calls == 0


def test_fallback_provider_redacts_hints_for_opt_out_child_provider() -> None:
    class CapturingProvider:
        name = "capturing"
        version = "test"
        allow_raw_hints = False

        def __init__(self) -> None:
            self.seen: list[str] = []

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            self.seen = [hint.description() for hint in hints or []]
            mark = MarkCandidate(mark_id="m1", bbox=[1, 2, 3, 4], center=[2, 3], source=self.name)
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
            )

    child = CapturingProvider()
    FallbackMarkProvider([child]).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="点击 13800138000", source="task")],
    )

    assert child.seen == ["点击 <redacted>"]


def test_fallback_provider_continues_when_tree_marks_do_not_match_hint() -> None:
    class StaticProvider:
        version = "test"
        allow_raw_hints = False

        def __init__(self, name, mark_id, text):
            self.name = name
            self.mark_id = mark_id
            self.text = text
            self.calls = 0

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            self.calls += 1
            mark = MarkCandidate(
                mark_id=self.mark_id,
                bbox=[100, 100, 200, 200],
                center=[150, 150],
                source=self.name,
                text_summary=self.text,
            )
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=f"{self.name}-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
            )

    tree = StaticProvider("accessibility_tree", "ax_1", "Bluetooth")
    vision = StaticProvider("locateanything_mlx", "la_1_1", "Wi-Fi")
    result = FallbackMarkProvider([tree, vision]).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="Wi-Fi")],
    )

    assert tree.calls == 1
    assert vision.calls == 1
    assert [mark.mark_id for mark in result.marks] == ["ax_1", "la_1_1"]
    assert result.metadata["fallback_chain"][0]["usable"] is False
    assert result.metadata["fallback_chain"][0]["structure_count"] == 0
    assert result.metadata["fallback_chain"][1]["usable"] is True


def test_fallback_provider_preserves_accessibility_and_visual_structures() -> None:
    class StaticProvider:
        version = "test"
        allow_raw_hints = False

        def __init__(self, name, mark_id, text, structure_kind):
            self.name = name
            self.mark_id = mark_id
            self.text = text
            self.structure_kind = structure_kind

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            mark = MarkCandidate(
                mark_id=self.mark_id,
                bbox=[100, 100, 200, 200],
                center=[150, 150],
                source=self.name,
                text_summary=self.text,
            )
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=f"{self.name}-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
                screen_structures=[
                    {
                        "screen_id": screen_binding.screen_id,
                        "structure_kind": self.structure_kind,
                        "source_provider": self.name,
                        "structure_digest": f"{self.name}-digest",
                        "topology_digest": f"{self.name}-digest",
                        "nodes": {
                            f"{self.name}_node": {
                                "node_id": f"{self.name}_node",
                                "path": "0",
                                "bounds": [100, 100, 200, 200],
                                "role": "TextView",
                                "text_summary": self.text,
                                "visible": True,
                            }
                        },
                    }
                ],
            )

    result = FallbackMarkProvider(
        [
            StaticProvider("accessibility_tree", "ax_1", "Bluetooth", "accessibility"),
            StaticProvider("locateanything_mlx", "la_1_1", "Wi-Fi", "visual"),
        ]
    ).provide_marks(Screenshot(), binding(), hints=[MarkProviderHint(text="Wi-Fi")])

    assert result.success is True
    assert [item["structure_kind"] for item in result.screen_structures] == ["accessibility", "visual"]
    assert [row["structure_count"] for row in result.metadata["fallback_chain"]] == [1, 1]


def test_fallback_provider_preserves_successful_visual_sidecar_without_marks() -> None:
    class VisualSidecarProvider:
        name = "locateanything_mlx"
        version = "test"
        allow_raw_hints = True
        structure_mode = "target"

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            candidate = MarkCandidate(
                mark_id="la_1",
                bbox=[100, 100, 200, 200],
                center=[150, 150],
                source=self.name,
                role="button",
            )
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="visual-hash",
                marks=[],
                candidates=[candidate],
                candidate_count=1,
                screen_structures=[
                    {
                        "screen_id": screen_binding.screen_id,
                        "structure_kind": "visual",
                        "source_provider": self.name,
                        "structure_digest": "visual-digest",
                        "topology_digest": "visual-digest",
                        "nodes": {
                            "visual_1": {
                                "node_id": "visual_1",
                                "path": "visual/1",
                                "bounds": [100, 100, 200, 200],
                                "role": "button",
                                "visible": True,
                            }
                        },
                    }
                ],
            )

    result = FallbackMarkProvider([VisualSidecarProvider()]).provide_marks(
        Screenshot(), binding(), hints=[MarkProviderHint(text="target")]
    )

    assert result.success is False
    assert result.candidates[0].mark_id == "la_1"
    assert result.screen_structures[0]["structure_kind"] == "visual"


def test_fallback_provider_stops_when_tree_marks_match_hint() -> None:
    class StaticProvider:
        name = "accessibility_tree"
        version = "test"
        allow_raw_hints = False

        def __init__(self) -> None:
            self.calls = 0

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            self.calls += 1
            mark = MarkCandidate(mark_id="ax_1", bbox=[1, 2, 3, 4], center=[2, 3], text_summary="Wi-Fi")
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="tree-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
            )

    class ShouldNotRunProvider(StaticProvider):
        name = "locateanything_mlx"

        def provide_marks(self, *args, **kwargs):
            raise AssertionError("fallback should stop on usable tree marks")

    tree = StaticProvider()
    result = FallbackMarkProvider([tree, ShouldNotRunProvider()]).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="Wi-Fi")],
    )

    assert tree.calls == 1
    assert [mark.mark_id for mark in result.marks] == ["ax_1"]
    assert result.metadata["fallback_chain"][0]["usable"] is True


def test_fallback_provider_screen_mode_supplements_usable_tree_without_executable_marks() -> None:
    class TreeProvider:
        name = "accessibility_tree"
        version = "test"
        allow_raw_hints = False

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            mark = MarkCandidate(mark_id="ax_1", bbox=[1, 2, 3, 4], center=[2, 3], text_summary="Wi-Fi")
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="tree-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
                screen_structures=[
                    {
                        "screen_id": screen_binding.screen_id,
                        "structure_kind": "accessibility",
                        "source_provider": self.name,
                        "structure_digest": "tree-digest",
                        "topology_digest": "tree-digest",
                        "nodes": {
                            "node_1": {
                                "node_id": "node_1",
                                "path": "0",
                                "bounds": [1, 2, 3, 4],
                                "role": "TextView",
                                "text_summary": "Wi-Fi",
                                "visible": True,
                            }
                        },
                    }
                ],
            )

    class ScreenProvider:
        name = "locateanything_mlx"
        version = "test"
        allow_raw_hints = True
        structure_mode = "screen"

        def __init__(self) -> None:
            self.calls = 0

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            self.calls += 1
            mark = MarkCandidate(mark_id="la_screen_1_1", bbox=[10, 20, 30, 40], center=[20, 30], source=self.name, role="button")
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="screen-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
                screen_structures=[
                    {
                        "screen_id": screen_binding.screen_id,
                        "structure_kind": "visual",
                        "source_provider": self.name,
                        "structure_digest": "visual-digest",
                        "topology_digest": "visual-digest",
                        "nodes": {
                            "visual_1": {
                                "node_id": "visual_1",
                                "path": "visual/1",
                                "bounds": [10, 20, 30, 40],
                                "role": "button",
                                "visible": True,
                            }
                        },
                    }
                ],
            )

    screen_provider = ScreenProvider()
    result = FallbackMarkProvider([TreeProvider(), screen_provider]).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="Wi-Fi")],
    )

    assert screen_provider.calls == 1
    assert result.success is True
    assert [mark.mark_id for mark in result.marks] == ["ax_1"]
    assert [item["structure_kind"] for item in result.screen_structures] == ["accessibility", "visual"]
    assert len(result.metadata["fallback_chain"]) == 2


def test_fallback_provider_fails_closed_when_all_marks_miss_hint() -> None:
    class StaticProvider:
        version = "test"
        allow_raw_hints = False

        def __init__(self, name, mark_id, text):
            self.name = name
            self.mark_id = mark_id
            self.text = text

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            mark = MarkCandidate(
                mark_id=self.mark_id,
                bbox=[100, 100, 200, 200],
                center=[150, 150],
                source=self.name,
                text_summary=self.text,
            )
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=f"{self.name}-hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
            )

    result = FallbackMarkProvider(
        [
            StaticProvider("accessibility_tree", "ax_1", "Bluetooth"),
            StaticProvider("locateanything_mlx", "la_1_1", "Display"),
        ]
    ).provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="Wi-Fi")],
    )

    assert result.success is False
    assert result.failure_code == "grounding_no_usable_candidate"
    assert result.marks == []
    assert [candidate.mark_id for candidate in result.candidates] == ["ax_1", "la_1_1"]
    assert [row["usable"] for row in result.metadata["fallback_chain"]] == [False, False]


def test_hybrid_factory_builds_accessibility_then_locateanything_fallback() -> None:
    providers = build_mark_providers(
        {
            "grounding_provider_name": "hybrid",
            "accessibility_tree_dump": lambda timeout=None: "<hierarchy />",
            "grounding_model_path": "models/LocateAnything-3B-4bit",
        }
    )

    assert len(providers) == 1
    assert isinstance(providers[0], FallbackMarkProvider)
    assert [provider.name for provider in providers[0].providers] == ["accessibility_tree", "locateanything_mlx"]
    assert providers[0].composition_metadata["accessibility_child_enabled"] is True


def test_hybrid_factory_can_skip_accessibility_child_when_base_marks_present() -> None:
    providers = build_mark_providers(
        {
            "grounding_provider_name": "hybrid",
            "accessibility_tree_dump": lambda timeout=None: pytest.fail("tree should be skipped"),
            "skip_accessibility_provider": True,
        }
    )

    assert len(providers) == 1
    assert isinstance(providers[0], FallbackMarkProvider)
    assert [provider.name for provider in providers[0].providers] == ["locateanything_mlx"]
    assert providers[0].composition_metadata["accessibility_child_skip_reason"] == "skip_accessibility_provider"


def test_hybrid_factory_missing_dump_callback_emits_synthetic_skip_row(monkeypatch) -> None:
    class StaticLocateProvider:
        name = "locateanything_mlx"
        version = "test"
        allow_raw_hints = True

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            mark = MarkCandidate(mark_id="la_1", bbox=[100, 100, 200, 200], center=[150, 150], source=self.name)
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

    monkeypatch.setattr("phone_agent.grounding.factory._build_locateanything_provider", lambda cfg: StaticLocateProvider())

    providers = build_mark_providers({"grounding_provider_name": "hybrid"})
    result = providers[0].provide_marks(Screenshot(), binding())

    assert isinstance(providers[0], FallbackMarkProvider)
    assert result.success is True
    assert result.metadata["hybrid_factory"]["accessibility_child_enabled"] is False
    assert result.metadata["hybrid_factory"]["accessibility_child_skip_reason"] == "accessibility_dump_callback_missing"
    assert result.metadata["fallback_chain"][0]["provider"] == "accessibility_tree"
    assert result.metadata["fallback_chain"][0]["skip_reason"] == "accessibility_dump_callback_missing"


def test_locateanything_provider_multiple_hints_create_multiple_marks(monkeypatch) -> None:
    outputs = iter(["<box>100 200 300 400</box>", "<box>500 600 700 800</box>"])
    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit")

    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.system", lambda: "Darwin")
    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.machine", lambda: "arm64")
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(provider, "_prepare_image", lambda screenshot: (object(), "input-hash"))
    monkeypatch.setattr(provider, "_run_model", lambda image, description, timeout=None: next(outputs))

    result = provider.provide_marks(
        Screenshot(),
        binding(),
        hints=[MarkProviderHint(text="first"), MarkProviderHint(text="second")],
    )

    assert result.success is True
    assert [mark.mark_id for mark in result.marks] == ["la_1_1", "la_2_1"]
    assert [mark.bbox for mark in result.marks] == [[100, 200, 300, 400], [500, 600, 700, 800]]
    assert result.provider_input_hash == "input-hash"


def test_locateanything_target_structure_mode_returns_visual_sidecar(monkeypatch) -> None:
    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit", structure_mode="target")

    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.system", lambda: "Darwin")
    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.machine", lambda: "arm64")
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(provider, "_prepare_image", lambda screenshot: (object(), "input-hash"))
    monkeypatch.setattr(provider, "_run_model", lambda image, description, timeout=None, context=None: "<box>100 200 300 400</box>")

    result = provider.provide_marks(Screenshot(), binding(), hints=[MarkProviderHint(text="搜索 13800138000")])

    assert result.success is True
    assert len(result.marks) == 1
    assert result.screen_structures[0]["structure_kind"] == "visual"
    node = result.screen_structures[0]["nodes"]["visual_1"]
    assert node["text_summary"] == "visual_target"
    assert "13800138000" not in str(result.to_dict())


def test_locateanything_target_structure_mode_multi_box_is_not_immediately_executable(monkeypatch) -> None:
    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit", structure_mode="target")

    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.system", lambda: "Darwin")
    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.machine", lambda: "arm64")
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(provider, "_prepare_image", lambda screenshot: (object(), "input-hash"))
    monkeypatch.setattr(
        provider,
        "_run_model",
        lambda image, description, timeout=None, context=None: "<box>100 200 300 400</box><box>500 600 700 800</box>",
    )

    result = provider.provide_marks(Screenshot(), binding(), hints=[MarkProviderHint(text="target")])

    assert result.success is True
    assert result.marks == []
    assert result.candidate_count == 2
    assert result.metadata["ambiguous_for_execution"] is True
    assert result.screen_structures[0]["node_count"] == 2


def test_mark_provider_result_to_dict_sanitizes_metadata_and_structure_text() -> None:
    mark = MarkCandidate(mark_id="m1", bbox=[1, 2, 3, 4], center=[2, 3], text_summary="13800138000")
    result = MarkProviderResult(
        success=True,
        provider="test",
        marks=[mark],
        candidates=[mark],
        candidate_count=1,
        metadata={"raw_hint": "请点击 13800138000", "nested": {"ocr": "foo@example.com"}},
        screen_structures=[
            {
                "structure_kind": "visual",
                "nodes": {
                    "visual_1": {
                        "node_id": "visual_1",
                        "text_summary": "13800138000",
                        "content_desc_summary": "foo@example.com",
                    }
                },
            }
        ],
    )

    serialized = str(result.to_dict())

    assert "13800138000" not in serialized
    assert "foo@example.com" not in serialized


def test_locateanything_provider_ambiguous_single_hint_fails_closed(monkeypatch) -> None:
    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit")

    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.system", lambda: "Darwin")
    monkeypatch.setattr("phone_agent.grounding.locateanything.platform.machine", lambda: "arm64")
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(provider, "_prepare_image", lambda screenshot: (object(), "input-hash"))
    monkeypatch.setattr(
        provider,
        "_run_model",
        lambda image, description, timeout=None: "<box>100 200 300 400</box><box>500 600 700 800</box>",
    )

    result = provider.provide_marks(Screenshot(), binding(), hints=[MarkProviderHint(text="target")])

    assert result.success is False
    assert result.failure_code == "grounding_ambiguous"
    assert result.marks == []
    assert result.candidate_count == 2


def test_locateanything_mlx_run_model_uses_gui_prompt_and_fallback_generate(monkeypatch) -> None:
    captured: dict = {}

    def fake_load(model_path: str):
        captured["model_path"] = model_path
        return SimpleNamespace(config={"model_type": "locateanything"}), "processor"

    def fake_apply_chat_template(processor, config, prompt, **kwargs):
        captured["template_processor"] = processor
        captured["template_config"] = config
        captured["template_prompt"] = prompt
        captured["template_kwargs"] = kwargs
        return f"<chat><image-1>{prompt}</chat>"

    def fake_generate(model, processor, **kwargs):
        captured.update({"model": model, "processor": processor, **kwargs})
        return SimpleNamespace(text="<ref>WLAN</ref><box><100><200><300><400></box>")

    monkeypatch.setitem(
        sys.modules,
        "mlx_vlm",
        SimpleNamespace(load=fake_load, generate=fake_generate),
    )
    monkeypatch.setitem(
        sys.modules,
        "mlx_vlm.prompt_utils",
        SimpleNamespace(apply_chat_template=fake_apply_chat_template),
    )

    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit")
    output = provider._run_model(object(), "WLAN setting")

    assert output == "<ref>WLAN</ref><box><100><200><300><400></box>"
    assert captured["model_path"] == "models/LocateAnything-3B-4bit"
    assert captured["model"].config == {"model_type": "locateanything"}
    assert captured["processor"] == "processor"
    assert captured["prompt"] == "<chat><image-1>Locate the region that matches the following description: WLAN setting.</chat>"
    assert captured["template_prompt"] == "Locate the region that matches the following description: WLAN setting."
    assert captured["template_kwargs"]["num_images"] == 1
    assert captured["image"] is not None
    assert captured["max_tokens"] == 2048
    assert captured["temperature"] == 0.0
    assert captured["generation_mode"] == "hybrid"


def test_locateanything_prompt_allows_bounded_context_without_changing_template(monkeypatch) -> None:
    captured: dict = {}

    def fake_apply_chat_template(processor, config, prompt, **kwargs):
        captured["template_prompt"] = prompt
        captured["template_kwargs"] = kwargs
        return f"<chat><image-0>{prompt}</chat>"

    monkeypatch.setitem(
        sys.modules,
        "mlx_vlm.prompt_utils",
        SimpleNamespace(apply_chat_template=fake_apply_chat_template),
    )
    provider = LocateAnythingMLXProvider(context_max_chars=18)
    provider._model = SimpleNamespace(config={"model_type": "locateanything"})
    provider._processor = "processor"

    prompt = provider._build_prompt("Wi-Fi row", context="Settings screen with many visible options")

    assert prompt.startswith("<chat><image-0>")
    assert captured["template_kwargs"]["num_images"] == 1
    assert captured["template_prompt"] == (
        "Locate the region that matches the following description: Wi-Fi row.\n"
        "Context: Settings screen wi"
    )


def test_locateanything_mlx_run_model_prefers_parallel_box_decoding(monkeypatch) -> None:
    captured: dict = {}

    class FakeModel:
        config = {"model_type": "locateanything"}

        def pbd_generate(self, input_ids, **kwargs):
            captured["pbd_input_ids"] = input_ids
            captured["pbd_kwargs"] = kwargs
            return [1, 2, 3]

    class FakeProcessor:
        def decode(self, tokens, **kwargs):
            captured["decode_tokens"] = tokens
            captured["decode_kwargs"] = kwargs
            return "<ref>WLAN</ref><box><100><200><300><400></box>"

    def fake_load(model_path: str):
        captured["model_path"] = model_path
        return FakeModel(), FakeProcessor()

    def fake_apply_chat_template(processor, config, prompt, **kwargs):
        captured["template_prompt"] = prompt
        return f"<chat><image-0>{prompt}</chat>"

    def fake_prepare_inputs(processor, **kwargs):
        captured["prepare_processor"] = processor
        captured["prepare_kwargs"] = kwargs
        return {"input_ids": "ids", "attention_mask": "mask", "pixel_values": "pixels", "image_grid_hws": "grid"}

    mlx_vlm_module = ModuleType("mlx_vlm")
    mlx_vlm_module.load = fake_load
    mlx_vlm_module.generate = lambda *args, **kwargs: pytest.fail("fallback generate should not be called")
    prompt_utils_module = ModuleType("mlx_vlm.prompt_utils")
    prompt_utils_module.apply_chat_template = fake_apply_chat_template
    utils_module = ModuleType("mlx_vlm.utils")
    utils_module.prepare_inputs = fake_prepare_inputs
    monkeypatch.setitem(sys.modules, "mlx_vlm", mlx_vlm_module)
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", prompt_utils_module)
    monkeypatch.setitem(sys.modules, "mlx_vlm.utils", utils_module)

    provider = LocateAnythingMLXProvider(model_path="models/LocateAnything-3B-4bit")
    output = provider._run_model(object(), "WLAN setting")

    assert output == "<ref>WLAN</ref><box><100><200><300><400></box>"
    assert captured["model_path"] == "models/LocateAnything-3B-4bit"
    assert captured["template_prompt"] == "Locate the region that matches the following description: WLAN setting."
    assert captured["prepare_kwargs"]["images"] == [object()] or len(captured["prepare_kwargs"]["images"]) == 1
    assert captured["prepare_kwargs"]["prompts"] == "<chat><image-0>Locate the region that matches the following description: WLAN setting.</chat>"
    assert captured["pbd_input_ids"] == "ids"
    assert captured["pbd_kwargs"] == {
        "generation_mode": "hybrid",
        "max_tokens": 2048,
        "pixel_values": "pixels",
        "image_grid_hws": "grid",
    }
    assert captured["decode_tokens"] == [1, 2, 3]
    assert captured["decode_kwargs"] == {"skip_special_tokens": False}


def _png_screenshot():
    import base64
    from io import BytesIO
    from PIL import Image

    image = Image.new("RGB", (100, 200), color="white")
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return SimpleNamespace(base64_data=base64.b64encode(buffered.getvalue()).decode("ascii"), width=100, height=200)


def test_parse_box_response_accepts_json_bbox() -> None:
    parsed = parse_box_response('{"bbox":[100,200,300,400]}')

    assert parsed.bbox == [100, 200, 300, 400]


def test_remote_openai_provider_success_and_sanitized_request() -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"content": "<box>100 200 300 400</box>"}}]}

    provider = RemoteOpenAIGroundingProvider(
        base_url="https://api.stepfun.com/v1?api_key=secret",
        api_key="sk-secret",
        model="step-3.7-flash",
        request_callable=fake_request,
    )

    result = provider.provide_marks(
        _png_screenshot(),
        binding(),
        hints=[MarkProviderHint(text="搜索 13800138000", role="input")],
    )

    assert result.success is True
    assert result.marks[0].bbox == [100, 200, 300, 400]
    assert result.marks[0].confidence == 0.5
    assert result.provider_input_hash
    assert result.metadata["base_url_host"] == "api.stepfun.com"
    assert result.metadata["raw_hint_sent"] is False
    assert calls[0]["model"] == "step-3.7-flash"
    assert calls[0]["image_data_url"].startswith("data:image/png;base64,")
    raw = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "sk-secret" not in raw
    assert "api_key=secret" not in raw
    assert "data:image" not in raw


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "grounding_no_candidate"),
        ("no box", "remote_invalid_response"),
        ("<box>100 100 200 200</box><box>300 300 400 400</box>", "grounding_ambiguous"),
        ("<box>-1 0 10 10</box>", "remote_invalid_bbox"),
    ],
)
def test_remote_openai_provider_fail_closed_parse_errors(text: str, code: str) -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    provider = RemoteOpenAIGroundingProvider(request_callable=lambda **kwargs: text)

    result = provider.provide_marks(_png_screenshot(), binding(), hints=[MarkProviderHint(text="search")])

    assert result.success is False
    assert result.failure_code == code
    assert result.marks == []


def test_remote_openai_provider_missing_config_without_fake_client() -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    provider = RemoteOpenAIGroundingProvider(api_key=None)

    result = provider.provide_marks(_png_screenshot(), binding(), hints=[MarkProviderHint(text="search")])

    assert result.success is False
    assert result.failure_code == "remote_missing_config"


def test_mark_providers_remote_modes_and_default_hybrid_unchanged(monkeypatch) -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    monkeypatch.delenv("PHONE_AGENT_GROUNDING_PROVIDER", raising=False)
    assert [provider.name for provider in build_mark_providers({"grounding_provider_name": "remote_openai", "remote_grounding_request_callable": lambda **kwargs: "<box>1 1 10 10</box>"})] == ["remote_openai"]
    assert [provider.name for provider in build_mark_providers({"grounding_provider_name": "stepfun", "remote_grounding_request_callable": lambda **kwargs: "<box>1 1 10 10</box>"})] == ["remote_openai"]

    single = build_mark_provider({"grounding_provider_name": "hybrid_remote"})
    assert single is None

    providers = build_mark_providers({"grounding_provider_name": "hybrid_remote", "remote_grounding_request_callable": lambda **kwargs: "<box>1 1 10 10</box>"})
    assert len(providers) == 1
    assert isinstance(providers[0], FallbackMarkProvider)
    assert [provider.name for provider in providers[0].providers] == ["remote_openai"]
    assert isinstance(providers[0].providers[0], RemoteOpenAIGroundingProvider)

    default_providers = build_mark_providers({"grounding_model_path": "models/LocateAnything-3B-4bit"})
    assert isinstance(default_providers[0], FallbackMarkProvider)
    assert [provider.name for provider in default_providers[0].providers] == ["locateanything_mlx"]


def test_remote_openai_provider_redacts_outbound_prompt_by_default() -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return "<box>100 200 300 400</box>"

    provider = RemoteOpenAIGroundingProvider(request_callable=fake_request)

    result = provider.provide_marks(_png_screenshot(), binding(), hints=[MarkProviderHint(text="搜索 13800138000", role="input")])

    assert result.success is True
    assert "13800138000" not in calls[0]["prompt"]
    assert "<redacted>" in calls[0]["prompt"]


def test_remote_openai_provider_raw_hint_requires_opt_in() -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    calls = []
    provider = RemoteOpenAIGroundingProvider(allow_raw_hints=True, request_callable=lambda **kwargs: calls.append(kwargs) or "<box>100 200 300 400</box>")

    result = provider.provide_marks(_png_screenshot(), binding(), hints=[MarkProviderHint(text="搜索 13800138000", role="input")])

    assert result.success is True
    assert "13800138000" in calls[0]["prompt"]
    assert result.metadata["raw_hint_sent"] is True


def test_remote_openai_provider_classifies_sdk_timeout_names() -> None:
    from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider

    class APITimeoutError(Exception):
        pass

    provider = RemoteOpenAIGroundingProvider(request_callable=lambda **kwargs: (_ for _ in ()).throw(APITimeoutError("secret")))

    result = provider.provide_marks(_png_screenshot(), binding(), hints=[MarkProviderHint(text="search")])

    assert result.success is False
    assert result.failure_code == "remote_timeout"
    assert result.message == "APITimeoutError"


def test_mark_providers_hybrid_remote_orders_accessibility_before_remote() -> None:
    providers = build_mark_providers(
        {
            "grounding_provider_name": "hybrid_remote",
            "accessibility_tree_dump": lambda timeout=None: '<hierarchy><node text="搜索" class="android.widget.EditText" clickable="true" enabled="true" bounds="[0,0][100,100]" /></hierarchy>',
            "remote_grounding_request_callable": lambda **kwargs: "<box>1 1 10 10</box>",
        }
    )

    assert len(providers) == 1
    assert isinstance(providers[0], FallbackMarkProvider)
    assert [provider.name for provider in providers[0].providers] == ["accessibility_tree", "remote_openai"]


def test_mark_providers_accessibility_remote_alias() -> None:
    providers = build_mark_providers(
        {
            "grounding_provider_name": "accessibility_remote",
            "remote_grounding_request_callable": lambda **kwargs: "<box>1 1 10 10</box>",
        }
    )

    assert isinstance(providers[0], FallbackMarkProvider)
    assert [provider.name for provider in providers[0].providers] == ["remote_openai"]
