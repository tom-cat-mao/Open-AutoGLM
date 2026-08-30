"""WP-H: full-resolution, hint-first and optional-scope locate tests."""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from phone_agent.adb.screenshot import Screenshot
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint
from phone_agent.v2.locate_scope import build_scope_crop
from phone_agent.v2.middleware.trace import TraceMiddleware
from phone_agent.v2.session import PhoneSession, StaleMarkError, mint_badge
from phone_agent.v2.tools.perception import build_perception_tools


def _png(width: int = 1000, height: int = 2000) -> str:
    image = Image.new("RGB", (width, height), color=(20, 40, 60))
    output = BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class _Device:
    def __init__(self) -> None:
        self.shot = Screenshot(_png(), width=1000, height=2000)

    def get_screenshot(self, device_id=None):
        return self.shot

    def get_foreground_app(self, device_id=None):
        return None


def _config(**overrides):
    values = {
        "device_id": None,
        "accessibility_timeout": 3.0,
        "accessibility_max_marks": 80,
        "grounding_provider": "fake",
        "locateanything_model": None,
        "locateanything_max_size": 960,
        "locate_max_size": 0,
        "scope_padding_ratio": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _mark(mark_id: str, bbox, *, role="TextView") -> MarkCandidate:
    return MarkCandidate(
        mark_id=mark_id,
        bbox=list(bbox),
        center=[(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2],
        role=role,
        text_summary=mark_id,
        source="fake",
        epoch=2,
    )


def _session(provider=None, **config_overrides) -> PhoneSession:
    session = PhoneSession(_config(**config_overrides), device_factory=_Device())
    session.epoch = 2
    session._last_width = 1000
    session._last_height = 2000
    session._locate_provider = provider or FakeGroundingProvider(
        bbox=[100, 200, 500, 600]
    )
    session._locate_provider_built = True
    return session


def test_prepare_image_zero_sentinel_keeps_original_resolution():
    provider = LocateAnythingMLXProvider(max_size=960)
    shot = Screenshot(_png(1216, 2640), width=1216, height=2640)

    original, _ = provider._prepare_image(shot, max_size=0)
    tiered, _ = provider._prepare_image(shot)

    assert original.size == (1216, 2640)
    assert max(tiered.size) == 960


def test_hint_maps_visible_text_into_instruction_and_context_only_keeps_intent():
    provider = LocateAnythingMLXProvider(context_max_chars=12)
    hint = MarkProviderHint(
        text="右上角放大镜圆形按钮",
        visible_text_hint="搜索商品和店铺入口",
        role="ImageButton",
        intent="打开搜索页",
        action="locate",
    )

    context = provider._context_for_hint(hint)
    visible = provider._visible_text_for_hint(hint)
    instruction = provider._build_instruction(
        hint.description(), context=context, visible_text_hint=visible
    )

    assert "右上角放大镜圆形按钮" in hint.description()
    assert context == "打开搜索页"
    assert "ImageButton" not in context
    assert "locate" not in context
    assert "Text on or near the target: 搜索商品和店铺入口" in instruction


def test_scope_crop_and_affine_mapping_are_exact_without_padding():
    session = _session()
    crop = build_scope_crop(
        session.device_factory.shot,
        session=session,
        region_bbox_1000=(100, 200, 600, 800),
        padding_ratio=0.0,
    )

    assert crop.crop.width == 500
    assert crop.crop.height == 1200
    assert crop.origin_1000 == (100.0, 200.0)
    assert crop.size_1000 == (500.0, 600.0)
    assert crop.map_box_to_full((100, 200, 500, 600)) == (
        150.0,
        320.0,
        350.0,
        560.0,
    )


def test_locate_container_scope_maps_crop_hit_back_and_uses_full_binding():
    provider = FakeGroundingProvider(bbox=[100, 200, 500, 600])
    session = _session(provider)
    scope_id = mint_badge("ax_1", 2)
    session.marks = {scope_id: _mark(scope_id, (100, 200, 600, 800), role="View")}

    mark = session.locate(
        "圆形日期按钮",
        visible_text_hint="15",
        intent="选择 15 日",
        scope_mark_id=scope_id,
    )

    assert mark.bbox == [150, 320, 350, 560]
    assert mark.center == [250, 440]
    request = provider.requests[0]
    assert request["max_size"] == 0
    assert request["screen_binding"]["width"] == 1000
    assert request["screen_binding"]["height"] == 2000
    assert request["hints"][0]["has_visible_text_hint"] is True
    assert session.epoch == 2
    assert session.resolve_mark(mark.mark_id) is mark
    assert session.last_locate_metadata()["provider_input_size_px"] == [500, 1200]


def test_interval_scope_uses_full_width_for_text_anchors():
    provider = FakeGroundingProvider(bbox=[0, 0, 1000, 1000])
    session = _session(provider)
    start_id = mint_badge("ax_2", 2)
    end_id = mint_badge("ax_3", 2)
    session.marks = {
        start_id: _mark(start_id, (100, 300, 400, 340)),
        end_id: _mark(end_id, (100, 700, 400, 740)),
    }

    mark = session.locate(
        "15 日", scope_start_mark_id=start_id, scope_end_mark_id=end_id
    )

    assert mark.bbox == [0, 300, 1000, 700]
    assert provider.requests[0]["max_size"] == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"scope_mark_id": "missing@e2"}, "not on the current screen"),
        ({"scope_mark_id": "ax_1@e1"}, "current batch is e2"),
        ({"scope_end_mark_id": "ax_3@e2"}, "requires scope_start_mark_id"),
    ],
)
def test_scope_validation_fails_before_provider_call(kwargs, message):
    provider = FakeGroundingProvider()
    session = _session(provider)
    with pytest.raises((StaleMarkError, ValueError), match=message):
        session.locate("target", **kwargs)
    assert provider.requests == []


def test_scope_validation_tool_receipt_is_fail_closed():
    provider = FakeGroundingProvider()
    session = _session(provider)
    tool = {
        item.name: item for item in build_perception_tools(session, _config())
    }["locate"]

    result = tool.invoke(
        {
            "description": "target",
            "scope_end_mark_id": mint_badge("ax_3", 2),
        }
    )

    assert result.startswith("定位失败:")
    assert "requires scope_start_mark_id" in result
    assert provider.requests == []


def test_inverted_interval_fails_before_provider_call():
    provider = FakeGroundingProvider()
    session = _session(provider)
    start_id = mint_badge("ax_2", 2)
    end_id = mint_badge("ax_3", 2)
    session.marks = {
        start_id: _mark(start_id, (0, 700, 1000, 740)),
        end_id: _mark(end_id, (0, 300, 1000, 340)),
    }

    with pytest.raises(ValueError, match="inverted or degenerate"):
        session.locate(
            "target", scope_start_mark_id=start_id, scope_end_mark_id=end_id
        )
    assert provider.requests == []


def test_failure_receipts_offer_hint_and_scope_recovery():
    no_match_session = _session(FakeGroundingProvider(failure_code="x"))
    no_match = {
        tool.name: tool for tool in build_perception_tools(no_match_session, _config())
    }["locate"].invoke({"description": "target"})
    assert "目标外观/可见文字/相对位置" in no_match
    assert "scope" in no_match

    ambiguous_session = _session(
        FakeGroundingProvider(bboxes=[[0, 0, 100, 100], [200, 200, 300, 300]])
    )
    ambiguous = {
        tool.name: tool
        for tool in build_perception_tools(ambiguous_session, _config())
    }["locate"].invoke({"description": "target"})
    assert "scope 收紧区域" in ambiguous
    assert "更独特的可见文字" in ambiguous


def test_marks_digest_labels_container_roles():
    container = _mark("ax_1@e2", (0, 0, 1000, 800), role="GridView")
    text = _mark("ax_2@e2", (0, 100, 1000, 140), role="TextView")

    digest = PhoneSession.format_marks_digest([container, text])

    assert "ax_1@e2 | [容器]GridView" in digest
    assert "ax_2@e2 | TextView" in digest


def test_tool_call_artifact_exposes_actual_provider_input_size():
    session = _session()
    tool = {
        item.name: item for item in build_perception_tools(session, _config())
    }["locate"]

    result = tool.invoke(
        {
            "name": "locate",
            "args": {"description": "target"},
            "id": "call-1",
            "type": "tool_call",
        }
    )

    assert result.artifact["provider_input_size_px"] == [1000, 2000]
    assert result.artifact["full_frame_size_px"] == [1000, 2000]


def test_trace_tool_result_records_locate_input_size(tmp_path):
    middleware = TraceMiddleware("locate-size", trace_dir=str(tmp_path))
    request = SimpleNamespace(
        tool_call={"name": "locate", "args": {"description": "target"}}
    )
    result = SimpleNamespace(
        content="已定位", artifact={"provider_input_size_px": [1000, 2000]}
    )

    middleware.wrap_tool_call(request, handler=lambda _request: result)

    logged = (tmp_path / "locate-size.jsonl").read_text(encoding="utf-8")
    assert '"provider_input_size_px": [1000, 2000]' in logged
