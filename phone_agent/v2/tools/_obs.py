"""Shared tool helpers: auto-observation block and relative coordinate conversion.

Kept tool-local so the tools layer stays self-contained before ``v2/session.py``
and ``v2/coords.py`` (owned by the core worktree) land. The §7.4 observation
block is appended by every actuation tool after a successful device action.
"""

from __future__ import annotations


def format_marks_digest_fallback(marks: dict, max_items: int = 40) -> str:
    """Render ``mark_id | role | text(<=32) | center`` lines (§6 sketch).

    Used only when the session does not expose ``format_marks_digest``. Matches
    the doc's per-line contract so the model sees a stable marks summary.
    """

    lines: list[str] = []
    for mark_id, mark in list(marks.items())[:max_items]:
        role = getattr(mark, "role", None) or "?"
        text = (getattr(mark, "text_summary", None) or "").strip()
        if len(text) > 32:
            text = text[:31] + "…"
        center = getattr(mark, "center", None) or [0, 0]
        try:
            cx, cy = int(center[0]), int(center[1])
        except (TypeError, ValueError, IndexError):
            cx, cy = 0, 0
        lines.append(f"{mark_id}|{role}|{text}|({cx},{cy})")
    body = " · ".join(lines)
    extra = ""
    if len(marks) > max_items:
        extra = f" …(+{len(marks) - max_items} more)"
    return body + extra


def auto_observation(session) -> str:
    """Return the §7.4 ``[OBS]`` block from a fresh ``session.observe()``.

    Failures are swallowed into a note (fail-closed: an actuation success is not
    lost just because re-observation hiccuped).
    """

    try:
        obs = session.observe()
    except Exception as exc:  # noqa: BLE001 - observation is best-effort here
        return f"[OBS] (re-observation failed: {exc})"

    current_app = getattr(obs, "current_app", None) or "?"
    seq = getattr(obs, "screen_seq", getattr(session, "screen_seq", 0))
    marks = getattr(obs, "marks", None)
    if marks is None:
        marks = getattr(session, "marks", {})

    digest_fn = getattr(session, "format_marks_digest", None)
    if callable(digest_fn):
        digest = digest_fn(marks)
    else:
        digest = format_marks_digest_fallback(marks)

    count = len(marks) if hasattr(marks, "__len__") else 0
    return f"[OBS] app={current_app} screen#{seq}\nmarks ({count}): {digest}"
