"""Process-separated bridge between the phone runner and NiceGUI.

The bridge owns no device or agent objects.  It launches a detached runner,
tails the runner's append-only event file, and mirrors the same public snapshot
shape consumed by :mod:`phone_agent.web.app`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from phone_agent.v2.agent import RunResult
from phone_agent.v2.config import V2Config, load_project_env
from phone_agent.v2.run_events import OBS_RE, WebEventMiddleware
from phone_agent.v2.run_ipc import (
    JsonlReader,
    JsonlWriter,
    RunPaths,
    RunSpec,
    app_kb_generation,
    append_control,
    atomic_write_json,
    capability_snapshot,
    config_fingerprint,
    pid_is_alive,
    read_pid,
    read_run_spec,
    resolved_config_dict,
    write_run_spec,
)

_ACTIVE_STATUSES = {"starting", "running", "waiting_hitl"}
_POLL_SECONDS = 0.3


def _read_json_mtime(cache: dict[str, Any], key: str, path: Path) -> Any:
    """Read a JSON file with mtime caching; None when missing/undecodable."""

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - memory readout is best-effort
        return None
    cache[key] = (mtime, data)
    return data


class WebRunBridge:
    """Launch or reattach one runner and expose a lock-protected snapshot."""

    def __init__(
        self,
        overrides: dict[str, Any] | None = None,
        *,
        config_factory: Callable[[dict[str, Any] | None], Any] = V2Config.from_env,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.overrides = dict(overrides or {})
        self._config_factory = config_factory
        self._popen_factory = popen_factory
        self._config: Any | None = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._pid_owner: int | None = None
        self._paths: RunPaths | None = None
        self._event_reader: JsonlReader | None = None
        self._run_snapshot: dict[str, Any] = {}
        self._memory_cache: dict[str, Any] = {}
        self._reset_state()
        self._reattach_existing()

    def _reset_state(self) -> None:
        self._pid_owner = None
        self.current_screen: str | None = None
        self.current_app: str | None = None
        self.screen_seq: int | None = None
        self.screens: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.task_board = ""
        self.status = "idle"
        self.pending_hitl_prompt: str | None = None
        self.final_result: RunResult | None = None
        self.tokens = 0
        self.usage: dict[str, int] = {}
        self.error: str | None = None
        self.run_id: str | None = None
        self.task = ""

    def _resolved_config(self, overrides: dict[str, Any] | None = None) -> Any:
        load_project_env()
        config = self._config_factory(overrides)
        self._config = config
        return config

    def _runs_dir(self, config: Any | None = None) -> Path:
        resolved = config or self._config
        if resolved is None:
            resolved = self._resolved_config(dict(self.overrides))
        return Path(getattr(resolved, "runs_dir", "memory/runs"))

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive()) or self.status in (
                _ACTIVE_STATUSES
            )

    def start(self, task: str, overrides: dict[str, Any] | None = None) -> str:
        clean_task = str(task).strip()
        if not clean_task:
            raise ValueError("任务不能为空")
        if not self.running:
            # Another console may have launched a run after this bridge object
            # was constructed. Re-scan at the actual admission point.
            self._reattach_existing()
        with self._lock:
            if self.running:
                raise RuntimeError("已有任务正在运行")
            merged = {**self.overrides, **(overrides or {})}
            config = self._resolved_config(merged)
            config_values = resolved_config_dict(config)
            run_id = uuid.uuid4().hex
            paths = RunPaths.for_run(self._runs_dir(config), run_id)
            paths.run_dir.mkdir(parents=True, exist_ok=False)
            paths.run_dir.chmod(0o700)
            paths.events.touch(mode=0o600)
            paths.control.touch(mode=0o600)
            snapshot = {
                "config_fingerprint": config_fingerprint(config_values),
                "memory_generation": app_kb_generation(config),
                "capabilities": capability_snapshot(config),
                "ts": time.time(),
            }
            spec = RunSpec(
                run_id=run_id,
                task=clean_task,
                overrides=config_values,
                snapshot=snapshot,
                events_path=str(paths.events.resolve()),
                control_path=str(paths.control.resolve()),
            )
            write_run_spec(paths.spec, spec)

            self._reset_state()
            self.run_id = run_id
            self.task = clean_task
            self.status = "starting"
            self._paths = paths
            self._run_snapshot = snapshot
            self._event_reader = JsonlReader(paths.events)
            try:
                self._process = self._popen_factory(
                    [sys.executable, "-m", "phone_agent.runner", str(paths.spec.resolve())],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                process_pid = getattr(self._process, "pid", None)
                if process_pid:
                    paths.pid.write_text(f"{int(process_pid)}\n", encoding="utf-8")
                    paths.pid.chmod(0o600)
                    self._pid_owner = int(process_pid)
            except Exception as exc:
                result = RunResult(
                    False, f"error: runner_start_failed:{type(exc).__name__}", 0, None
                )
                event = {
                    "event": "run_end",
                    "status": "error",
                    "result": asdict(result),
                    "tokens_total": 0,
                    "ts": time.time(),
                }
                atomic_write_json(
                    paths.summary,
                    {
                        "run_id": run_id,
                        "task": clean_task,
                        "status": "error",
                        "result": asdict(result),
                        "usage": {},
                        "snapshot": snapshot,
                        "finished_at": event["ts"],
                    },
                )
                with JsonlWriter(paths.events) as writer:
                    writer.put(event)
                self._apply_event(event)
                raise RuntimeError("runner 启动失败") from exc
            self.status = "running"
            self._start_tail_thread()
            return run_id

    # -- App-KB view / dream ---------------------------------------------
    def _kb_store(self) -> Any | None:
        """Best-effort on-disk App-KB store; runner state is process-local."""

        try:
            from phone_agent.v2.appkb import AppKnowledgeStore

            config = self._config or self._resolved_config(dict(self.overrides))
            memory_dir = getattr(config, "memory_dir", "memory")
            if not (Path(memory_dir) / "app_kb" / "kb.json").exists():
                return None
            return AppKnowledgeStore(memory_dir)
        except Exception:  # noqa: BLE001 - KB view is optional
            return None

    def kb_entries(self) -> list[dict[str, Any]]:
        store = self._kb_store()
        if store is None:
            return []
        try:
            return [dict(entry) for entry in store.entries(include_stale=True)]
        except Exception:  # noqa: BLE001
            return []

    def run_dream(self) -> dict[str, Any]:
        store = self._kb_store()
        if store is None:
            return {"status": "skipped", "reason": "app_kb_empty"}
        try:
            from phone_agent.v2.dream import consolidate

            return consolidate(store, inventory=None, light=False)
        except Exception as exc:  # noqa: BLE001
            return {"status": "skipped", "reason": type(exc).__name__}

    def memory_snapshot(self) -> dict[str, Any]:
        config = self._config
        if config is None:
            try:
                config = self._resolved_config(dict(self.overrides))
            except Exception:  # noqa: BLE001
                config = None
        base = getattr(config, "experience_dir", None) or "memory/experience"
        raw_episodes = _read_json_mtime(
            self._memory_cache, "episodes", Path(base) / "episodes.json"
        )
        episodes: list[dict[str, Any]] = []
        if isinstance(raw_episodes, dict):
            episodes = sorted(
                (dict(item) for item in raw_episodes.values() if isinstance(item, dict)),
                key=lambda item: float(item.get("ts_start", 0) or 0),
                reverse=True,
            )[:30]
        return {
            "episodes": episodes,
            "recall_stats": _read_json_mtime(
                self._memory_cache, "recall", Path(base) / "recall_stats.json"
            ),
        }

    # -- Control and process lifecycle -----------------------------------
    def request_stop(self) -> bool:
        with self._lock:
            if not self.running or self._paths is None:
                return False
            append_control(self._paths.control, {"type": "stop"})
            return True

    def submit_hitl(self, answer: str) -> None:
        clean_answer = str(answer).strip()
        if not clean_answer:
            raise ValueError("回答不能为空")
        with self._lock:
            if self._paths is None or not self.pending_hitl_prompt:
                raise RuntimeError("当前没有待处理的人工确认")
            append_control(
                self._paths.control, {"type": "hitl", "answer": clean_answer}
            )
            self.pending_hitl_prompt = None
            self.status = "running"

    def _start_tail_thread(self) -> None:
        self._thread = threading.Thread(
            target=self._tail_loop, name="phone-agent-web-tail", daemon=True
        )
        self._thread.start()

    def _tail_loop(self) -> None:
        while True:
            self._drain_events()
            with self._lock:
                if self.status not in _ACTIVE_STATUSES:
                    self._cleanup_pid()
                    return
                process = self._process
                pid = read_pid(self._paths.pid) if self._paths else None
                dead = process.poll() is not None if process is not None else not pid_is_alive(pid)
                if dead:
                    # The final line is flushed before normal process exit. Read
                    # once more before deciding that no run_end exists.
                    self._drain_events()
                    if self.status in _ACTIVE_STATUSES:
                        self._mark_runner_died()
                    self._cleanup_pid()
                    return
            time.sleep(_POLL_SECONDS)

    def _cleanup_pid(self) -> None:
        if self._paths is None:
            return
        recorded_pid = read_pid(self._paths.pid)
        if self._pid_owner is not None and recorded_pid != self._pid_owner:
            return
        try:
            self._paths.pid.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _mark_runner_died(self) -> None:
        if self._paths is None:
            return
        steps = max((int(step.get("step", 0)) for step in self.steps), default=0)
        result = RunResult(False, "error: runner_died", steps, None)
        event = {
            "event": "run_end",
            "status": "error",
            "result": asdict(result),
            "tokens_total": self.tokens,
            "ts": time.time(),
        }
        with JsonlWriter(self._paths.events) as writer:
            writer.put(event)
        atomic_write_json(
            self._paths.summary,
            {
                "run_id": self.run_id,
                "task": self.task,
                "status": "error",
                "result": asdict(result),
                "usage": self.usage,
                "snapshot": self._run_snapshot,
                "finished_at": event["ts"],
            },
        )
        self._apply_event(event)

    @staticmethod
    def _repair_dead_run(
        paths: RunPaths, spec: RunSpec, events: list[dict[str, Any]]
    ) -> None:
        """Persist the synthetic terminal event for an unreaped runner."""

        steps = max((int(event.get("step", 0)) for event in events), default=0)
        tokens = max(
            (int(event.get("tokens_total", 0)) for event in events), default=0
        )
        result = RunResult(False, "error: runner_died", steps, None)
        now = time.time()
        with JsonlWriter(paths.events) as writer:
            writer.put(
                {
                    "event": "run_end",
                    "status": "error",
                    "result": asdict(result),
                    "tokens_total": tokens,
                    "ts": now,
                }
            )
        atomic_write_json(
            paths.summary,
            {
                "run_id": spec.run_id,
                "task": spec.task,
                "status": "error",
                "result": asdict(result),
                "usage": {},
                "snapshot": spec.snapshot,
                "finished_at": now,
            },
        )
        try:
            paths.pid.unlink()
        except OSError:
            pass

    def _reattach_existing(self) -> None:
        try:
            config = self._resolved_config(dict(self.overrides))
            runs_dir = self._runs_dir(config)
            if not runs_dir.exists():
                return
            candidates: list[tuple[float, RunPaths, RunSpec]] = []
            repaired: list[tuple[float, RunPaths, RunSpec]] = []
            for run_dir in runs_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                paths = RunPaths.for_run(runs_dir, run_dir.name)
                try:
                    spec = read_run_spec(paths.spec)
                except Exception:  # noqa: BLE001 - incomplete directory is ignored
                    continue
                events = JsonlReader(paths.events).read_new()
                has_run_end = any(event.get("event") == "run_end" for event in events)
                pid = read_pid(paths.pid)
                if has_run_end:
                    try:
                        paths.pid.unlink()
                    except OSError:
                        pass
                    continue
                if pid_is_alive(pid):
                    try:
                        started = float(spec.snapshot.get("ts", 0))
                    except (TypeError, ValueError):
                        started = 0.0
                    candidates.append((started, paths, spec))
                elif pid is not None:
                    try:
                        started = float(spec.snapshot.get("ts", 0))
                    except (TypeError, ValueError):
                        started = 0.0
                    self._repair_dead_run(paths, spec, events)
                    repaired.append((started, paths, spec))
            if not candidates:
                if repaired:
                    _, paths, spec = max(repaired, key=lambda item: item[0])
                    self._reset_state()
                    self._paths = paths
                    self.run_id = spec.run_id
                    self.task = spec.task
                    self._run_snapshot = spec.snapshot
                    self.status = "running"
                    self._event_reader = JsonlReader(paths.events)
                    self._drain_events()
                return
            _, paths, spec = max(candidates, key=lambda item: item[0])
            self._reset_state()
            self._paths = paths
            self.run_id = spec.run_id
            self.task = spec.task
            self._run_snapshot = spec.snapshot
            self.status = "running"
            self._event_reader = JsonlReader(paths.events)
            self._drain_events()
            self._start_tail_thread()
        except Exception:  # noqa: BLE001 - console startup remains available
            self._reset_state()
            self._paths = None
            self._event_reader = None

    # -- Event projection -------------------------------------------------
    def _step(self, number: int) -> dict[str, Any]:
        for step in self.steps:
            if step["step"] == number:
                return step
        step = {
            "step": number,
            "intent": "",
            "tool": "",
            "target": "",
            "result": "",
            "ok": None,
            "latency_ms": 0,
            "status": "running",
            "args": {},
            "model_latency_ms": 0,
            "tool_latency_ms": 0,
            "screen_seq": None,
        }
        self.steps.append(step)
        self.steps.sort(key=lambda item: item["step"])
        return step

    @staticmethod
    def _target(args: dict[str, Any]) -> str:
        for key in ("target_description", "target_mark_id", "app_name", "direction"):
            if args.get(key) not in (None, ""):
                return str(args[key])
        if args.get("start") is not None and args.get("end") is not None:
            return f"{args['start']} → {args['end']}"
        if args.get("text") not in (None, ""):
            return str(args["text"])
        return ""

    def _apply_event(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "model_call":
            step = self._step(int(event.get("step", 0)))
            step["latency_ms"] = int(event.get("latency_ms", 0))
            step["model_latency_ms"] = int(event.get("latency_ms", 0))
            if event.get("error"):
                step.update(result=str(event["error"]), ok=False, status="error")
            self.tokens = int(event.get("tokens_total", self.tokens))
        elif kind == "tool_call":
            step = self._step(int(event.get("step", 0)))
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            step.update(
                intent=str(args.get("intent", "")),
                tool=str(event.get("tool", "")),
                target=self._target(args),
                status="running",
                args=args,
            )
        elif kind == "tool_result":
            step = self._step(int(event.get("step", 0)))
            ok = bool(event.get("ok"))
            result_text = event.get("text") or event.get("error") or ""
            step.update(
                result=str(result_text),
                ok=ok,
                status="success" if ok else "error",
                tool_latency_ms=int(event.get("latency_ms", 0)),
            )
            match = OBS_RE.search(str(result_text))
            if match:
                step["screen_seq"] = int(match.group("seq"))
        elif kind == "safety_warning":
            step = self._step(int(event.get("step", 0)))
            step.update(result=str(event.get("text", "")), ok=False, status="warning")
        elif kind == "screen":
            image = str(event.get("image", "")) or None
            self.current_screen = image
            self.current_app = event.get("current_app") or self.current_app
            self.screen_seq = event.get("screen_seq")
            if image:
                self.screens.append(
                    {"seq": self.screen_seq, "app": self.current_app, "image": image}
                )
                if len(self.screens) > 30:
                    del self.screens[: len(self.screens) - 30]
        elif kind == "taskdoc_snapshot":
            self.task_board = str(event.get("text", ""))
        elif kind == "pending_hitl":
            prompt = event.get("prompt")
            self.pending_hitl_prompt = str(prompt) if prompt else None
            self.status = "waiting_hitl" if prompt else "running"
        elif kind == "run_end":
            payload = event.get("result") or {}
            self.final_result = RunResult(**payload)
            self.status = str(event.get("status", "failed"))
            self.tokens = int(event.get("tokens_total", self.tokens))
            self.error = self.final_result.reason if self.status == "error" else None
            self.pending_hitl_prompt = None

    def _drain_events(self) -> list[dict[str, Any]]:
        with self._lock:
            reader = self._event_reader
            if reader is None:
                return []
            drained = reader.read_new()
            for event in drained:
                self._apply_event(event)
            return drained

    def poll_events(self) -> list[dict[str, Any]]:
        return self._drain_events()

    def snapshot(self) -> dict[str, Any]:
        self._drain_events()
        with self._lock:
            if self._paths is not None and not self.usage:
                summary = _read_json_mtime(
                    self._memory_cache, f"run:{self.run_id}", self._paths.summary
                )
                if isinstance(summary, dict) and isinstance(summary.get("usage"), dict):
                    self.usage = {
                        str(key): int(value)
                        for key, value in summary["usage"].items()
                    }
            capabilities_raw = self._run_snapshot.get("capabilities", {})
            agent_registry = getattr(
                getattr(self, "_agent", None), "capability_registry", None
            )
            if agent_registry is not None:
                # In-process agent (tests / direct embedding): its registry wins.
                capabilities = [dict(row) for row in agent_registry.status()]
            elif isinstance(capabilities_raw, dict) and capabilities_raw:
                capabilities = [dict(row) for row in capabilities_raw.values()]
            else:
                # No run snapshot yet (pre-run / idle): the registry is a pure
                # function of config + memory dir, so derive it directly — the
                # console's capability chips work before the first run.
                try:
                    from phone_agent.v2.capabilities import (
                        build_capability_registry,
                    )

                    cfg = self._config or self._config_factory(self.overrides)
                    capabilities = [
                        dict(row) for row in build_capability_registry(cfg).status()
                    ]
                except Exception:  # noqa: BLE001 - chips are best-effort
                    capabilities = []
            return {
                "run_id": self.run_id,
                "task": self.task,
                "status": self.status,
                "current_screen": self.current_screen,
                "current_app": self.current_app,
                "screen_seq": self.screen_seq,
                "screens": list(self.screens),
                "steps": [dict(step) for step in self.steps],
                "task_board": self.task_board,
                "pending_hitl_prompt": self.pending_hitl_prompt,
                "final_result": asdict(self.final_result) if self.final_result else None,
                "tokens": self.tokens,
                "usage": dict(self.usage),
                "capabilities": capabilities,
                "error": self.error,
            }

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            self._drain_events()
            return self.status not in _ACTIVE_STATUSES
        thread.join(timeout)
        self._drain_events()
        return not thread.is_alive()


__all__ = ["WebEventMiddleware", "WebRunBridge"]
