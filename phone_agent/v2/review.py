"""Finish review packet (S2 §1.4-1.5): the L0 world-state mirror.

The two-step ``finish`` (``tools/control.py``) emits a *review packet* on its
first call: a cheap, local snapshot of the world (current app + marks digest),
the route status (completed items and their evidence, blocked items, open
count), a short doubts list, and the non-directive option space. It does **not**
land ``finished`` — the model reads the mirror, reflects, and only then calls
``finish(confirm=true)``.

Everything here is *cheaply, locally computable* (constitution: 可廉价判定):
one ``observe()`` plus attribute reads. No second model, no network. The
independent-context verifier (L2) is deferred to the next relay; this module is
the L0 mirror only.
"""

from __future__ import annotations

from typing import Any

from phone_agent.v2.tools._obs import format_marks_digest_fallback

# Foreground labels that indicate the launcher/home screen. A completion claim
# while sitting on the home screen is a cheap hard contradiction (§1.5). Matched
# as a lowercase substring so package ids (``com.android.launcher``,
# ``...nexuslauncher``, ``com.huawei.android.launcher``) and display names
# (``Launcher`` / ``桌面``) all trip it.
_LAUNCHER_TOKENS: tuple[str, ...] = ("launcher", "miui.home", "桌面")


def is_launcher(current_app: str | None) -> bool:
    """True when ``current_app`` looks like the device launcher / home screen."""

    if not current_app:
        return False
    label = current_app.strip().lower()
    return any(token in label for token in _LAUNCHER_TOKENS)


def _marks_and_count(session: Any, obs: Any) -> tuple[str, int]:
    """Return ``(marks_digest, marks_count)`` defensively across session shapes.

    Prefers the just-taken observation's marks; falls back to ``session.marks``.
    Real sessions expose a ``format_marks_digest`` static method that expects a
    ``list``; fake/duck sessions do not and use the dict-based fallback.
    """

    marks = getattr(obs, "marks", None) if obs is not None else None
    if marks is None:
        marks = getattr(session, "marks", {})
    try:
        count = len(marks)
    except TypeError:
        count = 0

    digest_fn = getattr(session, "format_marks_digest", None)
    if callable(digest_fn):
        seq = list(marks.values()) if isinstance(marks, dict) else list(marks)
        try:
            return digest_fn(seq), count
        except Exception:  # noqa: BLE001 - fall back to the dict renderer
            pass
    as_dict = (
        marks
        if isinstance(marks, dict)
        else {getattr(m, "mark_id", str(i)): m for i, m in enumerate(marks)}
    )
    return format_marks_digest_fallback(as_dict), count


def finish_doubts(session: Any, obs: Any, obs_error: Exception | None) -> dict[str, list[str]]:
    """Compute the hard-contradiction / soft-doubt lists (§1.5, all cheap-local).

    Hard contradictions do **not** block ``confirm`` (the model may still be
    right) but are highlighted for the model / a future verifier. Soft doubts are
    weaker signals worth surfacing.
    """

    hard: list[str] = []
    soft: list[str] = []

    last_ok = getattr(session, "last_tool_ok", None)
    if last_ok is False:
        hard.append("最近一次动作失败（last_tool_ok=False）")
    if obs_error is not None:
        hard.append(f"截图/观测无效：{obs_error}")

    current_app = getattr(obs, "current_app", None) if obs is not None else None
    if obs is not None and is_launcher(current_app):
        hard.append(f"当前前台为桌面/Launcher（{current_app}），可能未停留在目标界面")

    _, count = _marks_and_count(session, obs)
    if obs is not None and count < 2:
        soft.append(f"当前屏幕可交互 marks 极少（{count}）")

    if getattr(session, "nudged", False):
        soft.append("此前已触发停滞轻推（seen_states 长期无增长）")

    doc = getattr(session, "task_doc", None)
    if doc is not None:
        items = list(getattr(doc, "items", []) or [])
        blocked = [it for it in items if getattr(it, "status", "") == "blocked"]
        if blocked:
            soft.append(f"存在 {len(blocked)} 个 blocked 项未解决")
        missing_evidence = [
            it
            for it in items
            if getattr(it, "status", "") == "completed"
            and not (getattr(it, "evidence_note", None) or "").strip()
        ]
        if missing_evidence:
            soft.append(f"{len(missing_evidence)} 个 completed 项缺少 evidence_note")

    return {"hard": hard, "soft": soft}


def _route_status_lines(doc: Any) -> list[str]:
    """Render the route status section: completed(+evidence) / blocked(+reason) / open count."""

    if doc is None:
        return ["- 路线：（无任务板）"]
    items = list(getattr(doc, "items", []) or [])
    completed = [it for it in items if getattr(it, "status", "") == "completed"]
    blocked = [it for it in items if getattr(it, "status", "") == "blocked"]
    open_items = [
        it for it in items if getattr(it, "status", "") in {"pending", "in_progress"}
    ]

    lines: list[str] = []
    if completed:
        for it in completed:
            note = (getattr(it, "evidence_note", None) or "").strip()
            evidence = f"（证据：{note}）" if note else "（无 evidence_note）"
            lines.append(f"- 已完成 {getattr(it, 'id', '?')}: {getattr(it, 'content', '')}{evidence}")
    else:
        lines.append("- 已完成：（无）")
    if blocked:
        for it in blocked:
            reason = (getattr(it, "reason", None) or "").strip()
            lines.append(
                f"- 阻塞 {getattr(it, 'id', '?')}: {getattr(it, 'content', '')}（原因：{reason}）"
            )
    lines.append(f"- 开放项：{len(open_items)}")
    return lines


def build_review_package(session: Any, config: Any) -> str:
    """Build the finish review packet text (does exactly one ``observe()``).

    On observe failure the mirror degrades to an ``(world mirror unavailable)``
    line plus a "screenshot invalid" hard contradiction — it never swallows the
    finish attempt and never fabricates an image (fail-closed).
    """

    obs: Any = None
    obs_error: Exception | None = None
    try:
        obs = session.observe()
    except Exception as exc:  # noqa: BLE001 - degrade the mirror, never crash finish
        obs_error = exc

    doubts = finish_doubts(session, obs, obs_error)
    # Persist the hard-contradiction list so the finish verifier trigger (S2
    # §4.1.2) can see "hard contradiction + model still confirms". Best-effort:
    # a session double without a settable attribute must not break the packet.
    try:
        session.finish_hard_doubts = list(doubts["hard"])
    except Exception:  # noqa: BLE001 - best-effort state write
        pass
    lines: list[str] = [
        "[FINISH 复核包] 已完成前请先核对下列世界事实；确认无误后再调用 "
        "finish(confirm=true, summary=..., evidence=[...]) 定稿。",
        "",
        "## 世界事实",
    ]

    if obs is not None:
        current_app = getattr(obs, "current_app", None) or "?"
        seq = getattr(obs, "screen_seq", getattr(session, "screen_seq", 0))
        digest, count = _marks_and_count(session, obs)
        lines.append(f"- 当前应用：{current_app}")
        lines.append(f"- 屏幕序号：screen#{seq}")
        lines.append(f"- marks（{count}）：{digest}")
    else:
        lines.append(f"- （world mirror unavailable：{obs_error}）")

    last_ok = getattr(session, "last_tool_ok", None)
    last_label = {True: "成功", False: "失败", None: "未知"}[last_ok if last_ok in (True, False) else None]
    lines.append(f"- 最近动作：{last_label}")

    lines.append("")
    lines.append("## 路线状态")
    lines.extend(_route_status_lines(getattr(session, "task_doc", None)))

    lines.append("")
    lines.append("## 疑点")
    hard = doubts["hard"]
    soft = doubts["soft"]
    lines.append("- 硬矛盾：" + ("；".join(hard) if hard else "（无）"))
    lines.append("- 软疑点：" + ("；".join(soft) if soft else "（无）"))

    lines.append("")
    lines.append("## 选项")
    lines.append("- finish(confirm=true, ...)：确认已达成则定稿")
    lines.append("- 继续操作（tap / type_text / scroll …）：若尚未达成")
    lines.append("- update_task_doc：修正路线/补记证据")
    lines.append("- take_over：交人工处理")

    return "\n".join(lines)


__all__ = ["build_review_package", "finish_doubts", "is_launcher"]
