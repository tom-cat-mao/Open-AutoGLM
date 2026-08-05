"""Fallback composition for ordered mark providers."""

from __future__ import annotations

import re
import time
from typing import Any

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.provider import MarkProvider, MarkProviderHint, MarkProviderResult, ScreenBinding


# R3: container roles whose full-width bbox marks are never executable targets.
_CONTAINER_ROLES = frozenset(
    {
        "listview",
        "recyclerview",
        "framelayout",
        "view",
        "viewgroup",
        "horizontalscrollview",
        "scrollview",
    }
)
# R3: a mark is a container only when it spans >= 90% of the normalized screen.
_CONTAINER_MIN_WIDTH = 900

# R1: maximal CJK runs and maximal non-CJK alnum runs inside a raw token.
_RUN_RE = re.compile(r"[\u4e00-\u9fff]+|[0-9a-zA-Z]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


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
        max_size: int | None = None,
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
            # R1: forward a per-call max_size tier only to providers that
            # support it (the shared LocateAnything singleton); the observation
            # fallback chain never overrides, so this is a no-op by default.
            child_kwargs: dict[str, Any] = {}
            if max_size is not None and isinstance(provider, LocateAnythingMLXProvider):
                child_kwargs["max_size"] = max_size
            result = provider.provide_marks(
                screenshot,
                screen_binding,
                hints=provider_hints,
                timeout=timeout,
                **child_kwargs,
            )
            last_result = result
            usable, usable_reason = _result_usability(
                result,
                hints or [],
                # D3: query-conditioned providers (LocateAnything) located the
                # region FROM the hint, so their marks are usable by
                # construction; the hint text no longer appears in the marks.
                hint_conditioned=getattr(provider, "allow_raw_hints", False),
            )
            summaries.append(_fallback_row(result, usable=usable, usable_reason=usable_reason))
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
            if _hint_has_words(hints or []):
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


def _fallback_row(result: MarkProviderResult, *, usable: bool, usable_reason: str | None = None) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "success": result.success,
        "failure_code": result.failure_code,
        "candidate_count": result.candidate_count,
        "mark_count": len(result.marks or []),
        "structure_count": len(_result_structures(result)),
        "latency_ms": result.latency_ms,
        "usable": usable,
        "usable_reason": usable_reason,
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
            "usable_reason": "no_marks",
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
    usable, _ = _result_usability(result, hints)
    return usable


def _result_usability(
    result: MarkProviderResult,
    hints: list[MarkProviderHint],
    *,
    hint_conditioned: bool = False,
) -> tuple[bool, str]:
    """R2/R3/R4: decide whether a provider result is usable for this hint set.

    A ``hint_conditioned`` result (the provider consumed the raw hint as its
    query — LocateAnything, Fake) is usable by construction whenever it returns
    marks: the region was located FROM the hint, so no text echo is needed to
    prove the match. D3 removed that echo (marks carry a neutral label), so the
    text-token check would otherwise wrongly reject the visual provider.

    Matching is tiered but unioned for non-conditioned results: a
    significant-term hit on a target-like mark makes the result usable,
    otherwise the purified fallback tokens (2-char CJK windows, short words)
    get the same chance.  Every hit must land on a target-like mark (R3), so a
    calendar page whose marks are only full-width row containers and a
    month-title label stays unusable and the chain proceeds to the visual
    provider.

    Returns ``(usable, reason)`` where reason is a short trace label:
    ``provider_query_matched`` / ``no_marks`` / ``no_hint_words`` /
    ``significant_hit`` / ``fallback_token_hit`` / ``significant_miss`` /
    ``fallback_token_miss`` / ``all_tokens_purged``.
    """
    if not result.success or not result.marks:
        return False, "no_marks"
    if hint_conditioned:
        return True, "provider_query_matched"
    if not _hint_has_words(hints):
        # R4: hint with no words at all keeps the legacy behavior (any marks usable).
        return True, "no_hint_words"
    significant = _significant_hint_terms(hints)
    if significant and _any_target_like_hit(result.marks, significant):
        return True, "significant_hit"
    terms = _hint_terms(hints)
    if not terms:
        # R4: every word was purged by R1 (e.g. "1") — treated as a real hint,
        # never an unconditional usable.
        return False, "all_tokens_purged"
    if _any_target_like_hit(result.marks, terms):
        return True, "fallback_token_hit"
    return False, ("significant_miss" if significant else "fallback_token_miss")


def _hint_has_words(hints: list[MarkProviderHint]) -> bool:
    """True when any hint field carries at least one alnum/CJK character."""
    for hint in hints:
        for value in (hint.text, hint.role, hint.intent, hint.action):
            if any(char.isalnum() for char in str(value or "")):
                return True
    return False


def _any_target_like_hit(marks: list[Any], terms: list[str]) -> bool:
    """True when any significant/fallback term hits a target-like mark (R2+R3)."""
    for mark in marks:
        if not _is_target_like_mark(mark):
            continue
        haystack = _mark_haystack(mark)
        if any(term in haystack for term in terms):
            return True
    return False


def _mark_haystack(mark: Any) -> str:
    return " ".join(
        str(value or "").casefold()
        for value in (
            getattr(mark, "role", None),
            getattr(mark, "text_summary", None),
            getattr(mark, "source", None),
        )
    )


def _is_target_like_mark(mark: Any) -> bool:
    """R3: a mark can carry a usable hit only when it is target-like.

    Full-width container roles (>= 90% screen width in 0-1000 space) are not
    executable targets, and neither are pure-display ``TextView`` labels
    (non-clickable, e.g. a calendar month title).  Interactive ``TextView``
    rows (clickable/focusable, confidence 1.0) stay target-like.
    TODO(grounding-usability): weak-hit rescue — a significant hit on a pure
    display label could count when no other mark matches; currently such hits
    are excluded outright so visual providers get a chance on custom-painted
    grids (e.g. calendar day cells the tree never exposes).
    """
    role = str(getattr(mark, "role", None) or "").casefold()
    if role == "textview":
        confidence = getattr(mark, "confidence", None)
        if confidence is None or confidence < 1.0:
            return False
    bbox = getattr(mark, "bbox", None)
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        width = int(bbox[2]) - int(bbox[0])
        if width >= _CONTAINER_MIN_WIDTH and role in _CONTAINER_ROLES:
            return False
    return True


def _hint_terms(hints: list[MarkProviderHint]) -> list[str]:
    terms: list[str] = []
    for hint in hints:
        for value in (hint.text, hint.role, hint.intent, hint.action):
            for token in _tokenize_hint(value):
                if token not in terms:
                    terms.append(token)
    # R1 purging raises the token count (full string + alnum runs + CJK
    # bigrams); the legacy 12-term cap silently dropped later bigrams — for a
    # realistic long task sentence the meaningful tokens ("吉隆") fell off
    # and every screen missed, over-triggering the visual provider.
    return terms[:48]


def _significant_hint_terms(hints: list[MarkProviderHint]) -> list[str]:
    """R2: long, distinctive hint terms a usable result must hit.

    A term is significant when it is a whole raw token of >= 4 alnum chars
    ("10月1日", "2026年10月1日") or contains a CJK / non-CJK alnum run of
    >= 4 chars ("2026", "chester117", "从北京到吉隆坡的机票").  Short CJK
    words ("携程", "吉隆坡") stay fallback-only.
    """
    terms: list[str] = []
    for hint in hints:
        for value in (hint.text, hint.role, hint.intent, hint.action):
            text = str(value or "").casefold()
            for raw in _split_alnum_runs(text):
                if len(raw) >= 4 and raw not in terms:
                    terms.append(raw)
                for run in _RUN_RE.findall(raw):
                    if len(run) >= 4 and run not in terms:
                        terms.append(run)
    return terms[:24]


def _tokenize_hint(value: str | None) -> list[str]:
    """R1: purify a hint into matchable tokens.

    Drops single-character tokens and pure-numeric tokens shorter than 4
    digits ("20"/"02"), keeps whole raw tokens plus their non-CJK alnum runs
    ("2026", "chester117"), and emits 2-char CJK sliding windows only within
    maximal CJK runs — so digit/CJK cross-boundary garbage ("6年", "年1")
    from date strings like "2026年10月1日" can never match.
    """
    text = str(value or "").casefold()
    tokens: list[str] = []
    for raw in _split_alnum_runs(text):
        if len(raw) == 1:
            continue
        if raw.isdigit() and len(raw) < 4:
            continue
        tokens.append(raw)
        tokens.extend(_cjk_bigram_windows(raw))
        for run in _RUN_RE.findall(raw):
            if len(run) == 1:
                continue
            if run.isdigit() and len(run) < 4:
                continue
            tokens.append(run)
    return _dedupe(tokens)


def _split_alnum_runs(text: str) -> list[str]:
    runs: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isalnum():
            current.append(char)
        else:
            if current:
                runs.append("".join(current))
                current = []
    if current:
        runs.append("".join(current))
    return runs


def _cjk_bigram_windows(raw: str) -> list[str]:
    return [run[index : index + 2] for run in _CJK_RUN_RE.findall(raw) for index in range(len(run) - 1)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique
