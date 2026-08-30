"""Safety middleware: risk detection behind a **warning** flow (U2 §3).

The v2 safety layer no longer hard-blocks by default. A tool call is run through
the same three-layer detection cascade — broad *recall* → optional *reviewer* →
*hard* signal — and mapped to a :class:`ToolCallVerdict`. But what a
``should_gate`` verdict *does* now depends on the safety mode:

* ``wary`` (default, U2): the warning system. A risky execution call
  (``tap``/``long_press``/``type_text``/``launch_app``) is **not executed and no
  human is summoned**. :class:`SafetyWarningMiddleware` short-circuits it and
  returns a warning ``ToolMessage`` (world fact + option space). The model must
  resend the same call with ``confirm_irreversible=true`` to actually act. A
  non-blocking notice is printed to stdout and the warning lands in the trace as
  the tool result.
* ``reviewer``: ``wary`` plus a second model that precision-ranks soft candidates
  (bare policy vocab) for reversibility before deciding whether to warn.
* ``hard``: the legacy HITL. Risky execution calls interrupt for a human
  ``approve``/``reject`` (for unattended runs). No warning middleware.
* ``off``: no gate at all.

Detection cascade (unchanged from S2 §3.1)::

    recall(policy vocab / password box / self-declaration)
        no candidate                          -> pass (level="none")
        candidate:
            hard(commit+irreversible-object | password box | credential input
                 | policy takeover | self-declared)   -> should_gate (hard signal)
            soft candidate (bare policy vocab, e.g. "确认" / "支付方式"):
                mode=reviewer + reviewer     -> second model judges reversibility
                mode=wary/hard / no reviewer -> pass (no warning) or fail-closed
                                                (reviewer mode, reviewer unavailable)

Design intent (S2 §3.2 + benchmark): broad vocab only produces *candidates* — a
recall hit is **not** an automatic warning ("召回≠预警"). A weak verb only
escalates to a hard signal when it co-occurs with an irreversible object, so
``确认支付`` / ``立即支付`` warn while ``支付方式`` / ``支付宝红包`` / ``删除`` stay
soft candidates (reviewer-judged, no warning in the default wary mode).

Vocabulary is read from ``phone_agent.config.policy.DEFAULT_SAFETY_POLICY``
(the multilingual, versioned safety registry). ``launch_app`` additionally
consults a curated local sensitive-app keyword set because the policy module
carries no app inventory (documented deviation).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from phone_agent.config.policy import (
    DEFAULT_SAFETY_POLICY,
    SafetyPolicyRegistry,
    _term_matches,
)
from phone_agent.config.redact import SENSITIVE_PATTERN, redact_context_text
from phone_agent.v2.middleware._tokens import (
    estimate_context_tokens,
    estimate_message_tokens,
)

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
    """Resolve the safety mode (off|wary|hard|reviewer); default ``wary`` (U2 §3)."""

    mode = getattr(config, "safety_mode", None) or "wary"
    mode = str(mode).strip().lower()
    return mode if mode in {"off", "wary", "hard", "reviewer"} else "wary"


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
    """Resolve a soft candidate: reviewer-judged (reviewer mode) or pass (wary/hard).

    In ``reviewer`` mode the second model judges reversibility; an unavailable
    or throwing reviewer is fail-closed (gate/warn). In ``wary``/``hard`` mode
    soft candidates do NOT warn or popup (§3.7, 召回≠预警) — only a hard signal
    escalates.
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
    # wary / hard mode: a recall candidate does not gate (§3.7).
    return ToolCallVerdict(False, "recall", route, reason + "_soft_pass")


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

    # Self-declared sensitivity always escalates (§3.4) — checked first so it
    # covers every actuation tool, including the otherwise-reversible launch_app.
    if _self_declared_sensitive(args):
        return ToolCallVerdict(True, "hard", "takeover", "self_declared")

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
    """Backward-compat thin wrapper over :func:`classify_tool_call`.

    Equivalent to ``classify_tool_call(request, session, config=None).should_gate``.
    With ``config=None`` the mode resolves to the default (``wary``), which shares
    the ``hard``-mode detection outcome (soft candidates never gate; hard signals
    always do; no reviewer), so this still reports the hard-signal decision only.
    Retained to keep the older predicate call sites and tests stable (§3.2).
    """

    return classify_tool_call(
        request, session, config=None, reviewer=None, policy=policy
    ).should_gate


def build_safety_reviewer(
    config: Any | None, session: Any | None = None
) -> Callable[[str, str], bool] | None:
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
        messages = [system, human]
        resp = model.invoke(messages)
        ledger = getattr(session, "usage_ledger", None)
        if ledger is not None:
            try:
                estimate = estimate_context_tokens(messages) + estimate_message_tokens(
                    resp
                )
                ledger.record("reviewer", resp, estimate_tokens=estimate)
            except Exception:  # noqa: BLE001 - accounting must not change safety
                pass
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


def _confirmed_irreversible(args: dict[str, Any]) -> bool:
    """True when the model re-sent the call with an explicit confirm flag (U2 §1).

    In the warning flow the actor reads the warning and re-issues the SAME call
    with ``confirm_irreversible=true`` to actually execute it. A truthy flag lets
    the warning middleware pass the call straight through to the tool.
    """

    return bool(args.get("confirm_irreversible"))


def format_warning(name: str, args: dict[str, Any], verdict: ToolCallVerdict) -> str:
    """Build the warning text returned in place of a risky execution (U2 §1).

    The message states the **world fact** (what the target is and why it is
    risky) and the **option space** (confirm / abandon / ask a human) — no
    device action is taken. Kept terse and redacted so it is cheap to carry in
    the transcript and safe to log.
    """

    reason_facts = {
        "irreversible_commit": "该操作疑似『不可逆提交』（如支付/转账/下单/删除等确认动作）",
        "password_field": "目标是『密码输入框』",
        "credential_input": "输入内容疑似『凭据/验证码』等敏感信息",
        "policy_takeover": "目标命中『凭据/验证码』敏感域",
        "self_declared": "你已自行申报本步为敏感操作",
        "reviewer_irreversible": "复核模型判定该操作『不可逆』",
        "sensitive_app": "目标疑似『支付/银行类』敏感应用",
    }
    fact = reason_facts.get(verdict.reason, "该操作被判定为敏感/高风险")

    target = _describe_target(name, args)
    head = f"⚠️ 已拦截（未执行）：{name}"
    if target:
        head += f" → {target}"
    body = (
        f"世界事实：{fact}。\n"
        "选项：\n"
        f"  1) 确认执行：带 confirm_irreversible=true 重新调用同一工具（其余参数不变）。\n"
        "  2) 放弃：改做其它操作或重新观测。\n"
        "  3) 交人工：调用 ask_user 询问，或 take_over 请求人工接管。"
    )
    return f"{head}\n{body}"


def _describe_target(name: str, args: dict[str, Any]) -> str:
    """A short, redacted target descriptor for a warning message."""

    if name == "type_text":
        text = redact_context_text(str(args.get("text", "")))[:32]
        return f"输入「{text}」" if text else "输入"
    if name == "launch_app":
        return f"「{str(args.get('app_name', ''))[:24]}」"
    desc = args.get("target_description")
    if desc:
        return f"「{redact_context_text(str(desc))[:24]}」"
    mark = args.get("target_mark_id")
    return f"({mark})" if mark else ""


class SafetyWarningMiddleware(AgentMiddleware):
    """Warning-flow safety gate (U2 §1): warn-not-execute, confirm-to-act.

    In ``wary``/``reviewer`` mode this middleware wraps every tool call. When
    :func:`classify_tool_call` flags a risky execution call AND the model did not
    pass ``confirm_irreversible=true``, the call is **short-circuited**: no device
    action runs, and a warning :class:`ToolMessage` (built by
    :func:`format_warning`) is returned as the tool result. A non-blocking notice
    is also printed to stdout (harness-side awareness, no desktop popup). The
    model resends with ``confirm_irreversible=true`` to actually execute.

    This never touches ``ask_user``/``take_over`` (control interrupts owned by the
    HITL middleware) nor non-actuation tools; it is a pure pass-through for them.
    """

    def __init__(
        self,
        session: Any | None,
        config: Any | None,
        *,
        reviewer: Callable[[str, str], bool] | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.config = config
        self._reviewer = reviewer
        self._notify = notify if notify is not None else _default_notify

    def _warn_message(self, request: Any) -> ToolMessage | None:
        """Return a warning ToolMessage if the call must be blocked, else ``None``."""

        name, args = _extract_call(request)
        if name not in ACTUATION_GATED_TOOLS:
            return None
        if _confirmed_irreversible(args):
            return None
        verdict = classify_tool_call(
            request, self.session, self.config, reviewer=self._reviewer
        )
        if not verdict.should_gate:
            return None
        text = format_warning(name, args, verdict)
        try:
            self._notify(f"[safety] {name}: {verdict.reason} — 已拦截，等待模型确认")
        except Exception:  # noqa: BLE001 - notification must never break the loop
            pass
        tool_call = getattr(request, "tool_call", {}) or {}
        call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
        return ToolMessage(
            content=text,
            tool_call_id=str(call_id or ""),
            name=name,
            status="error",
        )

    def wrap_tool_call(self, request, handler):  # noqa: ANN001
        warning = self._warn_message(request)
        if warning is not None:
            return warning
        return handler(request)

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001
        warning = self._warn_message(request)
        if warning is not None:
            return warning
        return await handler(request)


def _default_notify(message: str) -> None:
    """Default non-blocking notice: print to stdout (no desktop notification)."""

    print(message, flush=True)


def build_safety_warning_middleware(
    session: Any | None = None, config: Any | None = None
) -> SafetyWarningMiddleware | None:
    """Build the warning middleware for ``wary``/``reviewer`` mode, else ``None``.

    ``off``/``hard`` mode returns ``None`` (``off`` has no gate; ``hard`` uses the
    legacy HITL interrupt instead of the warning flow). In ``reviewer`` mode a
    lazily built second-model reviewer is attached for soft-candidate precision.
    """

    mode = _safety_mode(config)
    if mode not in {"wary", "reviewer"}:
        return None
    reviewer = (
        build_safety_reviewer(config, session=session) if mode == "reviewer" else None
    )
    return SafetyWarningMiddleware(session, config, reviewer=reviewer)


def build_hitl_middleware(session: Any | None = None, config: Any | None = None):
    """Build the ``HumanInTheLoopMiddleware`` for v2 control + legacy hard mode.

    Two responsibilities, split by safety mode (U2 §5):

    * ``ask_user`` / ``take_over`` **always** interrupt, in every mode — these are
      control interrupts (the human answers / takes over), never softened.
    * Actuation tools (``tap``/``long_press``/``type_text``/``launch_app``)
      interrupt for ``approve``/``reject`` **only in ``hard`` mode** (the legacy
      unattended-run HITL). In ``wary``/``reviewer``/``off`` mode they carry no
      ``when`` predicate here — the warning flow (:class:`SafetyWarningMiddleware`)
      owns risk handling for those modes instead.

    ``config`` is optional for backward compatibility: ``build_hitl_middleware()``
    and ``build_hitl_middleware(session)`` resolve to the default ``wary`` mode
    (actuation tools not interrupted here; only ask_user/take_over).
    """
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    mode = _safety_mode(config)
    interrupt_on: dict[str, Any] = {}

    if mode == "hard":
        reviewer = None  # hard mode keeps the classic hard-signal gate, no reviewer
        def _gate(req: Any) -> bool:
            return classify_tool_call(req, session, config, reviewer=reviewer).should_gate

        for tool in ACTUATION_GATED_TOOLS:
            interrupt_on[tool] = {
                "when": _gate,
                "allowed_decisions": ["approve", "reject"],
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
    "SafetyWarningMiddleware",
    "build_safety_warning_middleware",
    "format_warning",
    "SENSITIVE_APP_KEYWORDS",
    "ACTUATION_GATED_TOOLS",
]
