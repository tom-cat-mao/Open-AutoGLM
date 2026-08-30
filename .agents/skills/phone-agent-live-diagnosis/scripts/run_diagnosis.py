#!/usr/bin/env python3
"""Live-diagnosis CLI orchestration for the TaskWizard thin-loop (v2) agent.

Per ``outputs/design-council/ROUND2-D1.md`` §5. This is the driver that ties the
diagnostic evidence stream to the analysis + report package:

* ``run`` (default): build a :class:`~phone_agent.v2.config.V2Config` with the
  diagnostic evidence stream enabled, run :class:`~phone_agent.v2.agent.ThinPhoneAgent`
  **in process** (no subprocess — the v1 eval harness is gone), then analyze the
  emitted ``<run_id>.evidence.jsonl`` into ``summary.json`` + ``report.html``.
* ``run --dry-run``: no device / no network. A scripted fake model + fake session
  are injected via ``sys.modules`` (mirroring ``tests/v2/test_agent_loop.py``) and
  the **real** middleware stack runs (safety / images / trace / taskdoc /
  diagnostic), so a real evidence stream + summary + report are produced offline.
* ``analyze <evidence.jsonl>`` / ``report <summary.json>``: re-derive the summary
  or re-render the report from existing artifacts without re-running the agent.
* ``status <dir>``: print a run directory's ``status.json``.

The heavy lifting lives in the sibling modules: :mod:`evidence` (read the JSONL),
:mod:`taxonomy` (classify tool returns), :mod:`analyze` (build ``summary.json``),
:mod:`sourcemap` (v2 source map), :mod:`report` (render HTML). This file only
orchestrates + preflights + drives the agent.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

# The skill ships under <repo>/.agents/skills/phone-agent-live-diagnosis/scripts.
# Put the scripts dir first (sibling module imports) and the repo root next
# (``phone_agent`` package) on sys.path so this runs as a bare script *and*
# imports cleanly as a module in tests.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from sourcemap import resolve_repo_root  # noqa: E402  (after sys.path setup)

ROOT = resolve_repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyze import build_summary  # noqa: E402
from evidence import EvidenceView, read_evidence  # noqa: E402
from report import render_html  # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "live-diagnosis"
_APPROVE_TOKENS = {"approve", "yes", "y", "同意", "确认", "ok", "允许", "批准"}
_REJECT_TOKENS = {"reject", "no", "n", "拒绝", "取消", "deny", "否"}
_MLX_PROVIDERS = {
    "hybrid",
    "accessibility_locateanything",
    "uiautomator_locateanything",
    "locateanything",
    "locateanything_mlx",
    "mlx",
}


# ---------------------------------------------------------------------------
# small helpers (kept from v1; still valid against the v2 tree)
# ---------------------------------------------------------------------------
def load_project_env() -> None:
    """Load ``PHONE_AGENT_*`` defaults from the project .env (shell env wins)."""

    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("PHONE_AGENT_") or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def slugify(value: str) -> str:
    import re

    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", (value or "").strip()).strip("-")
    if not text:
        return uuid.uuid4().hex[:8]
    return text[:80]


def build_run_id(target: str) -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + slugify(target)[:36]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def trim(value: str, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[:limit] + "\n...<truncated>"


def safe_cmd(cmd: list[str], timeout: int = 8) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": trim(result.stdout, 4000),
            "stderr": trim(result.stderr, 4000),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - preflight is best-effort
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": type(exc).__name__,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def check_mlx_metal(python_path: str) -> dict[str, Any]:
    """Probe MLX + Metal so hybrid/LocateAnything runs fail fast, not mid-run."""

    script = "\n".join(
        [
            "import platform, json",
            "payload={'platform': platform.system(), 'machine': platform.machine()}",
            "try:",
            "    import mlx.core as mx",
            "    payload['import_ok']=True",
            "    payload['default_device']=str(mx.default_device())",
            "    payload['sum']=int(mx.sum(mx.array([1,2,3])).item())",
            "    payload['metal_ok']=payload['sum']==6",
            "except Exception as exc:",
            "    payload['import_ok']=False",
            "    payload['metal_ok']=False",
            "    payload['error_type']=type(exc).__name__",
            "    payload['error']=str(exc)[:500]",
            "print(json.dumps(payload, ensure_ascii=False))",
        ]
    )
    result = safe_cmd([python_path, "-c", script], timeout=12)
    payload: dict[str, Any] = {}
    if result.get("stdout"):
        try:
            payload = json.loads(str(result["stdout"]).splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            payload = {}
    return {**result, "parsed": payload}


def collect_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """v2 preflight: python/.venv, adb + ``wm size``, MLX-Metal, config digest.

    Dropped the v1 ``output-mode`` / ``context-mode`` / ``thinking`` probes (no
    v2 equivalent). Records the grounding provider + taskdoc switch so the report
    header reflects how the run was configured.
    """

    venv_python = ROOT / ".venv" / "bin" / "python"
    python_path = str(venv_python) if venv_python.exists() else sys.executable
    adb_path = shutil.which("adb")
    provider = str(getattr(args, "grounding_provider", None) or "hybrid")
    data: dict[str, Any] = {
        "repo": str(ROOT),
        "python": python_path,
        "adb_path": adb_path,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "device_id": getattr(args, "device_id", None),
        "grounding_provider": provider,
        "taskdoc_enabled": not bool(getattr(args, "no_taskdoc", False)),
        "checks": {},
    }
    data["checks"]["python_version"] = safe_cmd([python_path, "--version"])
    if provider.lower() in _MLX_PROVIDERS:
        data["checks"]["mlx_metal"] = check_mlx_metal(python_path)
    if adb_path and not data["dry_run"]:
        data["checks"]["adb_version"] = safe_cmd([adb_path, "version"])
        data["checks"]["adb_devices"] = safe_cmd([adb_path, "devices", "-l"])
        prefix = [adb_path, "-s", args.device_id] if args.device_id else [adb_path]
        data["checks"]["wm_size"] = safe_cmd(prefix + ["shell", "wm", "size"])
    return data


def resolve_reset_app(args: argparse.Namespace) -> str | None:
    """Package to ``pm clear`` before the run (explicit ``--reset-app`` only)."""

    if getattr(args, "reset_app", None):
        return str(args.reset_app).strip() or None
    return None


def reset_app_on_device(args: argparse.Namespace) -> dict[str, Any]:
    """Best-effort ``adb shell pm clear <package>``; never gates the run."""

    package = resolve_reset_app(args)
    if not package:
        return {"reset_app": None, "performed": False}
    adb_path = shutil.which("adb")
    if not adb_path:
        return {"reset_app": package, "performed": False, "error": "adb_not_found"}
    cmd = [adb_path]
    if args.device_id:
        cmd += ["-s", args.device_id]
    cmd += ["shell", "pm", "clear", package]
    result = safe_cmd(cmd, timeout=30)
    return {
        "reset_app": package,
        "performed": result.get("returncode") == 0,
        "returncode": result.get("returncode"),
        "stdout": trim(str(result.get("stdout") or ""), 500),
        "stderr": trim(str(result.get("stderr") or ""), 500),
    }


def read_status(path: Path) -> dict[str, Any]:
    """Read a run directory's (or explicit) ``status.json``."""

    target = path / "status.json" if path.is_dir() else path
    if target.exists():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"state": "invalid_status_json", "path": str(target)}
    return {"state": "status_missing", "path": str(target)}


# ---------------------------------------------------------------------------
# redaction (shares the production primitive so report parity holds)
# ---------------------------------------------------------------------------
def _redact(text: str | None) -> str:
    """Sensitive-substring redaction via the production shared primitive."""

    if not text:
        return ""
    try:
        from phone_agent.v2.middleware._redact import redact_text

        return redact_text(text)
    except Exception:  # noqa: BLE001 - degrade to identity only if import fails
        return str(text)


# Image keys whose values are screenshot file references; stripped for --share so
# the shared copy carries no on-disk screenshot pointer.
_SCREENSHOT_REF_KEYS = {"path"}


def _redact_deep(value: Any) -> Any:
    """Recursively redact strings and strip screenshot references (for --share).

    The default report is full-fidelity (local-first): unredacted text +
    ``<img src="screenshots/...">`` references. The ``--share`` copy must not
    leak either, so this pass (a) redacts every string via :func:`_redact` and
    (b) drops any ``image.path`` screenshot reference so the shared HTML renders
    no screenshot. Base64 never exists in the summary/evidence to begin with.
    """

    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SCREENSHOT_REF_KEYS and isinstance(item, str):
                # drop the screenshot file pointer entirely.
                continue
            out[str(key)] = _redact_deep(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_deep(item) for item in value]
    return value


def _chmod_600(path: Path) -> None:
    """Best-effort ``chmod 600`` on a produced artifact (local-first privacy)."""

    try:
        if path.exists():
            path.chmod(0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HITL logging handler (writes hitl_decision events into the evidence stream)
# ---------------------------------------------------------------------------
def _decision_from_answer(answer: str) -> str:
    low = (answer or "").strip().lower()
    if low in _APPROVE_TOKENS:
        return "approve"
    if not low or low in _REJECT_TOKENS:
        return "reject"
    return "respond"


def logging_hitl_handler(
    evidence_path: str | None,
    base_handler: Callable[[str], str] = input,
    unredacted: bool = True,
) -> Callable[[str], str]:
    """Wrap a HITL handler so each human verdict is appended to the evidence.

    A HITL interrupt unwinds the graph, so the diagnostic middleware's
    ``wrap_tool_call`` never sees the human decision (§1). The driver records it
    here instead: the requested action + the decision + the reply are appended as
    a ``hitl_decision`` event to the same ``<run_id>.evidence.jsonl``.

    Local-first full-fidelity (A5): by default (``unredacted``) the prompt/reply
    are kept verbatim to match the rest of the diagnosis stream; ``--share``
    deep-redacts the whole summary later. Pass ``unredacted=False`` to redact
    inline (parity with the pre-A5 behavior).
    """

    text = (lambda s: s) if unredacted else _redact

    def handler(prompt: str) -> str:
        answer = str(base_handler(prompt))
        if evidence_path:
            event = {
                "event": "hitl_decision",
                "ts": time.time(),
                "tool": None,
                "requested_action": text(prompt),
                "decision": _decision_from_answer(answer),
                "response_text": text(answer),
            }
            try:
                with open(evidence_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001 - observability must never crash the run
                pass
        return answer

    return handler


# ---------------------------------------------------------------------------
# outcome assembly
# ---------------------------------------------------------------------------
def _returncode_for(success: bool, reason: str, takeover: str | None) -> int:
    if success:
        return 0
    if takeover:
        return 2
    if reason in {"token_budget_exhausted", "loop_fuse", "max_model_calls"}:
        return 3
    return 1


def _outcome_from_run(session: Any, result: Any) -> dict[str, Any]:
    """Build the analyzer ``outcome`` dict from a live/ dry run result.

    Local-first full-fidelity (A5): ``finish_summary`` / ``takeover_reason`` are
    kept verbatim here so the default ``report.html`` shows the real terminal
    text. The ``--share`` export deep-redacts the whole summary separately; the
    P0 #6 production trace stays redacted regardless.
    """

    finished = bool(getattr(session, "finished", False))
    takeover = getattr(session, "takeover_reason", None) or None
    reason = str(getattr(result, "reason", "") or "")
    success = bool(getattr(result, "success", False))
    return {
        "finished": finished,
        "finish_summary": getattr(session, "finish_summary", None) or None,
        "takeover_reason": takeover,
        "reason": reason,
        "returncode": _returncode_for(success, reason, takeover),
        "steps": getattr(result, "steps", None),
    }


def _outcome_from_evidence(view: EvidenceView) -> dict[str, Any]:
    """Rebuild an ``outcome`` dict from an evidence stream (analyze subcommand).

    The evidence ``run_end.terminal`` is already redacted by the middleware, so
    nothing here re-introduces secrets.
    """

    terminal = (view.run_end or {}).get("terminal", {}) if view.run_end else {}
    finished = bool(terminal.get("finished"))
    takeover = terminal.get("takeover_reason")
    return {
        "finished": finished,
        "finish_summary": terminal.get("finish_summary"),
        "takeover_reason": takeover,
        "reason": None,
        "returncode": _returncode_for(finished, "", takeover),
        "steps": (view.run_end or {}).get("steps") if view.run_end else None,
    }


# ---------------------------------------------------------------------------
# run drivers
# ---------------------------------------------------------------------------
def _overrides_from_args(args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    """Map ``run`` CLI flags to V2Config field names.

    Unset flags default to ``None`` and are dropped by ``V2Config.from_env`` so
    they never clobber env-derived values. The diagnostic evidence stream + trace
    dir are forced on so the skill always captures a stream to analyze.
    """

    overrides: dict[str, Any] = {
        "device_id": args.device_id,
        "max_model_calls": args.max_steps,
        "base_url": args.base_url,
        "model_name": args.model,
        "api_key": args.apikey,
        "model_timeout": args.model_timeout,
        "model_max_retries": args.model_max_retries,
        "grounding_provider": args.grounding_provider,
        "accessibility_timeout": args.accessibility_timeout,
        "accessibility_max_marks": args.accessibility_max_marks,
        "locateanything_model": args.locateanything_model,
        "locateanything_max_size": args.locateanything_max_size,
        "lang": args.lang,
        "taskdoc_nudge_steps": args.nudge_steps,
        # forced on for diagnosis:
        "diagnostic_evidence": True,
        "diagnostic_evidence_dir": str(run_dir),
        "trace_dir": str(run_dir / "traces"),
        # local-first full-fidelity (A5): the diagnosis reader is the device owner
        # on their own machine, so the evidence stream is UNREDACTED by default.
        # ``--share`` never touches this — it re-derives a redacted copy from the
        # full-fidelity artifacts. The P0 #6 production trace stays redacted.
        "diagnostic_unredacted": True,
    }
    if args.no_taskdoc:
        overrides["taskdoc_enabled"] = False
    return overrides


def run_agent(args: argparse.Namespace, run_dir: Path) -> tuple[Any, Any]:
    """Run the real :class:`ThinPhoneAgent` in-process with diagnosis enabled."""

    from phone_agent.v2.agent import ThinPhoneAgent
    from phone_agent.v2.config import V2Config

    config = V2Config.from_env(_overrides_from_args(args, run_dir))
    agent = ThinPhoneAgent(config)
    handler = logging_hitl_handler(agent.evidence_path)
    result = agent.run(args.target, hitl_handler=handler)
    return agent, result


def run_dry(args: argparse.Namespace, run_dir: Path) -> tuple[Any, Any]:
    """Offline pipeline: scripted model + fake session, real middleware stack.

    Injects fake ``phone_agent.v2.{model,session,tools,prompts}`` modules via
    ``sys.modules`` (mirrors ``tests/v2/test_agent_loop.py``) so the real
    ThinPhoneAgent assembly + middleware run without a device or network, and a
    real ``evidence.jsonl`` / ``summary.json`` / ``report.html`` are produced.

    This validates the pipeline end-to-end; it does **not** exercise real
    grounding / finish semantics (that is stated in the report, per §5).
    """

    import types

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool

    from phone_agent.v2.config import V2Config

    class ScriptedToolModel(BaseChatModel):
        responses: list[AIMessage]
        i: int = 0

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ANN001
            response = self.responses[min(self.i, len(self.responses) - 1)]
            self.i += 1
            return ChatResult(generations=[ChatGeneration(message=response)])

        def bind_tools(self, tools, **kwargs):  # noqa: ANN001
            return self

        @property
        def _llm_type(self) -> str:
            return "scripted-tool-model"

    def _tool_call(name: str, tool_args: dict, call_id: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": tool_args, "id": call_id, "type": "tool_call"}
            ],
        )

    class DryConfig:
        """Minimal config the fake session exposes to the middleware digest."""

        def __init__(self, base: Any) -> None:
            self.model_name = getattr(base, "model_name", "dry-run")
            self.grounding_provider = getattr(base, "grounding_provider", "none")
            self.max_model_calls = getattr(base, "max_model_calls", 20)
            self.lang = getattr(base, "lang", "cn")
            self.taskdoc_enabled = getattr(base, "taskdoc_enabled", True)
            self.device_id = None

    class DryObservation:
        def __init__(self, seq: int) -> None:
            self.screenshot_b64 = "QUJD"  # "ABC"; never logged (base64-drop)
            self.width = 1080
            self.height = 2400
            self.current_app = "com.android.settings"
            self.screen_seq = seq
            self.marks: dict = {}

    class DrySession:
        def __init__(self, base_config: Any) -> None:
            self.config = DryConfig(base_config)
            self.screen_seq = 0
            self.finished = False
            self.finish_summary: str | None = None
            self.takeover_reason: str | None = None
            self.task_doc = None
            self.seen_states: set = set()
            self.nudged = False

        def observe(self) -> DryObservation:
            self.screen_seq += 1
            self.seen_states.add(("com.android.settings", f"screen_{self.screen_seq}"))
            return DryObservation(self.screen_seq)

    def _build_dry_tools(session: DrySession):
        from phone_agent.v2.taskdoc import TaskDoc, TaskItem

        @tool
        def read_screen() -> str:
            """Re-observe the current screen and return an observation digest."""
            obs = session.observe()
            return f"[OBS] app={obs.current_app} screen#{obs.screen_seq}\nmarks (0): "

        @tool
        def update_task_doc(
            items: list[dict] | None = None,
            add_amendments: list[str] | None = None,
            facts: list[str] | None = None,
        ) -> str:
            """Maintain the task board (goal / route / key facts)."""
            current = session.task_doc or TaskDoc()
            candidate = TaskDoc(
                goal_base=current.goal_base,
                amendments=list(current.amendments),
                items=list(current.items),
                facts=list(current.facts),
            )
            if items is not None:
                candidate.items = [
                    TaskItem(
                        id=str(it.get("id", "")),
                        content=str(it.get("content", "")),
                        status=str(it.get("status", "pending")),
                        reason=it.get("reason"),
                        evidence_note=it.get("evidence_note"),
                    )
                    for it in items
                ]
            if add_amendments:
                candidate.amendments.extend(str(a) for a in add_amendments)
            if facts is not None:
                candidate.facts = [str(f) for f in facts]
            # A4 contract alignment: validate against the pre-write board so the
            # transition discipline (no pending→completed jump / batch back-fill)
            # matches the real ``update_task_doc`` tool.
            error = candidate.validate(previous=current)
            if error is not None:
                return f"未写入（校验失败）：{error}"
            session.task_doc = candidate
            return "已更新任务板。"

        @tool
        def tap(
            target_mark_id: str | None = None, target_description: str | None = None
        ) -> str:
            """Tap a UI element by mark id or natural-language description."""
            return "OK. tapped"

        @tool
        def finish(summary: str, evidence: list[str]) -> str:
            """Declare the task finished. evidence must be non-empty."""
            if not [e for e in (evidence or []) if str(e).strip()]:
                return "error: finish requires non-empty evidence"
            doc = session.task_doc
            if doc is not None and doc.has_open_items():
                return f"路线仍有未完成项：{doc.open_items_summary()}。"
            session.finished = True
            session.finish_summary = summary
            return "已记录完成声明"

        return [read_screen, update_task_doc, tap, finish]

    config = V2Config.from_env(_overrides_from_args(args, run_dir))
    session = DrySession(config)
    responses = [
        _tool_call("read_screen", {}, "c1"),
        _tool_call(
            "update_task_doc",
            {"items": [{"id": "s1", "content": "打开设置页", "status": "completed", "evidence_note": "screen#1 设置页可见"}]},
            "c2",
        ),
        _tool_call("tap", {"target_mark_id": "ax_1"}, "c3"),
        _tool_call(
            "finish",
            {"summary": "已打开设置", "evidence": ["屏幕显示设置页"]},
            "c4",
        ),
        AIMessage(content="任务完成"),
    ]
    model = ScriptedToolModel(responses=responses)

    model_mod = types.ModuleType("phone_agent.v2.model")
    model_mod.build_chat_model = lambda cfg: model
    session_mod = types.ModuleType("phone_agent.v2.session")
    session_mod.PhoneSession = lambda cfg: session
    tools_mod = types.ModuleType("phone_agent.v2.tools")
    tools_mod.build_tools = lambda sess, cfg: _build_dry_tools(sess)
    prompts_mod = types.ModuleType("phone_agent.v2.prompts")
    prompts_mod.get_system_prompt = lambda lang="cn": "你是手机智能体。"

    saved: dict[str, Any] = {}
    injected = {
        "phone_agent.v2.model": model_mod,
        "phone_agent.v2.session": session_mod,
        "phone_agent.v2.tools": tools_mod,
        "phone_agent.v2.prompts": prompts_mod,
    }
    for name, mod in injected.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod
    try:
        from phone_agent.v2.agent import ThinPhoneAgent

        agent = ThinPhoneAgent(config)
        handler = logging_hitl_handler(
            agent.evidence_path, base_handler=lambda p: "approve"
        )
        result = agent.run(args.target, hitl_handler=handler)
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return agent, result


# ---------------------------------------------------------------------------
# post-run finalization (shared by run / dry-run)
# ---------------------------------------------------------------------------
_ARTIFACT_NAMES = ("summary.json", "report.html", "evidence.jsonl", "status.json")


def _lock_down_artifacts(run_dir: Path) -> None:
    """chmod 600 every produced artifact + screenshot (local-first privacy).

    The default report is full-fidelity (unredacted text + on-disk screenshots),
    so the run dir carries private data. Tighten file perms to the owner. The
    screenshots the middleware writes are already 600; re-assert here for any
    that predate this pass and for the top-level artifacts.
    """

    for name in _ARTIFACT_NAMES:
        _chmod_600(run_dir / name)
    for pattern in ("*.evidence.jsonl", "preflight.json"):
        for path in run_dir.glob(pattern):
            _chmod_600(path)
    shots = run_dir / "screenshots"
    if shots.is_dir():
        for png in shots.glob("*.png"):
            _chmod_600(png)


def _write_share_copy(
    run_dir: Path, summary: dict[str, Any], events: list[dict[str, Any]]
) -> Path:
    """Produce the redacted, screenshot-free ``report-share.html`` (A5 §4).

    The default artifacts are local-first full-fidelity. ``--share`` derives a
    copy safe to hand to someone else: every string is redacted via the
    production primitive and every ``image.path`` screenshot pointer is dropped,
    so the shared HTML references no screenshot and leaks no sensitive text. A
    ``summary-share.json`` is written alongside for parity.
    """

    share_summary = _redact_deep(summary)
    share_events = _redact_deep(events)
    share_summary.setdefault("notes", []).append(
        "share 副本：全文脱敏、无截图引用；本机全保真产物见 report.html。"
    )
    share_report = run_dir / "report-share.html"
    share_report.write_text(render_html(share_summary, share_events), encoding="utf-8")
    write_json(run_dir / "summary-share.json", share_summary)
    _chmod_600(share_report)
    _chmod_600(run_dir / "summary-share.json")
    return share_report


def _finalize(
    *,
    run_dir: Path,
    run_id: str,
    target: str,
    command: list[str],
    evidence_path: str | None,
    outcome: dict[str, Any],
    duration_sec: float,
    dry_run: bool,
    share: bool = False,
) -> dict[str, Any]:
    """Analyze the emitted evidence into summary.json + report.html + status.json.

    Local-first full-fidelity: ``report.html`` is the unredacted primary
    deliverable and references screenshots on disk. When ``share`` is set an
    additional redacted, screenshot-free ``report-share.html`` is written.
    """

    events = read_evidence(evidence_path) if evidence_path else []
    view = EvidenceView.from_events(events)
    # Copy the evidence into the run dir under a stable name if it lives elsewhere.
    local_evidence = run_dir / "evidence.jsonl"
    if evidence_path and Path(evidence_path).exists():
        try:
            shutil.copy2(evidence_path, local_evidence)
        except Exception:  # noqa: BLE001 - copy is a convenience, not required
            local_evidence = Path(evidence_path)

    summary = build_summary(
        outcome,
        view,
        run_id=run_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        target=target,
        run_dir=str(run_dir),
        command=command,
        duration_sec=round(duration_sec, 2),
        evidence_stream=str(local_evidence),
        trace=str(run_dir / "traces"),
        artifacts={
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "report.html"),
            "evidence": str(local_evidence),
        },
    )
    if dry_run:
        summary.setdefault("notes", []).append(
            "dry-run：脚本化模型 + 假会话，仅验证管线完整；不代表真实 grounding/finish 语义。"
        )

    write_json(run_dir / "summary.json", summary)
    (run_dir / "report.html").write_text(
        render_html(summary, events), encoding="utf-8"
    )
    share_report: Path | None = None
    if share:
        share_report = _write_share_copy(run_dir, summary, events)
    status = {
        "state": "completed" if outcome.get("returncode") == 0 else "failed",
        "run_id": run_id,
        "verdict": summary["verdict"],
        "steps": summary.get("steps"),
        "dry_run": dry_run,
        "report_path": str(run_dir / "report.html"),
        "summary_path": str(run_dir / "summary.json"),
        "evidence_path": str(local_evidence),
    }
    if share_report is not None:
        status["share_report_path"] = str(share_report)
    write_json(run_dir / "status.json", status)
    _lock_down_artifacts(run_dir)
    return summary


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    load_project_env()
    if getattr(args, "status", None):  # backward-compat: --status flag
        print(json.dumps(read_status(Path(args.status)), ensure_ascii=False, indent=2))
        return 0
    if not args.target:
        print("error: a target is required (or use --status / the status subcommand)", file=sys.stderr)
        return 2

    run_id = build_run_id(args.target)
    run_dir = Path(args.output_dir).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    preflight = collect_preflight(args)
    preflight["reset_app"] = reset_app_on_device(args)
    write_json(run_dir / "preflight.json", preflight)

    # Local-first: the command line is recorded verbatim (the run dir is
    # owner-private + 600). ``--share`` deep-redacts the whole summary later.
    command = ["run_diagnosis.py", "dry-run" if args.dry_run else "run", args.target]
    started = time.perf_counter()
    try:
        agent, result = run_dry(args, run_dir) if args.dry_run else run_agent(args, run_dir)
    except Exception as exc:  # noqa: BLE001 - surface bring-up failures cleanly
        duration = time.perf_counter() - started
        error = {
            "state": "error",
            "run_id": run_id,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "duration_sec": round(duration, 2),
        }
        write_json(run_dir / "status.json", error)
        _chmod_600(run_dir / "status.json")
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    duration = time.perf_counter() - started

    outcome = _outcome_from_run(agent.session, result)
    summary = _finalize(
        run_dir=run_dir,
        run_id=run_id,
        target=args.target,
        command=command,
        evidence_path=getattr(agent, "evidence_path", None),
        outcome=outcome,
        duration_sec=duration,
        dry_run=args.dry_run,
        share=bool(getattr(args, "share", False)),
    )
    if not args.quiet:
        payload = {
            "run_id": run_id,
            "verdict": summary["verdict"],
            "steps": summary.get("steps"),
            "run_dir": str(run_dir),
            "report_path": str(run_dir / "report.html"),
            "summary_path": str(run_dir / "summary.json"),
            "top_recommendations": [
                r.get("title") for r in summary.get("recommendations", [])[:3]
            ],
        }
        if getattr(args, "share", False):
            payload["share_report_path"] = str(run_dir / "report-share.html")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return int(outcome.get("returncode") or 0)


def _resolve_evidence_path(raw: str) -> Path:
    """Accept a run directory or an explicit ``*.evidence.jsonl`` / ``evidence.jsonl``."""

    p = Path(raw)
    if p.is_dir():
        canonical = p / "evidence.jsonl"
        if canonical.exists():
            return canonical
        matches = sorted(p.glob("*.evidence.jsonl"))
        if matches:
            return matches[0]
        return canonical
    return p


def cmd_analyze(args: argparse.Namespace) -> int:
    """Re-derive summary.json from an existing evidence stream (no re-run)."""

    evidence_path = _resolve_evidence_path(args.evidence)
    events = read_evidence(evidence_path)
    if not events:
        print(f"error: no evidence events read from {evidence_path}", file=sys.stderr)
        return 1
    view = EvidenceView.from_events(events)
    run_start = view.run_start or {}
    outcome = _outcome_from_evidence(view)
    run_id = run_start.get("run_id") or evidence_path.stem
    summary = build_summary(
        outcome,
        view,
        run_id=run_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        target=run_start.get("task_goal_base", ""),
        evidence_stream=str(evidence_path),
    )
    out = Path(args.output) if args.output else evidence_path.with_name("summary.json")
    write_json(out, summary)
    if args.report:
        report_out = Path(args.report)
        report_out.write_text(render_html(summary, events), encoding="utf-8")
    print(json.dumps({"summary_path": str(out), "verdict": summary["verdict"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Re-render report.html from an existing summary.json (no re-run).

    ``--share`` re-derives the redacted, screenshot-free share copy instead of
    (or alongside) the full-fidelity report, mirroring the ``run --share`` path.
    """

    summary_arg = Path(args.summary)
    summary_path = summary_arg / "summary.json" if summary_arg.is_dir() else summary_arg
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = []
    evidence_ref = args.evidence or summary.get("evidence_stream")
    if evidence_ref:
        events = read_evidence(_resolve_evidence_path(evidence_ref))
    if getattr(args, "share", False):
        share_summary = _redact_deep(summary)
        share_events = _redact_deep(events)
        share_summary.setdefault("notes", []).append(
            "share 副本：全文脱敏、无截图引用；本机全保真产物见 report.html。"
        )
        out = Path(args.output) if args.output else summary_path.with_name("report-share.html")
        out.write_text(render_html(share_summary, share_events), encoding="utf-8")
        _chmod_600(out)
        print(json.dumps({"share_report_path": str(out)}, ensure_ascii=False, indent=2))
        return 0
    out = Path(args.output) if args.output else summary_path.with_name("report.html")
    out.write_text(render_html(summary, events), encoding="utf-8")
    print(json.dumps({"report_path": str(out)}, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(read_status(Path(args.path)), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", help="natural-language phone-agent test target")
    parser.add_argument("--status", help="read a run dir / status.json and print runtime status")
    parser.add_argument("--dry-run", action="store_true", help="offline pipeline check (no device/network)")
    parser.add_argument("--device-id", default=None, help="ADB device serial")
    parser.add_argument("--max-steps", type=int, default=None, help="max model calls (loop budget)")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--model", default=None, help="model id")
    parser.add_argument("--apikey", default=None, help="API key")
    parser.add_argument("--model-timeout", type=float, default=None)
    parser.add_argument("--model-max-retries", type=int, default=None)
    parser.add_argument("--grounding-provider", default=None, help="grounding provider name")
    parser.add_argument("--accessibility-timeout", type=float, default=None)
    parser.add_argument("--accessibility-max-marks", type=int, default=None)
    parser.add_argument("--locateanything-model", default=None)
    parser.add_argument("--locateanything-max-size", type=int, default=None)
    parser.add_argument("--lang", choices=["cn", "en"], default=None)
    parser.add_argument("--evidence-dir", default=None, help="(reserved) evidence output dir; defaults to the run dir")
    parser.add_argument("--no-taskdoc", action="store_true", help="disable the TaskDoc board (maps PHONE_AGENT_TASKDOC=false)")
    parser.add_argument("--nudge-steps", type=int, default=None, help="stagnation nudge threshold")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--reset-app", default=None, help="package to pm-clear before the run")
    parser.add_argument(
        "--share",
        action="store_true",
        help="also emit a redacted, screenshot-free report-share.html for sharing "
        "(the default report.html stays local-first full-fidelity)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_diagnosis.py",
        description="TaskWizard thin-loop (v2) live diagnosis: run + analyze + report",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="run a live diagnosis (default)")
    _add_run_arguments(run_p)

    analyze_p = sub.add_parser("analyze", help="re-derive summary.json from an evidence stream")
    analyze_p.add_argument("evidence", help="path to <run_id>.evidence.jsonl")
    analyze_p.add_argument("--output", default=None, help="summary.json output path")
    analyze_p.add_argument("--report", default=None, help="also render report.html to this path")

    report_p = sub.add_parser("report", help="re-render report.html from a summary.json")
    report_p.add_argument("summary", help="path to summary.json")
    report_p.add_argument("--evidence", default=None, help="evidence.jsonl for the timeline/raw tabs")
    report_p.add_argument("--output", default=None, help="report.html output path")
    report_p.add_argument(
        "--share",
        action="store_true",
        help="render the redacted, screenshot-free share copy instead",
    )

    status_p = sub.add_parser("status", help="print a run directory's status.json")
    status_p.add_argument("path", help="run directory or status.json path")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default (no subcommand, or a bare target) -> the `run` command. Only the
    # four known subcommands are dispatched to their parsers.
    if not argv or argv[0] not in {"run", "analyze", "report", "status"}:
        run_p = argparse.ArgumentParser(prog="run_diagnosis.py")
        _add_run_arguments(run_p)
        return cmd_run(run_p.parse_args(argv))

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "status":
        return cmd_status(args)
    parser.error("unknown command")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    raise SystemExit(main())
