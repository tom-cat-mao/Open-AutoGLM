"""Structured screen sidecars and observation-local object registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.graph.marks import Mark, MarkRegistry, build_mark_topology_digest


OBJECT_FAILURE_CODES = {
    "object_registry_missing",
    "object_stale",
    "unknown_object",
    "ordinal_out_of_range",
    "object_ambiguous",
    "object_without_mark",
    "mark_stale",
    "mark_low_confidence",
}
MAX_OBJECT_EVIDENCE_CHARS = 120
MAX_PROMPT_OBJECTS = 30
MAX_PROMPT_LISTS = 5
MAX_OBJECTS_BLOCK_CHARS = 4000


@dataclass(frozen=True)
class StructureNode:
    """Trace-safe normalized accessibility node."""

    node_id: str
    path: str
    parent_id: str | None
    child_ids: list[str] = field(default_factory=list)
    depth: int = 0
    bounds: tuple[int, int, int, int] | None = None
    role: str | None = None
    class_name: str | None = None
    resource_id_hash: str | None = None
    text_summary: str | None = None
    content_desc_summary: str | None = None
    clickable: bool = False
    focusable: bool = False
    focused: bool = False
    checkable: bool = False
    checked: bool = False
    scrollable: bool = False
    enabled: bool = True
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.bounds is not None:
            data["bounds"] = list(self.bounds)
        return data


@dataclass(frozen=True)
class ScreenStructure:
    """Observation-local screen topology sidecar."""

    screen_id: str
    semantic_screen_id: str | None = None
    mark_set_version: str | None = None
    topology_digest: str | None = None
    status: str = "ok"
    nodes: dict[str, StructureNode] = field(default_factory=dict)
    root_node_id: str | None = None

    def with_binding(
        self,
        *,
        screen_id: str,
        semantic_screen_id: str | None,
        mark_set_version: str | None,
    ) -> "ScreenStructure":
        return ScreenStructure(
            screen_id=screen_id,
            semantic_screen_id=semantic_screen_id,
            mark_set_version=mark_set_version,
            topology_digest=self.topology_digest or build_structure_topology_digest(self.nodes),
            status=self.status,
            nodes=self.nodes,
            root_node_id=self.root_node_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "mark_set_version": self.mark_set_version,
            "topology_digest": self.topology_digest or build_structure_topology_digest(self.nodes),
            "status": self.status,
            "node_count": len(self.nodes),
            "root_node_id": self.root_node_id,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
        }

    def trace_summary(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "mark_set_version": self.mark_set_version,
            "topology_digest": self.topology_digest or build_structure_topology_digest(self.nodes),
            "status": self.status,
            "node_count": len(self.nodes),
        }


@dataclass(frozen=True)
class ScreenObject:
    """An observation-local semantic object compiled from structure plus marks."""

    object_id: str
    object_type: str
    atomic_mark_ids: list[str]
    primary_mark_id: str | None
    container_node_id: str | None = None
    parent_object_id: str | None = None
    list_id: str | None = None
    ordinal_index: int | None = None
    confidence: float = 1.0
    role: str | None = None
    source: str = "accessibility"
    evidence_summary: str | None = None
    sensitivity_evidence_summary: str | None = None
    title_hash: str | None = None
    text_hash: str | None = None
    resource_id_hash: str | None = None
    lineage_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_line(self) -> str:
        ordinal = "" if self.ordinal_index is None else f" ordinal={self.ordinal_index}"
        list_part = "" if not self.list_id else f" list={self.list_id}"
        return (
            f"- {self.object_id}: type={self.object_type} role={self.role or 'unknown'}"
            f"{list_part}{ordinal} primary_mark_id={self.primary_mark_id}"
            f" confidence={round(self.confidence, 3)}"
            f" title_hash={self.title_hash} text_hash={self.text_hash}"
            f" lineage_hash={self.lineage_hash}"
        )


@dataclass(frozen=True)
class ObjectRegistry:
    """Observation-local object lookup table."""

    screen_id: str
    objects: dict[str, ScreenObject] = field(default_factory=dict)
    semantic_screen_id: str | None = None
    object_set_version: str | None = None
    structure_topology_digest: str | None = None
    mark_set_version: str | None = None
    status: str = "ok"
    truncation_summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ObjectRegistry | None":
        if not isinstance(value, dict) or not value.get("screen_id"):
            return None
        objects: dict[str, ScreenObject] = {}
        raw_objects = value.get("objects") or {}
        iterable = raw_objects.values() if isinstance(raw_objects, dict) else raw_objects
        for item in iterable or []:
            if not isinstance(item, dict):
                continue
            obj = _coerce_object(item)
            if obj is not None:
                objects[obj.object_id] = obj
        return cls(
            screen_id=str(value["screen_id"]),
            objects=objects,
            semantic_screen_id=value.get("semantic_screen_id"),
            object_set_version=value.get("object_set_version") or build_object_set_version(objects),
            structure_topology_digest=value.get("structure_topology_digest"),
            mark_set_version=value.get("mark_set_version"),
            status=str(value.get("status") or "ok"),
            truncation_summary=dict(value.get("truncation_summary") or {}),
        )

    def with_binding(
        self,
        *,
        screen_id: str,
        semantic_screen_id: str | None,
        mark_set_version: str | None,
        structure_topology_digest: str | None,
    ) -> "ObjectRegistry":
        return ObjectRegistry(
            screen_id=screen_id,
            objects=self.objects,
            semantic_screen_id=semantic_screen_id,
            object_set_version=self.object_set_version or build_object_set_version(self.objects),
            structure_topology_digest=structure_topology_digest or self.structure_topology_digest,
            mark_set_version=mark_set_version,
            status=self.status,
            truncation_summary=self.truncation_summary,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "object_set_version": self.object_set_version or build_object_set_version(self.objects),
            "structure_topology_digest": self.structure_topology_digest,
            "mark_set_version": self.mark_set_version,
            "status": self.status,
            "object_count": len(self.objects),
            "truncation_summary": self.truncation_summary,
            "objects": {object_id: obj.to_dict() for object_id, obj in self.objects.items()},
        }

    def trace_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for obj in self.objects.values():
            counts[obj.object_type] = counts.get(obj.object_type, 0) + 1
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "object_set_version": self.object_set_version or build_object_set_version(self.objects),
            "structure_topology_digest": self.structure_topology_digest,
            "mark_set_version": self.mark_set_version,
            "status": self.status,
            "object_count": len(self.objects),
            "object_type_counts": counts,
            "truncation_summary": self.truncation_summary,
        }

    def get(self, object_id: str) -> ScreenObject | None:
        return self.objects.get(str(object_id))

    def prompt_block(self, *, mark_registry: MarkRegistry | None = None, lang: str = "cn") -> str:
        if not self.objects:
            return ""
        title = "** Screen Objects (observation-local; compile to target_mark_id before execution) **"
        limits = (
            f"limits: lists<={MAX_PROMPT_LISTS}, objects<={MAX_PROMPT_OBJECTS}, "
            f"object_evidence<={MAX_OBJECT_EVIDENCE_CHARS} chars, block<={MAX_OBJECTS_BLOCK_CHARS} chars"
        )
        rules = (
            "rules: object_id/list_id/ordinal are valid only for this observation; "
            "use target_object_id or object_role+ordinal/object_filter only when a unique object is visible; "
            "target_text_hint is only a provider hint and is not executable."
        )
        if lang != "en":
            title = "** 屏幕对象（仅当前 observation 有效；执行前必须编译为 target_mark_id） **"
            limits = (
                f"limits: lists<={MAX_PROMPT_LISTS}, objects<={MAX_PROMPT_OBJECTS}, "
                f"object_evidence<={MAX_OBJECT_EVIDENCE_CHARS} chars, block<={MAX_OBJECTS_BLOCK_CHARS} chars"
            )
            rules = (
                "规则：object_id/list_id/ordinal 只在当前 observation 有效；"
                "只有唯一可见对象时才使用 target_object_id 或 object_role+ordinal/object_filter；"
                "target_text_hint 只是 provider hint，不是可执行目标。"
            )
        list_ids = _top_list_ids(self.objects.values())
        rows = [title, f"screen={self.screen_id} topology={self.structure_topology_digest}", limits]
        if list_ids:
            rows.append("lists: " + ", ".join(list_ids[:MAX_PROMPT_LISTS]))
        selected = _prompt_objects(self.objects.values(), mark_registry=mark_registry)
        rows.extend(obj.prompt_line() for obj in selected[:MAX_PROMPT_OBJECTS])
        if len(selected) > MAX_PROMPT_OBJECTS:
            rows.append(f"truncated_objects={len(selected) - MAX_PROMPT_OBJECTS}")
        rows.append(rules)
        block = "\n".join(rows)
        if len(block) <= MAX_OBJECTS_BLOCK_CHARS:
            return block
        return block[: MAX_OBJECTS_BLOCK_CHARS - 80] + "\ntruncated_block=true"


def build_structure_topology_digest(nodes: dict[str, StructureNode]) -> str:
    rows = []
    for node in nodes.values():
        rows.append(
            f"{node.path}:{node.parent_id or ''}:{node.bounds}:{node.role or ''}:"
            f"{int(node.clickable)}{int(node.focusable)}{int(node.scrollable)}"
        )
    return hashlib.sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def build_object_set_version(objects: dict[str, ScreenObject]) -> str:
    rows = []
    for obj in objects.values():
        rows.append(
            f"{obj.object_id}:{obj.object_type}:{obj.primary_mark_id}:"
            f"{obj.list_id or ''}:{obj.ordinal_index or ''}:{obj.lineage_hash or ''}"
        )
    return hashlib.sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def build_object_registry(
    *,
    screen_id: str,
    structure: ScreenStructure | None,
    mark_registry: MarkRegistry,
    source: str = "accessibility",
) -> ObjectRegistry:
    """Build executable screen objects from current structure and atomic marks."""

    objects: dict[str, ScreenObject] = {}
    if structure is None or not structure.nodes or not mark_registry.marks:
        return ObjectRegistry(
            screen_id=screen_id,
            semantic_screen_id=mark_registry.semantic_screen_id,
            mark_set_version=mark_registry.mark_set_version,
            structure_topology_digest=structure.topology_digest if structure else None,
            status="missing_sidecar",
        )

    parent_to_marks = _map_marks_to_structure_nodes(structure, mark_registry)
    scrollable_nodes = [node for node in structure.nodes.values() if node.scrollable and node.visible]
    scrollable_nodes.sort(key=lambda node: (node.bounds or (0, 0, 0, 0))[1])
    list_by_node = {
        node.node_id: f"list_{index}"
        for index, node in enumerate(scrollable_nodes[:MAX_PROMPT_LISTS], start=1)
    }
    ordinal_by_list: dict[str, int] = {}
    for mark in mark_registry.marks.values():
        node = _find_node_for_mark(mark, structure, parent_to_marks)
        if node is None:
            continue
        object_type = _object_type_for(node, mark)
        if node.scrollable and object_type == "control":
            continue
        list_node_id = _nearest_scrollable_ancestor(node, structure, list_by_node)
        list_id = list_by_node.get(list_node_id) if list_node_id else None
        ordinal_index = None
        if list_id and object_type in {"card", "result", "video", "button"}:
            ordinal_by_list[list_id] = ordinal_by_list.get(list_id, 0) + 1
            ordinal_index = ordinal_by_list[list_id]
        lineage_hash = _hash_text(f"{node.path}|{list_id or ''}|{object_type}")[:12]
        object_id = f"obj_{len(objects) + 1}"
        evidence = _safe_evidence(mark.text_summary or node.text_summary or node.content_desc_summary or node.role or object_type)
        objects[object_id] = ScreenObject(
            object_id=object_id,
            object_type=object_type,
            atomic_mark_ids=[mark.mark_id],
            primary_mark_id=mark.mark_id,
            container_node_id=node.node_id,
            list_id=list_id,
            ordinal_index=ordinal_index,
            confidence=mark.confidence,
            role=mark.role or node.role,
            source=source,
            evidence_summary=evidence,
            sensitivity_evidence_summary=_safe_evidence(
                " ".join(
                    text
                    for text in [mark.text_summary, node.text_summary, node.content_desc_summary]
                    if text
                )
            ),
            title_hash=_hash_text(mark.text_summary or node.text_summary),
            text_hash=_hash_text(node.text_summary or mark.text_summary),
            resource_id_hash=node.resource_id_hash,
            lineage_hash=lineage_hash,
        )
    return ObjectRegistry(
        screen_id=screen_id,
        objects=objects,
        semantic_screen_id=mark_registry.semantic_screen_id,
        object_set_version=build_object_set_version(objects),
        structure_topology_digest=structure.topology_digest or build_structure_topology_digest(structure.nodes),
        mark_set_version=mark_registry.mark_set_version,
        truncation_summary={
            "object_count": len(objects),
            "prompt_object_limit": MAX_PROMPT_OBJECTS,
            "list_count": len(list_by_node),
            "prompt_list_limit": MAX_PROMPT_LISTS,
        },
    )


def object_selected_evidence(obj: ScreenObject | None) -> dict[str, Any] | None:
    """Build verifier-only hash/stub evidence for ExpectedOutcome."""

    if obj is None:
        return None
    return {
        "selected_object_id_hash": _hash_text(obj.object_id),
        "object_type": obj.object_type,
        "object_evidence_hash": _hash_text(obj.evidence_summary),
        "title_stub": _stub_title(obj.evidence_summary),
        "title_hash": obj.title_hash,
        "container_lineage_hash": obj.lineage_hash,
        "list_lineage_hash": _hash_text(obj.list_id),
        "expected_page_type": _expected_page_type(obj.object_type),
    }


def _coerce_object(item: dict[str, Any]) -> ScreenObject | None:
    object_id = item.get("object_id")
    object_type = item.get("object_type")
    if not isinstance(object_id, str) or not isinstance(object_type, str):
        return None
    atomic_mark_ids = [str(mark_id) for mark_id in item.get("atomic_mark_ids") or [] if isinstance(mark_id, str)]
    primary_mark_id = item.get("primary_mark_id")
    if primary_mark_id is not None and not isinstance(primary_mark_id, str):
        primary_mark_id = None
    return ScreenObject(
        object_id=object_id,
        object_type=object_type,
        atomic_mark_ids=atomic_mark_ids,
        primary_mark_id=primary_mark_id,
        container_node_id=item.get("container_node_id") if isinstance(item.get("container_node_id"), str) else None,
        parent_object_id=item.get("parent_object_id") if isinstance(item.get("parent_object_id"), str) else None,
        list_id=item.get("list_id") if isinstance(item.get("list_id"), str) else None,
        ordinal_index=item.get("ordinal_index") if isinstance(item.get("ordinal_index"), int) else None,
        confidence=float(item.get("confidence") or 0.0),
        role=item.get("role") if isinstance(item.get("role"), str) else None,
        source=item.get("source") if isinstance(item.get("source"), str) else "accessibility",
        evidence_summary=item.get("evidence_summary") if isinstance(item.get("evidence_summary"), str) else None,
        sensitivity_evidence_summary=item.get("sensitivity_evidence_summary")
        if isinstance(item.get("sensitivity_evidence_summary"), str)
        else None,
        title_hash=item.get("title_hash") if isinstance(item.get("title_hash"), str) else None,
        text_hash=item.get("text_hash") if isinstance(item.get("text_hash"), str) else None,
        resource_id_hash=item.get("resource_id_hash") if isinstance(item.get("resource_id_hash"), str) else None,
        lineage_hash=item.get("lineage_hash") if isinstance(item.get("lineage_hash"), str) else None,
    )


def _map_marks_to_structure_nodes(
    structure: ScreenStructure,
    mark_registry: MarkRegistry,
) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for mark in mark_registry.marks.values():
        best_node = _best_node_for_bbox(mark.bbox, structure)
        if best_node is not None:
            mapping.setdefault(best_node.node_id, []).append(mark.mark_id)
    return mapping


def _find_node_for_mark(mark: Mark, structure: ScreenStructure, parent_to_marks: dict[str, list[str]]) -> StructureNode | None:
    for node_id, mark_ids in parent_to_marks.items():
        if mark.mark_id in mark_ids:
            return structure.nodes.get(node_id)
    return _best_node_for_bbox(mark.bbox, structure)


def _best_node_for_bbox(bbox: tuple[float, float, float, float], structure: ScreenStructure) -> StructureNode | None:
    best: tuple[float, int, float, StructureNode] | None = None
    for node in structure.nodes.values():
        if node.bounds is None:
            continue
        score = _bbox_overlap_ratio(bbox, node.bounds)
        if score <= 0:
            continue
        area = _bbox_area(node.bounds)
        path_depth = max(node.depth, node.path.count("/"))
        candidate = (score, path_depth, -area, node)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    return best[3] if best else None


def _bbox_overlap_ratio(left: tuple[float, float, float, float], right: tuple[int, int, int, int]) -> float:
    lx1, ly1, lx2, ly2 = [float(v) for v in left]
    rx1, ry1, rx2, ry2 = [float(v) for v in right]
    x1 = max(lx1, rx1)
    y1 = max(ly1, ry1)
    x2 = min(lx2, rx2)
    y2 = min(ly2, ry2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    left_area = max(1.0, (lx2 - lx1) * (ly2 - ly1))
    return intersection / left_area


def _bbox_area(bounds: tuple[int, int, int, int]) -> float:
    return max(0.0, float(bounds[2] - bounds[0]) * float(bounds[3] - bounds[1]))


def _nearest_scrollable_ancestor(
    node: StructureNode,
    structure: ScreenStructure,
    list_by_node: dict[str, str],
) -> str | None:
    current: StructureNode | None = node
    while current is not None:
        if current.node_id in list_by_node:
            return current.node_id
        current = structure.nodes.get(current.parent_id or "")
    return None


def _object_type_for(node: StructureNode, mark: Mark) -> str:
    haystack = " ".join(str(value or "").lower() for value in (node.role, mark.role, node.text_summary, mark.text_summary))
    if "edittext" in haystack or "search" in haystack or "搜索" in haystack:
        return "input"
    if "video" in haystack or "播放" in haystack or "视频" in haystack:
        return "video"
    if "button" in haystack or node.clickable:
        return "button"
    if "textview" in haystack and mark.confidence < 1.0:
        return "card"
    return "control"


def _prompt_objects(objects: Any, *, mark_registry: MarkRegistry | None) -> list[ScreenObject]:
    selected = []
    for obj in objects:
        if obj.primary_mark_id and (mark_registry is None or mark_registry.get(obj.primary_mark_id)):
            selected.append(obj)
    return sorted(
        selected,
        key=lambda obj: (
            obj.list_id or "zz",
            obj.ordinal_index if obj.ordinal_index is not None else 10_000,
            obj.object_id,
        ),
    )


def _top_list_ids(objects: Any) -> list[str]:
    ids: list[str] = []
    for obj in objects:
        if obj.list_id and obj.list_id not in ids:
            ids.append(obj.list_id)
    return ids


def _safe_evidence(value: Any) -> str:
    text = str(sanitize_context_payload(str(value or ""), "message", consumer="inject")).strip()
    return text[:MAX_OBJECT_EVIDENCE_CHARS]


def _hash_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _stub_title(value: Any) -> str | None:
    text = _safe_evidence(value)
    if not text:
        return None
    if "<redacted>" in text:
        return "<redacted>"
    return f"len:{len(text)} sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def _expected_page_type(object_type: str) -> str:
    if object_type in {"video", "card", "result"}:
        return "detail_or_player"
    if object_type == "input":
        return "input_focused"
    return "page_opened"
