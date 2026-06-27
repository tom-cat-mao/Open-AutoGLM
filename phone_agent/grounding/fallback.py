"""Fallback composition for ordered mark providers."""

from __future__ import annotations

import time
from typing import Any

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.grounding.provider import MarkProvider, MarkProviderHint, MarkProviderResult, ScreenBinding


class FallbackMarkProvider:
    """Run providers in order and stop at the first successful mark set."""

    name = "fallback"
    version = "ordered"
    allow_raw_hints = True

    def __init__(self, providers: list[MarkProvider], *, composition_metadata: dict[str, Any] | None = None) -> None:
        self.providers = [provider for provider in providers if provider is not None]
        self.composition_metadata = _safe_composition_metadata(composition_metadata or {})

    def provide_marks(
        self,
        screenshot: Any,
        screen_binding: ScreenBinding,
        hints: list[MarkProviderHint] | None = None,
        timeout: float | None = None,
    ) -> MarkProviderResult:
        started = time.perf_counter()
        summaries: list[dict[str, Any]] = []
        summaries.extend(_synthetic_skip_rows(self.composition_metadata))
        collected_marks: list[Any] = []
        collected_candidates: list[Any] = []
        collected_structures: list[dict[str, Any]] = []
        last_result: MarkProviderResult | None = None
        usable_result: MarkProviderResult | None = None
        for index, provider in enumerate(self.providers):
            if usable_result is not None and getattr(provider, "structure_mode", None) != "screen":
                continue
            provider_hints = hints if getattr(provider, "allow_raw_hints", False) else _redact_hints(hints or [])
            result = provider.provide_marks(screenshot, screen_binding, hints=provider_hints, timeout=timeout)
            last_result = result
            usable = _result_is_usable(result, hints or [])
            summaries.append(_fallback_row(result, usable=usable))
            supplemental_screen = usable_result is not None and getattr(provider, "structure_mode", None) == "screen"
            if result.success and result.marks:
                if not supplemental_screen:
                    collected_marks.extend(result.marks)
                collected_candidates.extend(result.candidates or result.marks)
                collected_structures.extend(_result_structures(result))
            elif result.success:
                collected_candidates.extend(result.candidates or [])
                collected_structures.extend(_result_structures(result))
            if result.success and result.marks and usable and usable_result is None:
                usable_result = result
                if _has_later_screen_structure_provider(self.providers, index + 1):
                    continue
                return MarkProviderResult(
                    success=True,
                    provider=result.provider,
                    screen_id=result.screen_id,
                    raw_screenshot_hash=result.raw_screenshot_hash,
                    provider_input_hash=result.provider_input_hash,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    marks=collected_marks,
                    candidates=collected_candidates,
                    candidate_count=len(collected_candidates),
                    status="success",
                    hints=result.hints,
                    metadata=_fallback_metadata(summaries, self.composition_metadata),
                    screen_structures=collected_structures,
                )
        if usable_result is not None:
            return MarkProviderResult(
                success=True,
                provider=usable_result.provider,
                screen_id=usable_result.screen_id,
                raw_screenshot_hash=usable_result.raw_screenshot_hash,
                provider_input_hash=usable_result.provider_input_hash,
                latency_ms=int((time.perf_counter() - started) * 1000),
                marks=collected_marks,
                candidates=collected_candidates,
                candidate_count=len(collected_candidates),
                status="success",
                hints=usable_result.hints,
                metadata=_fallback_metadata(summaries, self.composition_metadata),
                screen_structures=collected_structures,
            )
        if collected_marks:
            if _hint_terms(hints or []):
                return MarkProviderResult(
                    success=False,
                    provider=self.name,
                    failure_code="grounding_no_usable_candidate",
                    message="no provider marks matched hint",
                    screen_id=screen_binding.screen_id,
                    raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                    provider_input_hash=(last_result.provider_input_hash if last_result else None),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    marks=[],
                    candidates=collected_candidates,
                    candidate_count=len(collected_candidates),
                    status="grounding_no_usable_candidate",
                    hints=(last_result.hints if last_result else [hint.redacted_summary() for hint in hints or []]),
                    metadata=_fallback_metadata(summaries, self.composition_metadata),
                    screen_structures=collected_structures,
                )
            return MarkProviderResult(
                success=True,
                provider=self.name,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=(last_result.provider_input_hash if last_result else None),
                latency_ms=int((time.perf_counter() - started) * 1000),
                marks=collected_marks,
                candidates=collected_candidates,
                candidate_count=len(collected_candidates),
                status="success",
                hints=(last_result.hints if last_result else [hint.redacted_summary() for hint in hints or []]),
                metadata=_fallback_metadata(summaries, self.composition_metadata),
                screen_structures=collected_structures,
            )
        return MarkProviderResult(
            success=False,
            provider=self.name,
            failure_code=(last_result.failure_code if last_result else "provider_unavailable"),
            message=(last_result.message if last_result else "no providers"),
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=(last_result.provider_input_hash if last_result else None),
            latency_ms=int((time.perf_counter() - started) * 1000),
            marks=[],
            candidates=collected_candidates or (last_result.candidates if last_result else []),
            candidate_count=len(collected_candidates) if collected_candidates else (last_result.candidate_count if last_result else 0),
            status=(last_result.status if last_result else "provider_unavailable"),
            hints=(last_result.hints if last_result else [hint.redacted_summary() for hint in hints or []]),
            metadata=_fallback_metadata(summaries, self.composition_metadata),
            screen_structures=collected_structures,
        )


def _result_structures(result: MarkProviderResult) -> list[dict[str, Any]]:
    structures: list[dict[str, Any]] = []
    if isinstance(result.screen_structures, list) and result.screen_structures:
        raw_items = result.screen_structures
    elif isinstance(result.screen_structure, dict):
        raw_items = [result.screen_structure]
    else:
        raw_items = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("source_provider") or item.get("provider") or result.provider),
            str(item.get("structure_kind") or "accessibility"),
            str(item.get("structure_digest") or item.get("topology_digest") or item.get("visual_structure_digest") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        structures.append(item)
    return structures


def _fallback_row(result: MarkProviderResult, *, usable: bool) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "success": result.success,
        "failure_code": result.failure_code,
        "candidate_count": result.candidate_count,
        "mark_count": len(result.marks or []),
        "structure_count": len(_result_structures(result)),
        "latency_ms": result.latency_ms,
        "usable": usable,
        "skip_reason": None,
    }


def _fallback_metadata(summaries: list[dict[str, Any]], composition_metadata: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"fallback_chain": summaries}
    if composition_metadata:
        metadata["hybrid_factory"] = composition_metadata
    return metadata


def _safe_composition_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if value.get("hybrid_mode") is not True:
        return {}
    provider_order = value.get("provider_order")
    if not isinstance(provider_order, list):
        provider_order = []
    skip_reason = value.get("accessibility_child_skip_reason")
    if skip_reason not in {None, "accessibility_dump_callback_missing", "skip_accessibility_provider"}:
        skip_reason = None
    return {
        "hybrid_mode": True,
        "accessibility_child_enabled": value.get("accessibility_child_enabled") is True,
        "accessibility_child_skip_reason": skip_reason,
        "provider_order": [str(item)[:64] for item in provider_order[:8]],
    }


def _synthetic_skip_rows(composition_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    skip_reason = composition_metadata.get("accessibility_child_skip_reason")
    if not skip_reason:
        return []
    return [
        {
            "provider": "accessibility_tree",
            "success": False,
            "failure_code": skip_reason,
            "candidate_count": 0,
            "mark_count": 0,
            "structure_count": 0,
            "latency_ms": 0,
            "usable": False,
            "skip_reason": skip_reason,
        }
    ]


def _has_later_screen_structure_provider(providers: list[MarkProvider], start_index: int) -> bool:
    return any(getattr(provider, "structure_mode", None) == "screen" for provider in providers[start_index:])


def _redact_hints(hints: list[MarkProviderHint]) -> list[MarkProviderHint]:
    redacted: list[MarkProviderHint] = []
    for hint in hints:
        text = str(sanitize_context_payload(hint.text, "message", consumer="inject")).strip()
        if not text:
            continue
        redacted.append(
            MarkProviderHint(
                text=text[:240],
                source=str(sanitize_context_payload(hint.source, "source", consumer="inject")).strip()[:64] or "hint",
                role=str(sanitize_context_payload(hint.role or "", "message", consumer="inject")).strip()[:240] or None,
                intent=str(sanitize_context_payload(hint.intent or "", "message", consumer="inject")).strip()[:240] or None,
                action=str(sanitize_context_payload(hint.action or "", "action", consumer="inject")).strip()[:64] or None,
            )
        )
    return redacted


def _result_is_usable(result: MarkProviderResult, hints: list[MarkProviderHint]) -> bool:
    if not result.success or not result.marks:
        return False
    terms = _hint_terms(hints)
    if not terms:
        return True
    for mark in result.marks:
        haystack = " ".join(
            str(value or "").casefold()
            for value in (
                getattr(mark, "role", None),
                getattr(mark, "text_summary", None),
                getattr(mark, "source", None),
            )
        )
        if any(term in haystack for term in terms):
            return True
    return False


def _hint_terms(hints: list[MarkProviderHint]) -> list[str]:
    terms: list[str] = []
    for hint in hints:
        for value in (hint.text, hint.role, hint.intent, hint.action):
            for token in _tokenize_hint(value):
                if token not in terms:
                    terms.append(token)
    return terms[:12]


def _tokenize_hint(value: str | None) -> list[str]:
    text = str(value or "").casefold()
    raw_tokens = []
    current = []
    for char in text:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current.append(char)
        else:
            if current:
                raw_tokens.append("".join(current))
                current = []
    if current:
        raw_tokens.append("".join(current))
    tokens: list[str] = []
    for token in raw_tokens:
        if len(token) >= 2:
            tokens.append(token)
        if any("\u4e00" <= char <= "\u9fff" for char in token):
            tokens.extend(token[index : index + 2] for index in range(0, max(0, len(token) - 1)))
    return [token for token in tokens if len(token) >= 2]
