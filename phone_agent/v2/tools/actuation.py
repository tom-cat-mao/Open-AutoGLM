"""Actuation tools: tap/long_press/type_text/scroll/swipe/back/home/wait/launch_app.

Per AGENTS.md. Every execution tool is marks-first and
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

import re
from datetime import datetime, timezone
from typing import Literal

from langchain_core.tools import StructuredTool

from phone_agent.config.apps import DEFAULT_LAUNCH_TARGET_RESOLVER
from phone_agent.config.redact import SENSITIVE_PATTERN
from phone_agent.grounding.provider import MarkCandidate

from phone_agent.v2.appkb import should_save
from phone_agent.v2.names import ResolverSettings, decide_name
from phone_agent.v2.resolver import (
    LocateAmbiguousError,
    ResolveAmbiguousError,
    StaleMarkError,
    authorize_app_candidate,
    resolve_app_name,
    resolve_description,
)
from phone_agent.v2.tools._obs import auto_observation, mark_tool_fail, mark_tool_ok


_PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+")


def _available_app_names(session, *, max_n: int) -> str:
    """Best-effort bounded app-name hint for launch resolution failures."""

    render = getattr(session, "app_list_for_prompt", None)
    if callable(render):
        try:
            rendered = render(max_n)
            if rendered:
                return str(rendered)
        except Exception:  # noqa: BLE001 - feedback enrichment is optional
            pass
    knowledge = getattr(session, "app_knowledge", None)
    snapshot = getattr(knowledge, "snapshot", None)
    if callable(snapshot):
        try:
            names = sorted(str(name) for name in snapshot())[:max_n]
            return "，".join(names)
        except Exception:  # noqa: BLE001 - preserve the original launch failure
            pass
    return ""


def _receipt_package_candidates(available: str) -> list[str]:
    """Extract only package-shaped entries from the rendered failure list."""

    packages: list[str] = []
    seen: set[str] = set()
    for candidate in re.split(r"[,，]", str(available or "")):
        package = "".join(candidate.split())
        if not _PACKAGE_NAME_RE.fullmatch(package) or package in seen:
            continue
        seen.add(package)
        packages.append(package)
    return packages


def _remember_unknown_launch(session, config, app_name: str, available: str) -> None:
    """Keep receipt-backed unknown-launch evidence in this session only."""

    if not getattr(config, "implicit_alias_enabled", True):
        return
    remember = getattr(session, "record_failed_launch", None)
    if callable(remember):
        try:
            remember(app_name, _receipt_package_candidates(available))
        except Exception:  # noqa: BLE001 - preserve the original failure receipt
            return


def _ranked_app_packages(resolution, *, max_n: int) -> str:
    """Render ranked package candidates without losing receipt evidence."""

    packages = [candidate.package for candidate in resolution.candidates[:max_n]]
    return "，".join(dict.fromkeys(packages))


def _record_resolution_attempt(session, resolution) -> None:
    """Best-effort custom trace event through the production redactor."""

    recorder = getattr(session, "resolution_trace_recorder", None)
    if not callable(recorder):
        return
    try:
        payload = resolution.to_trace()
        recorder(
            "resolution_attempt",
            mention=payload["mention"],
            candidates=payload["candidates"],
            decision=payload["decision"],
            winner=payload["winner"],
            match_type=payload["match_type"],
            authority=payload["authority"],
            decision_basis=payload["decision_basis"],
            reason=payload["reason"],
        )
    except Exception:  # noqa: BLE001 - trace cannot change launch semantics
        return


def _record_implicit_aliases(
    session, config, *, app_name: str, package: str
) -> None:
    """Persist aliases only for exact run-local failure-candidate matches."""

    if (
        not getattr(config, "app_kb_enabled", True)
        or not getattr(config, "implicit_alias_enabled", True)
    ):
        return
    used_term = "".join(str(app_name or "").split())
    resolved_package = "".join(str(package or "").split())
    if (
        not _PACKAGE_NAME_RE.fullmatch(used_term)
        or used_term != resolved_package
    ):
        return

    matching_terms = getattr(session, "implicit_alias_terms_for", None)
    mark_written = getattr(session, "mark_implicit_alias_written", None)
    store = getattr(session, "app_store", None)
    if not callable(matching_terms) or store is None:
        return

    run_id = str(getattr(session, "implicit_alias_run_id", "") or "unknown")
    try:
        failed_terms = matching_terms(resolved_package)
    except Exception:  # noqa: BLE001 - memory feedback must never break launch
        return
    for failed_term in failed_terms:
        sensitive = SENSITIVE_PATTERN.search(failed_term) is not None
        if not should_save("learned", durable=True, sensitive=sensitive):
            continue
        try:
            store.upsert(
                {
                    "term": failed_term,
                    "label": resolved_package,
                    "package": resolved_package,
                    "kind": "learned",
                    "scope": "global",
                    "confidence": 0.9,
                    "success_count": 1,
                    "last_success": datetime.now(timezone.utc).isoformat(),
                    "stale": False,
                },
                evidence_note=f"implicit: run<{run_id}> 失败叫法自愈",
            )
        except Exception:  # noqa: BLE001 - memory feedback must never break launch
            continue
        if callable(mark_written):
            try:
                mark_written(failed_term)
            except Exception:  # noqa: BLE001 - write already landed; launch still wins
                pass


def _record_verified_launch(
    session, config, *, app_name: str, resolution, kb_match
) -> None:
    """Best-effort App-KB feedback after a device-confirmed launch."""

    if not getattr(config, "app_kb_enabled", True):
        return
    store = getattr(session, "app_store", None)
    knowledge = getattr(session, "app_knowledge", None)
    if store is None or knowledge is None:
        return

    try:
        package = str(resolution.package_name or "").strip()
        if kb_match is not None:
            matched_term = str(kb_match.get("term", ""))
            matched_kind = str(kb_match.get("kind", ""))
            sensitive = SENSITIVE_PATTERN.search(matched_term) is not None
            if should_save(matched_kind, durable=True, sensitive=sensitive):
                store.record_success(matched_term, package)
            return

        used_term = str(app_name or "").strip()
        canonical_label = ""
        device_id_getter = getattr(session, "_kb_device_id", None)
        device_id = device_id_getter() if callable(device_id_getter) else None
        if device_id:
            device_entries = store.entries(
                scope=f"device:{device_id}", kind="device"
            )
            canonical_label = next(
                (
                    str(entry.get("label", "")).strip()
                    for entry in device_entries
                    if entry.get("package") == package
                    and str(entry.get("label", "")).strip()
                ),
                "",
            )
        if not canonical_label and resolution.identity is not None:
            canonical_label = str(resolution.identity.display_name or "").strip()
        matching_entry = next(
            (
                entry
                for entry in store.entries(include_stale=False)
                if entry.get("package") == package
                and str(entry.get("term", "")).casefold() == used_term.casefold()
            ),
            None,
        )
        if matching_entry is not None:
            store.record_success(str(matching_entry["term"]), package)
            return
        if (
            not used_term
            or not canonical_label
            or used_term.casefold() == canonical_label.casefold()
        ):
            return

        sensitive = SENSITIVE_PATTERN.search(used_term) is not None
        if not should_save("learned", durable=True, sensitive=sensitive):
            return
        store.upsert(
            {
                "term": used_term,
                "label": canonical_label,
                "package": package,
                "kind": "learned",
                "scope": "global",
                "confidence": 0.9,
                "success_count": 1,
                "last_success": datetime.now(timezone.utc).isoformat(),
                "stale": False,
            }
        )
    except Exception:  # noqa: BLE001 - memory feedback must never break launch
        return


def _ok_with_obs(
    head: str, session, *, settle_ms: int | None = None
) -> list[dict]:
    """Merge an ``OK. <head>`` text block with the multimodal observation blocks.

    Success paths return a content ``list`` (text + image when the screen
    changed); the observation layer owns image dedup and fail-closed text
    fallback (``tools/_obs.py``). Error branches stay ``str`` (no image).

    Records ``session.last_tool_ok=True`` (all actuation success paths funnel
    here) so the finish review packet can mirror the last action (S2 §1.2).
    """

    mark_tool_ok(session)
    return [
        {"type": "text", "text": f"OK. {head}"},
        *auto_observation(session, settle_ms=settle_ms),
    ]


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
        settle_ms: int | None,
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
        return _ok_with_obs(
            f"{verb}{_mark_label(mark)}", session, settle_ms=settle_ms
        )

    def tap(
        target_mark_id: str | None = None,
        target_description: str | None = None,
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
        confirm_irreversible: bool = False,
        sensitive: bool = False,
    ) -> str | list[dict]:
        """Tap one on-screen element.

        Provide exactly one of ``target_mark_id`` (a mark from the latest
        observation) or ``target_description`` (natural language, resolved to a
        unique mark; ambiguity returns candidates and does not tap).

        Always pass ``intent`` (this step's goal, e.g. 把出发地改成上海).
        ``note`` optionally records what you discovered this step.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。

        Safety (wary mode): if the target looks risky (irreversible commit /
        credential field), the call is NOT executed and a warning is returned
        instead — resend with ``confirm_irreversible=true`` to actually act. Set
        ``sensitive=true`` to self-declare a call you want double-checked.
        """

        return _tap_like("tap", target_mark_id, target_description, settle_ms)

    def long_press(
        target_mark_id: str | None = None,
        target_description: str | None = None,
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
        confirm_irreversible: bool = False,
        sensitive: bool = False,
    ) -> str | list[dict]:
        """Long-press one on-screen element (same addressing as ``tap``).

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step. Resend with ``confirm_irreversible=true``
        after a safety warning; set ``sensitive=true`` to self-declare.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        return _tap_like(
            "long_press", target_mark_id, target_description, settle_ms
        )

    def type_text(
        text: str,
        target_mark_id: str | None = None,
        target_description: str | None = None,
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
        confirm_irreversible: bool = False,
        sensitive: bool = False,
    ) -> str | list[dict]:
        """Type ``text`` into a field.

        If a target is given, the field is tapped to focus first. Text is
        entered through the ADB keyboard (switched in and restored when the
        device layer supports it).

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step. Resend with ``confirm_irreversible=true``
        after a safety warning; set ``sensitive=true`` to self-declare.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
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
        return _ok_with_obs(
            f"已输入 {preview!r}", session, settle_ms=settle_ms
        )

    def scroll(
        direction: Literal["up", "down", "left", "right"],
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
    ) -> str | list[dict]:
        """Scroll the screen by a mid-screen swipe in ``direction``.

        ``direction`` is the content scroll direction (``down`` reveals content
        below by swiping upward).

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        # Endpoints as 0-1000 relative points; converted to pixels through the
        # single conversion point (session.relative_to_abs -> v2/coords.py).
        moves = {
            "down": (500, 750, 500, 250),
            "up": (500, 250, 500, 750),
            "left": (750, 500, 250, 500),
            "right": (250, 500, 750, 500),
        }
        if direction not in moves:
            return _fail(
                session,
                f"error: unknown direction {direction!r}; use up|down|left|right",
            )
        rsx, rsy, rex, rey = moves[direction]
        sx, sy = session.relative_to_abs(rsx, rsy)
        ex, ey = session.relative_to_abs(rex, rey)
        device.swipe(sx, sy, ex, ey, device_id=device_id)
        return _ok_with_obs(f"scroll {direction}", session, settle_ms=settle_ms)

    def swipe(
        start: list[int],
        end: list[int],
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
    ) -> str | list[dict]:
        """Swipe between two 0-1000 relative points (coordinate fallback).

        Prefer ``scroll`` for list navigation. ``start``/``end`` are ``[x, y]``
        in 0-1000 relative coordinates and are converted to absolute pixels.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        if not (isinstance(start, (list, tuple)) and len(start) == 2):
            return _fail(session, "error: start must be [x, y] in 0-1000 relative coords")
        if not (isinstance(end, (list, tuple)) and len(end) == 2):
            return _fail(session, "error: end must be [x, y] in 0-1000 relative coords")
        sx, sy = session.relative_to_abs(int(start[0]), int(start[1]))
        ex, ey = session.relative_to_abs(int(end[0]), int(end[1]))
        device.swipe(sx, sy, ex, ey, device_id=device_id)
        return _ok_with_obs(
            f"swipe ({start[0]},{start[1]})->({end[0]},{end[1]})",
            session,
            settle_ms=settle_ms,
        )

    def back(
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
    ) -> str | list[dict]:
        """Press the system Back button.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        device.back(device_id=device_id)
        return _ok_with_obs("back", session, settle_ms=settle_ms)

    def home(
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
    ) -> str | list[dict]:
        """Press the system Home button.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        device.home(device_id=device_id)
        return _ok_with_obs("home", session, settle_ms=settle_ms)

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
        settle_ms: int | None = None,
        confirm_irreversible: bool = False,
        sensitive: bool = False,
    ) -> str | list[dict]:
        """Launch an installed app by name.

        The name is resolved through the app registry / launch policy. Unknown
        or denied apps return an error string and are never launched.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step. Resend with ``confirm_irreversible=true``
        after a safety warning; set ``sensitive=true`` to self-declare.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        # Resolve against the device's real installed inventory (fail-closed):
        # without it a static-registry app that is NOT installed would read as
        # "resolved" and the launch failure would be swallowed (P0 #5).
        inventory = None
        get_inventory = getattr(device, "get_installed_app_inventory", None)
        if callable(get_inventory):
            try:
                inventory = get_inventory(device_id)
            except Exception:  # noqa: BLE001 - best-effort; resolver degrades to static
                inventory = None

        learning = getattr(session, "app_knowledge", None)
        raw_name_resolution = resolve_app_name(
            session, config, app_name, inventory=inventory
        )
        name_resolution = raw_name_resolution
        resolution = None
        if raw_name_resolution.status == "resolved" and raw_name_resolution.winner:
            resolution = authorize_app_candidate(
                raw_name_resolution.winner.package,
                inventory=inventory,
                resolver=DEFAULT_LAUNCH_TARGET_RESOLVER,
            )
        elif raw_name_resolution.status == "ambiguous":
            allowed_candidates = []
            for candidate in raw_name_resolution.candidates:
                authorized = authorize_app_candidate(
                    candidate.package,
                    inventory=inventory,
                    resolver=DEFAULT_LAUNCH_TARGET_RESOLVER,
                )
                if authorized.status == "resolved":
                    allowed_candidates.append(candidate)
            name_resolution = decide_name(
                app_name,
                allowed_candidates,
                settings=ResolverSettings.from_config(config),
            )
            if name_resolution.status == "resolved" and name_resolution.winner:
                resolution = authorize_app_candidate(
                    name_resolution.winner.package,
                    inventory=inventory,
                    resolver=DEFAULT_LAUNCH_TARGET_RESOLVER,
                )
        _record_resolution_attempt(session, name_resolution)
        name_winner = name_resolution.winner
        kb_match = (
            name_winner.source_entry
            if name_winner is not None and name_winner.source_entry is not None
            else None
        )
        status = resolution.status if resolution is not None else name_resolution.status
        if status == "resolved" and resolution.package_name:
            launched = device.launch_app(
                resolution.package_name,
                device_id=device_id,
                package_candidates=[resolution.package_name],
                inventory=inventory,
                learning=learning,
            )
            if not launched:
                return _fail(
                    session,
                    f"error: 未能启动 {app_name!r}（{resolution.package_name}）——设备返回启动失败，"
                    "可 read_screen 重新观测后重试。",
                )
            record_launch = getattr(session, "record_launched_app", None)
            if callable(record_launch):
                try:
                    record_launch(resolution.package_name)
                except Exception:  # noqa: BLE001 - experience mirror is observe-only
                    pass
            _record_verified_launch(
                session,
                config,
                app_name=app_name,
                resolution=resolution,
                kb_match=kb_match,
            )
            _record_implicit_aliases(
                session,
                config,
                app_name=app_name,
                package=resolution.package_name,
            )
            return _ok_with_obs(
                f"launched {app_name} ({resolution.package_name})",
                session,
                settle_ms=settle_ms,
            )
        if status == "ambiguous":
            top_k = max(1, int(getattr(config, "resolver_top_k", 10)))
            names = [
                f"{candidate.package}"
                f"(rank_score={candidate.rank_score:.3f}, "
                f"{candidate.source_route}/{candidate.match_type})"
                for candidate in name_resolution.candidates[:top_k]
            ]
            return _fail(
                session,
                f"ambiguous app {app_name!r}: {', '.join(names)} — be more specific",
            )
        if status == "denied":
            return _fail(session, f"denied: {app_name!r} is not launch-authorized")
        if status == "not_installed":
            return _fail(
                session,
                f"error: {app_name!r} 未安装在这台设备上（{resolution.package_name}），无法启动。",
            )
        top_k = max(1, int(getattr(config, "resolver_top_k", 10)))
        ranked = _ranked_app_packages(name_resolution, max_n=top_k)
        available = _available_app_names(session, max_n=top_k)
        hints = []
        if ranked:
            hints.append(f"排序候选：{ranked}")
        if available:
            hints.append(f"本机可用应用：{available}")
        suffix = "；" + "；".join(hints) if hints else ""
        evidence_candidates = "，".join(
            value for value in (ranked, available) if value
        )
        _remember_unknown_launch(session, config, app_name, evidence_candidates)
        return _fail(
            session,
            f"unknown app {app_name!r}: not in registry/inventory — cannot launch"
            f"{suffix}",
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
