"""Control tools: finish / ask_user / take_over.

Per refactor-thin-loop-v2.md §7.3. These tools shape the run outcome rather than
touching the device. HITL interrupts for ``ask_user``/``take_over`` are enforced
by the safety middleware; the tool bodies only record intent / format text.

``finish`` enforces two fail-closed gates and a two-step review (S2 §1):

1. Non-empty ``evidence`` (list what was done + the on-screen proof).
2. TaskDoc guard (``docs/refactor-thin-loop-v2-taskdoc.md`` §2.4): open route
   items block the declaration.

Then, unless ``PHONE_AGENT_FINISH_VERIFY=off`` degrades it to the pre-two-step
single-call landing, ``finish`` is **two-step** (S2 §1): the first call returns a
*review packet* (a cheap L0 world-state mirror built by ``review.py`` — one
``observe()``) and does **not** land ``finished``; the model reflects and calls
``finish(confirm=true)`` to land it. A confirm is only honoured while the review
is still fresh (``screen_seq == finish_review_seq``); any intervening observation
invalidates the mirror and re-emits a fresh packet.

On a fresh confirm, an independent-context **verifier** (S2 §4, ``verify.py``) may
run — for a high-risk goal, a hard-contradiction confirm, or ``FINISH_VERIFY=always``.
A verifier REJECT is returned in-band (``finished`` stays False); the 2nd rejection
escalates to human takeover. The verifier is **fail-open**: any setup/call failure
lands the finish anyway (the L1 two-step already gated it).
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool


def _maybe_verify_finish(session, config):
    """Run the finish verifier when triggered (S2 §4); ``None`` when not triggered.

    Delegates the trigger decision and the independent-context verification to
    ``phone_agent.v2.verify``. Import is local so ``control.py`` stays importable
    (and the two-step finish keeps working) even if the verifier module is absent
    or fails to import — in that case verification is skipped (the L1 two-step
    confirm still landed the finish; fail-open, S2 §4.5).
    """

    try:
        from phone_agent.v2.verify import should_verify_finish, verify_finish
    except Exception:  # noqa: BLE001 - verifier optional; L1 already gated
        return None
    try:
        if not should_verify_finish(session, config):
            return None
        return verify_finish(session, config)
    except Exception:  # noqa: BLE001 - verifier failure is fail-open (§4.5)
        return None


def _handle_reject(session, verdict):
    """Handle a verifier REJECT (S2 §4.3/§4.4): in-band verdict or takeover.

    Increments ``finish_dispute_count``; on the 2nd rejection the run escalates
    to human takeover (L2 -> L3) by setting ``takeover_reason``. Otherwise the
    verdict is returned in-band (a ToolMessage) so the model can keep operating
    or add evidence — ``finished`` stays False.
    """

    from phone_agent.v2.verify import (
        DISPUTE_TAKEOVER_REASON,
        DISPUTE_TAKEOVER_THRESHOLD,
    )

    count = int(getattr(session, "finish_dispute_count", 0) or 0) + 1
    try:
        session.finish_dispute_count = count
    except Exception:  # noqa: BLE001 - best-effort state write
        pass
    if count >= DISPUTE_TAKEOVER_THRESHOLD:
        try:
            session.takeover_reason = DISPUTE_TAKEOVER_REASON
        except Exception:  # noqa: BLE001
            pass
        return (
            f"验收器再次驳回（第 {count} 次）：{verdict.reason}。"
            "已转人工确认（take_over）。"
        )
    return (
        f"验收未通过：{verdict.reason}。"
        "请补充屏幕证据或继续操作后再 finish(confirm=true)；"
        "若确信已达成，可再次确认。"
    )


def build_control_tools(session, config) -> list[StructuredTool]:
    """Return the control tool list bound to ``session``.

    ``config`` may be ``None`` (some integration tests build the control tools
    without a config); ``finish`` reads ``finish_verify`` defensively.
    """

    def finish(
        summary: str,
        evidence: list[str],
        confirm: bool = False,
        intent: str = "",
        note: str | None = None,
    ) -> str:
        """Declare the task complete (two-step review; S2 §1).

        ``evidence`` is REQUIRED and non-empty: enumerate what was accomplished
        and the concrete on-screen evidence for each claim. An empty list is
        rejected and nothing is recorded (fail-closed).

        The first call returns a review packet (current world state + route
        status + doubts + options) and does NOT finish. Read it, then call
        ``finish(confirm=true, summary=..., evidence=[...])`` to land the result.
        If any observation happened since the packet was shown, the confirm is
        stale and a fresh packet is returned instead.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        items = [str(e).strip() for e in (evidence or []) if str(e).strip()]
        if not items:
            return (
                "error: finish requires non-empty evidence — list what you "
                "completed and the on-screen proof for each claim, then retry."
            )
        # TaskDoc guard (§2.4): if the task board still has open items, block the
        # finish and point the model back at the route. Complete them, mark them
        # blocked (with a reason), or revise the route via update_task_doc first.
        doc = getattr(session, "task_doc", None)
        if doc is not None:
            try:
                has_open = bool(doc.has_open_items())
            except Exception:  # noqa: BLE001 - a broken predicate must not fake success
                has_open = False
            if has_open:
                try:
                    open_summary = doc.open_items_summary()
                except Exception:  # noqa: BLE001
                    open_summary = ""
                return (
                    f"路线仍有未完成项：{open_summary}。请先完成、标记 blocked"
                    "（带原因），或用 update_task_doc 修正路线后再 finish。"
                )

        # Backward-compat gate (S2 §1.6): FINISH_VERIFY=off degrades finish to the
        # pre-two-step single-call landing (no review packet, no seq guard).
        mode = getattr(config, "finish_verify", "auto") or "auto"
        if mode == "off":
            session.finished = True
            session.finish_summary = summary
            return "已记录完成声明"

        # Two-step (auto/always). Second stage lands only when the model confirms
        # AND the review packet is still fresh (no observation invalidated it).
        reviewed = bool(getattr(session, "finish_reviewed", False))
        review_seq = getattr(session, "finish_review_seq", -1)
        current_seq = getattr(session, "screen_seq", 0)
        if confirm and reviewed and current_seq == review_seq:
            # A fresh confirm. Optionally run the independent-context verifier
            # (S2 §4): high-risk goal / hard-contradiction confirm / always-mode.
            verdict = _maybe_verify_finish(session, config)
            if verdict is not None and not verdict.approve:
                return _handle_reject(session, verdict)
            session.finished = True
            session.finish_summary = summary
            session.finish_reviewed = False
            return "已确认完成"

        # First stage (or a stale confirm): build + return the review packet and
        # record the seq it was taken at, so the next confirm can be validated.
        from phone_agent.v2.review import build_review_package

        packet = build_review_package(session, config)
        try:
            session.finish_reviewed = True
            session.finish_review_seq = getattr(session, "screen_seq", 0)
        except Exception:  # noqa: BLE001 - best-effort state write; packet still returned
            pass
        return packet

    def ask_user(question: str, intent: str = "", note: str | None = None) -> str:
        """Ask the human operator a question and wait for their answer (HITL).

        The safety middleware turns this call into a LangGraph ``interrupt`` and
        resumes with the user's text reply; this body only formats the question.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        return f"[ASK_USER] {question}"

    def take_over(reason: str, intent: str = "", note: str | None = None) -> str:
        """Request human takeover (login/captcha/blocked flow).

        Records the takeover reason and ends the automated run; the middleware
        raises the actual interrupt.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        session.takeover_reason = reason
        return f"已请求人工接管: {reason}"

    return [
        StructuredTool.from_function(finish, parse_docstring=True),
        StructuredTool.from_function(ask_user, parse_docstring=True),
        StructuredTool.from_function(take_over, parse_docstring=True),
    ]
