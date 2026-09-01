"""Unified app-name candidate generation, ranking, and decision.

The resolver is deliberately split into two phases:

* L0/L2 produce high-recall package candidates from exact, lexical, pinyin,
  and optional vector-index evidence.
* L3 ranks the package-deduplicated candidates with explicit similarity and
  prior weights, then applies a score threshold plus a top-two margin.

This module identifies packages only.  Installation and launch authorization
remain the responsibility of :mod:`phone_agent.config.app_registry`; a name
match must never grant launch authority.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
import math
from pathlib import Path
import re
import unicodedata
from typing import Any, Literal

NameRoute = Literal["exact", "lexical", "pinyin", "embedding"]
NameDecision = Literal["resolved", "ambiguous", "unknown"]
EmbeddingSearch = Callable[[str, int], Sequence[Mapping[str, Any]]]

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PUNCT_RE = re.compile(r"[^0-9a-z\u3400-\u9fff]+")
_KIND_PRIOR = {
    "device": 1.0,
    "learned": 0.9,
    "user": 1.0,
    "registry": 0.95,
    # Existing App-KB files also contain explicit alias rows.
    "alias": 0.9,
}
_ROUTE_ORDER: dict[NameRoute, int] = {
    "exact": 0,
    "lexical": 1,
    "pinyin": 2,
    "embedding": 3,
}

# A deliberately small, dependency-free traditional-to-simplified bridge.
# NFKC already owns full/half-width folding; this table covers common app-name
# vocabulary without pretending to be a general Chinese converter.
_COMMON_TRADITIONAL = str.maketrans(
    {
        "臺": "台",
        "灣": "湾",
        "網": "网",
        "雲": "云",
        "圖": "图",
        "書": "书",
        "訊": "讯",
        "視": "视",
        "頻": "频",
        "應": "应",
        "用": "用",
        "設": "设",
        "錄": "录",
        "檔": "档",
        "樂": "乐",
        "讀": "读",
        "點": "点",
        "買": "买",
        "賣": "卖",
        "鐵": "铁",
        "車": "车",
    }
)


@dataclass(frozen=True)
class NameSource:
    """One registry/KB spelling that may generate a package candidate."""

    package: str
    term: str
    kind: str
    label: str = ""
    success_count: int = 0
    last_success: str | None = None
    ref_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class AppNameCandidate:
    """One ranked, package-deduplicated app-name candidate."""

    package: str
    source_route: NameRoute
    sim: float
    prior: float
    score: float
    matched_term: str
    provenance: tuple[str, ...] = ()
    source_ref: str | None = None
    source_entry: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        """Return the trace/receipt-safe public projection."""

        return {
            "package": self.package,
            "source_route": self.source_route,
            "sim": round(self.sim, 6),
            "prior": round(self.prior, 6),
            "score": round(self.score, 6),
            "matched_term": self.matched_term,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True)
class AppNameResolution:
    """Structured name-resolution result; never an execution receipt."""

    status: NameDecision
    mention: str
    candidates: tuple[AppNameCandidate, ...] = ()
    winner: AppNameCandidate | None = None

    def to_trace(self) -> dict[str, Any]:
        return {
            "mention": self.mention,
            "candidates": [item.to_dict() for item in self.candidates],
            "decision": self.status,
            "winner": self.winner.package if self.winner is not None else None,
        }


@dataclass(frozen=True)
class ResolverSettings:
    """Config projection used by the pure candidate/ranking core."""

    min_score: float = 0.90
    margin: float = 0.08
    top_k: int = 10
    lexical: bool = True
    pinyin: bool = True
    embed: bool = True
    w_sim: float = 0.8
    w_prior: float = 0.2

    @classmethod
    def from_config(cls, config: Any | None) -> "ResolverSettings":
        if config is None:
            return cls()
        return cls(
            min_score=float(getattr(config, "resolver_min_score", 0.90)),
            margin=float(getattr(config, "resolver_margin", 0.08)),
            top_k=int(getattr(config, "resolver_top_k", 10)),
            lexical=bool(getattr(config, "resolver_lexical", True)),
            pinyin=bool(getattr(config, "resolver_pinyin", True)),
            embed=bool(getattr(config, "resolver_embed", True)),
            w_sim=float(getattr(config, "resolver_w_sim", 0.8)),
            w_prior=float(getattr(config, "resolver_w_prior", 0.2)),
        )


def normalize_name(value: str) -> str:
    """L0 normalize with NFKC, common simplified forms, casefold, no spaces."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.translate(_COMMON_TRADITIONAL).casefold()
    return "".join(normalized.split())


def name_variants(value: str) -> tuple[str, ...]:
    """Return deterministic normalized and punctuation-light spellings."""

    normalized = normalize_name(value)
    compact = _PUNCT_RE.sub("", normalized)
    return tuple(dict.fromkeys(item for item in (normalized, compact) if item))


def _parse_last_success(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def source_prior(source: NameSource, *, now: datetime | None = None) -> float:
    """Compute the bounded L3 prior from kind, success count, and recency."""

    base = _KIND_PRIOR.get(source.kind, 0.85)
    count = max(0, int(source.success_count or 0))
    success_bonus = min(0.04, math.log2(1 + count) * 0.01)
    recent_bonus = 0.0
    last_success = _parse_last_success(source.last_success)
    if last_success is not None:
        current = now or datetime.now(timezone.utc)
        age_days = max(0.0, (current - last_success).total_seconds() / 86_400.0)
        recent_bonus = 0.02 * math.exp(-age_days / 90.0)
    return min(1.0, base + success_bonus + recent_bonus)


def _source_terms(entry: Mapping[str, Any]) -> list[str]:
    values: list[Any] = [entry.get("term"), entry.get("label")]
    mention_terms = entry.get("mention_terms", ())
    if isinstance(mention_terms, Sequence) and not isinstance(
        mention_terms, (str, bytes)
    ):
        values.extend(mention_terms)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = normalize_name(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def collect_name_sources(
    *,
    registry: Any | None = None,
    kb_entries: Iterable[Mapping[str, Any]] = (),
) -> list[NameSource]:
    """Flatten registry identities and applicable KB rows into spellings."""

    if registry is None:
        from phone_agent.config.apps import DEFAULT_APP_REGISTRY

        registry = DEFAULT_APP_REGISTRY

    sources: list[NameSource] = []
    for identity in getattr(registry, "identities", ()):
        if bool(getattr(identity, "observation_only", False)):
            continue
        terms = [
            getattr(identity, "canonical_id", ""),
            getattr(identity, "display_name", ""),
            *sorted(getattr(identity, "aliases", ())),
            *sorted(getattr(identity, "packages", ())),
        ]
        for package in sorted(getattr(identity, "packages", ())):
            for term in terms:
                if normalize_name(term):
                    sources.append(
                        NameSource(
                            package=str(package),
                            term=str(term),
                            label=str(getattr(identity, "display_name", "")),
                            kind="registry",
                            ref_id=f"registry:{getattr(identity, 'canonical_id', package)}",
                        )
                    )

    for entry in kb_entries:
        package = str(entry.get("app_package", entry.get("package", ""))).strip()
        if not package or bool(entry.get("stale", False)):
            continue
        try:
            success_count = max(0, int(entry.get("success_count", 0)))
        except (TypeError, ValueError):
            success_count = 0
        for term in _source_terms(entry):
            sources.append(
                NameSource(
                    package=package,
                    term=term,
                    label=str(entry.get("label", "")),
                    kind=str(entry.get("kind", "alias")),
                    success_count=success_count,
                    last_success=(
                        str(entry.get("last_success"))
                        if entry.get("last_success")
                        else None
                    ),
                    ref_id=str(entry.get("ref_id", "")) or None,
                    metadata=dict(entry),
                )
            )

    unique: dict[tuple[str, str, str, str | None], NameSource] = {}
    for source in sources:
        key = (source.package, normalize_name(source.term), source.kind, source.ref_id)
        unique.setdefault(key, source)
    return list(unique.values())


def _ngrams(value: str, size: int) -> set[str]:
    if not value:
        return set()
    if len(value) < size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


def lexical_similarity(mention: str, term: str) -> float:
    """Blend directional containment, char n-grams, and difflib similarity."""

    best = 0.0
    for query in name_variants(mention):
        for candidate in name_variants(term):
            if query == candidate:
                return 1.0
            ratio = SequenceMatcher(None, query, candidate).ratio()
            bigram = _dice(_ngrams(query, 2), _ngrams(candidate, 2))
            trigram = _dice(_ngrams(query, 3), _ngrams(candidate, 3))
            ngram = 0.6 * bigram + 0.4 * trigram
            containment = 0.0
            if len(candidate) >= 2 and candidate in query:
                containment = 0.86 + 0.10 * min(1.0, len(candidate) / len(query))
            elif len(query) >= 2 and query in candidate:
                containment = 0.72 + 0.12 * min(1.0, len(query) / len(candidate))
            best = max(best, ratio, ngram, containment)
    return min(1.0, best)


def mention_occurs(term: str, text: str) -> bool:
    """Return whether one source spelling occurs as an explicit mention.

    CJK aliases retain the WP-R2 substring contract.  Latin aliases require
    token boundaries so short names such as ``X`` do not fire inside words.
    Both paths share the L0 normalizer used by candidate generation.
    """

    term_text = unicodedata.normalize("NFKC", str(term or ""))
    term_text = term_text.translate(_COMMON_TRADITIONAL).casefold()
    text_value = unicodedata.normalize("NFKC", str(text or ""))
    text_value = text_value.translate(_COMMON_TRADITIONAL).casefold()
    normalized_term = " ".join(term_text.split())
    normalized_text = " ".join(text_value.split())
    if len(normalize_name(normalized_term)) < 2:
        return False
    if _CJK_RE.search(normalized_term):
        return normalized_term in normalize_name(normalized_text)
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(normalized_term)}(?![A-Za-z0-9_])",
            normalized_text,
        )
        is not None
    )


def _pinyin_forms(value: str) -> tuple[str, str] | None:
    if not _CJK_RE.search(str(value or "")):
        return None
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return None
    try:
        full = "".join(
            lazy_pinyin(str(value), style=Style.NORMAL, errors=lambda item: list(item))
        )
        initials = "".join(
            lazy_pinyin(
                str(value), style=Style.FIRST_LETTER, errors=lambda item: list(item)
            )
        )
    except Exception:  # noqa: BLE001 - the optional route is fail-open
        return None
    return normalize_name(full), normalize_name(initials)


def pinyin_similarity(mention: str, term: str) -> float:
    """Compare full pinyin and initials; unavailable pypinyin returns zero."""

    mention_forms = _pinyin_forms(mention)
    term_forms = _pinyin_forms(term)
    if mention_forms is None and term_forms is None:
        return 0.0
    mention_full, mention_initials = mention_forms or (normalize_name(mention),) * 2
    term_full, term_initials = term_forms or (normalize_name(term),) * 2
    full = lexical_similarity(mention_full, term_full)
    initials = lexical_similarity(mention_initials, term_initials)
    # Pinyin is weaker than literal evidence, especially for initials.
    return min(0.97, max(full * 0.97, initials * 0.92))


def _candidate(
    source: NameSource,
    route: NameRoute,
    sim: float,
    settings: ResolverSettings,
    *,
    provenance: str | None = None,
) -> AppNameCandidate:
    prior = source_prior(source)
    score = settings.w_sim * sim + settings.w_prior * prior
    return AppNameCandidate(
        package=source.package,
        source_route=route,
        sim=sim,
        prior=prior,
        score=score,
        matched_term=source.term,
        provenance=(provenance or f"{route}:{source.kind}:{source.term}",),
        source_ref=source.ref_id,
        source_entry=(dict(source.metadata) if source.metadata else None),
    )


def _better(candidate: AppNameCandidate, current: AppNameCandidate) -> bool:
    return (
        candidate.score,
        candidate.sim,
        candidate.prior,
        -_ROUTE_ORDER[candidate.source_route],
        candidate.matched_term,
    ) > (
        current.score,
        current.sim,
        current.prior,
        -_ROUTE_ORDER[current.source_route],
        current.matched_term,
    )


def generate_candidates(
    mention: str,
    *,
    registry: Any | None = None,
    kb_entries: Iterable[Mapping[str, Any]] = (),
    embedding_search: EmbeddingSearch | None = None,
    settings: ResolverSettings | None = None,
) -> list[AppNameCandidate]:
    """Run the four candidate routes and return package-deduplicated ranking."""

    active = settings or ResolverSettings()
    query = normalize_name(mention)
    if not query:
        return []
    sources = collect_name_sources(registry=registry, kb_entries=kb_entries)
    generated: list[AppNameCandidate] = []

    for source in sources:
        term = normalize_name(source.term)
        if not term:
            continue
        if query == term:
            generated.append(_candidate(source, "exact", 1.0, active))
        if active.lexical:
            sim = lexical_similarity(mention, source.term)
            if sim >= 0.35:
                generated.append(_candidate(source, "lexical", sim, active))
        if active.pinyin:
            sim = pinyin_similarity(mention, source.term)
            if sim >= 0.45:
                generated.append(_candidate(source, "pinyin", sim, active))

    if active.embed and embedding_search is not None:
        try:
            embedded = embedding_search(str(mention), max(active.top_k * 3, 20))
        except Exception:  # noqa: BLE001 - vector/model/index route is fail-open
            embedded = ()
        for item in embedded:
            package = str(item.get("package", item.get("app_package", ""))).strip()
            if not package:
                continue
            try:
                sim = max(0.0, min(1.0, float(item.get("sim", 0.0))))
            except (TypeError, ValueError):
                continue
            source = NameSource(
                package=package,
                term=str(item.get("term", item.get("label", package))),
                label=str(item.get("label", "")),
                kind=str(item.get("kind", "alias")),
                success_count=int(item.get("success_count", 0) or 0),
                last_success=(
                    str(item.get("last_success")) if item.get("last_success") else None
                ),
                ref_id=str(item.get("ref_id", "")) or None,
                metadata=dict(item.get("metadata", item)),
            )
            generated.append(
                _candidate(
                    source,
                    "embedding",
                    sim,
                    active,
                    provenance=f"embedding:{source.ref_id or source.term}",
                )
            )

    best_by_package: dict[str, AppNameCandidate] = {}
    provenance_by_package: dict[str, list[str]] = {}
    for item in generated:
        provenance = provenance_by_package.setdefault(item.package, [])
        for value in item.provenance:
            if value not in provenance:
                provenance.append(value)
        current = best_by_package.get(item.package)
        if current is None or _better(item, current):
            best_by_package[item.package] = item

    ranked = [
        AppNameCandidate(
            package=item.package,
            source_route=item.source_route,
            sim=item.sim,
            prior=item.prior,
            score=item.score,
            matched_term=item.matched_term,
            provenance=tuple(provenance_by_package[item.package]),
            source_ref=item.source_ref,
            source_entry=item.source_entry,
        )
        for item in best_by_package.values()
    ]
    ranked.sort(
        key=lambda item: (
            -item.score,
            -item.sim,
            -item.prior,
            _ROUTE_ORDER[item.source_route],
            item.package,
        )
    )
    return ranked


def decide_name(
    mention: str,
    candidates: Sequence[AppNameCandidate],
    *,
    settings: ResolverSettings | None = None,
) -> AppNameResolution:
    """Apply the minimum-score and top-two margin decision rule."""

    active = settings or ResolverSettings()
    all_ranked = tuple(candidates)
    visible = all_ranked[: max(0, active.top_k)]
    if not all_ranked or all_ranked[0].score < active.min_score:
        return AppNameResolution("unknown", str(mention), visible)
    margin = (
        all_ranked[0].score - all_ranked[1].score if len(all_ranked) > 1 else math.inf
    )
    if margin < active.margin:
        return AppNameResolution("ambiguous", str(mention), visible)
    return AppNameResolution("resolved", str(mention), visible, all_ranked[0])


def resolve_name(
    mention: str,
    *,
    registry: Any | None = None,
    kb_entries: Iterable[Mapping[str, Any]] = (),
    embedding_search: EmbeddingSearch | None = None,
    settings: ResolverSettings | None = None,
) -> AppNameResolution:
    """Generate, rank, and decide one app-name mention."""

    active = settings or ResolverSettings()
    return decide_name(
        mention,
        generate_candidates(
            mention,
            registry=registry,
            kb_entries=kb_entries,
            embedding_search=embedding_search,
            settings=active,
        ),
        settings=active,
    )


def embedding_search_from_config(
    config: Any, *, device_scope: str
) -> EmbeddingSearch | None:
    """Build a lazy VecIndex-backed route when a derived DB already exists."""

    if not bool(getattr(config, "resolver_embed", True)):
        return None
    db_path = Path(str(getattr(config, "vec_db", "memory/vec.db")))
    if not db_path.is_file():
        return None

    from phone_agent.v2.recall import MlxEmbedder

    embedder = MlxEmbedder(
        str(getattr(config, "embed_model", "Qwen/Qwen3-Embedding-0.6B")),
        int(getattr(config, "embed_dim", 1024)),
    )

    def search(query: str, top_k: int) -> Sequence[Mapping[str, Any]]:
        from phone_agent.v2.recall import VecIndex

        with VecIndex(db_path, embedder=embedder) as index:
            return index.app_name_vector_candidates(
                query, device_scope=device_scope, top_k=top_k
            )

    return search


__all__ = [
    "AppNameCandidate",
    "AppNameResolution",
    "EmbeddingSearch",
    "NameSource",
    "ResolverSettings",
    "collect_name_sources",
    "decide_name",
    "embedding_search_from_config",
    "generate_candidates",
    "lexical_similarity",
    "mention_occurs",
    "name_variants",
    "normalize_name",
    "pinyin_similarity",
    "resolve_name",
    "source_prior",
]
