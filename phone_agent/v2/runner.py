"""Detached runner process used exclusively by the NiceGUI console."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any, Callable

from phone_agent.v2.agent import RunResult, ThinPhoneAgent
from phone_agent.v2.config import V2Config
from phone_agent.v2.middleware._redact import redact_text
from phone_agent.v2.run_events import WebEventMiddleware, terminal_status
from phone_agent.v2.run_ipc import (
    JsonlReader,
    JsonlWriter,
    RunSpec,
    app_kb_generation,
    atomic_write_json,
    capability_snapshot,
    config_fingerprint,
    read_run_spec,
    resolved_config_dict,
)


class ControlChannel:
    """Poll an append-only control file and expose stop/HITL signals."""

    def __init__(
        self,
        path: str | Path,
        *,
        stop_callback: Callable[[], None],
        poll_seconds: float = 0.5,
    ) -> None:
        self._reader = JsonlReader(path)
        self._stop_callback = stop_callback
        self._poll_seconds = poll_seconds
        self._closed = threading.Event()
        self._condition = threading.Condition()
        self._answers: list[str] = []
        self._thread = threading.Thread(
            target=self._poll, name="phone-agent-runner-control", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=max(1.0, self._poll_seconds * 3))

    def _poll(self) -> None:
        while not self._closed.is_set():
            for message in self._reader.read_new():
                kind = message.get("type")
                if kind == "stop":
                    self._stop_callback()
                    with self._condition:
                        # A run blocked in HITL must first resume before the
                        # middleware can take its normal soft-stop jump.
                        self._answers.append("reject")
                        self._condition.notify_all()
                elif kind == "hitl":
                    answer = str(message.get("answer", "")).strip()
                    if answer:
                        with self._condition:
                            self._answers.append(answer)
                            self._condition.notify_all()
            self._closed.wait(self._poll_seconds)

    def wait_for_hitl(self) -> str:
        with self._condition:
            while not self._answers and not self._closed.is_set():
                self._condition.wait(timeout=self._poll_seconds)
            if self._answers:
                return self._answers.pop(0)
        return "reject"


def _summary(
    spec: RunSpec,
    result: RunResult,
    status: str,
    *,
    finished_at: float,
    usage: dict[str, int],
) -> dict[str, Any]:
    from dataclasses import asdict

    return {
        "run_id": spec.run_id,
        "task": spec.task,
        "status": status,
        "result": asdict(result),
        "usage": usage,
        "snapshot": spec.snapshot,
        "finished_at": finished_at,
    }


def run_spec(
    spec: RunSpec,
    *,
    agent_factory: Callable[..., Any] = ThinPhoneAgent,
    poll_seconds: float = 0.5,
) -> int:
    """Execute one spec, returning a process-style status code."""

    import time

    events_path = Path(spec.events_path)
    run_dir = events_path.parent
    summary_path = run_dir / "run.json"
    pid_path = run_dir / "runner.pid"
    run_dir.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        os.chmod(pid_path, 0o600)
    except OSError:
        pass

    agent: Any | None = None
    result: RunResult
    status = "error"
    exit_code = 0
    usage: dict[str, int] = {}
    terminal_written = False
    try:
        with JsonlWriter(events_path) as sink:
            middleware = WebEventMiddleware(sink)
            controls = ControlChannel(
                spec.control_path,
                stop_callback=middleware.request_stop,
                poll_seconds=poll_seconds,
            )
            controls.start()
            try:
                config = V2Config(**spec.overrides)
                actual_fingerprint = config_fingerprint(resolved_config_dict(config))
                expected_fingerprint = str(
                    spec.snapshot.get("config_fingerprint", "")
                )
                if actual_fingerprint != expected_fingerprint:
                    raise ValueError("run spec config fingerprint mismatch")
                if app_kb_generation(config) != spec.snapshot.get(
                    "memory_generation"
                ):
                    raise ValueError("run spec memory generation mismatch")
                if capability_snapshot(config) != spec.snapshot.get("capabilities"):
                    raise ValueError("run spec capability snapshot mismatch")
                agent = agent_factory(
                    config, extra_middleware=[middleware], run_id=spec.run_id
                )
                middleware.attach_session(getattr(agent, "session", None))

                def hitl_handler(prompt: str) -> str:
                    middleware.emit(
                        {"event": "pending_hitl", "prompt": str(prompt)}
                    )
                    answer = controls.wait_for_hitl()
                    middleware.emit({"event": "pending_hitl", "prompt": None})
                    return answer

                result = agent.run(spec.task, hitl_handler=hitl_handler)
                status = terminal_status(result, agent)
                ledger = getattr(agent, "usage_ledger", None) or getattr(
                    getattr(agent, "session", None), "usage_ledger", None
                )
                by_role = getattr(ledger, "by_role", None)
                if callable(by_role):
                    try:
                        usage = {
                            str(key): int(value) for key, value in by_role().items()
                        }
                    except Exception:  # noqa: BLE001 - display-only summary
                        usage = {}
            except Exception as exc:  # noqa: BLE001 - terminal IPC state
                result = RunResult(
                    success=False,
                    reason=redact_text(f"error: {type(exc).__name__}: {exc}"),
                    steps=middleware.step,
                    trace_path=getattr(agent, "trace_path", None),
                )
                status = "error"
                exit_code = 1
            finally:
                controls.close()
            # Publish the atomic summary first: once run_end is visible, every
            # reconnecting console can also read terminal usage and metadata.
            atomic_write_json(
                summary_path,
                _summary(spec, result, status, finished_at=time.time(), usage=usage),
            )
            middleware.emit_run_end(result, status=status)
            terminal_written = True
    finally:
        if terminal_written:
            try:
                pid_path.unlink()
            except FileNotFoundError:
                pass
    return exit_code


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m phone_agent.runner")
    parser.add_argument("spec_path")
    args = parser.parse_args(argv)
    try:
        spec = read_run_spec(args.spec_path)
    except Exception as exc:  # noqa: BLE001 - no trustworthy event path yet
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2
    return run_spec(spec)


__all__ = ["ControlChannel", "main", "run_spec"]
