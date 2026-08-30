"""Android accessibility tree marks from UiAutomator XML."""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, MarkProviderResult, ScreenBinding


BOUNDS_RE = re.compile(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]")
INVALID_XML_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
INTERACTIVE_CLASSES = (
    "Button",
    "CheckBox",
    "CheckedTextView",
    "EditText",
    "ImageButton",
    "RadioButton",
    "SeekBar",
    "Spinner",
    "Switch",
    "TextView",
)
MAX_TEXT_SUMMARY_CHARS = 120


def parse_uiautomator_marks(
    xml_text: str,
    *,
    screen_width: int,
    screen_height: int,
    source: str = "uiautomator",
    max_marks: int = 80,
) -> list[dict[str, Any]]:
    """Parse UiAutomator XML into normalized 0-1000 mark dicts."""

    parsed = _parse_uiautomator_xml(
        xml_text,
        screen_width=screen_width,
        screen_height=screen_height,
        source=source,
        max_marks=max_marks,
    )
    return parsed["marks"]


def parse_uiautomator_summary(
    xml_text: str,
    *,
    screen_width: int,
    screen_height: int,
    max_marks: int = 80,
) -> dict[str, Any]:
    """Return trace-safe UiAutomator parser diagnostics."""

    parsed = _parse_uiautomator_xml(
        xml_text,
        screen_width=screen_width,
        screen_height=screen_height,
        max_marks=max_marks,
    )
    return parsed["parse_summary"]


def _parse_uiautomator_xml(
    xml_text: str,
    *,
    screen_width: int,
    screen_height: int,
    source: str = "uiautomator",
    max_marks: int = 80,
) -> dict[str, Any]:
    summary = _empty_parse_summary()
    if screen_width <= 0 or screen_height <= 0 or not str(xml_text or "").strip():
        summary["xml_status"] = "accessibility_dump_empty"
        return {"marks": [], "parse_summary": summary}
    root, xml_status = _parse_xml_root(xml_text)
    summary["xml_status"] = xml_status
    if root is None:
        return {"marks": [], "parse_summary": summary}

    nodes = list(root.iter("node"))
    summary["raw_node_count"] = len(nodes)
    marks: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int, str, str]] = set()
    for node in nodes:
        attrs = node.attrib
        role = _role_from_class(attrs.get("class") or "")
        text = _node_text(attrs)
        if _is_candidate_node(attrs, role=role, text=text):
            summary["interactive_candidate_count"] += 1
        raw_bounds = _parse_bounds(attrs.get("bounds") or "")
        if raw_bounds is None:
            summary["bounds_parse_fail_count"] += 1
            continue
        x1, y1, x2, y2 = raw_bounds
        if x2 <= x1 or y2 <= y1:
            summary["filtered_zero_area_count"] += 1
            continue
        if len(marks) >= max_marks:
            continue
        parsed = _node_to_mark_from_parts(
            attrs,
            bounds=raw_bounds,
            role=role,
            text=text,
            index=len(marks) + 1,
            width=screen_width,
            height=screen_height,
            source=source,
        )
        if parsed is None:
            continue
        key = (
            int(parsed["bbox"][0]),
            int(parsed["bbox"][1]),
            int(parsed["bbox"][2]),
            int(parsed["bbox"][3]),
            str(parsed.get("role") or ""),
            str(parsed.get("text_summary") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        marks.append(parsed)
    summary["mark_count"] = len(marks)
    return {"marks": marks, "parse_summary": summary}


def _empty_parse_summary() -> dict[str, Any]:
    return {
        "xml_status": "ok",
        "raw_node_count": 0,
        "mark_count": 0,
        "bounds_parse_fail_count": 0,
        "filtered_zero_area_count": 0,
        "interactive_candidate_count": 0,
    }


def _parse_xml_root(xml_text: str) -> tuple[ET.Element | None, str]:
    cleaned = INVALID_XML_CONTROL_RE.sub("", str(xml_text or "").lstrip("\ufeff"))
    if not cleaned.strip():
        return None, "accessibility_dump_empty"
    try:
        return ET.fromstring(cleaned), "ok"
    except ET.ParseError:
        return None, "accessibility_xml_parse_error"


def visible_text_summary(xml_text: str, *, max_items: int = 20, max_chars: int = 300) -> str:
    """Extract a short visible-text summary for optional provider context."""

    if not xml_text.strip():
        return ""
    try:
        root, _status = _parse_xml_root(xml_text)
    except ET.ParseError:
        return ""
    if root is None:
        return ""
    values: list[str] = []
    seen: set[str] = set()
    for node in root.iter("node"):
        text = _node_text(node.attrib)
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
        if len(values) >= max_items:
            break
    return ", ".join(values)[:max_chars]


class AccessibilityTreeProvider:
    """MarkProvider backed by an Android UiAutomator XML dump callback."""

    name = "accessibility_tree"
    version = "uiautomator"
    allow_raw_hints = False

    def __init__(self, dump_tree: Any, *, max_marks: int = 80) -> None:
        if max_marks <= 0:
            raise ValueError("Accessibility max_marks must be positive")
        self._dump_tree = dump_tree
        self.max_marks = max_marks

    def provide_marks(
        self,
        screenshot: Any,
        screen_binding: ScreenBinding,
        hints: list[MarkProviderHint] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
    ) -> MarkProviderResult:
        started = time.perf_counter()
        try:
            xml_text = str(self._dump_tree(timeout=timeout) or "")
        except TimeoutError:
            return self._failure("timeout", screen_binding, started)
        except Exception as exc:
            return self._failure("provider_error", screen_binding, started, message=type(exc).__name__)
        provider_input_hash = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()[:16]
        parsed = _parse_uiautomator_xml(
            xml_text,
            screen_width=screen_binding.width,
            screen_height=screen_binding.height,
            source=self.name,
            max_marks=self.max_marks,
        )
        mark_dicts = parsed["marks"]
        parse_summary = parsed["parse_summary"]
        marks = [
            MarkCandidate(
                mark_id=str(item["mark_id"]),
                bbox=list(item["bbox"]),
                center=list(item["center"]),
                confidence=float(item.get("confidence", 1.0)),
                source=self.name,
                valid=True,
                role=item.get("role"),
                text_summary=item.get("text_summary"),
                password=bool(item.get("password", False)),
                editable=bool(item.get("editable", False)),
            )
            for item in mark_dicts
        ]
        if not marks:
            failure_code = _accessibility_failure_code(parse_summary)
            return MarkProviderResult(
                success=False,
                provider=self.name,
                failure_code=failure_code,
                message="no accessible marks",
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=provider_input_hash,
                latency_ms=self._latency_ms(started),
                marks=[],
                candidates=[],
                candidate_count=0,
                status=failure_code,
                hints=[hint.redacted_summary() for hint in hints or []],
                metadata={"max_marks": self.max_marks, "parse_summary": parse_summary},
            )
        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=provider_input_hash,
            latency_ms=self._latency_ms(started),
            marks=marks,
            candidates=marks,
            candidate_count=len(marks),
            status="success",
            hints=[hint.redacted_summary() for hint in hints or []],
            metadata={"max_marks": self.max_marks, "parse_summary": parse_summary},
        )

    def _failure(
        self,
        code: str,
        screen_binding: ScreenBinding,
        started: float,
        *,
        message: str | None = None,
    ) -> MarkProviderResult:
        return MarkProviderResult(
            success=False,
            provider=self.name,
            failure_code=code,
            message=message or code,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            latency_ms=self._latency_ms(started),
            marks=[],
            candidates=[],
            candidate_count=0,
            status=code,
            metadata={"parse_summary": {**_empty_parse_summary(), "xml_status": code}},
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


def _node_to_mark_from_parts(
    attrs: dict[str, str],
    *,
    bounds: tuple[int, int, int, int],
    role: str,
    text: str,
    index: int,
    width: int,
    height: int,
    source: str,
) -> dict[str, Any] | None:
    x1, y1, x2, y2 = bounds
    if x2 <= x1 or y2 <= y1:
        return None
    if not _is_candidate_node(attrs, role=role, text=text):
        return None
    bbox = [
        _to_relative(x1, width),
        _to_relative(y1, height),
        _to_relative(x2, width),
        _to_relative(y2, height),
    ]
    center = [int(round((bbox[0] + bbox[2]) / 2)), int(round((bbox[1] + bbox[3]) / 2))]
    confidence = 1.0 if attrs.get("clickable") == "true" or attrs.get("focusable") == "true" else 0.8
    password = attrs.get("password") == "true"
    # editable = Android text-input node: an EditText subclass, an explicitly editable
    # node, or a password field (password fields are always text inputs).
    editable = role.endswith("EditText") or attrs.get("editable") == "true" or password
    return {
        "mark_id": f"ax_{index}",
        "bbox": bbox,
        "center": center,
        "source": source,
        "confidence": confidence,
        "role": role,
        # A text-less node has no text summary. Falling back to `role` here put the
        # Java class name ("FrameLayout") into a field every consumer reads as
        # on-screen text, which made containment checks against the screen text blob
        # tautological. `role` travels as its own field, so type info is not lost.
        "text_summary": None if password else text[:MAX_TEXT_SUMMARY_CHARS] or None,
        "password": password,
        "editable": editable,
    }


def _parse_bounds(value: str) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _to_relative(value: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    return max(0, min(1000, int(round((value / maximum) * 1000))))


def _node_text(attrs: dict[str, str]) -> str:
    if attrs.get("password") == "true":
        return ""
    values = [
        attrs.get("text") or "",
        attrs.get("content-desc") or "",
    ]
    parts: list[str] = []
    for value in values:
        cleaned = " ".join(str(value).split())
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return " | ".join(parts)


def _role_from_class(class_name: str) -> str:
    role = class_name.rsplit(".", 1)[-1] if class_name else "node"
    return role[:32] or "node"


def _is_candidate_node(attrs: dict[str, str], *, role: str, text: str) -> bool:
    if attrs.get("enabled") == "false" or attrs.get("visible-to-user") == "false":
        return False
    if attrs.get("clickable") == "true" or attrs.get("focusable") == "true" or attrs.get("checkable") == "true":
        return True
    if attrs.get("long-clickable") == "true" or attrs.get("scrollable") == "true":
        return True
    if text and any(role.endswith(class_suffix) for class_suffix in INTERACTIVE_CLASSES):
        return True
    return False


def _accessibility_failure_code(parse_summary: dict[str, Any]) -> str:
    xml_status = str(parse_summary.get("xml_status") or "")
    if xml_status in {"accessibility_dump_empty", "accessibility_xml_parse_error"}:
        return xml_status
    return "accessibility_no_interactive_marks"
