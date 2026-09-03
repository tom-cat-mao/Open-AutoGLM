"""Android accessibility tree marks from UiAutomator XML.

WP-G2a adds *windowed marks* — a pure display-layer upgrade. The parser no
longer flattens the tree with ``root.iter("node")``; it groups nodes by window
(a real ``<window>`` from a ``--windows`` dump, or an inferred window per
top-level hierarchy node), records a sparse semantic container path per node,
and labels each mark with a four-state actionability evidence tag.

WP-G2cA (parser-layer fixes, still pure display layer):
* A1 — the ``max_marks`` cut is a per-window fair-share quota (each window gets
  a guaranteed floor before the remainder is filled by window priority) so a
  top-layer popup is never starved by a content window that fills the budget
  first. ``parse_summary`` gains ``total_candidates`` (pre-truncation count) and
  ``per_window_counts``.
* A2 — the dedup key carries ``window_id`` so a popup and the background never
  merge on identical bbox/role/text.
* A3 — one DFS per window builds records *and* tallies dominant package/display
  id (the old triple traversal is gone); actionability is computed only for the
  marks that survive the quota.
* A4 — the dead ``disabled``/``invisible`` ``blocked`` branch is removed
  (those nodes are already dropped by ``_is_candidate_node``).

None of this touches addressing, tool execution, the safety gate, folding, or
locate: for a legacy ``<hierarchy>`` dump a single window keeps the historic
document order, so the mark id sequence, dedup, ``max_marks`` cut, and every
legacy field are byte-identical; the windowed metadata is purely additive.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
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
# Sparse semantic container path (WP-G2a §2): only these container kinds travel
# with a mark; nameless layout ancestors (FrameLayout/LinearLayout/…) are
# dropped so the path stays a short intent signal, not a DOM dump.
MAX_CONTAINER_PATH = 3


@dataclass(frozen=True)
class WindowRecord:
    """One observation-local window grouping a subtree of accessibility nodes.

    ``source_confidence`` distinguishes *strong* windows (real ``<window>``
    metadata from a ``uiautomator dump --windows`` — true ``layer``/``type``/
    ``title``) from *weak* windows inferred from a legacy ``<hierarchy>`` root
    (no true layer/type; ``layer``/``window_type`` stay ``None``). Only strong
    evidence may drive a ``blocked`` actionability label.
    """

    window_id: str
    display_id: int | None
    layer: int | None
    window_type: str | None
    title: str | None
    package: str | None
    bounds: tuple[int, int, int, int] | None
    active: bool
    focused: bool
    source_confidence: str
    root_index: int


@dataclass
class _NodeRecord:
    """A single accessibility node bound to its window + container context."""

    attrs: dict[str, str]
    role: str
    text: str
    raw_bounds: tuple[int, int, int, int] | None
    window: WindowRecord
    container_path: tuple[str, ...]
    depth: int



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
        return {"marks": [], "parse_summary": summary, "windows": []}
    root, xml_status = _parse_xml_root(xml_text)
    summary["xml_status"] = xml_status
    if root is None:
        return {"marks": [], "parse_summary": summary, "windows": []}

    windows, window_source = _extract_windows(root)
    summary["window_source"] = window_source
    summary["window_count"] = len(windows)

    # Single DFS per window (WP-G2cA A3): ``_collect_nodes`` now folds the
    # dominant-package / dominant-display counting into the one pre-order walk
    # that builds the node records, so the common single-window path costs ~1
    # traversal instead of the old 3 (collect + dominant_package +
    # dominant_display). Concatenating each window's pre-order DFS reproduces the
    # exact node sequence the legacy ``root.iter("node")`` produced, so the mark
    # ids / dedup / ``max_marks`` cut stay byte-identical for a legacy
    # ``<hierarchy>`` dump — only the per-node window/container/actionability
    # metadata is new.
    records: list[_NodeRecord] = []
    finalized: list[tuple[WindowRecord, ET.Element]] = []
    for base_window, root_elem in windows:
        window_records: list[_NodeRecord] = []
        pkg_counts: dict[str, int] = {}
        display_counts: dict[int, int] = {}
        _collect_nodes(root_elem, base_window, window_records, pkg_counts, display_counts)
        window = _finalize_window(base_window, root_elem, pkg_counts, display_counts)
        for rec in window_records:
            rec.window = window
        records.extend(window_records)
        finalized.append((window, root_elem))
    windows = finalized
    summary["raw_node_count"] = len(records)

    # Phase 1 — collect every dedup-passing candidate mark in document order,
    # tagging its window. ``total_candidates`` is this pre-truncation total
    # (parallel package B renders ``marks (80/124)`` from it — see summary keys).
    candidates: list[tuple[_NodeRecord, dict[str, Any]]] = []
    seen: set[tuple[int, int, int, int, str, str, str]] = set()
    per_window_counts: dict[str, int] = {}
    for rec in records:
        attrs = rec.attrs
        role = rec.role
        text = rec.text
        if _is_candidate_node(attrs, role=role, text=text):
            summary["interactive_candidate_count"] += 1
        raw_bounds = rec.raw_bounds
        if raw_bounds is None:
            summary["bounds_parse_fail_count"] += 1
            continue
        x1, y1, x2, y2 = raw_bounds
        if x2 <= x1 or y2 <= y1:
            summary["filtered_zero_area_count"] += 1
            continue
        parsed = _node_to_mark_from_parts(
            attrs,
            bounds=raw_bounds,
            role=role,
            text=text,
            index=0,  # placeholder; real ax_N is assigned after quota selection
            width=screen_width,
            height=screen_height,
            source=source,
            window=rec.window,
            container_path=rec.container_path,
            actionability=None,  # computed lazily for kept marks only (A3)
            actionability_reasons=(),
        )
        if parsed is None:
            continue
        # WP-G2cA A2: the dedup key carries ``window_id`` so a popup and the
        # background never merge just because they share bbox/role/text. A legacy
        # single window makes ``window_id`` a constant ("W1"), so dedup stays
        # byte-identical; a windowless caller yields "" (None-compatible).
        key = (
            int(parsed["bbox"][0]),
            int(parsed["bbox"][1]),
            int(parsed["bbox"][2]),
            int(parsed["bbox"][3]),
            str(parsed.get("role") or ""),
            str(parsed.get("text_summary") or ""),
            str(parsed.get("window_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append((rec, parsed))
        wid = str(parsed.get("window_id") or "")
        per_window_counts[wid] = per_window_counts.get(wid, 0) + 1

    summary["total_candidates"] = len(candidates)
    summary["per_window_counts"] = per_window_counts

    # Phase 2 — fair-share window quota (A1). Below budget every candidate is
    # kept, so a single window stays byte-identical; over budget each window is
    # guaranteed a floor before the remainder is filled by window priority.
    kept = _select_by_window_quota(candidates, max_marks)

    # Phase 3 — number in document order (byte-identical ax_N for legacy) and
    # compute actionability for the kept candidate marks only.
    marks: list[dict[str, Any]] = []
    for rec, parsed in kept:
        parsed["mark_id"] = f"ax_{len(marks) + 1}"
        actionability, reasons = _compute_actionability(rec, windows)
        parsed["actionability"] = actionability
        parsed["actionability_reasons"] = tuple(reasons)
        marks.append(parsed)
    summary["mark_count"] = len(marks)
    summary["actionability_counts"] = _actionability_counts(marks)
    return {
        "marks": marks,
        "parse_summary": summary,
        "windows": _window_structures(windows, marks),
    }


# Per-window guaranteed floor for the fair-share quota (A1): each window that
# has candidates is reserved at least ``min(_WINDOW_QUOTA_FLOOR, max_marks // n)``
# slots before the remaining budget is filled by window priority (layer desc,
# weak windows in document order). Keeps a top-layer popup from being starved of
# marks by a content window that fills ``max_marks`` first.
_WINDOW_QUOTA_FLOOR = 8


def _select_by_window_quota(
    candidates: list[tuple[_NodeRecord, dict[str, Any]]],
    max_marks: int,
) -> list[tuple[_NodeRecord, dict[str, Any]]]:
    """Pick ≤ ``max_marks`` candidates with a per-window floor, doc order kept.

    Below budget everything survives (single window ⇒ byte-identical). Over
    budget: bucket by window, reserve ``guarantee`` slots per window, then fill
    the remainder by window priority (higher layer first, weak windows by
    document/root order). The kept set is returned in original document order so
    ax_N numbering matches the legacy flat flatten for a single window.
    """

    if len(candidates) <= max_marks:
        return list(candidates)

    buckets: dict[str, list[int]] = {}
    window_by_id: dict[str, WindowRecord] = {}
    for pos, (rec, _parsed) in enumerate(candidates):
        win = rec.window
        buckets.setdefault(win.window_id, []).append(pos)
        window_by_id.setdefault(win.window_id, win)

    n = len(buckets)
    guarantee = min(_WINDOW_QUOTA_FLOOR, max_marks // n) if n else 0

    def _priority(window_id: str) -> tuple[bool, int, int]:
        win = window_by_id[window_id]
        # layer desc for real windows; weak (layer None) sort last by root order.
        return (win.layer is None, -(win.layer or 0), win.root_index)

    ordered = sorted(buckets, key=_priority)

    selected: set[int] = set()
    for window_id in ordered:
        for pos in buckets[window_id][:guarantee]:
            selected.add(pos)
    remaining = max_marks - len(selected)
    if remaining > 0:
        for window_id in ordered:
            for pos in buckets[window_id][guarantee:]:
                if remaining <= 0:
                    break
                if pos not in selected:
                    selected.add(pos)
                    remaining -= 1
            if remaining <= 0:
                break
    return [candidates[i] for i in sorted(selected)]



def _empty_parse_summary() -> dict[str, Any]:
    return {
        "xml_status": "ok",
        "raw_node_count": 0,
        "mark_count": 0,
        "bounds_parse_fail_count": 0,
        "filtered_zero_area_count": 0,
        "interactive_candidate_count": 0,
        # WP-G2a windowed-marks diagnostics (trace-safe counts only).
        "window_source": "none",
        "window_count": 0,
        "actionability_counts": {},
        # WP-G2cA A1: candidate accounting for the fair-share window quota.
        # ``total_candidates`` is the dedup-passing candidate count *before* the
        # ``max_marks`` truncation (parallel package B renders ``marks (80/124)``
        # from ``mark_count``/``total_candidates`` — do not rename these keys).
        # ``per_window_counts`` maps window_id -> candidate count (pre-truncation).
        "total_candidates": 0,
        "per_window_counts": {},
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
        window_structures = parsed.get("windows") or []
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
                window_id=item.get("window_id"),
                window_layer=item.get("window_layer"),
                window_type=item.get("window_type"),
                window_title=item.get("window_title"),
                package=item.get("package"),
                container_path=tuple(item.get("container_path") or ()),
                actionability=item.get("actionability"),
                actionability_reasons=tuple(item.get("actionability_reasons") or ()),
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
                screen_structures=window_structures,
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
            screen_structures=window_structures,
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
    window: WindowRecord | None = None,
    container_path: tuple[str, ...] = (),
    actionability: str | None = None,
    actionability_reasons: tuple[str, ...] = (),
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
    package = (attrs.get("package") or "").strip() or None
    mark: dict[str, Any] = {
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
    # WP-G2a windowed metadata (additive; None/() for legacy callers).
    if window is not None:
        mark["window_id"] = window.window_id
        mark["window_layer"] = window.layer
        mark["window_type"] = window.window_type
        mark["window_title"] = window.title
        mark["package"] = package or window.package
    else:
        mark["package"] = package
    mark["container_path"] = tuple(container_path)
    mark["actionability"] = actionability
    mark["actionability_reasons"] = tuple(actionability_reasons)
    return mark



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


# ---------------------------------------------------------------------------
# WP-G2a windowed parsing (采集→解析) — pure display layer, additive only.
# ---------------------------------------------------------------------------

# Container kinds we surface in ``container_path`` (§2). Everything else (bare
# FrameLayout/LinearLayout/ViewGroup/…) is a traversal-only ancestor.
_CONTAINER_KIND_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # kind, class-suffix substrings (casefold), resource-id/text substrings
    ("dialog", ("dialog", "alertdialog", "popupwindow"), ("dialog", "alert", "popup", "permission")),
    ("toolbar", ("toolbar", "actionbar"), ("toolbar", "appbar", "action_bar", "app_bar")),
    ("tab", ("tablayout", "tabwidget", "radiogroup", "bottomnavigation"), ("tablayout", "tab_layout", "navigation", "tabbar")),
    ("list", ("recyclerview", "listview", "abslistview", "scrollview", "nestedscrollview", "viewpager"), ()),
    ("grid", ("gridview", "gridlayout"), ()),
    ("form", (), ()),  # form is inferred structurally, not by class name
)
_CONTAINER_KIND_ORDER = ("dialog", "toolbar", "tab", "list", "grid", "form")


def _extract_windows(
    root: ET.Element,
) -> tuple[list[tuple[WindowRecord, ET.Element]], str]:
    """Return ``[(WindowRecord, hierarchy_root_elem), …]`` and a source tag.

    ``<displays>`` (a ``uiautomator dump --windows`` root) yields *strong*
    windows carrying real ``layer``/``type``/``title``/``bounds``. A legacy
    ``<hierarchy>`` root yields one *weak* inferred window per top-level ``node``
    (single root → the historic single-window behaviour; multiple roots → one
    inferred window each). Any other root is treated as a single weak window.
    """

    tag = _localname(root.tag)
    if tag == "displays":
        windows = _windows_from_displays(root)
        if windows:
            return windows, "shell_windows"
        # A ``<displays>`` shell with no parseable window falls through to weak.
    if tag == "hierarchy":
        return _windows_from_hierarchy(root), "hierarchy"
    # Unknown root: wrap the whole tree as one weak window so parsing still works.
    return (
        [(_inferred_window("W1", root, root_index=0), root)],
        "unknown",
    )


def _windows_from_displays(
    root: ET.Element,
) -> list[tuple[WindowRecord, ET.Element]]:
    windows: list[tuple[WindowRecord, ET.Element]] = []
    idx = 0
    for display in root.iter("display"):
        display_id = _int_or_none(display.get("id"))
        for win in display.findall("window"):
            idx += 1
            hierarchy = win.find("hierarchy")
            root_elem = hierarchy if hierarchy is not None else win
            record = WindowRecord(
                window_id=f"W{idx}",
                display_id=display_id,
                layer=_int_or_none(win.get("layer")),
                window_type=(win.get("type") or None),
                title=(win.get("title") or None),
                # package resolved in _finalize_window from the single DFS (A3).
                package=None,
                bounds=_parse_bounds(win.get("bounds") or ""),
                active=win.get("active") == "true",
                focused=win.get("focused") == "true",
                source_confidence="strong",
                root_index=idx - 1,
            )
            windows.append((record, root_elem))
    return windows


def _windows_from_hierarchy(
    root: ET.Element,
) -> list[tuple[WindowRecord, ET.Element]]:
    top_nodes = list(root.findall("node"))
    if len(top_nodes) <= 1:
        # Single (or empty) root: preserve the historic single-window behaviour.
        # DFS still starts at the hierarchy root so a lone top node is included.
        return [(_inferred_window("W1", root, root_index=0), root)]
    windows: list[tuple[WindowRecord, ET.Element]] = []
    for i, node in enumerate(top_nodes):
        windows.append((_inferred_window(f"W{i + 1}", node, root_index=i), node))
    return windows


def _inferred_window(
    window_id: str, elem: ET.Element, *, root_index: int
) -> WindowRecord:
    """Build a weak (heuristic) window from a hierarchy subtree.

    No real ``layer``/``type`` is available, so those stay ``None`` — that is
    what keeps heuristic windows out of the ``blocked`` actionability tier.
    """

    bounds = None
    if _localname(elem.tag) == "node":
        bounds = _parse_bounds(elem.get("bounds") or "")
    return WindowRecord(
        window_id=window_id,
        # display_id / package resolved in _finalize_window from the single DFS (A3).
        display_id=None,
        layer=None,
        window_type=None,
        title=None,
        package=None,
        bounds=bounds,
        active=elem.get("focused") == "true",
        focused=elem.get("focused") == "true",
        source_confidence="weak",
        root_index=root_index,
    )


def _collect_nodes(
    root_elem: ET.Element,
    window: WindowRecord,
    out: list[_NodeRecord],
    pkg_counts: dict[str, int],
    display_counts: dict[int, int],
) -> None:
    """Pre-order DFS over ``<node>`` descendants, carrying a container path.

    Recursing pre-order reproduces ``root.iter("node")`` ordering while letting
    us track depth and the sparse semantic container ancestry. When ``root_elem``
    is itself a ``<node>`` (an inferred window built from a top-level hierarchy
    node) that node is included too, matching the legacy flat traversal.

    WP-G2cA A3: this one walk also tallies ``pkg_counts`` / ``display_counts``
    for the window (dominant package / display id), replacing the two extra
    ``elem.iter("node")`` passes ``_dominant_package`` / ``_dominant_display_id``
    used to make.
    """

    def emit(elem: ET.Element, path: tuple[str, ...], depth: int) -> None:
        attrs = dict(elem.attrib)
        role = _role_from_class(attrs.get("class") or "")
        text = _node_text(attrs)
        pkg = (attrs.get("package") or "").strip()
        if pkg:
            pkg_counts[pkg] = pkg_counts.get(pkg, 0) + 1
        did = _int_or_none(attrs.get("display-id"))
        if did is not None:
            display_counts[did] = display_counts.get(did, 0) + 1
        out.append(
            _NodeRecord(
                attrs=attrs,
                role=role,
                text=text,
                raw_bounds=_parse_bounds(attrs.get("bounds") or ""),
                window=window,
                container_path=path,
                depth=depth,
            )
        )
        kind = _container_kind(attrs, role)
        child_path = path
        if kind and (not path or path[-1] != kind):
            child_path = (path + (kind,))[-MAX_CONTAINER_PATH:]
        for child in list(elem):
            if _localname(child.tag) == "node":
                emit(child, child_path, depth + 1)

    if _localname(root_elem.tag) == "node":
        emit(root_elem, (), 0)
        return
    for child in list(root_elem):
        if _localname(child.tag) == "node":
            emit(child, (), 0)


def _finalize_window(
    base: WindowRecord,
    root_elem: ET.Element,
    pkg_counts: dict[str, int],
    display_counts: dict[int, int],
) -> WindowRecord:
    """Fill the dominant package / display id gathered during the single DFS.

    ``_windows_from_displays`` / ``_inferred_window`` build a ``WindowRecord``
    with ``package``/``display_id`` left unresolved; A3 resolves them here from
    the tallies collected while walking the subtree, matching the old
    ``_dominant_*`` results (including the empty-tree fallback to the root
    element's own attribute).
    """

    package = _dominant_from_counts(pkg_counts)
    if package is None:
        package = (root_elem.get("package") or "").strip() or None
    # Strong windows already carry the authoritative ``display_id`` from the
    # ``<display id=…>`` attribute; only weak (inferred) windows derive it from
    # the node tally (matching the old ``_dominant_display_id``).
    if base.source_confidence == "strong":
        display_id = base.display_id
    else:
        display_id = _dominant_from_counts(display_counts)
    if base.package == package and base.display_id == display_id:
        return base
    return replace(base, package=package, display_id=display_id)


def _dominant_from_counts(counts: dict[Any, int]) -> Any:
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]



def _container_kind(attrs: dict[str, str], role: str) -> str | None:
    """Classify a node into a semantic container kind, or ``None``.

    Nameless layout containers return ``None`` so they never enter the path.
    """

    role_cf = role.casefold()
    resource_id = (attrs.get("resource-id") or "").casefold()
    text = (attrs.get("text") or attrs.get("content-desc") or "").casefold()
    for kind, class_suffixes, id_substrings in _CONTAINER_KIND_RULES:
        if any(role_cf.endswith(suffix) for suffix in class_suffixes):
            return kind
        if id_substrings and any(sub in resource_id or sub in text for sub in id_substrings):
            return kind
    # A scrollable container with an unrecognized class still reads as a list.
    if attrs.get("scrollable") == "true":
        return "list"
    return None


def _compute_actionability(
    rec: _NodeRecord, windows: list[tuple[WindowRecord, ET.Element]]
) -> tuple[str, tuple[str, ...]]:
    """Four-state evidence label for one node (WP-G2a §2, WP-G2cA A4).

    Only ever runs on nodes that already passed ``_is_candidate_node``, so a
    ``disabled`` / ``visible-to-user=false`` node can never reach here — the old
    ``blocked`` branch for those was dead code and is removed (A4). The three
    reachable outcomes:

    * ``blocked`` — real evidence only: a *strong* window with a higher layer
      covers this node's center. Heuristic (weak) windows never emit ``blocked``.
    * ``confirmed`` — a strong window's node whose center is not covered by any
      higher strong window.
    * ``likely`` — the default for a plain accessibility candidate.
    * ``unknown`` — a weak-window node that a higher inferred window seems to
      cover (occlusion is a hint only, never a hard block).
    """

    reasons: list[str] = []
    win = rec.window
    center = _bounds_center(rec.raw_bounds)
    strong = win.source_confidence == "strong"

    if strong and center is not None:
        covering = _higher_strong_cover(win, center, windows)
        if covering is not None:
            return "blocked", (f"covered_by:{covering}",)
        reasons.append("strong_window")
        reasons.append("enabled")
        return "confirmed", tuple(reasons)

    # Weak window: geometry is a hint only. A higher inferred window that covers
    # this center downgrades to unknown but never blocks.
    if center is not None:
        covering = _higher_weak_cover(win, center, windows)
        if covering is not None:
            return "unknown", (f"maybe_covered_by:{covering}",)
    return "likely", ("heuristic",)


def _higher_strong_cover(
    win: WindowRecord,
    center: tuple[float, float],
    windows: list[tuple[WindowRecord, ET.Element]],
) -> str | None:
    win_layer = win.layer if win.layer is not None else -1
    for other, _elem in windows:
        if other.window_id == win.window_id or other.source_confidence != "strong":
            continue
        other_layer = other.layer if other.layer is not None else -1
        if other_layer <= win_layer:
            continue
        if other.bounds and _point_in_bounds(center, other.bounds):
            return other.window_id
    return None


def _higher_weak_cover(
    win: WindowRecord,
    center: tuple[float, float],
    windows: list[tuple[WindowRecord, ET.Element]],
) -> str | None:
    # For inferred windows, later root order is treated as visually-higher.
    for other, _elem in windows:
        if other.window_id == win.window_id:
            continue
        if other.root_index <= win.root_index:
            continue
        if other.bounds and _point_in_bounds(center, other.bounds):
            return other.window_id
    return None


def _window_structures(
    windows: list[tuple[WindowRecord, ET.Element]],
    marks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Trace-safe window sidecar for ``MarkProviderResult.screen_structures``."""

    counts: dict[str, int] = {}
    for mark in marks:
        wid = mark.get("window_id")
        if wid:
            counts[wid] = counts.get(wid, 0) + 1
    structures: list[dict[str, Any]] = []
    for win, _elem in windows:
        structures.append(
            {
                "window_id": win.window_id,
                "display_id": win.display_id,
                "layer": win.layer,
                "window_type": win.window_type,
                "title": win.title,
                "package": win.package,
                "active": win.active,
                "focused": win.focused,
                "source_confidence": win.source_confidence,
                "mark_count": counts.get(win.window_id, 0),
            }
        )
    return structures


def _actionability_counts(marks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for mark in marks:
        tier = mark.get("actionability")
        if tier:
            counts[tier] = counts.get(tier, 0) + 1
    return counts


def _localname(tag: Any) -> str:
    """Strip any XML namespace so ``{ns}window`` matches ``window``."""

    text = str(tag or "")
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bounds_center(
    bounds: tuple[int, int, int, int] | None
) -> tuple[float, float] | None:
    if not bounds:
        return None
    x1, y1, x2, y2 = bounds
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _point_in_bounds(
    point: tuple[float, float], bounds: tuple[int, int, int, int]
) -> bool:
    x, y = point
    x1, y1, x2, y2 = bounds
    return x1 <= x <= x2 and y1 <= y <= y2

