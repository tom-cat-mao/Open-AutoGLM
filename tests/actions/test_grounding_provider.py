import pytest
import sys
import subprocess
from types import ModuleType, SimpleNamespace

from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.grounding.accessibility import AccessibilityTreeProvider, parse_uiautomator_marks, visible_text_summary
from phone_agent.grounding.fallback import FallbackMarkProvider
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.factory import build_mark_provider, build_mark_providers
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.parser import GroundingParseError, calibrate_bbox_from_resized_input, parse_box_response
from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, MarkProviderResult, ScreenBinding


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


def test_mark_provider_factory_passes_locateanything_context_budget() -> None:
    provider = build_mark_provider(
        {
            "grounding_provider_name": "locateanything",
            "locateanything_context_max_chars": 32,
        }
    )

    assert isinstance(provider, LocateAnythingMLXProvider)
    assert provider.context_max_chars == 32


def test_locateanything_rejects_non_positive_max_size() -> None:
    with pytest.raises(ValueError):
        LocateAnythingMLXProvider(max_size=0)


def test_locateanything_rejects_negative_context_budget() -> None:
    with pytest.raises(ValueError):
        LocateAnythingMLXProvider(context_max_chars=-1)


def test_description_only_intent_requires_mark_id() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_text_hint": "Settings"},
            mark_registry=None,
            screen_id="screen-1",
            screen_binding=binding(),
        )

    assert exc_info.value.code == "mark_required"


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


def test_accessibility_tree_provider_no_marks_fails_closed() -> None:
    provider = AccessibilityTreeProvider(lambda timeout=None: "<hierarchy />")

    result = provider.provide_marks(Screenshot(), binding())

    assert result.success is False
    assert result.failure_code == "grounding_no_candidate"
    assert result.marks == []


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
    assert result.metadata["fallback_chain"][1]["usable"] is True


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
