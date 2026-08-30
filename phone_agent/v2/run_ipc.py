"""File protocols for detached web-console runs.

All files are append-only or atomically replaced.  The event payload itself is
owned by :mod:`phone_agent.v2.run_events`; this module only frames those
unchanged dictionaries as newline-delimited JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from phone_agent.v2.capabilities import build_capability_registry


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    spec: Path
    events: Path
    control: Path
    summary: Path
    pid: Path

    @classmethod
    def for_run(cls, runs_dir: str | Path, run_id: str) -> "RunPaths":
        run_dir = Path(runs_dir) / run_id
        return cls(
            run_dir=run_dir,
            spec=run_dir / "spec.json",
            events=run_dir / "events.jsonl",
            control=run_dir / "control.jsonl",
            summary=run_dir / "run.json",
            pid=run_dir / "runner.pid",
        )


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    task: str
    overrides: dict[str, Any]
    snapshot: dict[str, Any]
    events_path: str
    control_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "RunSpec":
        if not isinstance(payload, dict):
            raise ValueError("run spec must be a JSON object")
        required = {
            "run_id",
            "task",
            "overrides",
            "snapshot",
            "events_path",
            "control_path",
        }
        missing = required - payload.keys()
        if missing:
            raise ValueError(f"run spec missing fields: {sorted(missing)}")
        overrides = payload["overrides"]
        snapshot = payload["snapshot"]
        if not isinstance(overrides, dict) or not isinstance(snapshot, dict):
            raise ValueError("run spec overrides and snapshot must be objects")
        return cls(
            run_id=str(payload["run_id"]),
            task=str(payload["task"]),
            overrides=dict(overrides),
            snapshot=dict(snapshot),
            events_path=str(payload["events_path"]),
            control_path=str(payload["control_path"]),
        )


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def atomic_write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_json_bytes(payload) + b"\n")
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def write_run_spec(path: str | Path, spec: RunSpec) -> None:
    atomic_write_json(path, spec.to_dict())


def read_run_spec(path: str | Path) -> RunSpec:
    return RunSpec.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def resolved_config_dict(config: Any) -> dict[str, Any]:
    """Return the fully resolved, JSON-safe constructor values."""

    values = asdict(config) if is_dataclass(config) else dict(vars(config))
    return json.loads(_json_bytes(values).decode("utf-8"))


def config_fingerprint(config_values: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_json_bytes(config_values)).hexdigest()


def app_kb_generation(config: Any) -> dict[str, Any] | None:
    path = Path(getattr(config, "memory_dir", "memory")) / "app_kb/kb.json"
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        for key in ("generation", "version"):
            if key in payload:
                return {"source": f"kb.json.{key}", "value": payload[key]}
    return {"source": "kb.json.mtime_ns", "value": mtime_ns}


def capability_snapshot(config: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row["cap_id"]): dict(row)
        for row in build_capability_registry(config).status()
    }


class JsonlWriter:
    """Append one complete JSON object per line and flush immediately."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_separator = False
        try:
            if self.path.stat().st_size:
                with self.path.open("rb") as existing:
                    existing.seek(-1, os.SEEK_END)
                    needs_separator = existing.read(1) != b"\n"
        except OSError:
            pass
        self._stream = self.path.open("a", encoding="utf-8")
        if needs_separator:
            # A process can die between write and flush. Keep the torn record on
            # its own invalid line so the next valid terminal record survives.
            self._stream.write("\n")
            self._stream.flush()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def put(self, payload: dict[str, Any]) -> None:
        self._stream.write(_json_bytes(payload).decode("utf-8") + "\n")
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


class JsonlReader:
    """Incremental reader that never consumes an incomplete trailing line."""

    def __init__(self, path: str | Path, offset: int = 0) -> None:
        self.path = Path(path)
        self.offset = max(0, int(offset))

    def read_new(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    stream.seek(start)
                    break
                self.offset = stream.tell()
                try:
                    payload = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
        return events


def append_control(path: str | Path, message: dict[str, Any]) -> None:
    with JsonlWriter(path) as writer:
        writer.put(message)


def read_pid(path: str | Path) -> int | None:
    try:
        pid = int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def pid_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = [
    "JsonlReader",
    "JsonlWriter",
    "RunPaths",
    "RunSpec",
    "app_kb_generation",
    "append_control",
    "atomic_write_json",
    "capability_snapshot",
    "config_fingerprint",
    "pid_is_alive",
    "read_pid",
    "read_run_spec",
    "resolved_config_dict",
    "write_run_spec",
]
