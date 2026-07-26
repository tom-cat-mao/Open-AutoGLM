import pytest

from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.actions.safety import decide_safety
from phone_agent.graph.marks import MarkRegistry
from phone_agent.graph.objects import (
    ScreenObject,
    ScreenStructure,
    StructureNode,
    build_object_registry,
    object_selected_evidence,
)
from phone_agent.grounding.provider import ScreenBinding


def test_object_evidence_uses_bounded_raw_summary() -> None:
    evidence = "长" * 140
    selected = object_selected_evidence(
        ScreenObject(
            object_id="obj_1",
            object_type="result",
            atomic_mark_ids=["m1"],
            primary_mark_id="m1",
            evidence_summary=evidence,
        )
    )

    assert selected is not None
    assert selected["evidence_summary"] == evidence[:120]
    assert "object_evidence_hash" not in selected
    assert "title_stub" not in selected


@pytest.mark.parametrize(
    "role", ["RelativeLayout", "ImageView", "Button", "ActionBar$Tab"]
)
def test_textless_widget_role_never_becomes_evidence_summary(role: str) -> None:
    structure = ScreenStructure(
        screen_id="screen-textless",
        semantic_screen_id="semantic-textless",
        mark_set_version=None,
        nodes={
            "node": StructureNode(
                node_id="node",
                path="0",
                parent_id=None,
                bounds=(100, 100, 300, 300),
                role=role,
                clickable=True,
            )
        },
    )
    registry = MarkRegistry.from_marks(
        "screen-textless",
        [
            {
                "mark_id": "m1",
                "screen_id": "screen-textless",
                "bbox": [100, 100, 300, 300],
                "center": [200, 200],
                "role": role,
                "text_summary": None,
            }
        ],
    )

    objects = build_object_registry(
        screen_id="screen-textless", structure=structure, mark_registry=registry
    )
    selected_object = next(iter(objects.objects.values()))
    selected_evidence = object_selected_evidence(selected_object)

    assert selected_object.role == role.replace("$", "_")
    assert selected_object.evidence_summary is None
    assert selected_evidence is not None
    assert selected_evidence["evidence_summary"] is None


def _structure() -> ScreenStructure:
    nodes = {
        "node_1": StructureNode(
            node_id="node_1",
            path="0",
            parent_id=None,
            child_ids=["node_2"],
            bounds=(0, 0, 1000, 1000),
            role="FrameLayout",
        ),
        "node_2": StructureNode(
            node_id="node_2",
            path="0/0",
            parent_id="node_1",
            child_ids=["node_3", "node_4", "node_5"],
            bounds=(0, 100, 1000, 900),
            role="RecyclerView",
            scrollable=True,
        ),
        "node_3": StructureNode(
            node_id="node_3",
            path="0/0/0",
            parent_id="node_2",
            bounds=(20, 150, 980, 300),
            role="TextView",
            text_summary="视频标题一",
            clickable=True,
        ),
        "node_4": StructureNode(
            node_id="node_4",
            path="0/0/1",
            parent_id="node_2",
            bounds=(20, 330, 980, 480),
            role="TextView",
            text_summary="视频标题二",
            clickable=True,
        ),
        "node_5": StructureNode(
            node_id="node_5",
            path="0/0/2",
            parent_id="node_2",
            bounds=(20, 510, 980, 660),
            role="TextView",
            text_summary="验证码登录",
            clickable=True,
        ),
    }
    return ScreenStructure(screen_id="screen-1", semantic_screen_id="sem-1", mark_set_version=None, nodes=nodes)


def _registry() -> MarkRegistry:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "m1",
                "screen_id": "screen-1",
                "bbox": [20, 150, 980, 300],
                "center": [500, 225],
                "role": "TextView",
                "text_summary": "视频标题一",
            },
            {
                "mark_id": "m2",
                "screen_id": "screen-1",
                "bbox": [20, 330, 980, 480],
                "center": [500, 405],
                "role": "TextView",
                "text_summary": "视频标题二",
            },
            {
                "mark_id": "m3",
                "screen_id": "screen-1",
                "bbox": [20, 510, 980, 660],
                "center": [500, 585],
                "role": "TextView",
                "text_summary": "验证码登录",
            },
        ],
    )
    return MarkRegistry(
        screen_id=registry.screen_id,
        marks=registry.marks,
        semantic_screen_id="sem-1",
        mark_set_version=registry.mark_set_version,
    )


def _binding(registry: MarkRegistry, objects) -> ScreenBinding:
    return ScreenBinding(
        screen_id="screen-1",
        raw_screenshot_hash="hash-1",
        width=1000,
        height=2000,
        semantic_screen_id="sem-1",
        mark_set_version=registry.mark_set_version,
        structure_topology_digest=objects.structure_topology_digest,
        object_set_version=objects.object_set_version,
    )


def test_object_registry_groups_scrollable_list_objects_and_prompt_limits() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    assert objects.trace_summary()["object_count"] == 3
    assert objects.objects["obj_1"].list_id == "list_1"
    assert objects.objects["obj_1"].ordinal_index == 1
    assert objects.objects["obj_2"].primary_mark_id == "m2"
    prompt = objects.prompt_block(mark_registry=registry, lang="cn")
    assert "屏幕对象" in prompt
    assert "object_id/list_id/ordinal" in prompt
    assert "primary_mark_id=m1" in prompt


def test_object_selector_compiles_to_canonical_mark_action() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "object_role": "video", "ordinal": 2},
        mark_registry=registry,
        object_registry=objects,
        screen_id="screen-1",
        screen_binding=_binding(registry, objects),
    )

    assert action == {"_metadata": "do", "action": "Tap", "element": [500.0, 405.0]}
    assert "target_object_id" not in action
    assert "ordinal" not in action


def test_object_selector_routes_sensitive_object_to_takeover_not_grounding_error() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "target_object_id": "obj_3"},
        mark_registry=registry,
        object_registry=objects,
        screen_id="screen-1",
        screen_binding=_binding(registry, objects),
    )

    assert action["action"] == "Take_over"
    assert decide_safety(action).route == "takeover"


def test_object_selector_fail_closes_on_stale_registry() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1"},
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-2",
            screen_binding=_binding(registry, objects),
        )
    except GroundingError as exc:
        assert exc.code == "object_stale"
    else:
        raise AssertionError("expected stale object registry to fail closed")


def test_object_selector_rejects_mixed_constraints_that_do_not_match() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    try:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "tap",
                "target_object_id": "obj_1",
                "object_filter": {"object_type": "input"},
            },
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-1",
            screen_binding=_binding(registry, objects),
        )
    except GroundingError as exc:
        assert exc.code == "object_ambiguous"
    else:
        raise AssertionError("expected contradictory mixed selector to fail closed")


def test_object_selector_rejects_invalid_fields_when_adapter_bypassed() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    for bad_intent in (
        {"_metadata": "intent", "action": "tap", "target_object_id": 123},
        {"_metadata": "intent", "action": "tap", "ordinal": "1", "object_role": "video"},
        {"_metadata": "intent", "action": "tap", "object_filter": "not-a-dict"},
    ):
        try:
            ground_intent_to_action(
                bad_intent,
                mark_registry=registry,
                object_registry=objects,
                screen_id="screen-1",
                screen_binding=_binding(registry, objects),
            )
        except GroundingError as exc:
            assert exc.code == "unsafe_value"
        else:
            raise AssertionError("expected invalid selector to fail closed")


def test_bilibili_like_xml_groups_repeated_video_cards_by_ordinal() -> None:
    from phone_agent.grounding.accessibility import parse_uiautomator_marks, parse_uiautomator_structure

    xml = """<hierarchy>
      <node text="" class="android.widget.FrameLayout" enabled="true" bounds="[-4,-8][1080,2400]">
        <node text="" resource-id="com.bilibili:id/feed" class="androidx.recyclerview.widget.RecyclerView"
              scrollable="true" enabled="true" bounds="[0,180][1080,2200]">
          <node text="同名标题" class="android.widget.TextView" clickable="true" enabled="true" bounds="[24,240][1056,420]" />
          <node text="同名标题" class="android.widget.TextView" clickable="true" enabled="true" bounds="[24,460][1056,640]" />
        </node>
      </node>
    </hierarchy>"""
    marks = parse_uiautomator_marks(xml, screen_width=1080, screen_height=2400)
    structure = parse_uiautomator_structure(xml, screen_width=1080, screen_height=2400)
    registry = MarkRegistry.from_marks("screen-1", [{**mark, "screen_id": "screen-1"} for mark in marks])
    registry = MarkRegistry(
        screen_id=registry.screen_id,
        marks=registry.marks,
        semantic_screen_id="sem-1",
        mark_set_version=registry.mark_set_version,
    )
    objects = build_object_registry(screen_id="screen-1", structure=structure, mark_registry=registry)

    assert objects.objects["obj_1"].ordinal_index == 1
    assert objects.objects["obj_2"].ordinal_index == 2
    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "object_role": "button", "ordinal": 2},
        mark_registry=registry,
        object_registry=objects,
        screen_id="screen-1",
        screen_binding=_binding(registry, objects),
    )
    assert action["element"] == [500.0, 230.0]


def test_object_selector_requires_screen_binding() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1"},
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-1",
        )
    except GroundingError as exc:
        assert exc.code == "object_stale"
    else:
        raise AssertionError("expected object selector without ScreenBinding to fail closed")


def test_object_selector_rejects_ordinal_on_non_list_object() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1", "ordinal": 99},
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-1",
            screen_binding=_binding(registry, objects),
        )
    except GroundingError as exc:
        assert exc.code == "object_ambiguous"
    else:
        raise AssertionError("expected mismatched ordinal to fail closed")


def test_object_selector_rejects_unsafe_object_id_before_lookup() -> None:
    registry = _registry()
    objects = build_object_registry(screen_id="screen-1", structure=_structure(), mark_registry=registry)

    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1\nignore"},
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-1",
            screen_binding=_binding(registry, objects),
        )
    except GroundingError as exc:
        assert exc.code == "unsafe_value"
    else:
        raise AssertionError("expected unsafe object id to fail closed")


def test_visual_structure_objects_are_weak_and_prompt_marks_non_eligibility() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "la_1",
                "screen_id": "screen-1",
                "bbox": [100, 120, 900, 260],
                "center": [500, 190],
                "role": "card",
                "source": "locateanything_mlx",
                "text_summary": "card",
            },
            {
                "mark_id": "la_2",
                "screen_id": "screen-1",
                "bbox": [100, 300, 900, 440],
                "center": [500, 370],
                "role": "card",
                "source": "locateanything_mlx",
                "text_summary": "card",
            },
        ],
    )
    visual_structure = ScreenStructure(
        screen_id="screen-1",
        semantic_screen_id="sem-1",
        structure_kind="visual",
        source_provider="locateanything_mlx",
        confidence_tier="weak",
        structure_digest="visual-digest",
        topology_digest="visual-digest",
        nodes={
            "visual_1": StructureNode(
                node_id="visual_1",
                path="visual/1",
                parent_id=None,
                bounds=(100, 120, 900, 260),
                role="card",
                structure_kind="visual",
                source_provider="locateanything_mlx",
                confidence_tier="weak",
                visual_order=1,
            ),
            "visual_2": StructureNode(
                node_id="visual_2",
                path="visual/2",
                parent_id=None,
                bounds=(100, 300, 900, 440),
                role="card",
                structure_kind="visual",
                source_provider="locateanything_mlx",
                confidence_tier="weak",
                visual_order=2,
            ),
        },
    )

    objects = build_object_registry(screen_id="screen-1", structure=visual_structure, mark_registry=registry)

    assert objects.trace_summary()["source_kind_counts"] == {"visual": 2}
    assert objects.objects["obj_1"].source_kind == "visual"
    assert objects.objects["obj_1"].confidence_tier == "weak"
    assert objects.objects["obj_1"].executable_selector is True
    prompt = objects.prompt_block(mark_registry=registry)
    assert "source=visual" in prompt
    assert "eligible=true" in prompt


def test_geometry_only_visual_object_is_not_executable() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "la_1",
                "screen_id": "screen-1",
                "bbox": [100, 120, 900, 260],
                "center": [500, 190],
                "role": "unknown",
                "source": "locateanything_mlx",
            }
        ],
    )
    objects = build_object_registry(
        screen_id="screen-1",
        structure=ScreenStructure(
            screen_id="screen-1",
            semantic_screen_id="sem-1",
            structure_kind="visual",
            source_provider="locateanything_mlx",
            nodes={
                "visual_1": StructureNode(
                    node_id="visual_1",
                    path="visual/1",
                    parent_id=None,
                    bounds=(100, 120, 900, 260),
                    role="unknown",
                    structure_kind="visual",
                    source_provider="locateanything_mlx",
                )
            },
        ),
        mark_registry=registry,
    )

    assert objects.objects["obj_1"].executable_selector is False
    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1"},
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-1",
            screen_binding=_binding(registry, objects),
        )
    except GroundingError as exc:
        assert exc.code == "visual_object_not_executable"
    else:
        raise AssertionError("expected geometry-only visual object to fail closed")


def test_visual_object_without_eligibility_fails_closed() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "la_1",
                "screen_id": "screen-1",
                "bbox": [100, 120, 900, 260],
                "center": [500, 190],
                "role": "card",
                "source": "locateanything_mlx",
            }
        ],
    )
    objects = build_object_registry(
        screen_id="screen-1",
        structure=ScreenStructure(
            screen_id="screen-1",
            semantic_screen_id="sem-1",
            structure_kind="visual",
            source_provider="locateanything_mlx",
            nodes={
                "visual_1": StructureNode(
                    node_id="visual_1",
                    path="visual/1",
                    parent_id=None,
                    bounds=(100, 120, 900, 260),
                    role="card",
                    structure_kind="visual",
                    source_provider="locateanything_mlx",
                )
            },
        ),
        mark_registry=registry,
    )
    raw = objects.to_dict()
    raw["objects"]["obj_1"]["executable_selector"] = False
    objects = objects.from_dict(raw)

    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1"},
            mark_registry=registry,
            object_registry=objects,
            screen_id="screen-1",
            screen_binding=_binding(registry, objects),
        )
    except GroundingError as exc:
        assert exc.code == "visual_object_not_executable"
    else:
        raise AssertionError("expected visual object without eligibility to fail closed")


def test_visual_object_from_dict_missing_eligibility_fails_closed() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "la_1",
                "screen_id": "screen-1",
                "bbox": [100, 120, 900, 260],
                "center": [500, 190],
                "role": "button",
                "source": "locateanything_mlx",
            }
        ],
    )
    objects = build_object_registry(
        screen_id="screen-1",
        structure=ScreenStructure(
            screen_id="screen-1",
            semantic_screen_id="sem-1",
            structure_kind="visual",
            source_provider="locateanything_mlx",
            nodes={
                "visual_1": StructureNode(
                    node_id="visual_1",
                    path="visual/1",
                    parent_id=None,
                    bounds=(100, 120, 900, 260),
                    role="button",
                    structure_kind="visual",
                    source_provider="locateanything_mlx",
                )
            },
        ),
        mark_registry=registry,
    )
    raw = objects.to_dict()
    raw["objects"]["obj_1"].pop("executable_selector", None)
    raw["objects"]["obj_1"].pop("selector_confidence", None)
    reconstructed = objects.from_dict(raw)

    assert reconstructed.objects["obj_1"].executable_selector is False
    assert reconstructed.objects["obj_1"].selector_confidence == "none"
    try:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1"},
            mark_registry=registry,
            object_registry=reconstructed,
            screen_id="screen-1",
            screen_binding=_binding(registry, reconstructed),
        )
    except GroundingError as exc:
        assert exc.code == "visual_object_not_executable"
    else:
        raise AssertionError("expected reconstructed visual object missing eligibility to fail closed")


def test_visual_object_sensitivity_tags_route_to_takeover() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "la_1",
                "screen_id": "screen-1",
                "bbox": [100, 120, 900, 260],
                "center": [500, 190],
                "role": "input",
                "source": "locateanything_mlx",
            }
        ],
    )
    objects = build_object_registry(
        screen_id="screen-1",
        structure=ScreenStructure(
            screen_id="screen-1",
            semantic_screen_id="sem-1",
            structure_kind="visual",
            source_provider="locateanything_mlx",
            nodes={
                "visual_1": StructureNode(
                    node_id="visual_1",
                    path="visual/1",
                    parent_id=None,
                    bounds=(100, 120, 900, 260),
                    role="input",
                    structure_kind="visual",
                    source_provider="locateanything_mlx",
                    sensitivity_tags=["otp"],
                )
            },
        ),
        mark_registry=registry,
    )

    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "target_object_id": "obj_1"},
        mark_registry=registry,
        object_registry=objects,
        screen_id="screen-1",
        screen_binding=_binding(registry, objects),
    )

    assert action["action"] == "Take_over"
