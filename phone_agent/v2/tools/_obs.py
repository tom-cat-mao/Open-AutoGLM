"""Shared tool helpers: auto-observation block and relative coordinate conversion.

Kept tool-local so the tools layer stays self-contained before ``v2/session.py``
and ``v2/coords.py`` (owned by the core worktree) land. The §7.4 observation
block is appended by every actuation tool after a successful device action.

Vision reflux (S1 §1): ``auto_observation`` returns a multimodal content list
(``list[dict]``) — a ``[OBS]`` text block plus, when the screen changed, an
``image_url`` block carrying the fresh screenshot (with a ``screen_seq`` key the
trace/prune layers key on). Same-screen re-observations reuse the text block and
drop the image (``session.last_image_hash`` dedup) so a static screen is not
re-sent every step. Re-observation failure degrades to a single text block
(fail-closed: an actuation success is not lost just because a re-observe
hiccuped, and no fake image is ever emitted).
"""

from __future__ import annotations

import hashlib


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


def _obs_text(session) -> tuple[str, object]:
    """Observe once and build the ``[OBS]`` text; return ``(text, observation)``."""

    obs = session.observe()
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
    text = f"[OBS] app={current_app} screen#{seq}\nmarks ({count}): {digest}"
    return text, obs


def auto_observation(session) -> list[dict]:
    """Return the §7.4 ``[OBS]`` block as a multimodal content list.

    Success: ``[{text}, {image_url, screen_seq}]`` — but the image block is
    dropped (text only) when the screenshot hash matches the last one already
    sent (``session.last_image_hash``), appending a "（同上，未重复发图）" note.

    Failure (re-observation raised) degrades to a single text block, never a
    fake image — fail-closed.
    """

    try:
        text, obs = _obs_text(session)
    except Exception as exc:  # noqa: BLE001 - observation is best-effort here
        return [{"type": "text", "text": f"[OBS] (re-observation failed: {exc})"}]

    b64 = getattr(obs, "screenshot_b64", None)
    if not b64:
        # No screenshot payload (bring-up / text-only session): text only.
        return [{"type": "text", "text": text}]

    screen_hash = getattr(obs, "screen_hash", "") or hashlib.sha256(
        b64.encode("utf-8")
    ).hexdigest()[:16]
    last = getattr(session, "last_image_hash", None)
    if screen_hash and screen_hash == last:
        # Same screen as the last image we sent: keep the text, drop the image.
        return [{"type": "text", "text": text + "\n（同上，未重复发图）"}]

    # Screen changed (or first frame): ship the image and remember its hash.
    session.last_image_hash = screen_hash
    seq = getattr(obs, "screen_seq", getattr(session, "screen_seq", 0))
    mime = getattr(obs, "mime_type", None) or "image/png"
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
            "screen_seq": seq,
        },
    ]
