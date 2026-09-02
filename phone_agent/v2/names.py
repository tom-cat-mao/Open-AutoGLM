"""Unified app-name candidate generation, ranking, and decision.

The resolver is deliberately split into two phases:

* L0/L2 produce high-recall package candidates from exact, lexical, pinyin,
  and optional vector-index evidence.
* L3 types the evidence behind each candidate, ranks the package-deduplicated
  candidates, then applies either the typed decision policy or the legacy
  score-threshold fallback.

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
NameDecisionMode = Literal["typed", "legacy"]
NameMatchType = Literal[
    "exact_alias",
    "exact_label",
    "exact_package",
    "exact_package_segment",
    "token_prefix",
    "containment",
    "registered_containment",
    "fuzzy",
    "pinyin_full",
    "pinyin_initials",
    "embedding",
]
NameAuthority = Literal["user", "device", "learned", "registry", "embedding"]
SourceRole = Literal["alias", "label", "package"]
EmbeddingSearch = Callable[[str, int], Sequence[Mapping[str, Any]]]

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PUNCT_RE = re.compile(r"[^0-9a-z\u3400-\u9fff]+")
_TOKEN_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
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
_MATCH_TYPE_ORDER: dict[str, int] = {
    "exact_alias": 0,
    "exact_label": 0,
    "exact_package": 0,
    "exact_package_segment": 1,
    "registered_containment": 2,
    "token_prefix": 3,
    "containment": 4,
    "fuzzy": 5,
    "pinyin_full": 6,
    "pinyin_initials": 7,
    "embedding": 8,
}
_AUTHORITY_ORDER = {
    "user": 0,
    "device": 1,
    "learned": 2,
    "registry": 3,
    "embedding": 4,
}
_DEFAULT_PACKAGE_SEGMENT_STOPWORDS = (
    "com",
    "org",
    "net",
    "android",
    "example",
    "app",
    "mobile",
    "free",
    "debug",
    "release",
)
_DEFAULT_AUTO_MATCH_TYPES = (
    "exact_alias",
    "exact_label",
    "exact_package",
    "exact_package_segment",
    "registered_containment",
)
_DEFAULT_CLARIFY_MATCH_TYPES = (
    "fuzzy",
    "pinyin_full",
    "pinyin_initials",
    "embedding",
)

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
    role: SourceRole = "alias"
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
    match_type: str = "fuzzy"
    authority: str = "registry"
    success_count: int = 0
    provenance: tuple[str, ...] = ()
    source_ref: str | None = None
    source_entry: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def rank_score(self) -> float:
        """Return the ranking-only score retained for compatibility."""

        return self.score

    def to_dict(self) -> dict[str, Any]:
        """Return the trace/receipt-safe public projection."""

        return {
            "package": self.package,
            "source_route": self.source_route,
            "match_type": self.match_type,
            "authority": self.authority,
            "sim": round(self.sim, 6),
            "prior": round(self.prior, 6),
            "rank_score": round(self.rank_score, 6),
            # Keep the legacy key for old reports and tests. It is no longer a
            # resolver authority threshold in typed mode.
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
    decision_basis: str = ""
    reason: str = ""

    def to_trace(self) -> dict[str, Any]:
        leading = self.winner or (self.candidates[0] if self.candidates else None)
        return {
            "mention": self.mention,
            "candidates": [item.to_dict() for item in self.candidates],
            "decision": self.status,
            "winner": self.winner.package if self.winner is not None else None,
            "match_type": leading.match_type if leading is not None else None,
            "authority": leading.authority if leading is not None else None,
            "decision_basis": self.decision_basis,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResolverSettings:
    """Config projection used by the pure candidate/ranking core."""

    decision_mode: NameDecisionMode = "typed"
    min_score: float = 0.90
    margin: float = 0.08
    typed_margin: float = 0.08
    top_k: int = 10
    lexical: bool = True
    pinyin: bool = True
    embed: bool = True
    w_sim: float = 0.8
    w_prior: float = 0.2
    package_segment_min_len: int = 4
    package_segment_stopwords: tuple[str, ...] = _DEFAULT_PACKAGE_SEGMENT_STOPWORDS
    auto_match_types: tuple[str, ...] = _DEFAULT_AUTO_MATCH_TYPES
    clarify_match_types: tuple[str, ...] = _DEFAULT_CLARIFY_MATCH_TYPES

    @classmethod
    def from_config(cls, config: Any | None) -> "ResolverSettings":
        if config is None:
            return cls()
        return cls(
            decision_mode=str(
                getattr(config, "resolver_decision_mode", "typed")
            ).lower(),
            min_score=float(getattr(config, "resolver_min_score", 0.90)),
            margin=float(getattr(config, "resolver_margin", 0.08)),
            typed_margin=float(getattr(config, "resolver_typed_margin", 0.08)),
            top_k=int(getattr(config, "resolver_top_k", 10)),
            lexical=bool(getattr(config, "resolver_lexical", True)),
            pinyin=bool(getattr(config, "resolver_pinyin", True)),
            embed=bool(getattr(config, "resolver_embed", True)),
            w_sim=float(getattr(config, "resolver_w_sim", 0.8)),
            w_prior=float(getattr(config, "resolver_w_prior", 0.2)),
            package_segment_min_len=int(
                getattr(config, "resolver_package_segment_min_len", 4)
            ),
            package_segment_stopwords=_split_config_values(
                getattr(
                    config,
                    "resolver_package_segment_stopwords",
                    _DEFAULT_PACKAGE_SEGMENT_STOPWORDS,
                )
            ),
            auto_match_types=_split_config_values(
                getattr(config, "resolver_auto_match_types", _DEFAULT_AUTO_MATCH_TYPES)
            ),
            clarify_match_types=_split_config_values(
                getattr(
                    config,
                    "resolver_clarify_match_types",
                    _DEFAULT_CLARIFY_MATCH_TYPES,
                )
            ),
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


def _split_config_values(value: Any) -> tuple[str, ...]:
    """Coerce comma-separated or iterable config values into normalized tokens."""

    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_items = value
    else:
        raw_items = (value,)
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip().lower()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _authority_for_kind(kind: str, route: NameRoute) -> str:
    """Map source kind to the resolver-facing authority vocabulary."""

    if route == "embedding":
        return "embedding"
    source_kind = str(kind or "").strip().lower()
    if source_kind in {"user", "device", "learned", "registry"}:
        return source_kind
    return "learned" if source_kind == "alias" else "registry"


def _source_role_for_term(entry: Mapping[str, Any], term: str) -> SourceRole:
    """Classify one flattened spelling as alias, label, or package text."""

    package = str(entry.get("app_package", entry.get("package", ""))).strip()
    if term == package:
        return "package"
    label = str(entry.get("label", "")).strip()
    if label and normalize_name(term) == normalize_name(label):
        return "label"
    return "alias"


def _exact_match_type(source: NameSource) -> str:
    if source.role == "package":
        return "exact_package"
    if source.role == "label":
        return "exact_label"
    return "exact_alias"


def _registered_containment_allowed(source: NameSource) -> bool:
    """Return whether containment is a configured/registered textual alias."""

    return source.role in {"alias", "label"} and (
        source.kind in {"registry", "user", "device"}
        or (source.kind == "learned" and source.success_count > 0)
    )


def _classify_lexical_match(
    mention: str,
    source: NameSource,
    sim: float,
    settings: ResolverSettings,
) -> str | None:
    """Assign a typed lexical evidence label for one source spelling."""

    query_variants = name_variants(mention)
    term_variants = name_variants(source.term)
    if not query_variants or not term_variants:
        return None
    if any(query == term for query in query_variants for term in term_variants):
        return _exact_match_type(source)

    if source.role == "package" and _package_segment_match(
        mention,
        source.package,
        min_len=settings.package_segment_min_len,
        stopwords=settings.package_segment_stopwords,
    ):
        return "exact_package_segment"

    containment = any(
        len(term_variant) >= 2
        and len(query_variant) >= 2
        and (term_variant in query_variant or query_variant in term_variant)
        for query_variant in query_variants
        for term_variant in term_variants
    )
    if containment:
        return (
            "registered_containment"
            if _registered_containment_allowed(source)
            else "containment"
        )

    token_prefix = any(
        _token_prefix_match(query_variant, term_variant)
        for query_variant in query_variants
        for term_variant in term_variants
    )
    if token_prefix:
        return "token_prefix"
    return "fuzzy" if sim > 0.0 else None


def _classify_pinyin_match(mention: str, term: str) -> tuple[str, float] | None:
    """Return the stronger pinyin evidence subtype and similarity."""

    mention_forms = _pinyin_forms(mention)
    term_forms = _pinyin_forms(term)
    if mention_forms is None and term_forms is None:
        return None
    mention_full, mention_initials = mention_forms or (normalize_name(mention),) * 2
    term_full, term_initials = term_forms or (normalize_name(term),) * 2
    full = lexical_similarity(mention_full, term_full)
    initials = lexical_similarity(mention_initials, term_initials)
    if full * 0.97 >= initials * 0.92:
        return "pinyin_full", min(0.97, full * 0.97)
    return "pinyin_initials", min(0.97, initials * 0.92)


def _token_prefix_match(query: str, term: str) -> bool:
    """Detect token-prefix evidence without treating arbitrary substrings as strong."""

    if len(query) < 2 or len(term) < 2:
        return False
    query_tokens = _TOKEN_RE.findall(query)
    term_tokens = _TOKEN_RE.findall(term)
    return any(
        len(query_token) >= 2
        and len(term_token) >= 2
        and (
            query_token.startswith(term_token)
            or term_token.startswith(query_token)
        )
        for query_token in query_tokens
        for term_token in term_tokens
    )


def _split_package_segments(package: str) -> tuple[str, ...]:
    """Split package text on separators and camel-case humps."""

    tokens: list[str] = []
    for part in re.split(r"[._\\-]+", str(package or "")):
        if not part:
            continue
        tokens.extend(item for item in _CAMEL_BOUNDARY_RE.split(part) if item)
    return tuple(tokens)


def _mention_segment_terms(mention: str) -> tuple[str, ...]:
    """Return full and tokenized mention forms eligible for segment equality."""

    values: list[str] = []
    full = _PUNCT_RE.sub("", normalize_name(mention))
    if full:
        values.append(full)
    for token in _TOKEN_RE.findall(str(mention or "")):
        for part in _CAMEL_BOUNDARY_RE.split(token):
            normalized = normalize_name(part)
            if normalized:
                values.append(normalized)
    return tuple(dict.fromkeys(values))


def _package_segment_match(
    mention: str,
    package: str,
    *,
    min_len: int,
    stopwords: Sequence[str],
) -> bool:
    """True when the mention equals a non-stop package segment."""

    minimum = max(1, int(min_len))
    query_terms = tuple(
        term for term in _mention_segment_terms(mention) if len(term) >= minimum
    )
    if not query_terms:
        return False
    stop = {normalize_name(item) for item in stopwords}
    for segment in _split_package_segments(package):
        normalized = normalize_name(segment)
        if not normalized or normalized in stop:
            continue
        if len(normalized) >= minimum and normalized in query_terms:
            return True
    return False


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
        terms: list[tuple[str, SourceRole]] = [
            (getattr(identity, "canonical_id", ""), "alias"),
            (getattr(identity, "display_name", ""), "label"),
        ]
        terms.extend(
            (alias, "alias") for alias in sorted(getattr(identity, "aliases", ()))
        )
        terms.extend(
            (package, "package")
            for package in sorted(getattr(identity, "packages", ()))
        )
        for package in sorted(getattr(identity, "packages", ())):
            for term, role in terms:
                if normalize_name(term):
                    sources.append(
                        NameSource(
                            package=str(package),
                            term=str(term),
                            role=role,
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
                    role=_source_role_for_term(entry, term),
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

    unique: dict[tuple[str, str, str, SourceRole, str | None], NameSource] = {}
    for source in sources:
        key = (
            source.package,
            normalize_name(source.term),
            source.kind,
            source.role,
            source.ref_id,
        )
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

    classified = _classify_pinyin_match(mention, term)
    if classified is None:
        return 0.0
    return classified[1]


def _candidate(
    source: NameSource,
    route: NameRoute,
    sim: float,
    settings: ResolverSettings,
    *,
    match_type: str,
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
        match_type=match_type,
        authority=_authority_for_kind(source.kind, route),
        success_count=max(0, int(source.success_count or 0)),
        provenance=(provenance or f"{route}:{match_type}:{source.kind}:{source.term}",),
        source_ref=source.ref_id,
        source_entry=(dict(source.metadata) if source.metadata else None),
    )


def _better(candidate: AppNameCandidate, current: AppNameCandidate) -> bool:
    return (
        -_MATCH_TYPE_ORDER.get(candidate.match_type, 99),
        candidate.score,
        candidate.sim,
        candidate.prior,
        -_ROUTE_ORDER[candidate.source_route],
        -_AUTHORITY_ORDER.get(candidate.authority, 99),
        candidate.matched_term,
    ) > (
        -_MATCH_TYPE_ORDER.get(current.match_type, 99),
        current.score,
        current.sim,
        current.prior,
        -_ROUTE_ORDER[current.source_route],
        -_AUTHORITY_ORDER.get(current.authority, 99),
        current.matched_term,
    )


def _package_weak_display_allowed(mention: str) -> bool:
    """Allow weak package-name hints only for multi-token user mentions."""

    return len(_TOKEN_RE.findall(str(mention or ""))) >= 2


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
        query_variants = name_variants(mention)
        term_variants = name_variants(source.term)
        if any(
            query_variant == term_variant
            for query_variant in query_variants
            for term_variant in term_variants
        ):
            generated.append(
                _candidate(
                    source,
                    "exact",
                    1.0,
                    active,
                    match_type=_exact_match_type(source),
                )
            )
        elif active.decision_mode != "legacy" and source.role == "package" and _package_segment_match(
            mention,
            source.package,
            min_len=active.package_segment_min_len,
            stopwords=active.package_segment_stopwords,
        ):
            generated.append(
                _candidate(
                    source,
                    "exact",
                    1.0,
                    active,
                    match_type="exact_package_segment",
                )
            )
        if active.lexical:
            sim = lexical_similarity(mention, source.term)
            if sim >= 0.35:
                match_type = _classify_lexical_match(
                    mention, source, sim, active
                )
                if match_type is None:
                    continue
                if active.decision_mode == "legacy" and match_type == "exact_package_segment":
                    match_type = "containment"
                if (
                    active.decision_mode != "legacy"
                    and source.role == "package"
                    and match_type not in {
                    "exact_package",
                    "exact_package_segment",
                    }
                    and not _package_weak_display_allowed(mention)
                ):
                    continue
                if match_type in {
                    "exact_package",
                    "exact_package_segment",
                    "exact_alias",
                    "exact_label",
                }:
                    sim = 1.0
                generated.append(
                    _candidate(
                        source,
                        "lexical",
                        sim,
                        active,
                        match_type=match_type,
                    )
                )
        if active.pinyin:
            classified = _classify_pinyin_match(mention, source.term)
            if classified is not None:
                match_type, sim = classified
                if sim >= 0.45:
                    generated.append(
                        _candidate(
                            source,
                            "pinyin",
                            sim,
                            active,
                            match_type=match_type,
                        )
                    )

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
                    match_type="embedding",
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
            match_type=item.match_type,
            authority=item.authority,
            success_count=item.success_count,
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
            _MATCH_TYPE_ORDER.get(item.match_type, 99),
            _ROUTE_ORDER[item.source_route],
            _AUTHORITY_ORDER.get(item.authority, 99),
            item.package,
        )
    )
    return ranked


def _is_auto_candidate(candidate: AppNameCandidate, settings: ResolverSettings) -> bool:
    auto_types = set(settings.auto_match_types)
    clarify_types = set(settings.clarify_match_types)
    if candidate.authority == "learned":
        return candidate.success_count > 0 and candidate.match_type not in clarify_types
    if candidate.match_type in auto_types:
        return True
    return False


def decide_name_legacy(
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
        return AppNameResolution(
            "unknown",
            str(mention),
            visible,
            decision_basis="legacy:min_score",
            reason=(
                "no candidates"
                if not all_ranked
                else f"top rank_score {all_ranked[0].score:.3f} < {active.min_score:.3f}"
            ),
        )
    margin = (
        all_ranked[0].score - all_ranked[1].score if len(all_ranked) > 1 else math.inf
    )
    if margin < active.margin:
        return AppNameResolution(
            "ambiguous",
            str(mention),
            visible,
            decision_basis="legacy:margin",
            reason=f"top2 rank_score margin {margin:.3f} < {active.margin:.3f}",
        )
    return AppNameResolution(
        "resolved",
        str(mention),
        visible,
        all_ranked[0],
        decision_basis="legacy:min_score_margin",
        reason=(
            f"{all_ranked[0].source_route}/{all_ranked[0].match_type} "
            f"rank_score={all_ranked[0].score:.3f}"
        ),
    )


def decide_name_typed(
    mention: str,
    candidates: Sequence[AppNameCandidate],
    *,
    settings: ResolverSettings | None = None,
) -> AppNameResolution:
    """Apply evidence-typed three-state decision policy."""

    active = settings or ResolverSettings()
    all_ranked = tuple(candidates)
    visible = all_ranked[: max(0, active.top_k)]
    if not all_ranked:
        return AppNameResolution(
            "unknown",
            str(mention),
            visible,
            decision_basis="typed:no_candidates",
            reason="no resolver candidates survived generation",
        )

    auto_candidates = [
        candidate for candidate in all_ranked if _is_auto_candidate(candidate, active)
    ]
    if not auto_candidates:
        if all_ranked[0].score < active.min_score:
            return AppNameResolution(
                "unknown",
                str(mention),
                (),
                decision_basis="typed:weak_below_display_threshold",
                reason=(
                    f"only weak evidence and top rank_score "
                    f"{all_ranked[0].score:.3f} < {active.min_score:.3f}"
                ),
            )
        return AppNameResolution(
            "ambiguous",
            str(mention),
            visible,
            decision_basis="typed:clarify_only",
            reason=(
                "only clarification evidence types are present: "
                + ", ".join(
                    dict.fromkeys(candidate.match_type for candidate in visible)
                )
            ),
        )

    top = all_ranked[0]
    if not _is_auto_candidate(top, active):
        return AppNameResolution(
            "ambiguous",
            str(mention),
            visible,
            decision_basis="typed:top_requires_clarification",
            reason=(
                f"top candidate uses {top.match_type}; strongest auto evidence "
                f"is {auto_candidates[0].package}"
            ),
        )

    margin = all_ranked[0].score - all_ranked[1].score if len(all_ranked) > 1 else math.inf
    if margin < active.typed_margin:
        return AppNameResolution(
            "ambiguous",
            str(mention),
            visible,
            decision_basis="typed:margin",
            reason=f"top2 rank_score margin {margin:.3f} < {active.typed_margin:.3f}",
        )

    return AppNameResolution(
        "resolved",
        str(mention),
        visible,
        top,
        decision_basis=f"typed:auto:{top.match_type}",
        reason=(
            f"{top.match_type} from {top.authority}; "
            f"rank_score={top.score:.3f}; margin="
            f"{'inf' if math.isinf(margin) else f'{margin:.3f}'}"
        ),
    )


def decide_name(
    mention: str,
    candidates: Sequence[AppNameCandidate],
    *,
    settings: ResolverSettings | None = None,
) -> AppNameResolution:
    """Apply typed decision by default, or the legacy score rule on request."""

    active = settings or ResolverSettings()
    if active.decision_mode == "legacy":
        return decide_name_legacy(mention, candidates, settings=active)
    return decide_name_typed(mention, candidates, settings=active)


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
    "decide_name_legacy",
    "decide_name_typed",
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
