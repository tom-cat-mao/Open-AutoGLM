"""Wire-serialization contract for multimodal tool results (S1 §1, F1/F2).

Vision reflux returns tool results as a content ``list`` (text + image blocks).
These tests lock two facts against the installed langchain/langchain-openai so a
version bump that breaks them fails loudly:

- ``StructuredTool`` wraps a ``list[dict]`` return into a ``ToolMessage`` whose
  ``.content`` stays the list (a real tool_call id path), and returns the raw
  list on the plain-dict invoke path.
- ``_convert_message_to_dict`` serialises that ``ToolMessage`` for the
  chat/completions gateway preserving the ``image_url`` block (and the custom
  ``screen_seq`` key), only normalising text blocks.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from langchain_openai.chat_models.base import _convert_message_to_dict


def _multimodal_result() -> list[dict]:
    return [
        {"type": "text", "text": "OK. tap WLAN\n[OBS] app=x screen#3"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,QUJD"},
            "screen_seq": 3,
        },
    ]


def test_structuredtool_wraps_list_into_toolmessage():
    def read_screen() -> list[dict]:
        """Return multimodal content."""
        return _multimodal_result()

    tool = StructuredTool.from_function(read_screen, parse_docstring=False)

    # Plain-dict invoke (no tool_call_id) -> raw list content.
    raw = tool.invoke({})
    assert isinstance(raw, list)
    assert raw[1]["type"] == "image_url"

    # Real tool-call invoke -> ToolMessage whose content keeps the list.
    msg = tool.invoke({"type": "tool_call", "name": "read_screen", "args": {}, "id": "c1"})
    assert msg.__class__.__name__ == "ToolMessage"
    assert isinstance(msg.content, list)
    assert any(b.get("type") == "image_url" for b in msg.content)


def test_toolmessage_image_survives_gateway_serialization():
    from langchain_core.messages import ToolMessage

    msg = ToolMessage(content=_multimodal_result(), tool_call_id="c1")
    d = _convert_message_to_dict(msg)

    assert d["role"] == "tool"
    assert isinstance(d["content"], list)
    image_blocks = [b for b in d["content"] if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    # image_url payload transits untouched (URL preserved); custom key retained.
    assert image_blocks[0]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert image_blocks[0]["screen_seq"] == 3
