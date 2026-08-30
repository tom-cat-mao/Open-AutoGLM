"""Thread-safe bridge between the synchronous phone agent and a web UI.

The agent remains headless: this module attaches one observational middleware
through :class:`phone_agent.v2.agent.ThinPhoneAgent`'s optional extension point
and runs the blocking loop on a daemon thread. UI code consumes an in-memory
event queue and answers HITL prompts through a ``threading.Event``.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config

from phone_agent.v2.agent import RunResult, ThinPhoneAgent
from phone_agent.v2.config import V2Config, load_project_env
from phone_agent.v2.middleware._redact import redact_text
from phone_agent.v2.middleware._tokens import estimate_message_tokens, usage_tokens
from phone_agent.v2.middleware.trace import redact_args

_OBS_RE = re.compile(r"\[OBS\]\s+app=(?P<app>.*?)\s+screen#(?P<seq>\d+)")


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
_FAIL_PREFIXES = (
    "error:",
    "错误",
    "失败",
    "denied:",
    "未定位",
    "定位失败",
    "未写入（输入无效）",
    "未写入（校验失败）",
    "⚠️ 已拦截（未执行）",
)
_SAFETY_MARKERS = ("⚠️ 已拦截（未执行）", "confirm_irreversible=true")


def _message_text(message: Any) -> str:
    """Join textual content blocks without retaining image payloads."""

    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
    ).strip()


def _image_url(block: Any) -> str:
    if not isinstance(block, dict) or block.get("type") not in {"image", "image_url"}:
        return ""
    payload = block.get("image_url", block.get("source", ""))
    if isinstance(payload, dict):
        return str(payload.get("url", payload.get("data", "")) or "")
    return str(payload or "")


def _response_message(response: Any) -> Any | None:
    result = getattr(response, "result", None)
    messages = result if isinstance(result, list) else [response]
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai" or getattr(
            message, "tool_calls", None
        ):
            return message
    return messages[-1] if messages and messages[-1] is not None else None


def _safe_result_text(content: Any, limit: int = 1200) -> str:
    text = redact_text(_message_text(content)).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _result_ok(result: Any, text: str, error: str | None = None) -> bool:
    if error:
        return False
    if str(getattr(result, "status", "")).lower() == "error":
        return False
    normalized = text.strip().lower()
    return not any(normalized.startswith(prefix.lower()) for prefix in _FAIL_PREFIXES)


def _taskdoc_from_messages(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        text = _message_text(message)
        marker = "[TASK_DOC]"
        if marker in text:
            return text[text.index(marker) + len(marker) :].strip()
    return None


class WebEventMiddleware(AgentMiddleware):
    """Publish a compact, structured, in-memory event stream for the web UI.

    This middleware never writes screenshots or event payloads to disk. Tool
    arguments and result text follow the existing trace redaction helpers; the
    screenshot data URL is isolated in a dedicated ``screen`` event. Any event
    extraction failure is fail-open and cannot break the phone-agent loop.
    """

    def __init__(self, events: queue.Queue[dict[str, Any]]) -> None:
        super().__init__()
        self.events = events
        self._step = 0
        self._tokens = 0
        self._last_taskdoc: str | None = None
        self._last_screen_key: tuple[str, Any] | None = None
        # Soft-stop: the bridge sets the flag; the next before_model ends the run
        # through the existing takeover channel (no new terminal mechanism).
        self._session: Any | None = None
        self._stop_requested = False

    def attach_session(self, session: Any) -> None:
        """Bind the run's session (for soft-stop) once the agent exists."""

        self._session = session

    def request_stop(self) -> None:
        """Ask the run to stop after the current step (web console 停止 button)."""

        self._stop_requested = True

    @property
    def step(self) -> int:
        return self._step

    @property
    def tokens(self) -> int:
        return self._tokens

    def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        self.events.put(event)

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        try:
            if self._stop_requested:
                if self._session is not None:
                    try:
                        self._session.takeover_reason = "用户从 Web 控制台停止"
                    except Exception:  # noqa: BLE001 - best-effort flag only
                        pass
                self._emit({"event": "stopping", "step": self._step})
                return {"jump_to": "end"}
            messages = state.get("messages", []) if isinstance(state, dict) else []
            taskdoc = _taskdoc_from_messages(messages or [])
            if taskdoc is not None and taskdoc != self._last_taskdoc:
                self._last_taskdoc = taskdoc
                self._emit(
                    {
                        "event": "taskdoc_snapshot",
                        "step": self._step + 1,
                        "text": taskdoc,
                    }
                )
            for message in reversed(messages or []):
                if self._emit_screen_from_content(getattr(message, "content", None)):
                    break
        except Exception:  # noqa: BLE001 - observability must never break the loop
            pass
        return None

    async def abefore_model(
        self, state, runtime
    ) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def _record_model(self, response: Any, latency_ms: int, error: str | None) -> None:
        self._step += 1
        message = _response_message(response) if response is not None else None
        turn_tokens = 0
        if message is not None:
            reported_tokens = usage_tokens(message)
            turn_tokens = (
                reported_tokens
                if reported_tokens is not None
                else estimate_message_tokens(message)
            )
        self._tokens += turn_tokens
        self._emit(
            {
                "event": "model_call",
                "step": self._step,
                "latency_ms": latency_ms,
                "tokens": turn_tokens,
                "tokens_total": self._tokens,
                "error": redact_text(error) if error else None,
            }
        )

    def wrap_model_call(self, request, handler):  # noqa: ANN001
        started = time.perf_counter()
        try:
            response = handler(request)
        except Exception as exc:  # noqa: BLE001 - mirror then preserve core failure
            self._record_model(
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_model(response, int((time.perf_counter() - started) * 1000), None)
        return response

    async def awrap_model_call(self, request, handler):  # noqa: ANN001
        started = time.perf_counter()
        try:
            response = await handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_model(
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_model(response, int((time.perf_counter() - started) * 1000), None)
        return response

    def _record_tool_result(
        self, name: str, result: Any, latency_ms: int, error: str | None
    ) -> None:
        content = getattr(result, "content", None) if result is not None else None
        text = _safe_result_text(content) if content is not None else ""
        ok = _result_ok(result, text, error)
        self._emit(
            {
                "event": "tool_result",
                "step": self._step,
                "tool": name,
                "text": text,
                "ok": ok,
                "latency_ms": latency_ms,
                "error": redact_text(error) if error else None,
            }
        )
        if text and any(marker in text for marker in _SAFETY_MARKERS):
            self._emit(
                {
                    "event": "safety_warning",
                    "step": self._step,
                    "tool": name,
                    "text": text,
                }
            )

        self._emit_screen_from_content(content)

    def _emit_screen_from_content(self, content: Any) -> bool:
        """Emit the newest image in one message, deduplicated by URL and seq."""

        if not isinstance(content, list):
            return False
        text = _message_text(content)
        obs = _OBS_RE.search(text)
        current_app = obs.group("app") if obs else None
        parsed_seq = int(obs.group("seq")) if obs else None
        for block in reversed(content):
            url = _image_url(block)
            if not url:
                continue
            screen_seq = block.get("screen_seq", parsed_seq)
            screen_key = (url, screen_seq)
            if screen_key == self._last_screen_key:
                return True
            self._last_screen_key = screen_key
            self._emit(
                {
                    "event": "screen",
                    "step": self._step,
                    "image": url,
                    "current_app": current_app,
                    "screen_seq": screen_seq,
                }
            )
            return True
        return False

    def wrap_tool_call(self, request, handler):  # noqa: ANN001
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        self._emit(
            {
                "event": "tool_call",
                "step": self._step,
                "tool": name,
                "args": redact_args(args),
            }
        )
        started = time.perf_counter()
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_tool_result(
                name,
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_tool_result(
            name, result, int((time.perf_counter() - started) * 1000), None
        )
        return result

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        self._emit(
            {
                "event": "tool_call",
                "step": self._step,
                "tool": name,
                "args": redact_args(args),
            }
        )
        started = time.perf_counter()
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_tool_result(
                name,
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_tool_result(
            name, result, int((time.perf_counter() - started) * 1000), None
        )
        return result

    def emit_run_end(self, result: RunResult, *, status: str) -> None:
        """Publish the final result after ``ThinPhoneAgent.run`` classifies it."""

        self._emit(
            {
                "event": "run_end",
                "status": status,
                "result": asdict(result),
                "tokens_total": self._tokens,
            }
        )


def _terminal_status(result: RunResult, agent: Any) -> str:
    if result.success:
        return "succeeded"
    if result.reason == "token_budget_exhausted":
        return "budget_exhausted"
    if result.reason == "loop_fuse":
        return "loop_fuse"
    if getattr(getattr(agent, "session", None), "takeover_reason", None):
        return "takeover"
    if str(result.reason).startswith("error:"):
        return "error"
    return "failed"


class WebRunBridge:
    """Own one background run and expose a lock-protected UI snapshot."""

    def __init__(
        self,
        overrides: dict[str, Any] | None = None,
        *,
        config_factory: Callable[[dict[str, Any] | None], Any] = V2Config.from_env,
        agent_factory: Callable[..., Any] = ThinPhoneAgent,
    ) -> None:
        self.overrides = dict(overrides or {})
        self._config_factory = config_factory
        self._agent_factory = agent_factory
        self._config: Any | None = None
        self._lock = threading.RLock()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._hitl_event: threading.Event | None = None
        self._hitl_answer: str | None = None
        self._agent: Any | None = None
        self._middleware: WebEventMiddleware | None = None
        self._memory_cache: dict[str, Any] = {}
        self._reset_state()

    def _reset_state(self) -> None:
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
        self.error: str | None = None
        self.run_id: str | None = None
        self.task = ""

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive()) or self.status in {
                "starting",
                "running",
                "waiting_hitl",
            }

    def start(
        self,
        task: str,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        clean_task = str(task).strip()
        if not clean_task:
            raise ValueError("任务不能为空")
        with self._lock:
            if self.running:
                raise RuntimeError("已有任务正在运行")
            self._events = queue.Queue()
            self._reset_state()
            self._hitl_event = None
            self._hitl_answer = None
            self._agent = None
            self._middleware = None
            self.task = clean_task
            self.status = "starting"
            run_overrides = {**self.overrides, **(overrides or {})}
            self._thread = threading.Thread(
                target=self._run,
                args=(clean_task, run_overrides),
                name="phone-agent-web-run",
                daemon=True,
            )
            self._thread.start()
            return self._thread.name

    def _run(self, task: str, overrides: dict[str, Any]) -> None:
        middleware = WebEventMiddleware(self._events)
        self._middleware = middleware
        agent: Any | None = None
        try:
            load_project_env()
            config = self._config_factory(overrides)
            self._config = config
            agent = self._agent_factory(config, extra_middleware=[middleware])
            self._agent = agent
            middleware.attach_session(getattr(agent, "session", None))
            with self._lock:
                self.run_id = getattr(agent, "run_id", None)
                self.status = "running"
            result = agent.run(task, hitl_handler=self._hitl_handler)
            status = _terminal_status(result, agent)
        except Exception as exc:  # noqa: BLE001 - terminal error belongs in UI state
            result = RunResult(
                success=False,
                reason=redact_text(f"error: {type(exc).__name__}: {exc}"),
                steps=middleware.step,
                trace_path=getattr(agent, "trace_path", None),
            )
            status = "error"
        middleware.emit_run_end(result, status=status)
        self._drain_events()

    def _hitl_handler(self, prompt: str) -> str:
        wait_event = threading.Event()
        with self._lock:
            self.pending_hitl_prompt = str(prompt)
            self.status = "waiting_hitl"
            self._hitl_answer = None
            self._hitl_event = wait_event
        wait_event.wait()
        with self._lock:
            answer = self._hitl_answer or "reject"
            self._hitl_event = None
            self._hitl_answer = None
            self.pending_hitl_prompt = None
            self.status = "running"
        return answer

    # -- App-KB view / dream ---------------------------------------------
    def _kb_store(self) -> Any | None:
        """Best-effort store: the live run's store, else the on-disk KB."""

        agent = self._agent
        store = getattr(getattr(agent, "session", None), "app_store", None)
        if store is not None:
            return store
        try:
            from pathlib import Path

            from phone_agent.v2.appkb import AppKnowledgeStore

            config = self._config
            if config is None:
                config = self._config_factory(dict(self.overrides))
            memory_dir = getattr(config, "memory_dir", "memory")
            if not (Path(memory_dir) / "app_kb" / "kb.json").exists():
                return None
            return AppKnowledgeStore(memory_dir)
        except Exception:  # noqa: BLE001 - KB view is optional
            return None

    def kb_entries(self) -> list[dict[str, Any]]:
        """Current App-KB entries for the 应用库 tab (empty when unavailable)."""

        store = self._kb_store()
        if store is None:
            return []
        try:
            return [dict(entry) for entry in store.entries(include_stale=True)]
        except Exception:  # noqa: BLE001
            return []

    def run_dream(self) -> dict[str, Any]:
        """Run one full rule-based consolidation on the App-KB (manual dream)."""

        store = self._kb_store()
        if store is None:
            return {"status": "skipped", "reason": "app_kb_empty"}
        try:
            from phone_agent.v2.dream import consolidate

            return consolidate(store, inventory=None, light=False)
        except Exception as exc:  # noqa: BLE001 - maintenance never breaks the UI
            return {"status": "skipped", "reason": type(exc).__name__}

    def memory_snapshot(self) -> dict[str, Any]:
        """Experience-plane readout for the 记忆 tab (mtime-cached, best-effort).

        Reads the episode materialized view and the shadow-recall stats written
        by WP-I1/WP-I2. Returns empty sections when the files do not exist yet.
        """

        config = self._config
        if config is None:
            try:
                config = self._config_factory(self.overrides)
            except Exception:  # noqa: BLE001 - fall back to defaults below
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

    def request_stop(self) -> bool:
        """Ask the running task to stop after the current step (soft stop)."""

        with self._lock:
            middleware = self._middleware
            if middleware is None or not self.running:
                return False
            middleware.request_stop()
            return True

    def submit_hitl(self, answer: str) -> None:
        clean_answer = str(answer).strip()
        if not clean_answer:
            raise ValueError("回答不能为空")
        with self._lock:
            wait_event = self._hitl_event
            if wait_event is None or not self.pending_hitl_prompt:
                raise RuntimeError("当前没有待处理的人工确认")
            self._hitl_answer = clean_answer
            self.pending_hitl_prompt = None
            self.status = "running"
            wait_event.set()

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
            # Link the step to the frame its observation produced (if any), so
            # the UI can pin that exact screenshot when the step is clicked.
            match = _OBS_RE.search(str(result_text))
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
                    {
                        "seq": self.screen_seq,
                        "app": self.current_app,
                        "image": image,
                    }
                )
                if len(self.screens) > 30:
                    del self.screens[: len(self.screens) - 30]
        elif kind == "taskdoc_snapshot":
            self.task_board = str(event.get("text", ""))
        elif kind == "run_end":
            payload = event.get("result") or {}
            self.final_result = RunResult(**payload)
            self.status = str(event.get("status", "failed"))
            self.tokens = int(event.get("tokens_total", self.tokens))
            self.error = self.final_result.reason if self.status == "error" else None
            self.pending_hitl_prompt = None

    def _drain_events(self) -> list[dict[str, Any]]:
        drained: list[dict[str, Any]] = []
        with self._lock:
            while True:
                try:
                    event = self._events.get_nowait()
                except queue.Empty:
                    break
                self._apply_event(event)
                drained.append(event)
        return drained

    def poll_events(self) -> list[dict[str, Any]]:
        """Drain newly published events and update the public state mirror."""

        return self._drain_events()

    def snapshot(self) -> dict[str, Any]:
        self._drain_events()
        with self._lock:
            usage: dict[str, int] = {}
            agent = self._agent
            ledger = getattr(getattr(agent, "session", None), "usage_ledger", None)
            if ledger is not None:
                try:
                    usage = ledger.by_role()
                except Exception:  # noqa: BLE001 - usage display is best-effort
                    usage = {}
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
                "final_result": (
                    asdict(self.final_result) if self.final_result else None
                ),
                "tokens": self.tokens,
                "usage": usage,
                "error": self.error,
            }

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        self._drain_events()
        return not thread.is_alive()


__all__ = ["WebEventMiddleware", "WebRunBridge"]
