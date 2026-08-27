"""Safety middleware: dangerous tool calls are gated behind a human interrupt.

Per refactor-thin-loop-v2 §9.1: the only safety hard gate in v2 lives here.
When a tool call is classified as sensitive (payment / password / verification
code / sensitive app launch), the call is routed through
``HumanInTheLoopMiddleware`` and blocked on a human ``approve``/``reject``
decision before it can execute.

Vocabulary is read from ``phone_agent.config.policy.DEFAULT_SAFETY_POLICY``
(the multilingual, versioned safety registry). ``launch_app`` additionally
consults a curated local sensitive-app keyword set because the policy module
carries no app inventory (documented deviation).
"""

from __future__ import annotations

from typing import Any, Callable

from phone_agent.config.policy import DEFAULT_SAFETY_POLICY, SafetyPolicyRegistry

# Deviation (§9.1): policy.py has no sensitive-app table, so launch_app targets
# are matched against this curated CN+EN keyword set for banking/payment apps
# in addition to the generic policy vocabulary.
SENSITIVE_APP_KEYWORDS: tuple[str, ...] = (
    "bank",
    "alipay",
    "wallet",
    "pay",
    "paypal",
    "wechat pay",
    "unionpay",
    "银行",
    "支付宝",
    "钱包",
    "支付",
    "微信支付",
    "云闪付",
    "网银",
    "转账",
)

# Tools that participate in the HITL safety gate. All except take_over/ask_user
# only interrupt when the sensitive predicate fires (via ``when``).
ACTUATION_GATED_TOOLS: tuple[str, ...] = (
    "tap",
    "long_press",
    "type_text",
    "launch_app",
)


def _extract_call(request: Any) -> tuple[str, dict[str, Any]]:
    """Extract ``(tool_name, args)`` from a variety of request shapes.

    Supports the langchain ``ToolCallRequest`` (``request.tool_call``), a bare
    ``ToolCall``/dict with ``name``/``args`` keys, and simple test doubles.
    """
    tool_call = getattr(request, "tool_call", None)
    if tool_call is None and isinstance(request, dict):
        tool_call = request.get("tool_call", request)
    if tool_call is None:
        tool_call = request
    if isinstance(tool_call, dict):
        name = tool_call.get("name", "")
        args = tool_call.get("args", {}) or {}
    else:
        name = getattr(tool_call, "name", "") or ""
        args = getattr(tool_call, "args", {}) or {}
    if not isinstance(args, dict):
        args = {}
    return str(name), args


def _text_is_sensitive(
    text: str | None, *, policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY
) -> bool:
    if not text:
        return False
    return policy.classify(text=str(text)).route is not None


def _mark_text_for(args: dict[str, Any], session: Any | None) -> str:
    """Best-effort resolution of the target mark's display text.

    Reads both raw addressing args and, when a live session is available, the
    resolved mark's ``text_summary`` from the current screen marks.
    """
    parts: list[str] = []
    description = args.get("target_description")
    if description:
        parts.append(str(description))
    mark_id = args.get("target_mark_id")
    if mark_id:
        parts.append(str(mark_id))
        marks = getattr(session, "marks", None)
        if isinstance(marks, dict):
            mark = marks.get(mark_id)
            summary = getattr(mark, "text_summary", None) if mark is not None else None
            if summary:
                parts.append(str(summary))
    return " ".join(parts)


def is_sensitive_tool_call(
    request: Any,
    session: Any | None = None,
    *,
    policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY,
) -> bool:
    """Return ``True`` when a tool call must be gated behind human approval.

    * ``type_text``: the ``text`` argument hits the payment/password/captcha
      vocabulary.
    * ``tap`` / ``long_press``: the target mark text (raw ``target_description``
      / ``target_mark_id`` and, if a session is supplied, the resolved mark's
      ``text_summary``) hits the sensitive vocabulary.
    * ``launch_app``: the target app matches the policy vocabulary or the
      curated sensitive-app keyword set.
    """
    name, args = _extract_call(request)

    if name == "type_text":
        return _text_is_sensitive(args.get("text"), policy=policy)

    if name in {"tap", "long_press"}:
        return _text_is_sensitive(_mark_text_for(args, session), policy=policy)

    if name == "launch_app":
        app_name = str(args.get("app_name", ""))
        if _text_is_sensitive(app_name, policy=policy):
            return True
        lowered = app_name.casefold()
        return any(keyword.casefold() in lowered for keyword in SENSITIVE_APP_KEYWORDS)

    return False


def build_hitl_middleware(session: Any | None = None):
    """Build the ``HumanInTheLoopMiddleware`` configured for v2 tools.

    * Actuation tools (``tap``/``long_press``/``type_text``/``launch_app``)
      interrupt only when :func:`is_sensitive_tool_call` returns ``True``, and
      the human may ``approve`` or ``reject``.
    * ``ask_user`` interrupts always with a ``respond`` decision (the human
      answers on the tool's behalf).
    * ``take_over`` interrupts always (human must ``approve`` before the agent
      hands control over).
    """
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    def _sensitive(req: Any) -> bool:
        return is_sensitive_tool_call(req, session)

    interrupt_on: dict[str, Any] = {
        tool: {
            "when": _sensitive,
            "allowed_decisions": ["approve", "reject"],
        }
        for tool in ACTUATION_GATED_TOOLS
    }
    interrupt_on["ask_user"] = {"allowed_decisions": ["respond"]}
    interrupt_on["take_over"] = {"allowed_decisions": ["approve", "reject"]}

    return HumanInTheLoopMiddleware(interrupt_on=interrupt_on)


__all__ = [
    "is_sensitive_tool_call",
    "build_hitl_middleware",
    "SENSITIVE_APP_KEYWORDS",
    "ACTUATION_GATED_TOOLS",
]
