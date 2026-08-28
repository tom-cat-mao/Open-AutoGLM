"""Safety middleware: layered classification behind a human interrupt (S2 §3).

The v2 hard gate lives **only** here. A tool call is run through a three-layer
cascade — broad *recall* → optional *reviewer* → *hard* gate — and mapped to a
:class:`ToolCallVerdict`. Only a ``should_gate`` verdict routes the call through
``HumanInTheLoopMiddleware`` for an ``approve``/``reject`` decision.

Cascade (S2 §3.1)::

    recall(policy vocab / password box / self-declaration)
        no candidate                          -> pass (level="none")
        candidate:
            hard(commit+irreversible-object | password box | credential input
                 | policy takeover | self-declared)   -> hard gate (L4, always)
            soft candidate (bare policy vocab, e.g. "确认" / "支付方式"):
                SAFETY_MODE=reviewer + reviewer   -> second model judges reversibility
                SAFETY_MODE=hard / no reviewer    -> pass (hard mode) or
                                                     fail-closed gate (reviewer mode,
                                                     reviewer unavailable)

Design intent (S2 §3.2 refinement + benchmark): broad vocab only produces
*candidates* — a recall hit is **not** an automatic popup ("召回≠弹窗"). A weak
verb only escalates to a hard gate when it co-occurs with an irreversible
object, so ``确认支付`` / ``立即支付`` hard-gate while ``支付方式`` / ``支付宝红包`` /
``删除`` stay soft candidates (reviewer-judged, no popup in the default hard mode).

Vocabulary is read from ``phone_agent.config.policy.DEFAULT_SAFETY_POLICY``
(the multilingual, versioned safety registry). ``launch_app`` additionally
consults a curated local sensitive-app keyword set because the policy module
carries no app inventory (documented deviation).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from phone_agent.config.policy import (
    DEFAULT_SAFETY_POLICY,
    SafetyPolicyRegistry,
    _term_matches,
)
from phone_agent.config.redact import SENSITIVE_PATTERN, redact_context_text

# Deviation (§9.1): policy.py has no sensitive-app table, so launch_app targets
# are matched against this curated CN+EN keyword set for banking/payment apps
# in addition to the generic policy vocabulary. Launching an app is *reversible*
# (back/home exits), so a match is only ever a soft candidate (§3.6) — never a
# hard gate.
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
# only interrupt when the classifier gates the call (via ``when``). launch_app
# stays here so reviewer mode can still judge a sensitive-app launch, but its
# candidates never reach the hard gate (§3.6).
ACTUATION_GATED_TOOLS: tuple[str, ...] = (
    "tap",
    "long_press",
    "type_text",
    "launch_app",
)

# --- irreversible-commit vocabulary (S2 §3.4) ------------------------------
# A hard gate requires a COMMIT term AND an IRREVERSIBLE OBJECT to co-occur
# ("弱动词需共现敏感名词"). This deliberately overlaps the policy
# ``sensitive_side_effect`` vocabulary but is split into two roles so that a
# bare object token ("支付" inside "支付方式" / "支付宝红包") or a bare commit
# token ("确认") stays a soft candidate instead of firing the hard gate.
_COMMIT_TERMS: tuple[str, ...] = (
    "确认",
    "确定",
    "提交",
    "立即",
    "马上",
    "发送",
    "confirm",
    "submit",
    "place order",
    "pay now",
    "checkout",
)
_IRREVERSIBLE_OBJECTS: tuple[str, ...] = (
    "支付",
    "付款",
    "转账",
    "汇款",
    "提现",
    "红包",
    "下单",
    "购买",
    "订单",
    "删除",
    "移除",
    "清空",
    "卸载",
    "格式化",
    "pay",
    "payment",
    "checkout",
    "purchase",
    "buy",
    "order",
    "transfer",
    "remit",
    "withdraw",
    "delete",
    "remove",
    "clear",
    "wipe",
    "uninstall",
    "format",
)


@dataclass(frozen=True)
class ToolCallVerdict:
    """Layered safety decision for one tool call (S2 §3.2).

    * ``should_gate``: whether the call must interrupt for human approval.
    * ``level``: ``"none"`` | ``"recall"`` | ``"reviewer"`` | ``"hard"`` — the
      layer that produced the decision (for tracing / debugging).
    * ``route``: the policy route (``"confirm"`` / ``"takeover"``) when a
      candidate matched, for message shaping; ``None`` when nothing matched.
    * ``reason``: a short stable tag written to trace / logs.
    """

    should_gate: bool
    level: str
    route: str | None
    reason: str


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


def _safety_mode(config: Any | None) -> str:
    """Resolve the safety mode (off|hard|reviewer); default ``hard`` (§3.7)."""

    mode = getattr(config, "safety_mode", None) or "hard"
    mode = str(mode).strip().lower()
    return mode if mode in {"off", "hard", "reviewer"} else "hard"


def _normalize(text: str | None) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


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


def _mark_password_for(args: dict[str, Any], session: Any | None) -> bool:
    """True when the ``type_text`` target resolves to a password field.

    Depends on the ``MarkCandidate.password`` passthrough (S2 §3.5). Only the
    explicit ``target_mark_id`` path is checked: a resolved-by-description
    target is not available at gate time (the resolver runs inside the tool).
    """

    mark_id = args.get("target_mark_id")
    if not mark_id:
        return False
    marks = getattr(session, "marks", None)
    if not isinstance(marks, dict):
        return False
    mark = marks.get(mark_id)
    return bool(getattr(mark, "password", False)) if mark is not None else False


def _is_credential_text(
    text: str | None, *, policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY
) -> bool:
    """True when typed text is a credential / captcha (route=takeover) — hard.

    Detects either the redaction ``SENSITIVE_PATTERN`` (phone/email/order/code/
    api-key shapes) or the policy credential-or-captcha vocabulary (密码/验证码/
    登录/账户). Both map to the takeover route (S2 §3.4).
    """

    if not text:
        return False
    if SENSITIVE_PATTERN.search(str(text)):
        return True
    return policy.classify(text=str(text)).route == "takeover"


def _self_declared_sensitive(args: dict[str, Any]) -> bool:
    """True when the actor self-declares the call sensitive — always escalates.

    A model may pass an explicit ``sensitive``/``dangerous`` flag; self-declared
    sensitivity is always trusted upward to a hard gate (S2 §3.4).
    """

    return bool(args.get("sensitive") or args.get("dangerous"))


def _is_irreversible(text: str | None) -> bool:
    """True when a commit term co-occurs with an irreversible object (§3.4).

    ``确认支付`` / ``立即支付`` / ``发送红包`` / ``确认删除`` -> hard. A bare object
    (``支付方式``, ``支付宝红包``, ``删除``) or a bare commit (``确认``) stays soft.
    """

    normalized = _normalize(text)
    if not normalized:
        return False
    has_commit = any(_term_matches(term, normalized) for term in _COMMIT_TERMS)
    if not has_commit:
        return False
    return any(_term_matches(term, normalized) for term in _IRREVERSIBLE_OBJECTS)


def _app_is_candidate(
    app_name: str, policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY
) -> bool:
    """True when a launch_app target is a sensitive-app soft candidate (§3.6)."""

    if _text_is_sensitive(app_name, policy=policy):
        return True
    lowered = _normalize(app_name)
    return any(keyword.casefold() in lowered for keyword in SENSITIVE_APP_KEYWORDS)


def _soft_or_reviewer(
    mode: str,
    reviewer: Callable[[str, str], bool] | None,
    tool_name: str,
    text: str,
    *,
    route: str | None,
    reason: str,
) -> ToolCallVerdict:
    """Resolve a soft candidate: reviewer-judged (reviewer mode) or pass (hard).

    In ``reviewer`` mode the second model judges reversibility; an unavailable
    or throwing reviewer is fail-closed (gate). In ``hard`` mode soft candidates
    do NOT popup (§3.7) — only the hard gate fires.
    """

    if mode == "reviewer":
        if reviewer is None:
            return ToolCallVerdict(True, "reviewer", route, "reviewer_unavailable_failclosed")
        try:
            reversible = bool(reviewer(tool_name, text))
        except Exception:  # noqa: BLE001 - reviewer failure must never fake a pass
            return ToolCallVerdict(True, "reviewer", route, "reviewer_error_failclosed")
        if reversible:
            return ToolCallVerdict(False, "reviewer", route, "reviewer_reversible")
        return ToolCallVerdict(True, "reviewer", route, "reviewer_irreversible")
    # hard mode (default): recall candidate does not gate (§3.7).
    return ToolCallVerdict(False, "recall", route, reason + "_hard_pass")


def classify_tool_call(
    request: Any,
    session: Any | None = None,
    config: Any | None = None,
    *,
    reviewer: Callable[[str, str], bool] | None = None,
    policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY,
) -> ToolCallVerdict:
    """Classify one tool call into a :class:`ToolCallVerdict` (S2 §3.2).

    ``reviewer(tool_name, target_text) -> bool`` returns ``True`` when the action
    is *reversible* (safe, no gate). It is only consulted for soft candidates in
    ``reviewer`` mode. ``take_over`` / ``ask_user`` are control interrupts, not
    safety, and are handled directly by :func:`build_hitl_middleware`.
    """

    name, args = _extract_call(request)
    mode = _safety_mode(config)

    if mode == "off":
        return ToolCallVerdict(False, "none", None, "mode_off")
    if name not in ACTUATION_GATED_TOOLS:
        return ToolCallVerdict(False, "none", None, "not_actuation")

    # launch_app is reversible -> recall-only soft candidate, never hard (§3.6).
    if name == "launch_app":
        app_name = str(args.get("app_name", ""))
        if _app_is_candidate(app_name, policy):
            return _soft_or_reviewer(
                mode, reviewer, name, app_name, route="confirm", reason="sensitive_app"
            )
        return ToolCallVerdict(False, "none", None, "no_candidate")

    # Hard gates for typed input: password field, then credential/captcha text.
    if name == "type_text":
        if _mark_password_for(args, session):
            return ToolCallVerdict(True, "hard", "takeover", "password_field")
        if _is_credential_text(args.get("text"), policy=policy):
            return ToolCallVerdict(True, "hard", "takeover", "credential_input")

    # Self-declared sensitivity always escalates (§3.4).
    if _self_declared_sensitive(args):
        return ToolCallVerdict(True, "hard", "takeover", "self_declared")

    target_text = (
        str(args.get("text", "") or "")
        if name == "type_text"
        else _mark_text_for(args, session)
    )

    # Commit + irreversible object -> hard gate (§3.4).
    if _is_irreversible(target_text):
        return ToolCallVerdict(True, "hard", "confirm", "irreversible_commit")

    # Recall: broad policy vocab produces a candidate only.
    classification = policy.classify(text=target_text)
    if classification.route is None:
        return ToolCallVerdict(False, "none", None, "no_candidate")
    if classification.route == "takeover":
        # Credential/captcha domain reached via a tap/press target -> hard.
        return ToolCallVerdict(True, "hard", "takeover", "policy_takeover")

    return _soft_or_reviewer(
        mode, reviewer, name, target_text, route=classification.route, reason="soft_candidate"
    )


def is_sensitive_tool_call(
    request: Any,
    session: Any | None = None,
    *,
    policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY,
) -> bool:
    """Backward-compat thin wrapper over :func:`classify_tool_call` (hard mode).

    Equivalent to ``classify_tool_call(request, session, config=None).should_gate``
    — the default (``config=None``) resolves to the ``hard`` safety mode, so this
    reports only the hard-gate decision (no reviewer). Retained to keep the older
    predicate call sites and tests stable (§3.2).
    """

    return classify_tool_call(
        request, session, config=None, reviewer=None, policy=policy
    ).should_gate


def build_safety_reviewer(config: Any | None) -> Callable[[str, str], bool] | None:
    """Build the reviewer callable for soft-candidate precision (S2 §3.3).

    Returns ``reviewer(tool_name, target_text) -> bool`` (``True`` == reversible,
    no gate) or ``None`` when no reviewer model is configured. The reviewer model
    is ``safety_reviewer_model`` then ``verifier_model``; an unconfigured reviewer
    returns ``None`` so ``reviewer`` mode fail-closes (does NOT fall back to the
    main actor model — the second opinion must be an independent, opt-in model).

    The call feeds only the tool name + a redacted target-text summary (privacy +
    cheap), never the full transcript. Any construction error yields ``None``.
    """

    model_name = getattr(config, "safety_reviewer_model", None) or getattr(
        config, "verifier_model", None
    )
    if not model_name:
        return None

    try:
        from dataclasses import replace

        from phone_agent.v2.model import build_chat_model

        rcfg = replace(config, model_name=model_name)
        model = build_chat_model(rcfg)
    except Exception:  # noqa: BLE001 - unbuildable reviewer -> fail-closed (None)
        return None

    def _reviewer(tool_name: str, target_text: str) -> bool:
        from langchain_core.messages import HumanMessage, SystemMessage

        summary = redact_context_text(target_text)[:120]
        system = SystemMessage(
            content=(
                "你是移动端操作安全复核器。仅根据给定的动作目标文本，判断该动作是否"
                "『不可逆』（如支付/转账/删除/发送金额/提交不可撤销的订单）。"
                "只输出一个词：REVERSIBLE（可逆/安全）或 IRREVERSIBLE（不可逆）。"
            )
        )
        human = HumanMessage(
            content=f"工具：{tool_name}\n目标文本：{summary}\n该动作是否不可逆？"
        )
        resp = model.invoke([system, human])
        content = getattr(resp, "content", resp)
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text", "")) if isinstance(block, dict) else str(block)
                for block in content
            )
        return _parse_reversible(str(content))

    return _reviewer


def _parse_reversible(text: str) -> bool:
    """Parse the reviewer verdict; unparseable / ambiguous is fail-closed (gate)."""

    lowered = text.strip().lower()
    if "irreversible" in lowered or "不可逆" in lowered:
        return False
    if "reversible" in lowered or "可逆" in lowered or "安全" in lowered:
        return True
    # Ambiguous answer -> fail-closed: treat as irreversible (gate).
    return False


def build_hitl_middleware(session: Any | None = None, config: Any | None = None):
    """Build the ``HumanInTheLoopMiddleware`` configured for v2 tools (S2 §3.3).

    * Actuation tools (``tap``/``long_press``/``type_text``/``launch_app``)
      interrupt only when :func:`classify_tool_call` gates the call, and the
      human may ``approve`` or ``reject``. The classifier reads ``config`` for the
      safety mode and, in ``reviewer`` mode, consults a lazily built reviewer.
    * ``ask_user`` interrupts always with a ``respond`` decision (the human
      answers on the tool's behalf).
    * ``take_over`` interrupts always (human must ``approve`` before the agent
      hands control over).

    ``config`` is optional for backward compatibility: ``build_hitl_middleware()``
    and ``build_hitl_middleware(session)`` still work (they resolve to hard mode).
    """
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    reviewer = (
        build_safety_reviewer(config)
        if _safety_mode(config) == "reviewer"
        else None
    )

    def _gate(req: Any) -> bool:
        return classify_tool_call(req, session, config, reviewer=reviewer).should_gate

    interrupt_on: dict[str, Any] = {
        tool: {
            "when": _gate,
            "allowed_decisions": ["approve", "reject"],
        }
        for tool in ACTUATION_GATED_TOOLS
    }
    interrupt_on["ask_user"] = {"allowed_decisions": ["respond"]}
    interrupt_on["take_over"] = {"allowed_decisions": ["approve", "reject"]}

    return HumanInTheLoopMiddleware(interrupt_on=interrupt_on)


__all__ = [
    "ToolCallVerdict",
    "classify_tool_call",
    "is_sensitive_tool_call",
    "build_safety_reviewer",
    "build_hitl_middleware",
    "SENSITIVE_APP_KEYWORDS",
    "ACTUATION_GATED_TOOLS",
]
