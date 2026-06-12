import pytest
import sys
from types import ModuleType, SimpleNamespace

from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.factory import build_mark_provider
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.parser import GroundingParseError, calibrate_bbox_from_resized_input, parse_box_response
from phone_agent.grounding.provider import MarkProviderHint, ScreenBinding


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


def test_locateanything_rejects_non_positive_max_size() -> None:
    with pytest.raises(ValueError):
        LocateAnythingMLXProvider(max_size=0)


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
