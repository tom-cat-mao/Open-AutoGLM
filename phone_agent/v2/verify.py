"""Finish verifier (S2 §4): the independent-context L2 acceptance sub-agent.

When the two-step ``finish`` (``tools/control.py``) reaches a *fresh confirm*, it
asks :func:`should_verify_finish` whether an independent verifier must run (S2
§4.1). If so, :func:`verify_finish` builds an **independent** message context —
the authoritative goal (``goal_base`` + amendments), the evidence-bearing route
(completed items + ``evidence_note``, blocked + reason), and the last K
screenshots — and asks a second model whether the goal is actually met. The
actor's transcript / self-defence is deliberately **excluded** (防"说服式通过").

Failure semantics (S2 §4.5) — *opposite* to the safety gate. The safety gate is
fail-**closed** (a broken reviewer gates). This verifier is fail-**open**: the L1
two-step confirm is already a valid backstop, and the user constraint is to not
over-emphasise safety, so a verifier setup/call error lands the finish anyway and
records a warning. It must never wedge a correct completion behind a flaky model.

Everything the verifier reads is authoritative world/route state, not model
prose; the goal and evidence strings are redacted (``config/redact``) before
egress to the verifier model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from phone_agent.config.policy import DEFAULT_SAFETY_POLICY, SafetyPolicyRegistry
from phone_agent.config.redact import redact_context_text

logger = logging.getLogger(__name__)

# The takeover reason set after repeated verifier rejections (S2 §4.4). Kept as a
# module constant so control.py and tests agree on the exact string.
DISPUTE_TAKEOVER_REASON = "finish 反复被验收驳回，需人工确认"

# How many verifier rejections (with the model still insisting on confirm) before
# the run escalates to human takeover (L2 -> L3). Design §4.4: "2 次转 take_over".
DISPUTE_TAKEOVER_THRESHOLD = 2

_VERIFIER_SYSTEM = (
    "你是移动端任务验收器。你只会看到【目标】【已完成路线与证据】和【当前屏幕截图】，"
    "看不到执行体的任何自我说明或辩解。请仅凭这些世界事实与证据，判断目标是否真的达成。"
    "先输出裁决词 APPROVE（已达成）或 REJECT（未达成），再用一句话说明理由（陈述缺了什么，"
    "不要给出操作指导）。"
)


@dataclass(frozen=True)
class Verdict:
    """One acceptance decision from the finish verifier (S2 §4.3)."""

    approve: bool
    reason: str


def _goal_texts(session: Any) -> list[str]:
    """Return the authoritative goal texts: ``goal_base`` + amendments."""

    doc = getattr(session, "task_doc", None)
    if doc is None:
        return []
    texts: list[str] = []
    base = getattr(doc, "goal_base", "") or ""
    if base.strip():
        texts.append(base)
    for amendment in getattr(doc, "amendments", []) or []:
        if str(amendment).strip():
            texts.append(str(amendment))
    return texts


def _goal_is_high_risk(
    session: Any, *, policy: SafetyPolicyRegistry = DEFAULT_SAFETY_POLICY
) -> bool:
    """True when the goal itself touches an irreversible domain (S2 §4.1.1).

    A goal whose ``goal_base`` or any amendment hits the policy vocabulary
    (payment / delete / credential …) is high-risk: a completion claim there
    warrants the independent verifier even without a local hard contradiction.
    """

    return any(policy.classify(text=text).route is not None for text in _goal_texts(session))


def should_verify_finish(session: Any, config: Any) -> bool:
    """Whether a fresh ``finish(confirm=true)`` must run the verifier (S2 §4.1).

    * ``off``  -> never.
    * ``always`` -> every confirm.
    * ``auto`` (default) -> only on a trigger: a high-risk goal, or a non-empty
      hard-contradiction list persisted by the last review packet (the model is
      insisting on confirm despite a cheap local contradiction).
    """

    mode = getattr(config, "finish_verify", "auto") or "auto"
    mode = str(mode).strip().lower()
    if mode == "off":
        return False
    if mode == "always":
        return True
    # auto
    if _goal_is_high_risk(session):
        return True
    hard = getattr(session, "finish_hard_doubts", None) or []
    return bool(hard)


def _route_evidence_lines(session: Any) -> list[str]:
    """Render the evidence-bearing route for the verifier (completed/blocked)."""

    doc = getattr(session, "task_doc", None)
    if doc is None:
        return ["（无任务板路线）"]
    items = list(getattr(doc, "items", []) or [])
    lines: list[str] = []
    for item in items:
        status = getattr(item, "status", "")
        content = redact_context_text(getattr(item, "content", "") or "")
        if status == "completed":
            note = redact_context_text((getattr(item, "evidence_note", None) or "").strip())
            lines.append(f"- 已完成 {getattr(item, 'id', '?')}: {content}（证据：{note or '无'}）")
        elif status == "blocked":
            reason = redact_context_text((getattr(item, "reason", None) or "").strip())
            lines.append(f"- 阻塞 {getattr(item, 'id', '?')}: {content}（原因：{reason or '无'}）")
        else:
            lines.append(f"- 待办 {getattr(item, 'id', '?')}: {content}[{status}]")
    return lines or ["（路线为空）"]


def _screenshot_blocks(session: Any, config: Any) -> list[dict[str, Any]]:
    """Return up to K trailing screenshot image blocks (S2 §4.2).

    K=1 (default) uses the current frame from a fresh ``observe()``. K>1 requires
    S1 historical-frame retention which is not wired here, so it degrades to the
    current frame only (documented). An observe failure yields no image (the
    verifier then judges on route evidence alone).
    """

    try:
        obs = session.observe()
    except Exception:  # noqa: BLE001 - no frame -> route-only verification
        return []
    b64 = getattr(obs, "screenshot_b64", None)
    if not b64:
        return []
    mime = getattr(obs, "mime_type", None) or "image/png"
    return [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]


def _build_verifier_messages(session: Any, config: Any) -> list[Any]:
    """Build the independent verifier context (system + one human message).

    No actor transcript is included — only the authoritative goal, evidence route,
    and the trailing screenshot(s). Goal/route text is redacted before egress.
    """

    from langchain_core.messages import HumanMessage, SystemMessage

    goals = [redact_context_text(text) for text in _goal_texts(session)]
    goal_text = "\n".join(f"- {g}" for g in goals) if goals else "（未提供目标）"
    route_text = "\n".join(_route_evidence_lines(session))

    text = (
        "【目标】\n"
        f"{goal_text}\n\n"
        "【已完成路线与证据】\n"
        f"{route_text}\n\n"
        "【问题】仅凭上述目标、路线证据与当前屏幕截图，目标是否已达成？"
        "先输出 APPROVE 或 REJECT，再给一句理由。"
    )
    human_content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    human_content.extend(_screenshot_blocks(session, config))
    return [SystemMessage(content=_VERIFIER_SYSTEM), HumanMessage(content=human_content)]


def _build_verifier_model(config: Any) -> Any:
    """Build the verifier chat model (``verifier_model`` or the main model)."""

    from phone_agent.v2.model import build_chat_model

    model_name = getattr(config, "verifier_model", None)
    if model_name:
        config = replace(config, model_name=model_name)
    return build_chat_model(config)


def _content_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, list):
        return " ".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _parse_verdict(text: str) -> Verdict:
    """Parse the verifier answer into a :class:`Verdict` (fail-open on ambiguity).

    A clear ``REJECT`` / ``未达成`` / ``没有达成`` rejects; everything else — including
    an unparseable answer — approves. This keeps the verifier from wedging a
    correct completion on a fuzzy reply (fail-open bias, S2 §4.5).
    """

    reason = " ".join(text.split())[:200] or "（无理由）"
    lowered = text.strip().lower()
    rejected = (
        "reject" in lowered
        or "未达成" in text
        or "没有达成" in text
        or "未完成" in text
    )
    if rejected and "approve" not in lowered:
        return Verdict(False, reason)
    return Verdict(True, reason)


def verify_finish(session: Any, config: Any, *, model: Any | None = None) -> Verdict:
    """Run the independent-context verifier for a finish confirm (S2 §4).

    Returns a :class:`Verdict`. Any setup or call failure is **fail-open**
    (``approve=True``) with a warning-shaped reason and a ``logger.warning`` — a
    flaky verifier must never block a completion the L1 two-step already cleared.
    ``model`` may be injected (tests / reuse); otherwise it is built from config.
    """

    try:
        messages = _build_verifier_messages(session, config)
    except Exception as exc:  # noqa: BLE001 - setup failure -> fail-open
        logger.warning("finish verifier setup failed, fail-open: %s", exc)
        return Verdict(True, f"验收器构建失败，已放行（fail-open）：{exc}")

    try:
        chat = model if model is not None else _build_verifier_model(config)
        resp = chat.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - call failure -> fail-open
        logger.warning("finish verifier call failed, fail-open: %s", exc)
        return Verdict(True, f"验收器调用失败，已放行（fail-open）：{exc}")

    return _parse_verdict(_content_text(resp))


__all__ = [
    "Verdict",
    "verify_finish",
    "should_verify_finish",
    "DISPUTE_TAKEOVER_REASON",
    "DISPUTE_TAKEOVER_THRESHOLD",
]
