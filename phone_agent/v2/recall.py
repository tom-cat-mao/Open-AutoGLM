"""Rebuildable semantic recall for experience episodes and App-KB aliases.

The runtime contract is deliberately observe-only in ``shadow`` mode: this
module can retrieve and evaluate candidates, but it never constructs model
messages or mutates the actor context.  Episode JSONL is consumed directly so
the shared WP-I1 schema remains owned by the experience plane.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from typing import Any, Protocol


DEFAULT_EMBED_MODEL = "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"
_NAMESPACES = frozenset({"episode", "app_alias"})
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+|[\u3400-\u9fff]+")
_LAUNCH_RE = re.compile(
    r"\blaunched\s+.*\(([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\)\s*$",
    re.IGNORECASE,
)
_STATS_LOCK = threading.Lock()


class Embedder(Protocol):
    """Minimal embedding boundary used by the index and fake-only tests."""

    model_id: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one normalized vector per input text."""


def _normalize(vector: Sequence[float], *, dimension: int) -> list[float]:
    values = [float(value) for value in vector]
    if len(values) != dimension:
        raise ValueError(
            f"embedding dimension mismatch: expected {dimension}, got {len(values)}"
        )
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("embedding must have a finite, non-zero norm")
    return [value / norm for value in values]


def _semantic_tokens(text: str) -> list[str]:
    """Return stable word/CJK n-gram features for the deterministic fake."""

    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(str(text or "").casefold()):
        value = match.group(0)
        tokens.append(value)
        if any("\u3400" <= char <= "\u9fff" for char in value):
            tokens.extend(value)
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
    return tokens or [str(text or "").casefold()]


class HashEmbedder:
    """Deterministic dependency-free embedder for tests and local checks."""

    def __init__(self, dimension: int = 64, model_id: str = "hash-v1") -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = int(dimension)
        self.model_id = str(model_id)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in _semantic_tokens(str(text)):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
                index = int.from_bytes(digest[:8], "little") % self.dimension
                sign = 1.0 if digest[8] & 1 else -1.0
                vector[index] += sign
            vectors.append(_normalize(vector, dimension=self.dimension))
        return vectors


class MlxEmbedder:
    """Lazy ``mlx-embeddings`` adapter for normalized Qwen3 embeddings."""

    def __init__(
        self,
        model_id: str = DEFAULT_EMBED_MODEL,
        dimension: int = 1024,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.model_id = str(model_id)
        self.dimension = int(dimension)
        self._model: Any | None = None
        self._processor: Any | None = None
        self._generate: Any | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            # Importing mlx-embeddings pulls in the MLX/transformers stack, so it
            # belongs here rather than at module import or object construction.
            from mlx_embeddings import generate, load

            model, processor = load(self.model_id, lazy=True)
            self._model = model
            self._processor = processor
            self._generate = generate

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = [str(text) for text in texts]
        if not inputs:
            return []
        self._ensure_loaded()
        output = self._generate(self._model, self._processor, texts=inputs)
        embeddings = getattr(output, "text_embeds", output)
        rows = embeddings.tolist()
        if rows and isinstance(rows[0], (int, float)):
            rows = [rows]
        if len(rows) != len(inputs):
            raise ValueError(
                f"embedder returned {len(rows)} vectors for {len(inputs)} texts"
            )
        return [_normalize(row, dimension=self.dimension) for row in rows]


def read_episode_events(events_path: str | Path) -> list[dict[str, Any]]:
    """Read valid WP-I1 ``episode_outcome`` schema-v1 records directly."""

    path = Path(events_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "episode_outcome"
                and event.get("schema_v") == 1
                and str(event.get("run_id", "")).strip()
                and str(event.get("goal_text", "")).strip()
                and isinstance(event.get("apps"), list)
            ):
                events.append(event)
    return events


def _episode_text(event: Mapping[str, Any]) -> str:
    apps = " ".join(str(app) for app in event.get("apps", []) if app)
    outcome = "success" if event.get("success") is True else "failure"
    return (
        f"goal: {event.get('goal_text', '')}\n"
        f"apps: {apps}\n"
        f"outcome: {outcome}; reason: {event.get('reason', '')}; "
        f"verifier: {event.get('verifier', 'skipped')}"
    )


def _parse_timestamp(value: Any, *, default: float | None = None) -> float:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return datetime.now(timezone.utc).timestamp() if default is None else default


def _alias_ref_id(entry: Mapping[str, Any]) -> str:
    identity = "\0".join(
        str(entry.get(key, "")) for key in ("term", "package", "kind", "scope")
    )
    return "alias:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _fts_terms(query: str) -> list[str]:
    terms: list[str] = []
    for match in _TOKEN_RE.finditer(str(query or "")):
        value = match.group(0)
        terms.append(value)
        if any("\u3400" <= char <= "\u9fff" for char in value):
            terms.extend(value[index : index + 2] for index in range(len(value) - 1))
    return list(dict.fromkeys(term for term in terms if term))


def _fts_query(query: str) -> str:
    return " OR ".join(
        f'"{term.replace(chr(34), chr(34) * 2)}"' for term in _fts_terms(query)
    )


def _keyword_score(query: str, text: str, *, fts_hit: bool) -> float:
    if not fts_hit:
        return 0.0
    normalized_query = "".join(str(query).casefold().split())
    normalized_text = "".join(str(text).casefold().split())
    if normalized_query and normalized_query in normalized_text:
        return 1.0
    query_terms = set(_fts_terms(query))
    text_terms = set(_fts_terms(text))
    overlap = len(query_terms & text_terms) / max(1, len(query_terms))
    return min(1.0, 0.6 + 0.4 * overlap)


class VecIndex:
    """Single-file sqlite-vec + FTS5 hybrid index."""

    def __init__(
        self,
        db_path: str | Path = "memory/vec.db",
        *,
        embedder: Embedder,
    ) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.row_factory = sqlite3.Row
        self._load_extension()
        self._initialize()

    def _load_extension(self) -> None:
        import sqlite_vec

        self.connection.enable_load_extension(True)
        try:
            sqlite_vec.load(self.connection)
        finally:
            self.connection.enable_load_extension(False)

    def _initialize(self) -> None:
        dimension = int(self.embedder.dimension)
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS recall_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recall_items (
                id INTEGER PRIMARY KEY,
                namespace TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                text TEXT NOT NULL,
                embed_model TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                app_package TEXT,
                device_scope TEXT NOT NULL,
                ts REAL NOT NULL,
                generation INTEGER NOT NULL DEFAULT 1,
                revoked INTEGER NOT NULL DEFAULT 0,
                UNIQUE(namespace, ref_id, embed_model)
            );
            CREATE INDEX IF NOT EXISTS recall_items_filter_idx
                ON recall_items(embed_model, device_scope, revoked, namespace);
            CREATE VIRTUAL TABLE IF NOT EXISTS recall_fts USING fts5(
                ref_id UNINDEXED, text, tokenize='unicode61'
            );
            """
        )
        stored = self.connection.execute(
            "SELECT value FROM recall_index_meta WHERE key = 'embed_dim'"
        ).fetchone()
        if stored is not None and int(stored[0]) != dimension:
            self.connection.close()
            raise ValueError(
                f"vector DB dimension is {stored[0]}, configured dimension is {dimension}; "
                "run --rebuild-vec"
            )
        self.connection.execute(
            "INSERT OR REPLACE INTO recall_index_meta(key, value) VALUES ('embed_dim', ?)",
            (str(dimension),),
        )
        self.connection.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS recall_vectors "
            f"USING vec0(embedding float[{dimension}])"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "VecIndex":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def count(self, *, embed_model: str | None = None) -> int:
        if embed_model is None:
            row = self.connection.execute("SELECT count(*) FROM recall_items").fetchone()
        else:
            row = self.connection.execute(
                "SELECT count(*) FROM recall_items WHERE embed_model = ?",
                (embed_model,),
            ).fetchone()
        return int(row[0])

    def upsert(
        self,
        *,
        namespace: str,
        ref_id: str,
        text: str,
        metadata: Mapping[str, Any],
    ) -> None:
        if namespace not in _NAMESPACES:
            raise ValueError(f"unknown namespace: {namespace!r}")
        ref_id = str(ref_id).strip()
        text = str(text).strip()
        if not ref_id or not text:
            raise ValueError("ref_id and text must not be empty")
        vector = self.embedder.embed([text])[0]
        from sqlite_vec import serialize_float32

        payload = dict(metadata)
        device_scope = str(payload.get("device_scope", "")).strip()
        if not device_scope:
            raise ValueError("metadata.device_scope must not be empty")
        apps = payload.get("apps", [])
        app_package = str(payload.get("app_package", "") or "").strip()
        if not app_package and isinstance(apps, list) and apps:
            app_package = str(apps[0])
        timestamp = _parse_timestamp(payload.get("ts"))
        generation = int(payload.get("generation", 1))
        revoked = int(bool(payload.get("revoked", False)))
        payload.update(
            {
                "app_package": app_package or None,
                "device_scope": device_scope,
                "ts": timestamp,
                "generation": generation,
                "revoked": bool(revoked),
            }
        )
        metadata_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        with self.connection:
            existing = self.connection.execute(
                "SELECT id FROM recall_items "
                "WHERE namespace = ? AND ref_id = ? AND embed_model = ?",
                (namespace, ref_id, self.embedder.model_id),
            ).fetchone()
            if existing is None:
                cursor = self.connection.execute(
                    """
                    INSERT INTO recall_items(
                        namespace, ref_id, text, embed_model, metadata_json,
                        app_package, device_scope, ts, generation, revoked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        namespace,
                        ref_id,
                        text,
                        self.embedder.model_id,
                        metadata_json,
                        app_package or None,
                        device_scope,
                        timestamp,
                        generation,
                        revoked,
                    ),
                )
                row_id = int(cursor.lastrowid)
            else:
                row_id = int(existing[0])
                self.connection.execute("DELETE FROM recall_vectors WHERE rowid = ?", (row_id,))
                self.connection.execute("DELETE FROM recall_fts WHERE rowid = ?", (row_id,))
                self.connection.execute(
                    """
                    UPDATE recall_items SET text = ?, metadata_json = ?, app_package = ?,
                        device_scope = ?, ts = ?, generation = ?, revoked = ?
                    WHERE id = ?
                    """,
                    (
                        text,
                        metadata_json,
                        app_package or None,
                        device_scope,
                        timestamp,
                        generation,
                        revoked,
                        row_id,
                    ),
                )
            self.connection.execute(
                "INSERT INTO recall_vectors(rowid, embedding) VALUES (?, ?)",
                (row_id, serialize_float32(vector)),
            )
            self.connection.execute(
                "INSERT INTO recall_fts(rowid, ref_id, text) VALUES (?, ?, ?)",
                (row_id, ref_id, text + "\n" + " ".join(_fts_terms(text))),
            )

    def index_episodes(self, events_path: str | Path) -> dict[str, int]:
        indexed = 0
        for event in read_episode_events(events_path):
            apps = [str(app) for app in event.get("apps", []) if str(app).strip()]
            self.upsert(
                namespace="episode",
                ref_id=str(event["run_id"]),
                text=_episode_text(event),
                metadata={
                    "app_package": apps[0] if apps else None,
                    "apps": apps,
                    "device_scope": str(event.get("device_scope", "")),
                    "ts": event.get("ts_end", event.get("ts_start", 0.0)),
                    "generation": int(event.get("schema_v", 1)),
                    "revoked": bool(event.get("revoked", False)),
                    "success": event.get("success") is True,
                    "reason": str(event.get("reason", "")),
                    "verifier": str(event.get("verifier", "skipped")),
                },
            )
            indexed += 1
        return {"episodes": indexed}

    def index_app_aliases(
        self,
        *,
        memory_dir: str | Path = "memory",
        store: Any | None = None,
    ) -> dict[str, int]:
        if store is None:
            from phone_agent.v2.appkb import AppKnowledgeStore

            store = AppKnowledgeStore(str(memory_dir))
        indexed = 0
        for entry in store.entries(include_stale=True):
            term = str(entry.get("term", "")).strip()
            label = str(entry.get("label", "")).strip()
            package = str(entry.get("package", "")).strip()
            scope = str(entry.get("scope", "")).strip()
            if not term or not label or not package or not scope:
                continue
            text = (
                f"alias: {term}\nlabel: {label}\npackage: {package}\n"
                f"kind: {entry.get('kind', '')}"
            )
            self.upsert(
                namespace="app_alias",
                ref_id=_alias_ref_id(entry),
                text=text,
                metadata={
                    "app_package": package,
                    "device_scope": scope,
                    "ts": _parse_timestamp(entry.get("last_seen")),
                    "generation": int(entry.get("success_count", 0)) + 1,
                    "revoked": bool(entry.get("stale", False)),
                    "term": term,
                    "label": label,
                    "kind": str(entry.get("kind", "")),
                },
            )
            indexed += 1
        return {"app_aliases": indexed}

    def recall(
        self,
        query: str,
        *,
        device_scope: str,
        top_k: int = 5,
        min_score: float = 0.35,
        decay_lambda: float = 0.02,
        now: float | None = None,
        namespaces: Sequence[str] = ("episode", "app_alias"),
    ) -> list[dict[str, Any]]:
        """Return hybrid-ranked candidates after all hard filters."""

        query = str(query).strip()
        device_scope = str(device_scope).strip()
        selected = tuple(dict.fromkeys(str(value) for value in namespaces))
        if not query or not device_scope or top_k <= 0 or not selected:
            return []
        if any(namespace not in _NAMESPACES for namespace in selected):
            raise ValueError("namespaces must contain only episode/app_alias")
        if not 0.0 <= min_score <= 1.0:
            raise ValueError("min_score must be between 0 and 1")
        if decay_lambda < 0.0:
            raise ValueError("decay_lambda must be non-negative")
        if self.count(embed_model=self.embedder.model_id) == 0:
            return []

        from sqlite_vec import serialize_float32

        query_vector = serialize_float32(self.embedder.embed([query])[0])
        placeholders = ",".join("?" for _ in selected)
        # Global App-KB aliases are explicitly applicable to every device; all
        # episode rows and device-scoped aliases must equal the current device.
        filter_sql = (
            f"i.embed_model = ? AND i.revoked = 0 "
            f"AND i.namespace IN ({placeholders}) "
            "AND (i.device_scope = ? OR "
            "(i.namespace = 'app_alias' AND i.device_scope = 'global'))"
        )
        params: list[Any] = [self.embedder.model_id, *selected, device_scope]
        rows = self.connection.execute(
            f"""
            SELECT i.*, vec_distance_cosine(v.embedding, ?) AS vector_distance
            FROM recall_items AS i
            JOIN recall_vectors AS v ON v.rowid = i.id
            WHERE {filter_sql}
            """,
            [query_vector, *params],
        ).fetchall()
        if not rows:
            return []

        fts_hits: set[int] = set()
        fts_query = _fts_query(query)
        if fts_query:
            fts_hits = {
                int(row[0])
                for row in self.connection.execute(
                    f"""
                    SELECT i.id
                    FROM recall_fts
                    JOIN recall_items AS i ON i.id = recall_fts.rowid
                    WHERE recall_fts MATCH ? AND {filter_sql}
                    """,
                    [fts_query, *params],
                ).fetchall()
            }

        current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
        candidates: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            vector_score = max(0.0, min(1.0, 1.0 - float(row["vector_distance"])))
            keyword_score = _keyword_score(
                query, row["text"], fts_hit=int(row["id"]) in fts_hits
            )
            age_days = max(0.0, (current - float(row["ts"])) / 86_400.0)
            time_score = math.exp(-decay_lambda * age_days)
            score = 0.65 * vector_score + 0.25 * keyword_score + 0.10 * time_score
            if score < min_score:
                continue
            reasons = [f"vector={vector_score:.3f}"]
            if keyword_score > 0.0:
                reasons.append(f"fts5={keyword_score:.3f}")
            reasons.append(f"time_decay={time_score:.3f}")
            candidates.append(
                {
                    "namespace": row["namespace"],
                    "ref_id": row["ref_id"],
                    "score": round(score, 6),
                    "vector_score": round(vector_score, 6),
                    "keyword_score": round(keyword_score, 6),
                    "time_score": round(time_score, 6),
                    "match_reasons": reasons,
                    "metadata": metadata,
                }
            )
        candidates.sort(key=lambda item: (-item["score"], item["ref_id"]))
        return candidates[:top_k]


def evaluate_recall(
    recalled: Sequence[Mapping[str, Any]], actual_apps: Sequence[str] | set[str]
) -> dict[str, Any]:
    """Classify one shadow run using recalled vs actually launched packages."""

    recalled_apps: set[str] = set()
    for candidate in recalled:
        metadata = candidate.get("metadata", {})
        if not isinstance(metadata, Mapping):
            continue
        package = str(metadata.get("app_package", "") or "").strip()
        if package:
            recalled_apps.add(package)
        apps = metadata.get("apps", [])
        if isinstance(apps, list):
            recalled_apps.update(str(app).strip() for app in apps if str(app).strip())
    actual = {str(app).strip() for app in actual_apps if str(app).strip()}
    matched = recalled_apps & actual
    hit = bool(matched)
    return {
        "hit": hit,
        "false_hit": bool(recalled_apps - actual),
        "recalled_apps": sorted(recalled_apps),
        "actual_apps": sorted(actual),
        "matched_apps": sorted(matched),
    }


def extract_launched_apps(content: Any) -> set[str]:
    """Extract packages only from successful ``launch_app`` receipts."""

    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
    packages: set[str] = set()
    for text in texts:
        if not text.lstrip().startswith("OK."):
            continue
        packages.update(match.strip() for match in _LAUNCH_RE.findall(text) if match.strip())
    return packages


def update_recall_stats(
    stats_path: str | Path,
    evaluation: Mapping[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Atomically accumulate run-level shadow hit/false-hit rates."""

    path = Path(stats_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _STATS_LOCK, lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, TypeError):
                current = {}
            evaluations = int(current.get("evaluations", 0)) + 1
            hits = int(current.get("hits", 0)) + int(bool(evaluation.get("hit")))
            false_hits = int(current.get("false_hits", 0)) + int(
                bool(evaluation.get("false_hit"))
            )
            recall_runs = int(current.get("recall_runs", 0)) + int(
                bool(evaluation.get("recalled_apps"))
            )
            updated = {
                "schema_v": 1,
                "evaluations": evaluations,
                "recall_runs": recall_runs,
                "hits": hits,
                "false_hits": false_hits,
                "hit_rate": round(hits / evaluations, 6),
                "false_hit_rate": round(false_hits / max(1, recall_runs), 6),
                "latest": {"run_id": run_id, **dict(evaluation)},
            }
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(updated, stream, ensure_ascii=False, indent=2, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return updated
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def rebuild_index(
    config: Any,
    *,
    embedder: Embedder | None = None,
    events_path: str | Path | None = None,
    app_store: Any | None = None,
) -> dict[str, Any]:
    """Delete the derived DB and rebuild it from episode/App-KB sources."""

    db_path = Path(getattr(config, "vec_db", "memory/vec.db"))
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(db_path) + suffix)
        if target.exists():
            target.unlink()
    active_embedder = embedder or MlxEmbedder(
        getattr(config, "embed_model", DEFAULT_EMBED_MODEL),
        getattr(config, "embed_dim", 1024),
    )
    memory_dir = Path(getattr(config, "memory_dir", "memory"))
    source = Path(events_path) if events_path is not None else memory_dir / "experience/events.jsonl"
    with VecIndex(db_path, embedder=active_embedder) as index:
        episodes = index.index_episodes(source)["episodes"]
        aliases = index.index_app_aliases(memory_dir=memory_dir, store=app_store)[
            "app_aliases"
        ]
        total = index.count(embed_model=active_embedder.model_id)
    return {
        "status": "rebuilt",
        "db_path": str(db_path),
        "embed_model": active_embedder.model_id,
        "embed_dim": active_embedder.dimension,
        "episodes": episodes,
        "app_aliases": aliases,
        "total": total,
    }


def index_episodes(
    events_path: str | Path,
    *,
    db_path: str | Path = "memory/vec.db",
    embedder: Embedder | None = None,
    embed_model: str = DEFAULT_EMBED_MODEL,
    embed_dim: int = 1024,
) -> dict[str, int]:
    """Index episode JSONL through the default or caller-supplied embedder."""

    active = embedder or MlxEmbedder(embed_model, embed_dim)
    with VecIndex(db_path, embedder=active) as index:
        return index.index_episodes(events_path)


def index_app_aliases(
    *,
    memory_dir: str | Path = "memory",
    db_path: str | Path = "memory/vec.db",
    embedder: Embedder | None = None,
    embed_model: str = DEFAULT_EMBED_MODEL,
    embed_dim: int = 1024,
    store: Any | None = None,
) -> dict[str, int]:
    """Mirror App-KB terms, labels, and packages into semantic recall."""

    active = embedder or MlxEmbedder(embed_model, embed_dim)
    with VecIndex(db_path, embedder=active) as index:
        return index.index_app_aliases(memory_dir=memory_dir, store=store)


def recall(
    query: str,
    *,
    device_scope: str,
    db_path: str | Path = "memory/vec.db",
    embedder: Embedder | None = None,
    embed_model: str = DEFAULT_EMBED_MODEL,
    embed_dim: int = 1024,
    top_k: int = 5,
    min_score: float = 0.35,
    decay_lambda: float = 0.02,
    now: float | None = None,
    namespaces: Sequence[str] = ("episode", "app_alias"),
) -> list[dict[str, Any]]:
    """Retrieve candidates from a configured single-file vector index."""

    active = embedder or MlxEmbedder(embed_model, embed_dim)
    with VecIndex(db_path, embedder=active) as index:
        return index.recall(
            query,
            device_scope=device_scope,
            top_k=top_k,
            min_score=min_score,
            decay_lambda=decay_lambda,
            now=now,
            namespaces=namespaces,
        )


__all__ = [
    "DEFAULT_EMBED_MODEL",
    "Embedder",
    "HashEmbedder",
    "MlxEmbedder",
    "VecIndex",
    "evaluate_recall",
    "extract_launched_apps",
    "index_app_aliases",
    "index_episodes",
    "read_episode_events",
    "recall",
    "rebuild_index",
    "update_recall_stats",
]
