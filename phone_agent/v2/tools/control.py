"""Control tools: finish / ask_user / take_over.

Per refactor-thin-loop-v2.md §7.3. These tools shape the run outcome rather than
touching the device. HITL interrupts for ``ask_user``/``take_over`` are enforced
by the safety middleware; the tool bodies only record intent / format text.

``finish`` additionally enforces the TaskDoc guard
(``docs/refactor-thin-loop-v2-taskdoc.md`` §2.4): if the task board still has
open items it rejects the declaration and lists them, so the model cannot claim
success while the route is unfinished.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool


def build_control_tools(session, config) -> list[StructuredTool]:
    """Return the control tool list bound to ``session``."""

    def finish(summary: str, evidence: list[str]) -> str:
        """Declare the task complete.

        ``evidence`` is REQUIRED and non-empty: enumerate what was accomplished
        and the concrete on-screen evidence for each claim. An empty list is
        rejected and nothing is recorded (fail-closed).
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
        session.finished = True
        session.finish_summary = summary
        return "已记录完成声明"

    def ask_user(question: str) -> str:
        """Ask the human operator a question and wait for their answer (HITL).

        The safety middleware turns this call into a LangGraph ``interrupt`` and
        resumes with the user's text reply; this body only formats the question.
        """

        return f"[ASK_USER] {question}"

    def take_over(reason: str) -> str:
        """Request human takeover (login/captcha/blocked flow).

        Records the takeover reason and ends the automated run; the middleware
        raises the actual interrupt.
        """

        session.takeover_reason = reason
        return f"已请求人工接管: {reason}"

    return [
        StructuredTool.from_function(finish, parse_docstring=True),
        StructuredTool.from_function(ask_user, parse_docstring=True),
        StructuredTool.from_function(take_over, parse_docstring=True),
    ]
