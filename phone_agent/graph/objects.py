"""Structured screen sidecars and observation-local object registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any

from phone_agent.config.policy import DEFAULT_SAFETY_POLICY
from phone_agent.graph.context import sanitize_context_payload
from phone_agent.graph.marks import Mark, MarkRegistry
from phone_agent.graph.marks import MARK_CONFIDENCE_THRESHOLD


OBJECT_FAILURE_CODES = {
    "object_registry_missing",
    "object_stale",
    "unknown_object",
    "ordinal_out_of_range",
    "object_ambiguous",
    "object_without_mark",
    "mark_stale",
    "mark_low_confidence",
    "visual_object_ambiguous",
    "visual_object_not_executable",
    "visual_structure_missing",
}
MAX_OBJECT_EVIDENCE_CHARS = 120
MAX_PROMPT_OBJECTS = 30
MAX_PROMPT_LISTS = 5
MAX_OBJECTS_BLOCK_CHARS = 4000
SENSITIVITY_TAGS = set(DEFAULT_SAFETY_POLICY.semantic_tags)
VISUAL_OBJECT_TYPES = {
    "visual_target",
    "visual_text",
    "visual_control",
    "visual_group",
    "visual_card",
}


@dataclass(frozen=True)
class StructureNode:
    """Trace-safe normalized structure node."""

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
    password: bool = False
    clickable: bool = False
    focusable: bool = False
    focused: bool = False
    checkable: bool = False
    checked: bool = False
    scrollable: bool = False
    enabled: bool = True
    visible: bool = True
    structure_kind: str = "accessibility"
    source_provider: str | None = None
    confidence_tier: str | None = None
    node_provenance: str | None = None
    visual_order: int | None = None
    confidence: float | None = None
    sensitivity_tags: list[str] = field(default_factory=list)

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
    structure_kind: str = "accessibility"
    source_provider: str | None = None
    confidence_tier: str | None = None
    structure_version: str | None = None
    structure_digest: str | None = None

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
            topology_digest=self.topology_digest
            or build_structure_topology_digest(self.nodes),
            status=self.status,
            nodes=self.nodes,
            root_node_id=self.root_node_id,
            structure_kind=self.structure_kind,
            source_provider=self.source_provider,
            confidence_tier=self.confidence_tier,
            structure_version=self.structure_version,
            structure_digest=self.structure_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "mark_set_version": self.mark_set_version,
            "topology_digest": self.topology_digest
            or build_structure_topology_digest(self.nodes),
            "status": self.status,
            "structure_kind": self.structure_kind,
            "source_provider": self.source_provider,
            "confidence_tier": self.confidence_tier,
            "structure_version": self.structure_version,
            "structure_digest": self.structure_digest
            or self.topology_digest
            or build_structure_topology_digest(self.nodes),
            "node_count": len(self.nodes),
            "root_node_id": self.root_node_id,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
        }

    def trace_summary(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "mark_set_version": self.mark_set_version,
            "topology_digest": self.topology_digest
            or build_structure_topology_digest(self.nodes),
            "status": self.status,
            "structure_kind": self.structure_kind,
            "source_provider": self.source_provider,
            "confidence_tier": self.confidence_tier,
            "structure_digest": self.structure_digest
            or self.topology_digest
            or build_structure_topology_digest(self.nodes),
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
    source_kind: str = "accessibility"
    source_provider: str | None = None
    confidence_tier: str = "strong"
    executable_selector: bool = True
    selector_confidence: str = "strong"
    selector_reasons: list[str] = field(default_factory=list)
    sensitivity_tags: list[str] = field(default_factory=list)
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
        evidence = _safe_evidence(self.evidence_summary)
        return (
            f"- {self.object_id}: type={self.object_type} role={self.role or 'unknown'}"
            f"{list_part}{ordinal} primary_mark_id={self.primary_mark_id}"
            f" confidence={round(self.confidence, 3)}"
            f" source={self.source_kind} tier={self.confidence_tier}"
            f" eligible={str(self.executable_selector).lower()} selector_confidence={self.selector_confidence}"
            f" evidence={evidence}"
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
        iterable = (
            raw_objects.values() if isinstance(raw_objects, dict) else raw_objects
        )
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
            object_set_version=value.get("object_set_version")
            or build_object_set_version(objects),
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
            object_set_version=self.object_set_version
            or build_object_set_version(self.objects),
            structure_topology_digest=structure_topology_digest
            or self.structure_topology_digest,
            mark_set_version=mark_set_version,
            status=self.status,
            truncation_summary=self.truncation_summary,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "object_set_version": self.object_set_version
            or build_object_set_version(self.objects),
            "structure_topology_digest": self.structure_topology_digest,
            "mark_set_version": self.mark_set_version,
            "status": self.status,
            "object_count": len(self.objects),
            "truncation_summary": self.truncation_summary,
            "objects": {
                object_id: obj.to_dict() for object_id, obj in self.objects.items()
            },
        }

    def trace_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        eligible_count = 0
        for obj in self.objects.values():
            counts[obj.object_type] = counts.get(obj.object_type, 0) + 1
            source_counts[obj.source_kind] = source_counts.get(obj.source_kind, 0) + 1
            if obj.executable_selector:
                eligible_count += 1
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "object_set_version": self.object_set_version
            or build_object_set_version(self.objects),
            "structure_topology_digest": self.structure_topology_digest,
            "mark_set_version": self.mark_set_version,
            "status": self.status,
            "object_count": len(self.objects),
            "object_type_counts": counts,
            "source_kind_counts": source_counts,
            "eligible_selector_count": eligible_count,
            "truncation_summary": self.truncation_summary,
        }

    def get(self, object_id: str) -> ScreenObject | None:
        return self.objects.get(str(object_id))

    def prompt_block(
        self,
        *,
        mark_registry: MarkRegistry | None = None,
        lang: str = "cn",
    ) -> str:
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
            "visual objects are weaker than accessibility objects and require eligible=true; "
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
                "visual 对象是弱视觉推断，必须 eligible=true 才可选择；"
                "target_text_hint 只是 provider hint，不是可执行目标。"
            )
        list_ids = _top_list_ids(self.objects.values())
        rows = [
            title,
            f"screen={self.screen_id} topology={self.structure_topology_digest}",
            limits,
        ]
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
            f"{node.structure_kind}:{node.source_provider or ''}:{node.path}:{node.parent_id or ''}:{node.bounds}:{node.role or ''}:"
            f"{int(node.clickable)}{int(node.focusable)}{int(node.scrollable)}"
        )
    return hashlib.sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def build_object_set_version(objects: dict[str, ScreenObject]) -> str:
    rows = []
    for obj in objects.values():
        rows.append(
            f"{obj.object_id}:{obj.object_type}:{obj.primary_mark_id}:"
            f"{obj.list_id or ''}:{obj.ordinal_index or ''}:{obj.lineage_hash or ''}:"
            f"{obj.source_kind}:{int(obj.executable_selector)}"
        )
    return hashlib.sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def build_object_registry(
    *,
    screen_id: str,
    structure: ScreenStructure | list[ScreenStructure] | None,
    mark_registry: MarkRegistry,
    source: str = "accessibility",
) -> ObjectRegistry:
    """Build executable screen objects from current structure and atomic marks."""

    objects: dict[str, ScreenObject] = {}
    structures = _normalize_structures(structure)
    if not structures or not mark_registry.marks:
        return ObjectRegistry(
            screen_id=screen_id,
            semantic_screen_id=mark_registry.semantic_screen_id,
            mark_set_version=mark_registry.mark_set_version,
            structure_topology_digest=None,
            status="missing_sidecar",
        )

    conflict_count = 0
    list_count = 0
    for current_structure in sorted(structures, key=_structure_sort_key):
        before_count = len(objects)
        if current_structure.structure_kind == "visual":
            conflict_count += _append_visual_objects(
                objects, current_structure, mark_registry
            )
        else:
            list_count += _append_accessibility_objects(
                objects, current_structure, mark_registry, source=source
            )
        if (
            len(objects) == before_count
            and current_structure.structure_kind == "visual"
        ):
            continue
    structure_digest = build_composite_structure_digest(structures)
    return ObjectRegistry(
        screen_id=screen_id,
        objects=objects,
        semantic_screen_id=mark_registry.semantic_screen_id,
        object_set_version=build_object_set_version(objects),
        structure_topology_digest=_strong_structure_digest(structures)
        or structure_digest,
        mark_set_version=mark_registry.mark_set_version,
        truncation_summary={
            "object_count": len(objects),
            "prompt_object_limit": MAX_PROMPT_OBJECTS,
            "list_count": list_count,
            "prompt_list_limit": MAX_PROMPT_LISTS,
            "structure_count": len(structures),
            "visual_conflict_count": conflict_count,
            "composite_structure_digest": structure_digest,
            "strong_structure_digest": _strong_structure_digest(structures),
        },
    )


def _normalize_structures(
    structure: ScreenStructure | list[ScreenStructure] | None,
) -> list[ScreenStructure]:
    if structure is None:
        return []
    if isinstance(structure, ScreenStructure):
        return [structure]
    return [item for item in structure if isinstance(item, ScreenStructure)]


def _structure_sort_key(structure: ScreenStructure) -> tuple[int, str, str]:
    priority = 1 if structure.structure_kind == "visual" else 0
    return (
        priority,
        structure.source_provider or "",
        structure.structure_digest or structure.topology_digest or "",
    )


def build_composite_structure_digest(structures: list[ScreenStructure]) -> str | None:
    if not structures:
        return None
    rows = [
        f"{structure.structure_kind}:{structure.source_provider or ''}:"
        f"{structure.structure_digest or structure.topology_digest or build_structure_topology_digest(structure.nodes)}"
        for structure in structures
    ]
    return hashlib.sha256("|".join(rows).encode("utf-8")).hexdigest()[:16]


def _strong_structure_digest(structures: list[ScreenStructure]) -> str | None:
    for structure in structures:
        if structure.structure_kind == "accessibility":
            return (
                structure.topology_digest
                or structure.structure_digest
                or build_structure_topology_digest(structure.nodes)
            )
    return None


def summarize_structures(structures: list[ScreenStructure]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    node_counts: dict[str, int] = {}
    total_nodes = 0
    for structure in structures:
        kind = structure.structure_kind or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
        node_counts[kind] = node_counts.get(kind, 0) + len(structure.nodes)
        total_nodes += len(structure.nodes)
    return {
        "status": "ok" if structures else "missing_sidecar",
        "structure_count": len(structures),
        "kind_counts": counts,
        "node_counts": node_counts,
        "node_count": total_nodes,
        "topology_digest": _strong_structure_digest(structures)
        or (structures[0].topology_digest if structures else None),
        "merge_order": [structure.structure_kind for structure in structures],
        "composite_structure_digest": build_composite_structure_digest(structures),
        "strong_structure_digest": _strong_structure_digest(structures),
        "structures": [structure.trace_summary() for structure in structures[:8]],
    }


def _append_accessibility_objects(
    objects: dict[str, ScreenObject],
    structure: ScreenStructure,
    mark_registry: MarkRegistry,
    *,
    source: str,
) -> int:
    parent_to_marks = _map_marks_to_structure_nodes(structure, mark_registry)
    scrollable_nodes = [
        node for node in structure.nodes.values() if node.scrollable and node.visible
    ]
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
        lineage_hash = _hash_text(f"{node.path}|{list_id or ''}|{object_type}") or None
        object_id = f"obj_{len(objects) + 1}"
        evidence = _safe_evidence(
            mark.text_summary
            or node.text_summary
            or node.content_desc_summary
        ) or None
        tags = _normalize_sensitivity_tags(
            node.sensitivity_tags
        ) or _infer_sensitivity_tags(
            " ".join(
                text
                for text in [
                    mark.text_summary,
                    node.text_summary,
                    node.content_desc_summary,
                ]
                if text
            )
        )
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
            source_kind="accessibility",
            source_provider=structure.source_provider or source,
            confidence_tier=structure.confidence_tier or "strong",
            executable_selector=True,
            selector_confidence="strong",
            selector_reasons=["accessibility_structure"],
            sensitivity_tags=tags,
            evidence_summary=evidence,
            sensitivity_evidence_summary=_safe_evidence(
                " ".join(tags)
                or " ".join(
                    text
                    for text in [
                        mark.text_summary,
                        node.text_summary,
                        node.content_desc_summary,
                    ]
                    if text
                )
            ),
            title_hash=_hash_text(mark.text_summary or node.text_summary),
            text_hash=_hash_text(node.text_summary or mark.text_summary),
            resource_id_hash=node.resource_id_hash,
            lineage_hash=lineage_hash,
        )
    return len(list_by_node)


def _append_visual_objects(
    objects: dict[str, ScreenObject],
    structure: ScreenStructure,
    mark_registry: MarkRegistry,
) -> int:
    conflict_count = 0
    visual_nodes = sorted(
        [
            node
            for node in structure.nodes.values()
            if node.visible and node.bounds is not None
        ],
        key=lambda node: (
            node.visual_order if node.visual_order is not None else 10_000,
            (node.bounds or (0, 0, 0, 0))[1],
            (node.bounds or (0, 0, 0, 0))[0],
            node.node_id,
        ),
    )
    ordinal_by_row: dict[int, int] = {}
    for node in visual_nodes:
        mark = _best_mark_for_node(node, mark_registry)
        if mark is None:
            continue
        overlaps_accessibility = any(
            obj.source_kind == "accessibility"
            and obj.primary_mark_id
            and (existing_mark := mark_registry.get(obj.primary_mark_id)) is not None
            and _bbox_iou(mark.bbox, existing_mark.bbox) >= 0.5
            for obj in objects.values()
        )
        if overlaps_accessibility:
            conflict_count += 1
        object_type = _visual_object_type_for(node, mark)
        row_bucket = _visual_row_bucket(node.bounds)
        ordinal_by_row[row_bucket] = ordinal_by_row.get(row_bucket, 0) + 1
        ordinal_index = (
            ordinal_by_row[row_bucket]
            if len(visual_nodes) <= MAX_PROMPT_OBJECTS
            else None
        )
        list_id = (
            f"visual_row_{row_bucket}"
            if ordinal_index is not None and len(visual_nodes) > 1
            else None
        )
        selector_reasons = ["visual_structure", "single_primary_mark"]
        source_safe_bound = _visual_node_has_source_safe_binding(node, mark)
        executable_selector = (
            source_safe_bound
            and not overlaps_accessibility
            and mark.confidence >= MARK_CONFIDENCE_THRESHOLD
        )
        selector_confidence = "weak" if executable_selector else "none"
        if not source_safe_bound:
            selector_reasons.append("missing_source_safe_binding")
        if overlaps_accessibility:
            selector_reasons.append("overlaps_accessibility")
        if mark.confidence < MARK_CONFIDENCE_THRESHOLD:
            selector_reasons.append("low_mark_confidence")
        object_id = f"obj_{len(objects) + 1}"
        evidence = _safe_evidence(node.text_summary or mark.text_summary) or None
        tags = _normalize_sensitivity_tags(
            node.sensitivity_tags
        ) or _infer_sensitivity_tags(node.text_summary or mark.text_summary)
        objects[object_id] = ScreenObject(
            object_id=object_id,
            object_type=object_type,
            atomic_mark_ids=[mark.mark_id],
            primary_mark_id=mark.mark_id,
            container_node_id=node.node_id,
            list_id=list_id,
            ordinal_index=ordinal_index,
            confidence=min(
                float(
                    node.confidence if node.confidence is not None else mark.confidence
                ),
                mark.confidence,
            ),
            role=mark.role or node.role,
            source="visual",
            source_kind="visual",
            source_provider=structure.source_provider or node.source_provider,
            confidence_tier=structure.confidence_tier or node.confidence_tier or "weak",
            executable_selector=executable_selector,
            selector_confidence=selector_confidence,
            selector_reasons=selector_reasons,
            sensitivity_tags=tags,
            evidence_summary=evidence,
            sensitivity_evidence_summary=_safe_evidence(" ".join(tags)),
            title_hash=_hash_text(node.text_summary or mark.text_summary),
            text_hash=_hash_text(node.text_summary or mark.text_summary),
            lineage_hash=_hash_text(
                f"{structure.structure_digest or structure.topology_digest}|{node.path}|{list_id or ''}|{object_type}"
            ),
        )
    return conflict_count


def _best_mark_for_node(
    node: StructureNode, mark_registry: MarkRegistry
) -> Mark | None:
    if node.bounds is None:
        return None
    best: tuple[float, str, Mark] | None = None
    for mark in mark_registry.marks.values():
        score = _bbox_overlap_ratio(mark.bbox, node.bounds)
        if score <= 0:
            continue
        candidate = (score, mark.mark_id, mark)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


def _visual_object_type_for(node: StructureNode, mark: Mark) -> str:
    haystack = " ".join(
        str(value or "").casefold()
        for value in (node.role, mark.role, node.text_summary, mark.text_summary)
    )
    if "text" in haystack or "ocr" in haystack:
        return "visual_text"
    if "card" in haystack or "video" in haystack or "result" in haystack:
        return "visual_card"
    if (
        "button" in haystack
        or "control" in haystack
        or "search" in haystack
        or "搜索" in haystack
    ):
        return "visual_control"
    if "group" in haystack or "container" in haystack:
        return "visual_group"
    return "visual_target"


def _visual_node_has_source_safe_binding(node: StructureNode, mark: Mark) -> bool:
    haystack = " ".join(
        str(value or "").casefold()
        for value in (node.role, mark.role, node.text_summary, mark.text_summary)
    )
    safe_terms = {
        "button",
        "text",
        "input",
        "card",
        "image",
        "search",
        "搜索",
        "visual_target",
    }
    if any(term in haystack for term in safe_terms):
        return True
    return bool(node.sensitivity_tags)


def _visual_row_bucket(bounds: tuple[int, int, int, int] | None) -> int:
    if bounds is None:
        return 0
    return int(round(bounds[1] / 80.0))


def _bbox_iou(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    lx1, ly1, lx2, ly2 = [float(v) for v in left]
    rx1, ry1, rx2, ry2 = [float(v) for v in right]
    x1 = max(lx1, rx1)
    y1 = max(ly1, ry1)
    x2 = min(lx2, rx2)
    y2 = min(ly2, ry2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    union = max(
        1.0, (lx2 - lx1) * (ly2 - ly1) + (rx2 - rx1) * (ry2 - ry1) - intersection
    )
    return intersection / union


def _normalize_sensitivity_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    normalized: list[str] = []
    for tag in tags:
        value = str(tag or "").strip().casefold()
        if value in SENSITIVITY_TAGS and value not in normalized:
            normalized.append(value)
    return normalized


def _infer_sensitivity_tags(value: Any) -> list[str]:
    text = str(value or "").casefold()
    tags: list[str] = []
    term_map = {
        "payment": ("pay", "payment", "purchase", "支付", "付款", "购买"),
        "privacy": ("privacy", "隐私"),
        "login": ("login", "登录", "账号", "账户"),
        "password": ("password", "密码"),
        "otp": ("captcha", "otp", "验证码"),
        "delete": ("delete", "remove", "删除", "移除"),
        "permission": ("permission", "权限"),
    }
    for tag, terms in term_map.items():
        if any(term in text for term in terms):
            tags.append(tag)
    return tags


def object_selected_evidence(obj: ScreenObject | None) -> dict[str, Any] | None:
    """Build verifier-only raw evidence for ExpectedOutcome."""

    if obj is None:
        return None
    return {
        "object_type": obj.object_type,
        "evidence_summary": _safe_evidence(obj.evidence_summary) or None,
        "expected_page_type": _expected_page_type(obj.object_type),
        "expected_rank": obj.ordinal_index,
    }


def _coerce_object(item: dict[str, Any]) -> ScreenObject | None:
    object_id = item.get("object_id")
    object_type = item.get("object_type")
    if not isinstance(object_id, str) or not isinstance(object_type, str):
        return None
    atomic_mark_ids = [
        str(mark_id)
        for mark_id in item.get("atomic_mark_ids") or []
        if isinstance(mark_id, str)
    ]
    primary_mark_id = item.get("primary_mark_id")
    if primary_mark_id is not None and not isinstance(primary_mark_id, str):
        primary_mark_id = None
    source_kind = (
        item.get("source_kind")
        if isinstance(item.get("source_kind"), str)
        else "accessibility"
    )
    has_explicit_eligibility = "executable_selector" in item
    has_explicit_selector_confidence = "selector_confidence" in item
    executable_selector = (
        bool(item.get("executable_selector"))
        if has_explicit_eligibility
        else source_kind != "visual"
    )
    selector_confidence = (
        item.get("selector_confidence")
        if isinstance(item.get("selector_confidence"), str)
        else (
            "none"
            if source_kind == "visual" and not has_explicit_selector_confidence
            else "strong"
        )
    )
    return ScreenObject(
        object_id=object_id,
        object_type=object_type,
        atomic_mark_ids=atomic_mark_ids,
        primary_mark_id=primary_mark_id,
        container_node_id=item.get("container_node_id")
        if isinstance(item.get("container_node_id"), str)
        else None,
        parent_object_id=item.get("parent_object_id")
        if isinstance(item.get("parent_object_id"), str)
        else None,
        list_id=item.get("list_id") if isinstance(item.get("list_id"), str) else None,
        ordinal_index=item.get("ordinal_index")
        if isinstance(item.get("ordinal_index"), int)
        else None,
        confidence=float(item.get("confidence") or 0.0),
        role=item.get("role") if isinstance(item.get("role"), str) else None,
        source=item.get("source")
        if isinstance(item.get("source"), str)
        else "accessibility",
        source_kind=source_kind,
        source_provider=item.get("source_provider")
        if isinstance(item.get("source_provider"), str)
        else None,
        confidence_tier=item.get("confidence_tier")
        if isinstance(item.get("confidence_tier"), str)
        else "strong",
        executable_selector=executable_selector,
        selector_confidence=selector_confidence,
        selector_reasons=[
            str(reason)
            for reason in item.get("selector_reasons") or []
            if isinstance(reason, str)
        ],
        sensitivity_tags=_normalize_sensitivity_tags(item.get("sensitivity_tags")),
        evidence_summary=item.get("evidence_summary")
        if isinstance(item.get("evidence_summary"), str)
        else None,
        sensitivity_evidence_summary=item.get("sensitivity_evidence_summary")
        if isinstance(item.get("sensitivity_evidence_summary"), str)
        else None,
        title_hash=item.get("title_hash")
        if isinstance(item.get("title_hash"), str)
        else None,
        text_hash=item.get("text_hash")
        if isinstance(item.get("text_hash"), str)
        else None,
        resource_id_hash=item.get("resource_id_hash")
        if isinstance(item.get("resource_id_hash"), str)
        else None,
        lineage_hash=item.get("lineage_hash")
        if isinstance(item.get("lineage_hash"), str)
        else None,
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


def _find_node_for_mark(
    mark: Mark, structure: ScreenStructure, parent_to_marks: dict[str, list[str]]
) -> StructureNode | None:
    for node_id, mark_ids in parent_to_marks.items():
        if mark.mark_id in mark_ids:
            return structure.nodes.get(node_id)
    return _best_node_for_bbox(mark.bbox, structure)


def _best_node_for_bbox(
    bbox: tuple[float, float, float, float], structure: ScreenStructure
) -> StructureNode | None:
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


def _bbox_overlap_ratio(
    left: tuple[float, float, float, float], right: tuple[int, int, int, int]
) -> float:
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
    haystack = " ".join(
        str(value or "").lower()
        for value in (node.role, mark.role, node.text_summary, mark.text_summary)
    )
    if "edittext" in haystack or "search" in haystack or "搜索" in haystack:
        return "input"
    if "video" in haystack or "播放" in haystack or "视频" in haystack:
        return "video"
    if "button" in haystack or node.clickable:
        return "button"
    if "textview" in haystack and mark.confidence < 1.0:
        if _looks_like_tab(node, mark):
            return "control"
        return "card"
    return "control"


# Short tab-bar labels (「全部」「用户」「视频」「图文」…) are TextViews too; the
# live run showed the model tapping them as if they were content cards. Class
# names and container/resource ids are the structural signals; keep the label
# list bounded to generic selectors so real cards with specific titles are not
# demoted.
_TAB_CLASS_MARKERS = ("tablayout", "tabitem", "tabhost", "pagertabstrip")
_TAB_CONTAINER_MARKERS = ("tab", "indicator", "navigationbar", "nav_bar")
_TAB_GENERIC_LABELS = {
    "全部",
    "推荐",
    "用户",
    "视频",
    "图文",
    "直播",
    "关注",
    "发现",
    "附近",
    "最新",
    "最热",
    "精华",
    "all",
    "recommend",
    "recommended",
    "user",
    "users",
    "video",
    "videos",
    "live",
    "following",
    "latest",
    "hot",
    "top",
}
_TAB_MAX_LABEL_CHARS = 4
# Tab-strip entries are short bars (e.g. 124x60 device px at the top of the
# screen, normalized to ~115x25); content cards span hundreds of px tall.
# Normalized marks/structures are both in the 0-1000 space, so the thresholds
# below are directly comparable.
_TAB_MAX_HEIGHT_NORMALIZED = 80.0
_TAB_MAX_HEIGHT_TO_WIDTH = 0.5


def _looks_like_tab(node: StructureNode, mark: Mark) -> bool:
    """Conservative tab detector: structural signals first, bounded generic
    label + tab-strip geometry as fallback. Anything uncertain stays a card."""

    structural = " ".join(
        str(value or "").lower()
        for value in (node.class_name, node.path, node.role, mark.role)
    )
    if any(marker in structural for marker in _TAB_CLASS_MARKERS):
        return True
    if "tab" in structural:
        return True

    label = str(mark.text_summary or node.text_summary or "").strip()
    if not label or len(label) > _TAB_MAX_LABEL_CHARS:
        return False
    if label.lower() not in _TAB_GENERIC_LABELS:
        return False
    return _in_tab_strip_geometry(node, mark)


def _in_tab_strip_geometry(node: StructureNode, mark: Mark) -> bool:
    bounds = node.bounds
    if bounds is None:
        x1, y1, x2, y2 = (float(v) for v in mark.bbox)
        width, height = x2 - x1, y2 - y1
    else:
        width, height = float(bounds[2] - bounds[0]), float(bounds[3] - bounds[1])
    if width <= 0 or height <= 0:
        return False
    return height <= _TAB_MAX_HEIGHT_NORMALIZED and (height / width) <= _TAB_MAX_HEIGHT_TO_WIDTH


def _prompt_objects(
    objects: Any, *, mark_registry: MarkRegistry | None
) -> list[ScreenObject]:
    selected = []
    for obj in objects:
        if obj.primary_mark_id and (
            mark_registry is None or mark_registry.get(obj.primary_mark_id)
        ):
            selected.append(obj)
    return sorted(
        selected,
        key=lambda obj: (
            1 if obj.source_kind == "visual" else 0,
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
    text = str(
        sanitize_context_payload(str(value or ""), "message", consumer="inject")
    ).strip()
    return text[:MAX_OBJECT_EVIDENCE_CHARS]


def _hash_text(value: Any) -> str | None:
    text = str(value or "").strip()[:MAX_OBJECT_EVIDENCE_CHARS]
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _expected_page_type(object_type: str) -> str:
    if object_type in {"video", "card", "result"}:
        return "detail_or_player"
    if object_type == "input":
        return "input_focused"
    return "page_opened"
