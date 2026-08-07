"""Execute node: execute action → strip images → append assistant message."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.capability import ToolCapability, get_tool_capability
from phone_agent.actions.receipt import ActionReceipt
from phone_agent.actions.result import ActionResult
from phone_agent.actions.gesture import compile_action_to_gesture
from phone_agent.actions.safety import decide_safety
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.graph.context import (
    REPEATED_ACTION_THRESHOLD,
    action_point,
    action_target_center,
    action_text_identity,
    build_action_outcome_summary,
    consecutive_no_effect_count,
    build_skinny_trajectory_line,
    context_enabled,
    get_context_mode,
    locate_hint_digest,
    sanitize_context_payload,
    repeated_action_key,
    state_surface_identity,
    _trajectory_step_index,
    update_gui_memory,
)
from phone_agent.graph.tools import dispatch_tool
from phone_agent.graph.tools.locate import locate_target, trace_safe_payload
from phone_agent.graph.tools.runtime import (
    reset_tool_app_learning,
    reset_tool_trace_emitter,
    set_tool_app_learning,
    set_tool_trace_emitter,
)
from phone_agent.graph.goal import finish_claim_summary
from phone_agent.graph.marks import MarkRegistry
from phone_agent.graph.trace import emit_trace
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def _replace_last_user_message(messages: list[dict], text: str) -> None:
    """Replace the last user message with a text-only (skinny) message.

    Used by the trajectory conversion: the step's fat observation tail is
    replaced by its one-line record once the result is known. No-op when no
    user message exists (e.g. a resume path that already stripped it).
    """
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            messages[index] = {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            }
            return


def _skinny_for_step(
    state: "AgentState",
    result: dict | None = None,
    *,
    placeholder_message: str | None = None,
) -> str:
    """Build the trajectory row for the step whose fat tail is being replaced.

    The step index is derived from the assistant rows already in the history,
    so it stays correct on resume/confirm-reject paths where step_count may
    have been incremented without a completed step.
    """
    lang = state.get("lang", "cn")
    messages = list(state.get("messages") or [])
    if placeholder_message is not None:
        result = {"success": None, "message": placeholder_message}

    return build_skinny_trajectory_line(
        state,
        step_index=_trajectory_step_index(messages),
        result=result,
        lang=lang,
    )


def _strip_think_block(content: str) -> str:
    """Remove one ``<think>...</think>`` (or the historical ``<think...>...``
    placeholder) section from an assistant message.

    F10: form-level text processing, same spirit as P0 #3's image stripping —
    called right before the new assistant message is appended, so every
    existing assistant row is reduced to its ``<answer>`` part and history
    stays bounded; the just-appended assistant keeps its think block (its
    reasoning is visible to the model for exactly one plan step). No think
    block → returned byte-for-byte unchanged; the answer part is never
    touched. A side benefit: sensitive content that was only inside
    historical thinking also disappears from subsequent requests.
    """

    for open_tag, close_tag in (
        ("<think...>", "</think...>"),
        ("<think>", "</think>"),
    ):
        start = content.find(open_tag)
        if start < 0:
            continue
        end = content.find(close_tag, start + len(open_tag))
        if end >= 0:
            return content[:start] + content[end + len(close_tag):]
    return content

def _strip_think_from_history(messages: list[dict]) -> None:
    """In-place: drop think blocks from every existing assistant message.

    F10: this runs right before the new assistant message is appended, so the
    "newest" assistant (which keeps its think block) is not in ``messages``
    yet — every historical assistant row is reduced to its answer part.
    """

    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        stripped = _strip_think_block(content)
        if stripped != content:
            messages[index] = {**message, "content": stripped}


def _strip_and_append(
    messages: list[dict],
    thinking: str,
    action_raw: str,
    skinny_line: str | None = None,
) -> list[dict]:
    """Strip images from last user message and append assistant message.

    P4 #2: when a real thinking trace was captured (reasoning_content streamed
    by the provider), it is wrapped in proper ``<think>...</think>`` tags so the
    model sees its own reasoning in history. Providers without a reasoning
    channel keep the historical ``<think...>...</think...>`` placeholder
    byte-for-byte, so no-reasoning runs are format-identical to before.

    P-E: when ``skinny_line`` is provided the step's fat observation tail is
    replaced by its one-line trajectory record (the image is dropped with it,
    which is P0 #3's stronger form); otherwise the historical image is simply
    stripped as before.

    F10: on the full-rebuild path every historical assistant message (except
    the one appended here) loses its ``<think>...</think>`` section — only the
    answer text remains — so assistant history stays bounded across long runs.
    """
    if messages:
        if skinny_line is not None:
            _replace_last_user_message(messages, skinny_line)
        else:
            messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])
    if thinking and thinking.strip():
        assistant_content = f"<think>{thinking}</think>\n<answer>{action_raw}</answer>"
    else:
        assistant_content = (
            f"<think...>{thinking}</think...>\n<answer>{action_raw}</answer>"
        )
    _strip_think_from_history(messages)
    messages.append(
        MessageBuilder.create_assistant_message(assistant_content)
    )
    return messages


def _layered_error(layer: str, code: str, *, recoverable: bool = False, retry_policy: str = "none") -> dict:
    """Build stable layered error fields for terminal execute failures."""

    return {
        "error_layer": layer,
        "error_code": code,
        "recoverable": recoverable,
        "retry_policy": retry_policy,
    }


def _receipt_for_result(
    capability: ToolCapability,
    result: ActionResult,
    *,
    correlation_id: str | None,
) -> ActionReceipt:
    """Describe dispatch only; the receipt never claims a UI transition."""

    dispatch_status = "accepted" if result.success else "rejected"
    return ActionReceipt.create(
        capability,
        dispatch_status,
        correlation_id=correlation_id,
        side_effect_receipt={"tool_dispatch_status": dispatch_status},
    )


def _dispatch_with_receipt(
    action: dict,
    capability: ToolCapability,
    *,
    screen_width: int,
    screen_height: int,
    device_id: str | None,
    device_factory: object | None,
    correlation_id: str | None,
    verbose: bool,
    config: RunnableConfig | None = None,
    state: dict | None = None,
) -> tuple[ActionResult, ActionReceipt, dict]:
    """Dispatch an implemented capability and return receipt plus compatibility result."""

    app_learning = None
    if config is not None:
        configurable = config.get("configurable", {}) if config else {}
        app_learning = configurable.get("app_learning_context")

    def _trace_launch(event: str, payload: dict) -> None:
        if state is not None:
            emit_trace(config, state, "execute", event, payload)

    token_learning = set_tool_app_learning(app_learning)
    token_emitter = set_tool_trace_emitter(_trace_launch)
    try:
        result = dispatch_tool(
            action,
            screen_width,
            screen_height,
            device_id,
            device_factory=device_factory,
        )
    except Exception as exc:
        if verbose:
            traceback.print_exc()
        result = ActionResult(
            success=False, should_finish=True, message=f"Action failed: {exc}"
        )
        receipt = ActionReceipt.create(
            capability,
            "unknown",
            correlation_id=correlation_id,
            side_effect_receipt={"tool_dispatch_status": "unknown"},
        )
        return result, receipt, _layered_error("execution", "dispatch_failed")
    finally:
        reset_tool_trace_emitter(token_emitter)
        reset_tool_app_learning(token_learning)
    return (
        result,
        _receipt_for_result(
            capability, result, correlation_id=correlation_id
        ),
        {},
    )


def _receipt_ledger_update(
    state: "AgentState", action_name: str | None, receipt: ActionReceipt
) -> dict:
    """Append dispatch evidence without asserting transition or Goal progress."""

    entry = {
        "record_type": "action_receipt",
        "action": action_name,
        "receipt": receipt.to_dict(),
    }
    return {"action_ledger": (list(state.get("action_ledger") or []) + [entry])[-10:]}


def execute_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Execute node: run action, strip images, append assistant message.

    Uses dispatch_tool for action execution.

    Corresponds to agent.py:185-243 (execute + strip images + append context + finish check).
    """
    configurable = config.get("configurable", {})
    verbose = configurable.get("verbose", True)
    device_factory = configurable.get("device_factory")

    action_parsed = state.get("action_parsed")
    messages = list(state["messages"])  # copy
    thinking = state.get("thinking", "")
    action_raw = state.get("action_raw", "")
    screen_width = state["screen_width"]
    screen_height = state["screen_height"]
    device_id = state.get("device_id")
    context_mode = get_context_mode(state, config)
    correlation_id = configurable.get("correlation_id")

    def _context_update(result_dict: dict, state_overrides: dict | None = None) -> dict:
        if not context_enabled(context_mode):
            return {"context_mode": context_mode}
        outcome_state = {
            **state,
            **(state_overrides or {}),
            "action_result": result_dict,
            "current_app": state.get("current_app") or "unknown",
            "context_mode": context_mode,
        }
        return {
            "context_mode": context_mode,
            "action_outcome_summary": build_action_outcome_summary(outcome_state),
        }

    # Plan-stage parse/model failures are terminal and must not be converted into
    # a successful finish or a generic execute error.
    if state.get("finished") and state.get("error"):
        result_dict = state.get("action_result") or {
            "success": False,
            "should_finish": True,
            "message": state.get("error"),
        }
        emit_trace(
            config,
            state,
            "execute",
            "execute_error",
            {"message": state.get("error"), "failure_cause": state.get("failure_cause")},
        )
        return {
            "action_result": result_dict,
            "finished": True,
            "error": state.get("error"),
            "failure_cause": state.get("failure_cause"),
            "error_layer": state.get("error_layer"),
            "error_code": state.get("error_code"),
            "recoverable": state.get("recoverable"),
            "retry_policy": state.get("retry_policy"),
            **_context_update(result_dict),
        }

    # 1. Check action_parsed
    if action_parsed is None:
        emit_trace(config, state, "execute", "execute_error", {"message": "No action to execute"})
        return {
            "action_result": ActionResult(
                success=False, should_finish=True, message="No action to execute"
            ).__dict__,
            "finished": True,
            "error": "No action to execute",
            **_layered_error("execution", "missing_action"),
            **_context_update({"success": False, "should_finish": True, "message": "No action to execute"}),
        }

    try:
        action_parsed = validate_action(action_parsed)
    except ActionValidationError as exc:
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Invalid action: {exc.code}: {exc}",
        )
        emit_trace(
            config,
            state,
            "execute",
            "execute_error",
            {"message": result.message, "failure_cause": "action_validation_failed", "validation_error_code": exc.code},
        )
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "action_validation_failed",
            **_layered_error("validation", exc.code),
            **_context_update(result.__dict__),
        }

    candidate_repeat = {
        "action": action_parsed.get("action"),
        "target_center": action_target_center(state, action_parsed),
        "surface": state_surface_identity(state),
        "text_identity": action_text_identity(action_parsed.get("text")),
        # H4: Locate has no target center; the repeat identity comes from the
        # sanitized hint digest, so repeated locate queries on one surface are
        # counted by the same guard.
        "hint_digest": locate_hint_digest(action_parsed.get("target_text_hint")),
        # F4: Launch has no target center either; the repeat identity comes
        # from the sanitized app digest (see _launch_repeat_key), written at
        # digest time so raw terms never enter state (P0 #10).
        "app": locate_hint_digest(action_parsed.get("app")),
        # Swipe geometry (P3 #3): Swipe has no target center, so the repeat
        # guard keys on start/end instead of center.
        "start": action_point(action_parsed.get("start")),
        "end": action_point(action_parsed.get("end")),
    }
    repeat_key = repeated_action_key(candidate_repeat)
    tried_actions = (state.get("gui_memory") or {}).get("tried_actions") or []
    # Effect-guards: the guard counts CONSECUTIVE no-effect attempts of the
    # same repeat key, not raw attempts. Any same-key attempt that had an
    # effect (screen change / new criterion observation / succeeded verdict)
    # resets the streak, so a slider dragged 08:00 -> 07:00 -> 06:00 with the
    # panel value changing every time is never blocked; a true dead loop
    # (same target, screen unchanged) is still caught at the threshold. Old
    # entries without ``had_effect`` count as no-effect (legacy compat).
    prior_repeat_count = (
        consecutive_no_effect_count(tried_actions, repeat_key)
        if repeat_key is not None
        else 0
    )
    if prior_repeat_count >= REPEATED_ACTION_THRESHOLD:
        action_name = str(action_parsed.get("action") or "")
        capability = get_tool_capability(action_name)
        repeat_count = prior_repeat_count + 1
        message = (
            "Repeated target action rejected; choose a different target or strategy."
            if state.get("lang") == "en"
            else "重复目标动作已被拒绝；请改换目标或策略。"
        )
        result = ActionResult(success=False, should_finish=False, message=message)
        receipt = (
            ActionReceipt.create(
                capability,
                "rejected",
                correlation_id=correlation_id,
                side_effect_receipt={
                    "reason_code": "repeated_target_loop",
                    "repeat_count": repeat_count,
                },
            )
            if capability is not None
            else None
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        emit_trace(
            config,
            state,
            "execute",
            "execute_result",
            {
                "action": action_name,
                "result": result.__dict__,
                "action_receipt": receipt.to_dict() if receipt else None,
                "repeated_action_detected": True,
                "repeat_rejected": True,
                "repeat_count": repeat_count,
                "consecutive_no_effect": prior_repeat_count,
            },
        )
        # The rejected action is a system decision, not an action failure: it must
        # NOT enter failure_memory (that path runs through reflect, which is
        # skipped) — but it MUST still be counted in gui_memory.tried_actions,
        # which is the repeat guard's counting source. Without the write, a model
        # that keeps proposing the same target would see a constant repeat_count
        # instead of an escalating one. `update_gui_memory` is the same writer
        # reflect uses, so the recorded entry has the identical shape.
        # Effect-guards: the rejected attempt had no effect by construction
        # (it never dispatched), so had_effect=False keeps the streak rising.
        rejected_memory = update_gui_memory(
            {
                **state,
                "action_result": result.__dict__,
                "failure_cause": "repeated_action",
            },
            current_app=state.get("current_app") or "unknown",
            screen_id=None,
            had_effect=False,
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict() if receipt else None,
            **(
                _receipt_ledger_update(state, action_name, receipt)
                if receipt is not None
                else {}
            ),
            "gui_memory": rejected_memory,
            "messages": messages,
            "finished": False,
            "failure_cause": "repeated_action",
            "repeated_action_detected": True,
            "repeat_rejected": True,
            **_context_update(
                result.__dict__,
                {
                    "action_receipt": receipt.to_dict() if receipt else None,
                    "failure_cause": "repeated_action",
                },
            ),
        }

    pending_execute_confirmed = state.get("pending_execute") and state.get("interrupt_result") is True
    safety_decision = decide_safety(action_parsed)
    safety_route = "approved" if pending_execute_confirmed else safety_decision.route
    safety_reason = "confirmation_accepted" if pending_execute_confirmed else safety_decision.reason
    emit_trace(
        config,
        state,
        "execute",
        "safety_decision",
        {
            "route": safety_route,
            "interrupt_type": safety_decision.interrupt_type,
            "reason": safety_reason,
            "confirmation_accepted": pending_execute_confirmed,
            "decision": safety_decision.sanitized_trace_payload or {},
        },
    )
    if safety_route == "rejected":
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Action rejected by safety gate: {safety_decision.reason}",
        )
        emit_trace(config, state, "execute", "execute_error", {"message": result.message})
        capability = get_tool_capability(str(action_parsed.get("action")))
        receipt = (
            ActionReceipt.create(
                capability,
                "rejected",
                correlation_id=correlation_id,
                side_effect_receipt={"reason_code": "action_safety_rejected"},
            )
            if capability is not None
            else None
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict() if receipt else None,
            **(
                _receipt_ledger_update(
                    state, action_parsed.get("action"), receipt
                )
                if receipt
                else {}
            ),
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "action_safety_rejected",
            **_layered_error("safety", "action_safety_rejected"),
            **_context_update(
                result.__dict__,
                {"action_receipt": receipt.to_dict() if receipt else None},
            ),
        }

    # Internal capability dispatch (F1 locate): Locate runs in-process against
    # the CURRENT observation — no device side effect, no Goal progress, no
    # reobservation (capability observation_effect="none"/can_advance_goal=False
    # so after_execute routes back to plan). This branch MUST sit after the
    # safety gate and before the unknown-action terminal branch so an internal
    # intent is never intercepted by the `_metadata != "do"` guard. Failure
    # writes failure_cause here in execute (replan skips reflect, so no other
    # node would record it).
    if action_parsed.get("action") == "Locate":
        locate_capability = get_tool_capability("Locate")
        hint = str(action_parsed.get("target_text_hint") or "")
        outcome = locate_target(state, config)
        emit_trace(
            config,
            state,
            "execute",
            "locate_result",
            trace_safe_payload(outcome, hint_length=len(hint)),
        )
        # Effect-guards: the locate budget is now a pure runaway fuse
        # (LOCATE_MAX_PER_RUN=20, normal runs never hit it). Successful locates
        # are progress and no longer fail-closed the run after 3 queries;
        # repeated failed/effect-less locates are handled by the
        # consecutive-no-effect repeat guard instead. The budget counter still
        # advances on every attempted locate (success or failure); only the
        # hard budget gate itself (locate_budget_exhausted) does not advance —
        # it is the refusal of further attempts, not one.
        locate_count = int(state.get("locate_count") or 0)
        next_locate_count = (
            locate_count
            if outcome.failure_code == "locate_budget_exhausted"
            else locate_count + 1
        )
        if not outcome.success:
            failure_code = outcome.failure_code or "locate_failed"
            result = ActionResult(
                success=False,
                should_finish=False,
                message=f"Locate failed: {failure_code}: {outcome.message or ''}",
            )
            receipt = ActionReceipt.create(
                locate_capability,
                "rejected",
                correlation_id=correlation_id,
                side_effect_receipt={
                    "tool_dispatch_status": "rejected",
                    "reason_code": failure_code,
                },
            )
            messages = _strip_and_append(
                messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
            )
            emit_trace(
                config,
                state,
                "execute",
                "execute_error",
                {"message": result.message, "failure_cause": failure_code},
            )
            # R2: failed locates still count in gui_memory.tried_actions (the
            # repeat guard's counting source) — a failed locate that replans
            # skips reflect, so without this write the same-query repeat guard
            # would never escalate on the failure path either.
            # Effect-guards: a failed locate had no effect, so had_effect=False
            # feeds the consecutive-no-effect streak.
            locate_memory = update_gui_memory(
                {
                    **state,
                    "action_result": result.__dict__,
                    "failure_cause": failure_code,
                },
                current_app=state.get("current_app") or "unknown",
                screen_id=None,
                had_effect=False,
            )
            return {
                "action_result": result.__dict__,
                "action_receipt": receipt.to_dict(),
                **_receipt_ledger_update(state, "Locate", receipt),
                "gui_memory": locate_memory,
                "messages": messages,
                "finished": False,
                "failure_cause": failure_code,
                "grounding_error": failure_code,
                "grounding_failure_code": failure_code,
                "suggested_strategy": "reobserve",
                # H2: failures count against the per-run locate budget.
                "locate_count": next_locate_count,
                # H1: drift is diagnostics only; never a rejection.
                "observation_drifted": outcome.observation_drifted,
                "context_mode": context_mode,
                **_context_update(
                    result.__dict__,
                    {
                        "action_receipt": receipt.to_dict(),
                        "failure_cause": failure_code,
                        "grounding_failure_code": failure_code,
                        "locate_count": next_locate_count,
                    },
                ),
            }
        registry = MarkRegistry.from_dict(state.get("mark_registry"))
        if registry is None:
            result = ActionResult(
                success=False,
                should_finish=False,
                message="Locate failed: mark registry unavailable",
            )
            receipt = ActionReceipt.create(
                locate_capability,
                "rejected",
                correlation_id=correlation_id,
                side_effect_receipt={
                    "tool_dispatch_status": "rejected",
                    "reason_code": "registry_missing",
                },
            )
            messages = _strip_and_append(
                messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
            )
            locate_memory = update_gui_memory(
                {
                    **state,
                    "action_result": result.__dict__,
                    "failure_cause": "registry_missing",
                },
                current_app=state.get("current_app") or "unknown",
                screen_id=None,
                had_effect=False,
            )
            return {
                "action_result": result.__dict__,
                "action_receipt": receipt.to_dict(),
                **_receipt_ledger_update(state, "Locate", receipt),
                "gui_memory": locate_memory,
                "messages": messages,
                "finished": False,
                "failure_cause": "registry_missing",
                "grounding_error": "registry_missing",
                "grounding_failure_code": "registry_missing",
                "locate_count": next_locate_count,
                "observation_drifted": outcome.observation_drifted,
                "context_mode": context_mode,
                **_context_update(
                    result.__dict__,
                    {
                        "action_receipt": receipt.to_dict(),
                        "failure_cause": "registry_missing",
                        "locate_count": next_locate_count,
                    },
                ),
            }
        # H1 atomic merge: with_extra_marks keeps the screen identity and
        # recomputes mark_set_version; the registry hash is rebound to hash_F
        # (the frame LA actually saw) so the merged mark is never bound to a
        # stale snapshot — drift is carried by observation_drifted instead.
        merged = registry.with_extra_marks([outcome.mark])
        new_registry = MarkRegistry(
            screen_id=merged.screen_id,
            marks=merged.marks,
            semantic_screen_id=merged.semantic_screen_id,
            observation_epoch=merged.observation_epoch,
            mark_set_version=merged.mark_set_version,
            perceptual_hash=merged.perceptual_hash,
            raw_screenshot_hash=outcome.raw_screenshot_hash or merged.raw_screenshot_hash,
        )
        result = ActionResult(
            success=True,
            should_finish=False,
            message=f"Locate registered {outcome.mark.mark_id} from {outcome.provider or 'visual provider'}",
        )
        receipt = ActionReceipt.create(
            locate_capability,
            "accepted",
            correlation_id=correlation_id,
            side_effect_receipt={
                "tool_dispatch_status": "accepted",
                "mark_id": outcome.mark.mark_id,
                "provider": outcome.provider,
                "latency_ms": outcome.latency_ms,
            },
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        # R2: a SUCCESSFUL locate skips reflect (capability routes straight
        # back to plan), so this is the only writer that records the attempt in
        # gui_memory.tried_actions — without it the repeat guard's prior count
        # for the same (Locate, surface, hint_digest) key would stay 0 forever
        # and "同屏同述重复拒绝" would never fire on the success path.
        # Effect-guards: a successful locate is progress (it registered a new
        # executable mark), so had_effect=True — it never feeds the
        # consecutive-no-effect streak. Repeated same-query locates only get
        # blocked when they keep FAILING without effect.
        locate_memory = update_gui_memory(
            {
                **state,
                "action_result": result.__dict__,
                "failure_cause": None,
            },
            current_app=state.get("current_app") or "unknown",
            screen_id=None,
            had_effect=True,
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict(),
            **_receipt_ledger_update(state, "Locate", receipt),
            "gui_memory": locate_memory,
            "messages": messages,
            "finished": False,
            "mark_registry": new_registry.to_dict(),
            "locate_count": next_locate_count,
            "observation_drifted": outcome.observation_drifted,
            "failure_cause": None,
            "grounding_error": None,
            "grounding_failure_code": None,
            "grounding_result": {
                "provider": outcome.provider,
                "mark_id": outcome.mark.mark_id,
                "bbox": [round(v, 1) for v in outcome.mark.bbox],
                "center": [round(v, 1) for v in outcome.mark.center],
            },
            "context_mode": context_mode,
            **_context_update(
                result.__dict__,
                {"action_receipt": receipt.to_dict(), "locate_count": next_locate_count},
            ),
        }

    if action_parsed.get("_metadata") == "finish":
        result = ActionResult(
            success=True,
            should_finish=False,
            message=action_parsed.get("message"),
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        matched_evidence = action_parsed.get("matched_terminal_evidence") or []
        finish_claim = finish_claim_summary(result.message or "")
        if isinstance(matched_evidence, list) and matched_evidence:
            finish_claim["matched_terminal_evidence"] = [str(e) for e in matched_evidence]
        emit_trace(
            config,
            state,
            "execute",
            "execute_finish_pending",
            {"finish_claim": finish_claim, "pending_finish": True, "matched_terminal_evidence": matched_evidence},
        )
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": False,
            "pending_finish": True,
            "finish_claim": finish_claim,
            "finish_validation_status": "pending",
            **_context_update(result.__dict__),
        }

    if action_parsed.get("_metadata") != "do":
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Unknown action type: {action_parsed.get('_metadata')}",
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        emit_trace(config, state, "execute", "execute_error", {"message": result.message})
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "unknown_action_type",
            **_layered_error("validation", "unknown_action_type"),
            **_context_update(result.__dict__),
        }

    # 2. Pending execute branch (BUG 2 fix)
    # If confirm was accepted, execute the pending action directly
    if state.get("pending_execute"):
        if state.get("interrupt_result") is not True:
            result = ActionResult(
                success=False,
                should_finish=True,
                message="Pending sensitive action requires accepted confirmation",
            )
            emit_trace(
                config,
                state,
                "execute",
                "execute_error",
                {"message": result.message, "pending_execute": True},
            )
            return {
                "action_result": result.__dict__,
                "messages": messages,
                "finished": True,
                "error": result.message,
                "pending_execute": False,
                "action_confirmed": False,
                "failure_cause": "confirmation_required",
                **_layered_error("safety", "confirmation_required", recoverable=True, retry_policy="takeover"),
                **_context_update(result.__dict__),
            }
        # CRITICAL-1: do NOT call _strip_and_append again (images already stripped on first pass)
        capability = get_tool_capability(str(action_parsed.get("action")))
        if capability is None or capability.implementation_status != "implemented":
            code = "capability_missing" if capability is None else "capability_unavailable"
            result = ActionResult(
                success=False,
                should_finish=True,
                message=f"Confirmed action cannot dispatch: {code}",
            )
            receipt = (
                ActionReceipt.create(
                    capability,
                    "rejected",
                    correlation_id=correlation_id,
                    side_effect_receipt={"reason_code": code},
                )
                if capability is not None
                else None
            )
            return {
                "action_result": result.__dict__,
                "action_receipt": receipt.to_dict() if receipt else None,
                **(
                    _receipt_ledger_update(
                        state, action_parsed.get("action"), receipt
                    )
                    if receipt
                    else {}
                ),
                "messages": messages,
                "finished": True,
                "error": result.message,
                "pending_execute": False,
                "action_confirmed": False,
                "failure_cause": code,
                **_layered_error("execution", code),
                **_context_update(
                    result.__dict__,
                    {"action_receipt": receipt.to_dict() if receipt else None},
                ),
            }
        result, receipt, execution_error = _dispatch_with_receipt(
            action_parsed,
            capability,
            screen_width=screen_width,
            screen_height=screen_height,
            device_id=device_id,
            device_factory=device_factory,
            correlation_id=correlation_id,
            verbose=verbose,
            config=config,
            state=state,
        )
        emit_trace(
            config,
            state,
            "execute",
            "execute_result",
            {
                "action": action_parsed.get("action"),
                "result": result.__dict__,
                "action_receipt": receipt.to_dict(),
                "pending_execute": True,
            },
        )

        # CRITICAL-2: mark action_confirmed=True (keep action_parsed for reflect)
        # P-E: the confirm first pass already converted the fat tail into a
        # skinny row with a pending label; now that the real result exists,
        # replace that row in place (no second assistant append).
        _replace_last_user_message(
            messages, _skinny_for_step(state, result.__dict__)
        )
        finished = result.should_finish
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict(),
            **_receipt_ledger_update(
                state, action_parsed.get("action"), receipt
            ),
            "messages": messages,  # in-place skinny update, no duplicate append
            "finished": finished,
            "pending_execute": False,
            "action_confirmed": True,
            "pending_interrupt": None,
            "interrupt_result": None,
            **({"error": result.message, "failure_cause": "execution_failed", **execution_error} if execution_error else {}),
            **_context_update(
                result.__dict__, {"action_receipt": receipt.to_dict()}
            ),
        }

    # 3. Human-in-the-Loop checks (Phase 2)
    action_name = action_parsed.get("action")
    if safety_decision.route == "takeover":
        capability = get_tool_capability(str(action_name))
        receipt = (
            ActionReceipt.create(
                capability,
                "accepted",
                correlation_id=correlation_id,
                side_effect_receipt={
                    "delegation_status": "awaiting_acknowledgement"
                },
            )
            if capability is not None
            else None
        )
        result = ActionResult(
            success=False,
            should_finish=False,
            message=action_parsed.get("message", "User intervention required"),
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        emit_trace(
            config,
            state,
            "execute",
            "takeover_interrupt",
            {
                "interrupt_message": action_parsed.get("message", "User intervention required"),
                "safety_reason": safety_decision.reason,
                "action_receipt": receipt.to_dict() if receipt else None,
            },
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict() if receipt else None,
            **(
                _receipt_ledger_update(state, action_name, receipt)
                if receipt
                else {}
            ),
            "messages": messages,
            "pending_interrupt": safety_decision.interrupt_type or "takeover",
            "interrupt_message": action_parsed.get(
                "message", "User intervention required"
            ),
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
            **_context_update(
                result.__dict__,
                {"action_receipt": receipt.to_dict() if receipt else None},
            ),
        }

    if safety_decision.route == "confirm":
        pending_label = (
            "awaiting confirmation" if state.get("lang") == "en" else "待确认"
        )
        messages = _strip_and_append(
            messages,
            thinking,
            action_raw,
            skinny_line=_skinny_for_step(state, placeholder_message=pending_label),
        )
        emit_trace(
            config,
            state,
            "execute",
            "confirm_interrupt",
            {"interrupt_message": action_parsed["message"], "safety_reason": safety_decision.reason},
        )
        return {
            "messages": messages,
            "pending_interrupt": safety_decision.interrupt_type or "confirmation",
            "interrupt_message": action_parsed["message"],
            "pending_execute": True,
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
        }

    capability = get_tool_capability(str(action_name))
    if capability is None:
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Capability declaration missing: {action_name}",
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": None,
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "capability_missing",
            **_layered_error("execution", "capability_missing"),
            **_context_update(result.__dict__, {"action_receipt": None}),
        }

    if capability.implementation_status == "unavailable":
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Capability unavailable: {action_name}",
        )
        receipt = ActionReceipt.create(
            capability,
            "rejected",
            correlation_id=correlation_id,
            side_effect_receipt={"reason_code": "capability_unavailable"},
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        emit_trace(
            config,
            state,
            "execute",
            "capability_rejected",
            {"action": action_name, "action_receipt": receipt.to_dict()},
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict(),
            **_receipt_ledger_update(state, action_name, receipt),
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "capability_unavailable",
            **_layered_error("execution", "capability_unavailable"),
            **_context_update(
                result.__dict__, {"action_receipt": receipt.to_dict()}
            ),
        }

    if capability.implementation_status == "delegated":
        result = ActionResult(
            success=False,
            should_finish=False,
            message=action_parsed.get("message", "User takeover required"),
        )
        receipt = ActionReceipt.create(
            capability,
            "accepted",
            correlation_id=correlation_id,
            side_effect_receipt={"delegation_status": "awaiting_acknowledgement"},
        )
        messages = _strip_and_append(
            messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
        )
        emit_trace(
            config,
            state,
            "execute",
            "delegated_action_interrupt",
            {"action": action_name, "action_receipt": receipt.to_dict()},
        )
        return {
            "action_result": result.__dict__,
            "action_receipt": receipt.to_dict(),
            **_receipt_ledger_update(state, action_name, receipt),
            "messages": messages,
            "pending_interrupt": capability.hitl_policy_id or "takeover",
            "interrupt_message": result.message,
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
            **_context_update(
                result.__dict__, {"action_receipt": receipt.to_dict()}
            ),
        }

    # 4. Execute action via tool dispatch
    gesture_trace = None
    try:
        gesture_trace = compile_action_to_gesture(action_parsed).to_dict()
    except Exception:
        gesture_trace = None
    emit_trace(
        config,
        state,
        "execute",
        "gesture_compiled",
        {"gesture": sanitize_context_payload(gesture_trace, consumer="trace_payload"), "coordinate_space": "relative_0_1000"},
    )
    result, receipt, execution_error = _dispatch_with_receipt(
        action_parsed,
        capability,
        screen_width=screen_width,
        screen_height=screen_height,
        device_id=device_id,
        device_factory=device_factory,
        correlation_id=correlation_id,
        verbose=verbose,
        config=config,
        state=state,
    )
    emit_trace(
        config,
        state,
        "execute",
        "execute_result",
        {
            "action": action_parsed.get("action"),
            "result": result.__dict__,
            "action_receipt": receipt.to_dict(),
        },
    )

    # 5. Strip images and append assistant message
    messages = _strip_and_append(
        messages, thinking, action_raw, skinny_line=_skinny_for_step(state, result.__dict__)
    )

    # 6. Check should_finish
    finished = result.should_finish
    return {
        "action_result": result.__dict__,
        "action_receipt": receipt.to_dict(),
        **_receipt_ledger_update(state, action_name, receipt),
        "messages": messages,
        "finished": finished,
        **({"error": result.message, "failure_cause": "execution_failed", **execution_error} if execution_error else {}),
        **_context_update(
            result.__dict__, {"action_receipt": receipt.to_dict()}
        ),
    }
