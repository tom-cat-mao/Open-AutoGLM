"""Harness-side grounding from IntentIR to canonical executable ActionIR."""

from __future__ import annotations

import re
from typing import Any

from phone_agent.actions.adapter import ActionAdapterError, _canonical_action_name
from phone_agent.actions.ir import is_intent_dict
from phone_agent.actions.selectors import validate_object_filter
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.config.policy import DEFAULT_SAFETY_POLICY
from phone_agent.graph.marks import (
    MARK_CONFIDENCE_THRESHOLD,
    PERCEPTUAL_HASH_THRESHOLD,
    SAFE_MARK_ID_RE,
    MarkRegistry,
    hash_hamming_distance,
)
from phone_agent.graph.objects import (
    ObjectRegistry,
    ScreenObject,
    object_selected_evidence,
)
from phone_agent.grounding.provider import ScreenBinding


class GroundingError(ValueError):
    """Grounding failure with stable code for trace/eval."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


INTENT_ALLOWED_FIELDS = {
    "_metadata",
    "action",
    "target_mark_id",
    "target_object_id",
    "ordinal",
    "object_role",
    "object_filter",
    "target_role",
    "target_text_hint",
    "scope_mark_id",
    "requires_grounding",
    "text",
    "message",
    "app",
    "duration",
}

SAFE_OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def validate_intent(intent: dict[str, Any]) -> dict[str, Any]:
    if not is_intent_dict(intent):
        raise GroundingError("invalid_intent", "intent metadata must be intent")
    extras = set(intent) - INTENT_ALLOWED_FIELDS
    if extras:
        raise GroundingError("unsafe_value", f"unsupported intent fields: {sorted(extras)}")
    if "target_mark_id" in intent:
        if not isinstance(intent["target_mark_id"], str):
            raise GroundingError("unsafe_value", "target_mark_id must be a string")
        if not SAFE_MARK_ID_RE.fullmatch(intent["target_mark_id"]):
            raise GroundingError("unsafe_value", "target_mark_id contains unsafe characters")
    if "scope_mark_id" in intent:
        if not isinstance(intent["scope_mark_id"], str):
            raise GroundingError("unsafe_value", "scope_mark_id must be a string")
        if not intent["scope_mark_id"].strip():
            raise GroundingError("missing_field", "scope_mark_id must be non-empty")
        if not SAFE_MARK_ID_RE.fullmatch(intent["scope_mark_id"]):
            raise GroundingError("unsafe_value", "scope_mark_id contains unsafe characters")
    if "target_object_id" in intent:
        if not isinstance(intent["target_object_id"], str) or not intent["target_object_id"].strip():
            raise GroundingError("unsafe_value", "target_object_id must be a non-empty string")
        if not SAFE_OBJECT_ID_RE.fullmatch(intent["target_object_id"]):
            raise GroundingError("unsafe_value", "target_object_id contains unsafe characters")
    if "object_role" in intent:
        if not isinstance(intent["object_role"], str) or not intent["object_role"].strip():
            raise GroundingError("unsafe_value", "object_role must be a non-empty string")
    if "ordinal" in intent:
        if not isinstance(intent["ordinal"], int) or isinstance(intent["ordinal"], bool) or intent["ordinal"] <= 0 or intent["ordinal"] > 100:
            raise GroundingError("unsafe_value", "ordinal must be a positive integer <= 100")
    if "object_filter" in intent:
        try:
            intent["object_filter"] = validate_object_filter(intent["object_filter"])
        except ValueError as exc:
            raise GroundingError("unsafe_value", str(exc)) from exc
    for key in ("target_role", "target_text_hint", "text", "message", "app", "duration"):
        if key in intent and not isinstance(intent[key], str):
            raise GroundingError("unsafe_value", f"{key} must be a string")
    if "requires_grounding" in intent and not isinstance(intent["requires_grounding"], bool):
        raise GroundingError("unsafe_value", "requires_grounding must be a boolean")
    action = intent.get("action")
    if action is not None:
        try:
            intent["action"] = _canonical_action_name(action)
        except ActionAdapterError as exc:
            raise GroundingError("unknown_action", str(exc)) from exc
    return intent


def ground_intent_to_action(
    intent: dict[str, Any], *, mark_registry: MarkRegistry | dict[str, Any] | None, screen_id: str | None,
    screen_binding: ScreenBinding | None = None, timeout: float | None = None,
    grounding_metadata: dict[str, Any] | None = None,
    object_registry: ObjectRegistry | dict[str, Any] | None = None,
    invalidated_mark_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    """Compile a validated IntentIR dict into canonical ActionIR dict."""

    intent = validate_intent(dict(intent))
    registry = mark_registry if isinstance(mark_registry, MarkRegistry) else MarkRegistry.from_dict(mark_registry)
    invalidated = {str(mark_id) for mark_id in (invalidated_mark_ids or [])}
    action_name = intent.get("action")
    if not action_name:
        raise GroundingError("missing_field", "intent requires action")
    action_name = _canonical_action_name(action_name)

    if action_name == "Locate":
        # F1: Locate is an internal visual-search capability, not a device
        # action. It must pass through to the execute-node internal dispatch
        # untouched (grounding never resolves it to a mark): the model asks
        # "where is X?" and the harness answers by running the visual provider
        # against the current screenshot.
        hint = intent.get("target_text_hint")
        if not isinstance(hint, str) or not hint.strip():
            raise GroundingError("missing_field", "Locate requires target_text_hint")
        locate_action: dict[str, Any] = {
            "_metadata": "do",
            "action": "Locate",
            "target_text_hint": hint,
        }
        # S1/S4: an optional scope mark must exist in the CURRENT registry and
        # must not be invalidated (a locate_* mark invalidated after a failed
        # tap cannot act as a trusted container region either). The format is
        # enforced by validate_intent; existence is enforced here where the
        # registry is available — both fail closed through the standard
        # grounding error path (replan), never a silent full-frame fallback.
        scope_mark_id = intent.get("scope_mark_id")
        if scope_mark_id is not None:
            if registry is None or registry.get(scope_mark_id) is None:
                raise GroundingError(
                    "scope_mark_unknown",
                    f"Locate scope mark not in registry: {scope_mark_id}",
                )
            if str(scope_mark_id) in invalidated:
                raise GroundingError(
                    "mark_invalidated",
                    f"Locate scope mark has been invalidated: {scope_mark_id}",
                )
            locate_action["scope_mark_id"] = scope_mark_id
        try:
            return validate_action(locate_action)
        except ActionValidationError as exc:
            raise GroundingError(exc.code, str(exc)) from exc

    mark_id = intent.get("target_mark_id")
    if not mark_id and _has_object_selector(intent):
        registry = _require_mark_registry(registry)
        object_reg = object_registry if isinstance(object_registry, ObjectRegistry) else ObjectRegistry.from_dict(object_registry)
        selected_object = _resolve_object_selector(
            intent,
            object_registry=object_reg,
            mark_registry=registry,
            screen_id=screen_id,
            screen_binding=screen_binding,
            grounding_metadata=grounding_metadata,
        )
        mark_id = selected_object.primary_mark_id
        intent = {
            **intent,
            "target_mark_id": mark_id,
            "_selected_object_evidence": selected_object.sensitivity_evidence_summary or selected_object.evidence_summary,
            "_selected_object_sensitivity_tags": selected_object.sensitivity_tags,
        }
        if grounding_metadata is not None:
            grounding_metadata["selected_object"] = {
                "object_type": selected_object.object_type,
                "primary_mark_id": selected_object.primary_mark_id,
                "list_id": selected_object.list_id,
                "ordinal_index": selected_object.ordinal_index,
                "sensitivity_route": None,
            }
            grounding_metadata["selected_object_evidence"] = object_selected_evidence(selected_object)
    if mark_id:
        if registry is None or not registry.marks:
            raise GroundingError("mark_unavailable", "no MarkRegistry available for mark intent")
        _validate_mark_binding(registry, screen_id=screen_id, screen_binding=screen_binding, grounding_metadata=grounding_metadata)
        mark = registry.get(mark_id)
        if mark is None:
            raise GroundingError("unknown_mark", f"unknown mark: {mark_id}")
        if str(mark_id) in invalidated:
            raise GroundingError(
                "mark_invalidated",
                f"mark has been invalidated after a failed tap: {mark_id}",
            )
        if screen_id and mark.screen_id != registry.screen_id:
            raise GroundingError("stale_mark", "mark belongs to another screen")
        if mark.confidence < MARK_CONFIDENCE_THRESHOLD:
            raise GroundingError("low_confidence", "mark confidence is below threshold")
        _validate_mark_semantics(intent, mark)
        sensitivity = _mark_sensitivity(intent, mark)
        if grounding_metadata is not None and grounding_metadata.get("selected_object"):
            grounding_metadata["selected_object"]["sensitivity_route"] = sensitivity
        if (
            grounding_metadata is not None
            and not grounding_metadata.get("selected_object_evidence")
            and object_registry is not None
        ):
            bound_object = _resolve_object_for_mark(mark_id, object_registry)
            if bound_object is not None:
                evidence = object_selected_evidence(bound_object)
                if evidence:
                    grounding_metadata["selected_object_evidence"] = evidence
                    grounding_metadata.setdefault(
                        "selected_object",
                        {
                            "object_type": bound_object.object_type,
                            "primary_mark_id": bound_object.primary_mark_id,
                            "list_id": bound_object.list_id,
                            "ordinal_index": bound_object.ordinal_index,
                            "sensitivity_route": sensitivity,
                        },
                    )
        if sensitivity == "takeover":
            try:
                return validate_action(
                    {
                        "_metadata": "do",
                        "action": "Take_over",
                        "message": "Sensitive mark-grounded action requires takeover",
                    }
                )
            except ActionValidationError as exc:
                raise GroundingError(exc.code, str(exc)) from exc
        if action_name not in {"Tap", "Double Tap", "Long Press"}:
            raise GroundingError("unsupported_intent", "mark grounding currently supports tap-like actions only")
        action: dict[str, Any] = {"_metadata": "do", "action": action_name, "element": [mark.center[0], mark.center[1]]}
        if grounding_metadata is not None:
            grounding_metadata.update(
                {
                    "success": True,
                    "provider": "mark_registry",
                    "screen_id": screen_id,
                    "bbox": list(mark.bbox),
                    "center": [mark.center[0], mark.center[1]],
                    "target": {"mark_id": mark_id},
                }
            )
        if sensitivity == "confirm":
            action["message"] = "Sensitive mark-grounded tap requires confirmation"
        elif "message" in intent:
            action["message"] = intent["message"]
        try:
            return validate_action(action)
        except ActionValidationError as exc:
            raise GroundingError(exc.code, str(exc)) from exc

    if not intent.get("requires_grounding", True):
        return _ground_non_target_intent(intent, action_name)

    raise GroundingError("mark_required", "tap-like intent requires target_mark_id")


def _require_mark_registry(registry: MarkRegistry | None) -> MarkRegistry:
    if registry is None or not registry.marks:
        raise GroundingError("mark_unavailable", "no MarkRegistry available for object intent")
    return registry


def _has_object_selector(intent: dict[str, Any]) -> bool:
    return any(key in intent for key in ("target_object_id", "ordinal", "object_role", "object_filter"))


def _resolve_object_for_mark(
    mark_id: str, object_registry: ObjectRegistry | dict[str, Any] | None
) -> ScreenObject | None:
    """Find the ScreenObject bound to a mark_id (mark-based Tap path).

    Used to backfill object_selected_evidence for mark-based Taps,
    so that GoalEvaluator._check_object_rank can match `ordinal=N` even
    when the model did not use an object selector.
    """
    if object_registry is None:
        return None
    registry = (
        object_registry
        if isinstance(object_registry, ObjectRegistry)
        else ObjectRegistry.from_dict(object_registry)
    )
    if not registry.objects:
        return None
    for obj in registry.objects.values():
        if obj.primary_mark_id == mark_id or mark_id in (obj.atomic_mark_ids or []):
            return obj
    return None


def _resolve_object_selector(
    intent: dict[str, Any],
    *,
    object_registry: ObjectRegistry | None,
    mark_registry: MarkRegistry,
    screen_id: str | None,
    screen_binding: ScreenBinding | None,
    grounding_metadata: dict[str, Any] | None,
) -> ScreenObject:
    if object_registry is None or not object_registry.objects:
        raise GroundingError("object_registry_missing", "no ObjectRegistry available for object selector")
    _validate_object_registry_binding(
        object_registry,
        mark_registry=mark_registry,
        screen_id=screen_id,
        screen_binding=screen_binding,
        grounding_metadata=grounding_metadata,
    )
    candidates: list[ScreenObject]
    target_object_id = intent.get("target_object_id")
    if isinstance(target_object_id, str) and target_object_id.strip():
        obj = object_registry.get(target_object_id)
        if obj is None:
            raise GroundingError("unknown_object", f"unknown object: {target_object_id}")
        _validate_selected_object_constraints(intent, obj)
        candidates = [obj]
    else:
        candidates = _filter_objects(intent, object_registry)
        ordinal = intent.get("ordinal")
        if isinstance(ordinal, int):
            if ordinal <= 0 or ordinal > len(candidates):
                raise GroundingError("ordinal_out_of_range", "object ordinal is outside current list")
            candidates = [candidates[ordinal - 1]]
    if not candidates:
        raise GroundingError("unknown_object", "object selector matched no objects")
    if len(candidates) != 1:
        raise GroundingError("object_ambiguous", "object selector matched multiple objects")
    selected = candidates[0]
    if not selected.primary_mark_id:
        raise GroundingError("object_without_mark", "object is not bound to an atomic mark")
    _validate_visual_object_eligibility(selected)
    mark = mark_registry.get(selected.primary_mark_id)
    if mark is None:
        raise GroundingError("mark_stale", "selected object primary mark is missing")
    if mark.confidence < MARK_CONFIDENCE_THRESHOLD:
        raise GroundingError("mark_low_confidence", "selected object primary mark confidence is below threshold")
    return selected


def _validate_visual_object_eligibility(obj: ScreenObject) -> None:
    if obj.source_kind != "visual":
        return
    if not obj.executable_selector or obj.selector_confidence == "none":
        raise GroundingError("visual_object_not_executable", "visual object is not selector-executable")
    if obj.selector_confidence not in {"weak", "strong"}:
        raise GroundingError("visual_object_not_executable", "visual object selector confidence is missing")
    if not obj.primary_mark_id or len(obj.atomic_mark_ids or []) != 1:
        raise GroundingError("visual_object_ambiguous", "visual object must bind to exactly one atomic mark")


def _validate_selected_object_constraints(intent: dict[str, Any], obj: ScreenObject) -> None:
    role = intent.get("object_role")
    if isinstance(role, str) and role.strip():
        terms = _semantic_terms(role)
        if terms and not any(term in _object_haystack(obj) for term in terms):
            raise GroundingError("object_ambiguous", "target_object_id does not match object_role")
    ordinal = intent.get("ordinal")
    if isinstance(ordinal, int):
        if obj.ordinal_index is None or obj.ordinal_index != ordinal:
            raise GroundingError("object_ambiguous", "target_object_id does not match ordinal")
    object_filter = intent.get("object_filter")
    if isinstance(object_filter, dict):
        try:
            object_filter = validate_object_filter(object_filter)
        except ValueError as exc:
            raise GroundingError("unsafe_value", str(exc)) from exc
        if not _object_matches_filter(obj, object_filter):
            raise GroundingError("object_ambiguous", "target_object_id does not match object_filter")


def _validate_object_registry_binding(
    object_registry: ObjectRegistry,
    *,
    mark_registry: MarkRegistry,
    screen_id: str | None,
    screen_binding: ScreenBinding | None,
    grounding_metadata: dict[str, Any] | None,
) -> None:
    binding_summary = {
        "object_registry_screen_id": object_registry.screen_id,
        "current_screen_id": screen_id,
        "object_set_version": object_registry.object_set_version,
        "structure_topology_digest": object_registry.structure_topology_digest,
        "mark_set_version": object_registry.mark_set_version,
        "current_mark_set_version": mark_registry.mark_set_version,
    }
    if grounding_metadata is not None:
        grounding_metadata["object_binding"] = binding_summary
    if screen_binding is None:
        raise GroundingError("object_stale", "screen binding is required for object selector validation")
    if screen_id and object_registry.screen_id != screen_id:
        raise GroundingError("object_stale", "ObjectRegistry screen does not match current screen")
    if object_registry.mark_set_version and mark_registry.mark_set_version and object_registry.mark_set_version != mark_registry.mark_set_version:
        raise GroundingError("object_stale", "ObjectRegistry mark version does not match current marks")
    semantic_match = bool(
        object_registry.semantic_screen_id
        and object_registry.semantic_screen_id == screen_binding.semantic_screen_id
    )
    if object_registry.semantic_screen_id and not semantic_match:
        raise GroundingError("object_stale", "ObjectRegistry semantic screen does not match current screen")
    if (
        object_registry.structure_topology_digest
        and screen_binding.structure_topology_digest
        and object_registry.structure_topology_digest != screen_binding.structure_topology_digest
    ):
        raise GroundingError("object_stale", "ObjectRegistry topology does not match current screen")
    if (
        object_registry.object_set_version
        and screen_binding.object_set_version
        and object_registry.object_set_version != screen_binding.object_set_version
    ):
        raise GroundingError("object_stale", "ObjectRegistry version does not match current screen")


def _filter_objects(intent: dict[str, Any], object_registry: ObjectRegistry) -> list[ScreenObject]:
    objects = list(object_registry.objects.values())
    role = intent.get("object_role")
    if isinstance(role, str) and role.strip():
        terms = _semantic_terms(role)
        objects = [
            obj
            for obj in objects
            if any(term in _object_haystack(obj) for term in terms)
        ]
    object_filter = intent.get("object_filter")
    if isinstance(object_filter, dict):
        try:
            object_filter = validate_object_filter(object_filter)
        except ValueError as exc:
            raise GroundingError("unsafe_value", str(exc)) from exc
        objects = [obj for obj in objects if _object_matches_filter(obj, object_filter)]
    return sorted(
        objects,
        key=lambda obj: (
            obj.list_id or "zz",
            obj.ordinal_index if obj.ordinal_index is not None else 10_000,
            obj.object_id,
        ),
    )


def _object_matches_filter(obj: ScreenObject, object_filter: dict[str, str]) -> bool:
    for key, value in object_filter.items():
        lowered = value.casefold()
        if key == "object_type" and obj.object_type.casefold() != lowered:
            return False
        if key == "role" and lowered not in str(obj.role or "").casefold():
            return False
        if key == "source" and obj.source.casefold() != lowered:
            return False
        if key == "list_id" and obj.list_id != value:
            return False
        if key == "title_hash_prefix" and not str(obj.title_hash or "").startswith(lowered):
            return False
        if key == "text_hash_prefix" and not str(obj.text_hash or "").startswith(lowered):
            return False
        if key == "resource_id_hash_prefix" and not str(obj.resource_id_hash or "").startswith(lowered):
            return False
        if key == "lineage_hash_prefix" and not str(obj.lineage_hash or "").startswith(lowered):
            return False
    return True


def _object_haystack(obj: ScreenObject) -> str:
    return " ".join(
        str(value or "").casefold()
        for value in (
            obj.object_type,
            obj.role,
            obj.source,
            obj.list_id,
            obj.evidence_summary,
        )
    )


def _validate_mark_binding(
    registry: MarkRegistry,
    *,
    screen_id: str | None,
    screen_binding: ScreenBinding | None,
    grounding_metadata: dict[str, Any] | None,
) -> None:
    if screen_id and registry.screen_id == screen_id:
        return
    binding_summary: dict[str, Any] = {
        "registry_screen_id": registry.screen_id,
        "current_screen_id": screen_id,
        "semantic_screen_id": registry.semantic_screen_id,
        "mark_set_version": registry.mark_set_version,
        "perceptual_hash": registry.perceptual_hash,
    }
    if screen_binding is None:
        if grounding_metadata is not None:
            grounding_metadata["binding"] = binding_summary
        raise GroundingError("screen_binding_missing", "screen binding is required for stale mark validation")
    semantic_match = bool(registry.semantic_screen_id and registry.semantic_screen_id == screen_binding.semantic_screen_id)
    topology_match = bool(registry.mark_set_version and registry.mark_set_version == screen_binding.mark_set_version)
    distance = hash_hamming_distance(registry.perceptual_hash, screen_binding.perceptual_hash)
    hash_match = distance is not None and distance <= PERCEPTUAL_HASH_THRESHOLD
    binding_summary.update(
        {
            "current_semantic_screen_id": screen_binding.semantic_screen_id,
            "current_mark_set_version": screen_binding.mark_set_version,
            "current_perceptual_hash": screen_binding.perceptual_hash,
            "perceptual_distance": distance,
            "perceptual_threshold": PERCEPTUAL_HASH_THRESHOLD,
            "semantic_match": semantic_match,
            "topology_match": topology_match,
            "hash_match": hash_match,
        }
    )
    if grounding_metadata is not None:
        grounding_metadata["binding"] = binding_summary
    if not semantic_match:
        raise GroundingError("stale_mark", "MarkRegistry semantic screen does not match current screen")
    if not topology_match:
        raise GroundingError("mark_topology_mismatch", "MarkRegistry topology changed")
    if not hash_match:
        raise GroundingError("hash_mismatch", "MarkRegistry perceptual hash does not match current screen")


def _ground_non_target_intent(intent: dict[str, Any], action_name: str) -> dict[str, Any]:
    action: dict[str, Any] = {"_metadata": "do", "action": action_name}
    if action_name in {"Back", "Home"}:
        return validate_action(action)
    if action_name == "Launch" and intent.get("app"):
        action["app"] = intent["app"]
        return validate_action(action)
    if action_name == "Wait" and intent.get("duration"):
        action["duration"] = intent["duration"]
        return validate_action(action)
    if action_name in {"Type", "Type_Name"} and intent.get("text"):
        action["text"] = intent["text"]
        return validate_action(action)
    raise GroundingError("unsupported_intent", "non-target intent is missing required fields")


def _mark_sensitivity(intent: dict[str, Any], mark: Any) -> str | None:
    tags = intent.get("_selected_object_sensitivity_tags")
    if isinstance(tags, list):
        semantic_tags = tuple(str(tag or "") for tag in tags)
    else:
        semantic_tags = ()
    haystack = " ".join(
        str(value or "")
        for value in (
            intent.get("target_role"),
            intent.get("target_text_hint"),
            intent.get("message"),
            intent.get("_selected_object_evidence"),
            getattr(mark, "role", None),
            getattr(mark, "text_summary", None),
        )
    )
    return DEFAULT_SAFETY_POLICY.classify(
        text=haystack, semantic_tags=semantic_tags
    ).route


def _validate_mark_semantics(intent: dict[str, Any], mark: Any) -> None:
    expected_terms = _semantic_terms(intent.get("target_text_hint"))
    expected_role_terms = _semantic_terms(intent.get("target_role"))
    if not expected_terms and not expected_role_terms:
        return

    haystack = _semantic_haystack(mark)
    if expected_role_terms and not any(term in haystack for term in expected_role_terms):
        raise GroundingError("mark_semantic_mismatch", "mark role does not match target_role")

    if expected_terms:
        if any(term in haystack for term in expected_terms):
            return
        if _search_like_terms(expected_terms) and _mark_looks_like_search_target(mark, haystack):
            return
        raise GroundingError("mark_semantic_mismatch", "mark text does not match target_text_hint")


def _semantic_haystack(mark: Any) -> str:
    return " ".join(
        str(value or "").casefold()
        for value in (
            getattr(mark, "role", None),
            getattr(mark, "text_summary", None),
            getattr(mark, "source", None),
        )
    )


def _semantic_terms(value: Any) -> list[str]:
    text = str(value or "").casefold()
    if not text.strip():
        return []
    raw_tokens = [token for token in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", text) if token]
    terms: list[str] = []
    for token in raw_tokens:
        if len(token) >= 2:
            terms.append(token)
        if any("\u4e00" <= char <= "\u9fff" for char in token):
            terms.extend(token[index : index + 2] for index in range(0, max(0, len(token) - 1)))
    return _unique_terms(terms)


def _unique_terms(terms: list[str]) -> list[str]:
    result: list[str] = []
    for term in terms:
        if len(term) >= 2 and term not in result:
            result.append(term)
    return result[:12]


def _search_like_terms(terms: list[str]) -> bool:
    search_terms = {"搜索", "搜一", "搜素", "search", "query", "input", "输入"}
    return any(term in search_terms for term in terms)


def _mark_looks_like_search_target(mark: Any, haystack: str) -> bool:
    if any(term in haystack for term in ("search", "搜索", "edittext", "input")):
        return True
    bbox = getattr(mark, "bbox", None)
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    try:
        width = float(bbox[2]) - float(bbox[0])
        height = float(bbox[3]) - float(bbox[1])
        center_y = (float(bbox[1]) + float(bbox[3])) / 2
    except (TypeError, ValueError):
        return False
    return width >= 350 and height <= 100 and center_y <= 180
