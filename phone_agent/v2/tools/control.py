"""Control tools: finish / ask_user / take_over.

Per refactor-thin-loop-v2.md §7.3. These tools shape the run outcome rather than
touching the device. HITL interrupts for ``ask_user``/``take_over`` are enforced
by the safety middleware; the tool bodies only record intent / format text.
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
