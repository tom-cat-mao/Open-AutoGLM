"""R1: locate-tool path queries LocateAnything at the high-resolution tier
(LOCATE_LA_MAX_SIZE=2048, env/config overridable); the observation fallback
path keeps the 960 instance tier; the shared P2 singleton is never mutated.

The 1216x2066 crop is the scope crop of the real 20260805 trace frame
(scope=[0,200.38,1000,983.33] over a 1216x2640 screenshot): at 960 it
squeezes to 565x960, at 2048 it passes through at full crop resolution.
"""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from PIL import Image

from phone_agent.config.policy import LOCATE_LA_MAX_SIZE
from phone_agent.graph.marks import Mark, MarkRegistry
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.fallback import FallbackMarkProvider
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.provider import MarkProviderHint, ScreenBinding

_SURFACE = "com.example/.MainActivity"
_SCREEN = "screen-1"
# The scope crop of the real 20260805 trace frame (scope=[0,200.38,1000,983.33]
# over a 1216x2640 screenshot) is 1216x2066 px.
_SCOPE_CROP_PX = (1216, 2066)


def _observation(surface: str = _SURFACE) -> dict:
    return {"snapshot": {"foreground_activity": surface}}


def _screenshot(size: tuple[int, int]):
    image = Image.new("RGB", size, color=(200, 200, 200))
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    class _S:
        base64_data = base64.b64encode(buffered.getvalue()).decode("ascii")
    return _S()


def _mark_registry() -> MarkRegistry:
    marks = {
        # P1: scope is mandatory; a full-frame container mark makes the crop
        # an identity mapping so the LA box passes through unchanged.
        "scope_full": Mark(
            mark_id="scope_full",
            screen_id=_SCREEN,
            bbox=(0, 0, 1000, 1000),
            center=(500, 500),
            source="accessibility",
            role="View",
            text_summary="全屏容器",
        ),
    }
    return MarkRegistry(
        screen_id=_SCREEN,
        marks=marks,
        semantic_screen_id="semantic-1",
        observation_epoch=1,
        raw_screenshot_hash="fake-hash",
    )


def _locate_state(base_state: dict, **overrides) -> dict:
    state = dict(base_state)
    state["action_parsed"] = {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月1日",
        "scope_mark_id": "scope_full",
    }
    state["action_raw"] = (
        '{"type":"intent","action":"locate","target_text_hint":"10月1日",'
        '"scope_mark_id":"scope_full"}'
    )
    state["observation"] = _observation()
    state["mark_registry"] = _mark_registry().to_dict()
    state["locate_count"] = 0
    state.update(overrides)
    return state


def _config(provider, fake_device, **configurable) -> dict:
    cfg = {
        "configurable": {
            "device_factory": fake_device,
            "verbose": False,
            "locate_provider": provider,
        }
    }
    cfg["configurable"].update(configurable)
    return cfg


# ----------------------------------------------------------------------
# Provider-level: _prepare_image tier behavior
# ----------------------------------------------------------------------


def test_prepare_image_2048_tier_keeps_crop_resolution() -> None:
    provider = LocateAnythingMLXProvider(max_size=960)
    crop = _screenshot(_SCOPE_CROP_PX)

    # R1: at the 2048 tier the 1216x2066 crop keeps ~full resolution — PIL
    # thumbnail only clamps the 18px height overshoot above the 2048 box
    # (1205x2048), never the 565x960 squeeze the 960 tier applies. Digit
    # height stays ~38px instead of collapsing to ~17px.
    high_tier, _ = provider._prepare_image(crop, max_size=2048)
    assert high_tier.size[0] == 1205
    assert high_tier.size[1] == 2048
    assert high_tier.size[0] * high_tier.size[1] > 2_000_000

    fallback_tier, _ = provider._prepare_image(crop)
    assert fallback_tier.size == (565, 960)

    # Instance tier is untouched by a per-call override (P2 singleton safety).
    assert provider.max_size == 960


def test_prepare_image_short_side_below_960_never_upscaled() -> None:
    provider = LocateAnythingMLXProvider(max_size=960)
    small = _screenshot((500, 900))

    high_tier, _ = provider._prepare_image(small, max_size=2048)
    fallback_tier, _ = provider._prepare_image(small)
    assert high_tier.size == (500, 900)
    assert fallback_tier.size == (500, 900)


def test_prepare_image_none_tier_skips_thumbnail() -> None:
    provider = LocateAnythingMLXProvider(max_size=None)
    assert provider.max_size is None
    crop = _screenshot(_SCOPE_CROP_PX)
    out, _ = provider._prepare_image(crop)
    assert out.size == (1216, 2066)


# ----------------------------------------------------------------------
# Locate tool path: per-call tier override
# ----------------------------------------------------------------------


def test_locate_path_queries_at_locate_max_size_tier(
    base_state, fake_device
) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(
        _locate_state(base_state), _config(provider, fake_device)
    )

    assert result["action_result"]["success"] is True
    assert provider.requests and provider.requests[0]["max_size"] == LOCATE_LA_MAX_SIZE
    assert LOCATE_LA_MAX_SIZE == 2048


def test_locate_path_max_size_config_override(base_state, fake_device) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(
        _locate_state(base_state),
        _config(provider, fake_device, locate_max_size=1024),
    )

    assert result["action_result"]["success"] is True
    assert provider.requests[0]["max_size"] == 1024


def test_locate_path_max_size_env_override(monkeypatch, base_state, fake_device) -> None:
    monkeypatch.setenv("PHONE_AGENT_LOCATE_LA_MAX_SIZE", "4096")
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(
        _locate_state(base_state), _config(provider, fake_device)
    )

    assert result["action_result"]["success"] is True
    assert provider.requests[0]["max_size"] == 4096


def test_locate_path_invalid_env_falls_back_to_policy_constant(
    monkeypatch, base_state, fake_device
) -> None:
    monkeypatch.setenv("PHONE_AGENT_LOCATE_LA_MAX_SIZE", "not-an-int")
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(
        _locate_state(base_state), _config(provider, fake_device)
    )

    assert result["action_result"]["success"] is True
    assert provider.requests[0]["max_size"] == LOCATE_LA_MAX_SIZE


def test_locate_path_passes_tier_every_call_not_just_first(
    base_state, fake_device
) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _locate_state(base_state)
    first = execute_node(state, _config(provider, fake_device))
    state2 = {
        **state,
        "gui_memory": first["gui_memory"],
        "locate_count": first["locate_count"],
        "mark_registry": first["mark_registry"],
    }
    second = execute_node(state2, _config(provider, fake_device))

    assert second["action_result"]["success"] is True
    assert [request["max_size"] for request in provider.requests] == [
        LOCATE_LA_MAX_SIZE,
        LOCATE_LA_MAX_SIZE,
    ]


# ----------------------------------------------------------------------
# Observation fallback path: 960 instance tier, no per-call override
# ----------------------------------------------------------------------


def test_observation_fallback_path_keeps_960_tier(monkeypatch) -> None:
    provider = LocateAnythingMLXProvider(max_size=960)
    # Synthetic: the provider's file-existence gate is stubbed so the test is
    # CWD-independent and never needs the real LocateAnything checkpoint.
    monkeypatch.setattr(
        provider, "model_path", SimpleNamespace(exists=lambda: True)
    )
    received: dict = {}

    def fake_prepare(screenshot, max_size=None):
        received["max_size"] = max_size
        return Image.new("RGB", (10, 10)), "input-hash"

    def fake_run_model(image, description, **kwargs):
        return "<box>100 200 300 400</box>"

    monkeypatch.setattr(provider, "_prepare_image", fake_prepare)
    monkeypatch.setattr(provider, "_run_model", fake_run_model)
    fallback = FallbackMarkProvider([provider])

    binding = ScreenBinding(
        screen_id="screen-1", raw_screenshot_hash="hash-1", width=1216, height=2640
    )
    result = fallback.provide_marks(
        _screenshot(_SCOPE_CROP_PX),
        binding,
        hints=[MarkProviderHint(text="某个目标")],
    )

    assert result.success is True
    # The observation chain never overrides the tier: the child is called with
    # its instance default (960), not a locate-path override.
    assert received["max_size"] is None
    assert provider.max_size == 960
