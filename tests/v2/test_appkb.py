"""Tests for the persistent v2 application knowledge base."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from phone_agent.v2.appkb import AppKnowledge, AppKnowledgeStore, should_save


def _entry(
    term: str,
    package: str,
    *,
    label: str | None = None,
    kind: str = "learned",
    scope: str = "global",
    confidence: float = 0.8,
    success_count: int = 1,
    stale: bool = False,
) -> dict[str, object]:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    return {
        "term": term,
        "label": label or term,
        "package": package,
        "kind": kind,
        "scope": scope,
        "confidence": confidence,
        "success_count": success_count,
        "first_seen": timestamp,
        "last_seen": timestamp,
        "stale": stale,
    }


def test_mutations_append_events_and_keep_materialized_view_consistent(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(_entry("Maps", "com.example.maps"))
    store.upsert(_entry("Music", "com.example.music"))
    store.mark_stale("Music", "com.example.music")
    store.delete("Maps", "com.example.maps")

    events = [
        json.loads(line)
        for line in store.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["op"] for event in events] == [
        "upsert",
        "upsert",
        "mark_stale",
        "delete",
    ]
    assert all("entry" in event and "ts" in event for event in events)

    materialized = json.loads(store.kb_path.read_text(encoding="utf-8"))
    assert materialized == store.entries(include_stale=True)
    assert materialized == [
        {
            **_entry("Music", "com.example.music"),
            "stale": True,
        }
    ]

    reloaded = AppKnowledgeStore(str(tmp_path))
    assert reloaded.entries(include_stale=True) == materialized


def test_entries_filter_scope_kind_and_stale(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(
        _entry(
            "Camera",
            "com.example.camera",
            kind="device",
            scope="device:one",
        )
    )
    store.upsert(_entry("Photos", "com.example.photos", kind="user"))
    store.upsert(_entry("Old", "com.example.old", stale=True))

    assert [entry["term"] for entry in store.entries()] == ["Camera", "Photos"]
    assert [
        entry["term"] for entry in store.entries(scope="device:one", kind="device")
    ] == ["Camera"]
    assert len(store.entries(include_stale=True)) == 3


def test_lookup_precedence_exact_normalized_and_substring(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(_entry("Map", "com.example.short", confidence=1.0))
    store.upsert(_entry("Maps Pro", "com.example.maps", confidence=0.7))
    knowledge = AppKnowledge(store)

    assert knowledge.lookup("Map") == "com.example.short"
    assert knowledge.lookup("  MAPS   PRO ") == "com.example.maps"
    assert knowledge.lookup("please open maps pro now") == "com.example.maps"


def test_lookup_alias_bridge_device_scope_and_stale_exclusion(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(
        _entry(
            "MapMaster",
            "com.example.maps",
            label="地图大师",
            kind="device",
            scope="device:alpha",
        )
    )
    store.upsert(
        _entry(
            "地图",
            "com.example.maps",
            label="地图大师",
            kind="alias",
            scope="global",
        )
    )
    store.upsert(_entry("Dead", "com.example.dead", stale=True))

    alpha = AppKnowledge(store, device_id="alpha")
    beta = AppKnowledge(store, device_id="beta")
    assert alpha.lookup("地图") == "com.example.maps"
    assert alpha.lookup("MapMaster") == "com.example.maps"
    assert beta.lookup("MapMaster") is None
    assert beta.lookup("Dead") is None


def test_snapshot_has_resolver_facing_term_package_shape(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(_entry("Maps", "com.example.maps"))
    store.upsert(_entry("Music", "com.example.music"))

    assert AppKnowledge(store).snapshot() == {
        "Maps": "com.example.maps",
        "Music": "com.example.music",
    }


def test_corrupted_event_line_is_skipped_on_reload(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(_entry("Maps", "com.example.maps"))
    with store.events_path.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    with store.events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"op": "upsert", "entry": {}}) + "\n")

    reloaded = AppKnowledgeStore(str(tmp_path))
    assert [entry["term"] for entry in reloaded.entries()] == ["Maps"]
    assert json.loads(reloaded.kb_path.read_text(encoding="utf-8")) == [
        _entry("Maps", "com.example.maps")
    ]


def test_sync_device_bulk_upserts_and_refreshes_last_seen(tmp_path, monkeypatch):
    store = AppKnowledgeStore(str(tmp_path))
    clock = {"value": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr("phone_agent.v2.appkb._iso_now", lambda: clock["value"])

    store.sync_device("serial-1", [("com.example.maps", "Maps")])
    first = store.entries(scope="device:serial-1")[0]
    clock["value"] = "2026-02-01T00:00:00+00:00"
    store.sync_device("serial-1", [("com.example.maps", "Maps")])
    refreshed = store.entries(scope="device:serial-1")[0]

    assert refreshed["kind"] == "device"
    assert refreshed["confidence"] == 1.0
    assert refreshed["first_seen"] == first["first_seen"]
    assert refreshed["last_seen"] == "2026-02-01T00:00:00+00:00"
    assert len(store.events_path.read_text(encoding="utf-8").splitlines()) == 2


def test_should_save_fails_closed_for_sensitive_transient_or_unknown_knowledge():
    assert should_save("learned", durable=True, sensitive=False)
    assert not should_save("learned", durable=False, sensitive=False)
    assert not should_save("user", durable=True, sensitive=True)
    assert not should_save("mystery", durable=True, sensitive=False)
    assert not should_save("learned", durable=True, sensitive=None)  # type: ignore[arg-type]
