"""Optional legacy vocabulary adapters excluded from typed Goal truth."""

from __future__ import annotations

from collections import Counter
from threading import Lock
from typing import Any, Protocol


class CompatibilityTelemetry:
    """Process-local counters used only to support evidence-based migration."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = Lock()

    def increment(self, counter: str) -> None:
        with self._lock:
            self._counts[counter] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


COMPATIBILITY_TELEMETRY = CompatibilityTelemetry()


class PageSignalAdapter(Protocol):
    """Compatibility-only page signal classifier."""

    def detail_signal(
        self,
        observation: dict[str, Any] | None,
        text_blob: str,
        expected_page_type: str,
    ) -> bool: ...

    def feed_signal(
        self, observation: dict[str, Any] | None, text_blob: str
    ) -> bool: ...


class LegacyPageSignalAdapter:
    """Old feed/player vocabulary retained outside generic verifier control flow."""

    def detail_signal(
        self,
        observation: dict[str, Any] | None,
        text_blob: str,
        expected_page_type: str,
    ) -> bool:
        COMPATIBILITY_TELEMETRY.increment("legacy_page_detail_signal")
        if expected_page_type == "input_focused":
            return False
        terms = {
            "播放",
            "播放器",
            "全屏",
            "暂停",
            "弹幕",
            "详情",
            "作者",
            "关注",
            "评论",
            "like",
            "comment",
            "player",
            "pause",
            "fullscreen",
            "detail",
        }
        return any(term in text_blob for term in terms)

    def feed_signal(self, observation: dict[str, Any] | None, text_blob: str) -> bool:
        COMPATIBILITY_TELEMETRY.increment("legacy_page_feed_signal")
        terms = {
            "推荐",
            "首页",
            "搜索结果",
            "综合",
            "筛选",
            "feed",
            "search results",
            "recommend",
        }
        if any(term in text_blob for term in terms):
            return True
        if isinstance(observation, dict):
            object_registry = observation.get("object_registry")
            if isinstance(object_registry, dict) and object_registry.get(
                "object_count", 0
            ):
                return True
        return False


DEFAULT_LEGACY_PAGE_SIGNAL_ADAPTER = LegacyPageSignalAdapter()


def observe_legacy_page_signals(
    *, expected: dict[str, Any] | None, observation: dict[str, Any] | None
) -> None:
    """Record legacy classifier usage without returning authoritative signals."""

    expected_page_type = (
        str(expected.get("expected_page_type") or "")
        if isinstance(expected, dict)
        else ""
    )
    text_blob = str(observation or {}).casefold()
    DEFAULT_LEGACY_PAGE_SIGNAL_ADAPTER.detail_signal(
        observation, text_blob, expected_page_type
    )
    DEFAULT_LEGACY_PAGE_SIGNAL_ADAPTER.feed_signal(observation, text_blob)
