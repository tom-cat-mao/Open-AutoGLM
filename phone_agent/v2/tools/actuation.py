"""Actuation tools: tap/long_press/type_text/scroll/swipe/back/home/wait/launch_app.

Per refactor-thin-loop-v2.md §7.1. Every execution tool is marks-first and
fail-closed:

- ``tap`` / ``long_press`` accept dual addressing (``target_mark_id`` direct or
  ``target_description`` via the resolver). Neither raw-coordinate tap nor a
  black-image path exists.
- Resolver ambiguity / stale marks / unknown apps return an error string and
  DO NOT execute (the error stays in the transcript for the model to read).
- On success the tool returns a multimodal content ``list`` — an ``"OK. <result>"``
  text block followed by the §7.4 auto observation blocks (text + a fresh
  screenshot image when the screen changed). Error/ambiguity branches stay a
  plain ``str`` (fail-closed, no image). See ``tools/_obs.py``.

Tools are built as closures over ``session`` and ``config`` by
:func:`phone_agent.v2.tools.build_tools`.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import StructuredTool

from phone_agent.config.apps import DEFAULT_LAUNCH_TARGET_RESOLVER
from phone_agent.grounding.provider import MarkCandidate

from phone_agent.v2.resolver import (
    LocateAmbiguousError,
    ResolveAmbiguousError,
    StaleMarkError,
    resolve_description,
)
from phone_agent.v2.tools._obs import auto_observation, mark_tool_fail, mark_tool_ok


def _ok_with_obs(head: str, session) -> list[dict]:
    """Merge an ``OK. <head>`` text block with the multimodal observation blocks.

    Success paths return a content ``list`` (text + image when the screen
    changed); the observation layer owns image dedup and fail-closed text
    fallback (``tools/_obs.py``). Error branches stay ``str`` (no image).

    Records ``session.last_tool_ok=True`` (all actuation success paths funnel
    here) so the finish review packet can mirror the last action (S2 §1.2).
    """

    mark_tool_ok(session)
    return [{"type": "text", "text": f"OK. {head}"}, *auto_observation(session)]


def _fail(session, message: str) -> str:
    """Record an actuation failure (``last_tool_ok=False``) and return the error text.

    Every actuation error branch funnels here so the finish review packet's
    hard-contradiction check sees the failed last action (S2 §1.5). The error
    string stays in the transcript unchanged (fail-closed, no device action).
    """

    mark_tool_fail(session)
    return message


def _resolve_target(
    session,
    target_mark_id: str | None,
    target_description: str | None,
) -> tuple[MarkCandidate | None, str | None]:
    """Return ``(mark, error_text)``; exactly one is non-None.

    ``mark_id`` path -> ``session.resolve_mark`` (stale -> hint string).
    ``description`` path -> resolver (ambiguity/locate-failure -> candidate text).
    """

    if target_mark_id and target_description:
        return None, (
            "error: pass only one of target_mark_id or target_description, not both"
        )
    if target_mark_id:
        try:
            return session.resolve_mark(target_mark_id), None
        except StaleMarkError:
            return None, (
                f"stale mark: {target_mark_id!r} is no longer on the current "
                "screen. Call read_screen() to refresh marks, then retry."
            )
    if target_description:
        try:
            return resolve_description(session, target_description), None
        except ResolveAmbiguousError as exc:
            return None, (
                "ambiguous: " + "; ".join(exc.candidates)
                + " — refine the description or use target_mark_id"
            )
        except LocateAmbiguousError as exc:
            return None, (
                f"ambiguous: {exc} — refine the description or use target_mark_id"
            )
    return None, "error: one of target_mark_id or target_description is required"


def _screen_dims(session) -> tuple[int, int]:
    """Best-effort current screen size in pixels for coordinate math.

    §6 does not put a raw relative->absolute helper on the session, so swipe and
    scroll derive pixels from the session's known dimensions. Defaults match the
    adb placeholder screenshot (1080x2400).
    """

    w = getattr(session, "screen_width", None)
    h = getattr(session, "screen_height", None)
    return int(w or 1080), int(h or 2400)


def _relative_to_abs(session, rx: int, ry: int) -> tuple[int, int]:
    """Convert a 0-1000 relative point to absolute pixels (v2.coords semantics).

    Prefers a session-provided converter when present; otherwise applies
    ``x = int(rx / 1000 * w)`` inline so the tools work before ``v2/coords.py``
    is wired.
    """

    conv = getattr(session, "relative_to_abs", None)
    if callable(conv):
        return conv(rx, ry)
    w, h = _screen_dims(session)
    return int(rx / 1000 * w), int(ry / 1000 * h)


def _mark_label(mark) -> str:
    """Human-facing element label for a receipt: ``「文本」(mark_id)`` or ``(mark_id)``.

    Prefers the mark's visible text so the tool receipt names *what* was acted on
    (the output-contract receipt, e.g. ``已点击「上海」(ax_3)``); falls back to the
    bare mark id when the element has no text.
    """

    text = (getattr(mark, "text_summary", None) or "").strip().replace("\n", " ")
    mark_id = getattr(mark, "mark_id", "?")
    if text:
        if len(text) > 24:
            text = text[:23] + "…"
        return f"「{text}」({mark_id})"
    return f"({mark_id})"


def build_actuation_tools(session, config) -> list[StructuredTool]:
    """Return the actuation tool list bound to ``session``/``config``."""

    device = session.device_factory
    device_id = getattr(config, "device_id", None)

    def _tap_like(
        action: str,
        target_mark_id: str | None,
        target_description: str | None,
    ) -> str | list[dict]:
        mark, err = _resolve_target(session, target_mark_id, target_description)
        if err is not None:
            return _fail(session, err)
        x, y = session.mark_center_abs(mark)
        if action == "long_press":
            device.long_press(x, y, device_id=device_id)
            verb = "已长按"
        else:
            device.tap(x, y, device_id=device_id)
            verb = "已点击"
        return _ok_with_obs(f"{verb}{_mark_label(mark)}", session)

    def tap(
        target_mark_id: str | None = None,
        target_description: str | None = None,
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Tap one on-screen element.

        Provide exactly one of ``target_mark_id`` (a mark from the latest
        observation) or ``target_description`` (natural language, resolved to a
        unique mark; ambiguity returns candidates and does not tap).

        Always pass ``intent`` (this step's goal, e.g. 把出发地改成上海).
        ``note`` optionally records what you discovered this step.
        """

        return _tap_like("tap", target_mark_id, target_description)

    def long_press(
        target_mark_id: str | None = None,
        target_description: str | None = None,
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Long-press one on-screen element (same addressing as ``tap``).

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        return _tap_like("long_press", target_mark_id, target_description)

    def type_text(
        text: str,
        target_mark_id: str | None = None,
        target_description: str | None = None,
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Type ``text`` into a field.

        If a target is given, the field is tapped to focus first. Text is
        entered through the ADB keyboard (switched in and restored when the
        device layer supports it).

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        if target_mark_id or target_description:
            mark, err = _resolve_target(session, target_mark_id, target_description)
            if err is not None:
                return _fail(session, err)
            fx, fy = session.mark_center_abs(mark)
            device.tap(fx, fy, device_id=device_id)

        ime = None
        detect = getattr(device, "detect_and_set_adb_keyboard", None)
        restore = getattr(device, "restore_keyboard", None)
        try:
            if callable(detect):
                ime = detect(device_id=device_id)
            device.type_text(text, device_id=device_id)
        finally:
            if ime and callable(restore):
                restore(ime, device_id=device_id)

        preview = text if len(text) <= 32 else text[:31] + "…"
        return _ok_with_obs(f"已输入 {preview!r}", session)

    def scroll(
        direction: Literal["up", "down", "left", "right"],
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Scroll the screen by a mid-screen swipe in ``direction``.

        ``direction`` is the content scroll direction (``down`` reveals content
        below by swiping upward).

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        obs_w, obs_h = _screen_dims(session)
        w, h = obs_w, obs_h
        cx, cy = w // 2, h // 2
        near, far = int(h * 0.25), int(h * 0.75)
        lx, rx = int(w * 0.25), int(w * 0.75)
        moves = {
            "down": (cx, far, cx, near),
            "up": (cx, near, cx, far),
            "left": (rx, cy, lx, cy),
            "right": (lx, cy, rx, cy),
        }
        if direction not in moves:
            return _fail(
                session,
                f"error: unknown direction {direction!r}; use up|down|left|right",
            )
        sx, sy, ex, ey = moves[direction]
        device.swipe(sx, sy, ex, ey, device_id=device_id)
        return _ok_with_obs(f"scroll {direction}", session)

    def swipe(
        start: list[int],
        end: list[int],
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Swipe between two 0-1000 relative points (coordinate fallback).

        Prefer ``scroll`` for list navigation. ``start``/``end`` are ``[x, y]``
        in 0-1000 relative coordinates and are converted to absolute pixels.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        if not (isinstance(start, (list, tuple)) and len(start) == 2):
            return _fail(session, "error: start must be [x, y] in 0-1000 relative coords")
        if not (isinstance(end, (list, tuple)) and len(end) == 2):
            return _fail(session, "error: end must be [x, y] in 0-1000 relative coords")
        sx, sy = _relative_to_abs(session, int(start[0]), int(start[1]))
        ex, ey = _relative_to_abs(session, int(end[0]), int(end[1]))
        device.swipe(sx, sy, ex, ey, device_id=device_id)
        return _ok_with_obs(
            f"swipe ({start[0]},{start[1]})->({end[0]},{end[1]})", session
        )

    def back(intent: str = "", note: str | None = None) -> str | list[dict]:
        """Press the system Back button.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        device.back(device_id=device_id)
        return _ok_with_obs("back", session)

    def home(intent: str = "", note: str | None = None) -> str | list[dict]:
        """Press the system Home button.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        device.home(device_id=device_id)
        return _ok_with_obs("home", session)

    def wait(
        seconds: float = 2.0,
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Wait for the UI to settle, then re-observe.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        import time

        time.sleep(max(0.0, float(seconds)))
        return _ok_with_obs(f"waited {seconds}s", session)

    def launch_app(
        app_name: str,
        intent: str = "",
        note: str | None = None,
    ) -> str | list[dict]:
        """Launch an installed app by name.

        The name is resolved through the app registry / launch policy. Unknown
        or denied apps return an error string and are never launched.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        resolution = DEFAULT_LAUNCH_TARGET_RESOLVER.resolve(app_name)
        status = resolution.status
        if status == "resolved" and resolution.package_name:
            device.launch_app(app_name, device_id=device_id)
            return _ok_with_obs(
                f"launched {app_name} ({resolution.package_name})", session
            )
        if status == "ambiguous":
            names = []
            for cand in resolution.candidates[:5]:
                names.append(getattr(cand, "canonical_id", str(cand)))
            return _fail(
                session,
                f"ambiguous app {app_name!r}: {', '.join(names)} — be more specific",
            )
        if status == "denied":
            return _fail(session, f"denied: {app_name!r} is not launch-authorized")
        if status == "not_installed":
            return _fail(session, f"error: {app_name!r} is not installed on this device")
        return _fail(
            session,
            f"unknown app {app_name!r}: not in registry/inventory — cannot launch",
        )

    return [
        StructuredTool.from_function(tap, parse_docstring=True),
        StructuredTool.from_function(long_press, parse_docstring=True),
        StructuredTool.from_function(type_text, parse_docstring=True),
        StructuredTool.from_function(scroll, parse_docstring=True),
        StructuredTool.from_function(swipe, parse_docstring=True),
        StructuredTool.from_function(back, parse_docstring=True),
        StructuredTool.from_function(home, parse_docstring=True),
        StructuredTool.from_function(wait, parse_docstring=True),
        StructuredTool.from_function(launch_app, parse_docstring=True),
    ]
