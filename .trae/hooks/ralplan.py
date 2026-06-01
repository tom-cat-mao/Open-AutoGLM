#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MAX_REINFORCEMENTS = 30
BREAKER_TTL_MINUTES = 45
SUBAGENT_FRESH_SECONDS = 5
CONFIRMATION_TTL_MINUTES = 2
TERMINAL_PHASES = {
    "complete",
    "failed",
    "cancelled",
    "aborted",
    "terminated",
    "handoff",
    "pending approval",
    "pending-approval",
    "pending_approval",
    "awaiting approval",
    "awaiting-approval",
    "awaiting_approval",
    "approval-required",
    "approval_required",
    "rejected",
}
EXECUTION_KEYWORDS = {"ralph", "autopilot", "team", "ultrawork", "ultrapilot", "fix", "implement", "execute"}
COMMAND_PREFIXES = ("/ralplan",)
MODE_NAME = "ralplan"
MODE_EXCLUSIVE_PEERS = {"autopilot", "team", "ralph"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def load_json_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def workspace_from_event(event: dict[str, Any]) -> Path:
    return Path(str(event.get("cwd") or os.getcwd())).resolve()


def state_dir(workspace: Path) -> Path:
    return workspace / ".trae" / "ralplan"


def state_path(workspace: Path) -> Path:
    return state_dir(workspace) / "state.json"


def breaker_path(workspace: Path) -> Path:
    return state_dir(workspace) / "stop-breaker.json"


def subagent_path(workspace: Path) -> Path:
    return state_dir(workspace) / "subagent-tracking.json"


def mode_registry_path(workspace: Path) -> Path:
    return workspace / ".trae" / "modes" / "state.json"


def graph_path(workspace: Path) -> Path:
    return workspace / ".trae" / "rules" / "graph.mdc"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_state(workspace: Path) -> dict[str, Any] | None:
    return read_json(state_path(workspace))


def write_state(workspace: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(state_path(workspace), state)


def clear_runtime(workspace: Path) -> None:
    for path in (state_path(workspace), breaker_path(workspace), subagent_path(workspace)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    release_mode(workspace)


def active_mode_conflict(workspace: Path, session_id: Any) -> str | None:
    registry = read_json(mode_registry_path(workspace)) or {}
    active = registry.get("active")
    if not isinstance(active, dict):
        return None
    mode = str(active.get("mode") or "")
    if not mode or mode == MODE_NAME or mode not in MODE_EXCLUSIVE_PEERS:
        return None
    return mode


def acquire_mode(workspace: Path, event: dict[str, Any]) -> None:
    write_json(
        mode_registry_path(workspace),
        {
            "active": {
                "mode": MODE_NAME,
                "session_id": event.get("session_id"),
                "started_at": now_iso(),
                "updated_at": now_iso(),
            }
        },
    )


def release_mode(workspace: Path) -> None:
    registry = read_json(mode_registry_path(workspace)) or {}
    active = registry.get("active")
    if isinstance(active, dict) and active.get("mode") == MODE_NAME:
        registry["active"] = None
        registry["updated_at"] = now_iso()
        write_json(mode_registry_path(workspace), registry)


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_invocation(prompt: str) -> tuple[str, set[str], str]:
    text = prompt.strip()
    for prefix in COMMAND_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            if not rest:
                return "start", set(), ""
            head, _, tail = rest.partition(" ")
            if head in {"status", "cancel", "resume", "reset", "approve", "reject"}:
                return head, set(), tail.strip()
            flags, task = parse_flags(rest)
            return "start", flags, task
    flags, task = parse_flags(text)
    return "", flags, task


def parse_flags(text: str) -> tuple[set[str], str]:
    parts = text.split()
    flags: set[str] = set()
    body: list[str] = []
    skip_next = False
    for index, part in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if part in {"--architect", "--critic"}:
            flags.add(part)
            if index + 1 < len(parts):
                flags.add(f"{part}={parts[index + 1]}")
                skip_next = True
            continue
        if part.startswith("--"):
            flags.add(part)
        else:
            body.append(part)
    return flags, " ".join(body).strip()


def has_explicit_ralplan_invocation(prompt: str) -> bool:
    text = prompt.strip().lower()
    if text.startswith(COMMAND_PREFIXES):
        return True
    if re.search(r"共识规划|先规划再执行|规划模式", text):
        return True
    if "ralplan" not in text:
        return False
    return bool(
        re.search(r"(^|\s)(run|use|start|invoke|执行|启动|使用|进入)\s+ralplan\b", text)
        or re.search(r"\bralplan\s+(mode|模式|规划|plan|一下|这个)", text)
        or text.startswith("ralplan")
    )


def has_execution_keyword(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in EXECUTION_KEYWORDS)


def effective_word_count(prompt: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_./#:-]+|[\u4e00-\u9fff]", prompt))


def has_execution_anchor(prompt: str) -> bool:
    text = prompt.strip()
    if text.startswith("force:") or text.startswith("!"):
        return True
    insensitive_patterns = [
        r"`{3}[\s\S]*?`{3}",
        r"(?:^|\s)[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|md|yaml|yml|json)(?::\d+)?",
        r"#[0-9]+",
        r"\b[A-Za-z][A-Za-z0-9]*(?:Case|Config|Manager|Service|Node|State|Tool|Graph)\b",
        r"\b[a-z]+_[a-z0-9_]+\b",
        r"\b(pytest|npm test|pnpm test|yarn test|go test|cargo test)\b",
        r"(^|\n)\s*\d+[.)]\s+",
        r"验收标准|acceptance criteria|expected|actual|traceback|stack trace|error:",
    ]
    case_sensitive_patterns = [r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"]
    return any(re.search(pattern, text, re.I | re.M) for pattern in insensitive_patterns) or any(
        re.search(pattern, text, re.M) for pattern in case_sensitive_patterns
    )


def is_underspecified_for_execution(prompt: str) -> bool:
    stripped = prompt.strip()
    if not stripped or stripped.startswith("/"):
        return False
    return has_execution_keyword(stripped) and effective_word_count(stripped) <= 15 and not has_execution_anchor(stripped)


def graph_status(workspace: Path) -> dict[str, str]:
    path = graph_path(workspace)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.search(r"# RALPLAN Status\s*\n\s*```yaml\s*\n(?P<body>.*?)\n```", text, re.S)
    if not match:
        return {}
    status: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        status[key.strip()] = value.strip().strip("'\"")
    return status


def graph_is_critic_approved(workspace: Path) -> bool:
    status = graph_status(workspace)
    return status.get("design_status") == "critic_approved" and status.get("last_critic_verdict") == "APPROVE"


def is_deliberate(flags: set[str], task: str) -> bool:
    if "--deliberate" in flags:
        return True
    return bool(re.search(r"auth|security|migration|destructive|production|incident|compliance|pii|privacy|public api|破坏|迁移|安全|隐私|合规|生产", task, re.I))


def init_state(event: dict[str, Any], task: str, flags: set[str], *, source: str) -> dict[str, Any]:
    workspace = workspace_from_event(event)
    state = {
        "version": 1,
        "active": True,
        "session_id": event.get("session_id"),
        "current_phase": "ralplan",
        "phase": "ralplan",
        "status": "active",
        "task": task,
        "flags": sorted(flags),
        "source": source,
        "deliberate": is_deliberate(flags, task),
        "awaiting_confirmation": False,
        "started_at": now_iso(),
        "updated_at": now_iso(),
    }
    write_state(workspace, state)
    write_json(breaker_path(workspace), {"count": 0, "started_at": now_iso(), "updated_at": now_iso()})
    acquire_mode(workspace, event)
    return state


def summarize_state(state: dict[str, Any] | None) -> str:
    if not state:
        return "RALPLAN is idle."
    return (
        f"RALPLAN active={state.get('active')}, phase={state.get('current_phase') or state.get('phase')}, "
        f"status={state.get('status')}, task={state.get('task') or 'current roadmap'}"
    )


def set_terminal(workspace: Path, state: dict[str, Any], phase: str, reason: str | None = None) -> dict[str, Any]:
    state["active"] = False
    state["current_phase"] = phase
    state["phase"] = phase
    state["status"] = phase
    state["completed_at"] = now_iso()
    if reason:
        state["deactivated_reason"] = reason
    write_state(workspace, state)
    release_mode(workspace)
    return state


def approval_prompt(state: dict[str, Any]) -> str:
    task = state.get("task") or "当前 graph.mdc roadmap"
    return f"""<ralplan-pending-approval>
[RALPLAN - PENDING APPROVAL]

任务：{task}

Critic 已 APPROVE 且 Final Check 已通过。当前仅完成规划，不执行代码变更。

可选下一步：
- `/ralplan approve`：批准计划并交接执行。
- `/autopilot --use-current-plan`：用当前 approved roadmap 继续执行流水线。
- `/ralplan reject`：拒绝计划。
- `/ralplan resume`：继续修订计划。
</ralplan-pending-approval>"""


def continuation_prompt(state: dict[str, Any], reinforcement: int) -> str:
    task = state.get("task") or "当前规划任务"
    deliberate = "是" if state.get("deliberate") else "否"
    return f"""<ralplan-continuation>
[RALPLAN - CONSENSUS PLANNING | REINFORCEMENT {reinforcement}/{MAX_REINFORCEMENTS}]

任务：{task}
Deliberate 模式：{deliberate}

继续 RALPLAN 共识规划，只做规划，不做实现。

必须执行：
1. 读取 `.trae/rules/ralplan.mdc`、`.trae/rules/graph.mdc`。
2. 调用 planner subagent 整体覆写 `.trae/rules/graph.mdc`。
3. 串行调用 architect subagent 只读审查。
4. 串行调用 critic subagent 输出 APPROVE / ITERATE / REJECT。
5. ITERATE 最多 5 轮；REJECT 则说明澄清问题。
6. APPROVE 后执行 Final Check，并单独输出：`RALPLAN_SIGNAL: PENDING_APPROVAL`。

禁止：修改业务代码、调用执行型 skill、commit、push、开 PR。
</ralplan-continuation>"""


def gate_prompt(prompt: str, state: dict[str, Any]) -> str:
    return f"""<ralplan-pre-execution-gate>
[RALPLAN GATE]

原始请求过于模糊，已重定向到 RALPLAN 共识规划：

```text
{prompt.strip()}
```

{continuation_prompt(state, 1)}
</ralplan-pre-execution-gate>"""


def breaker_count(workspace: Path) -> int:
    data = read_json(breaker_path(workspace)) or {}
    started = parse_dt(data.get("started_at"))
    if not started or now() - started > timedelta(minutes=BREAKER_TTL_MINUTES):
        data = {"count": 0, "started_at": now_iso()}
    data["count"] = int(data.get("count") or 0) + 1
    data["updated_at"] = now_iso()
    write_json(breaker_path(workspace), data)
    return int(data["count"])


def subagent_active(workspace: Path) -> bool:
    data = read_json(subagent_path(workspace)) or {}
    active = data.get("active")
    if not isinstance(active, dict):
        return False
    threshold = now() - timedelta(seconds=SUBAGENT_FRESH_SECONDS)
    for item in active.values():
        if not isinstance(item, dict):
            continue
        ts = parse_dt(item.get("updated_at") or item.get("started_at"))
        if ts and ts >= threshold:
            return True
    return False


def track_subagent(event: dict[str, Any], *, active: bool) -> None:
    workspace = workspace_from_event(event)
    data = read_json(subagent_path(workspace)) or {"active": {}}
    active_map = data.setdefault("active", {})
    agent_id = str(event.get("agent_id") or event.get("agent_type") or "unknown")
    if active:
        active_map[agent_id] = {"agent_type": event.get("agent_type"), "started_at": now_iso(), "updated_at": now_iso()}
    else:
        active_map.pop(agent_id, None)
    data["updated_at"] = now_iso()
    write_json(subagent_path(workspace), data)


def phase_of(state: dict[str, Any]) -> str:
    return str(state.get("current_phase") or state.get("phase") or state.get("status") or "")


def awaiting_confirmation_expired(state: dict[str, Any]) -> bool:
    if not state.get("awaiting_confirmation"):
        return False
    ts = parse_dt(state.get("awaiting_confirmation_set_at"))
    return bool(ts and now() - ts > timedelta(minutes=CONFIRMATION_TTL_MINUTES))


def handle_user_prompt_submit(event: dict[str, Any]) -> None:
    prompt = str(event.get("prompt") or event.get("user_prompt_submit", {}).get("prompt") or "")
    workspace = workspace_from_event(event)
    command, flags, task = parse_invocation(prompt)
    if command == "status":
        print(json.dumps({"decision": "block", "reason": summarize_state(read_state(workspace))}, ensure_ascii=False))
        return
    if command in {"cancel", "reject"}:
        state = read_state(workspace) or {"task": task}
        set_terminal(workspace, state, "cancelled" if command == "cancel" else "rejected")
        print(json.dumps({"decision": "block", "reason": f"RALPLAN {command}ed."}, ensure_ascii=False))
        return
    if command == "reset":
        clear_runtime(workspace)
        print(json.dumps({"decision": "block", "reason": "RALPLAN runtime state reset; graph.mdc preserved."}, ensure_ascii=False))
        return
    if command == "resume":
        conflict = active_mode_conflict(workspace, event.get("session_id"))
        if conflict:
            print(json.dumps({"decision": "block", "reason": f"Cannot resume RALPLAN while {conflict} mode is active."}, ensure_ascii=False))
            return
        state = read_state(workspace) or init_state(event, task, flags, source="resume")
        state["active"] = True
        state["status"] = "active"
        state["current_phase"] = "ralplan"
        state["phase"] = "ralplan"
        state["session_id"] = event.get("session_id")
        state["awaiting_confirmation"] = False
        write_state(workspace, state)
        acquire_mode(workspace, event)
        print(json.dumps({"decision": "block", "reason": continuation_prompt(state, 1)}, ensure_ascii=False))
        return
    if command == "approve":
        state = read_state(workspace) or {}
        if graph_is_critic_approved(workspace):
            set_terminal(workspace, state, "handoff", "approved_by_user")
            print(json.dumps({"decision": "block", "reason": "RALPLAN approved. Continue with `/autopilot --use-current-plan` or the requested execution path."}, ensure_ascii=False))
        else:
            print(json.dumps({"decision": "block", "reason": "Cannot approve: graph.mdc is not critic_approved with APPROVE verdict."}, ensure_ascii=False))
        return
    if command == "start" or has_explicit_ralplan_invocation(prompt):
        conflict = active_mode_conflict(workspace, event.get("session_id"))
        if conflict:
            print(json.dumps({"decision": "block", "reason": f"Cannot start RALPLAN while {conflict} mode is active."}, ensure_ascii=False))
            return
        if command != "start":
            flags, task = parse_flags(prompt)
        state = init_state(event, task, flags, source="explicit")
        if prompt.strip().startswith(COMMAND_PREFIXES):
            print(json.dumps({"systemMessage": "RALPLAN initialized; prompt command will execute."}, ensure_ascii=False))
            return
        print(json.dumps({"decision": "block", "reason": continuation_prompt(state, 1)}, ensure_ascii=False))
        return
    if is_underspecified_for_execution(prompt):
        conflict = active_mode_conflict(workspace, event.get("session_id"))
        if conflict:
            return
        state = init_state(event, prompt.strip(), {"--gate"}, source="pre_execution_gate")
        print(json.dumps({"decision": "block", "reason": gate_prompt(prompt, state)}, ensure_ascii=False))


def handle_stop(event: dict[str, Any]) -> None:
    workspace = workspace_from_event(event)
    state = read_state(workspace)
    if not state or not state.get("active"):
        return
    session_id = state.get("session_id")
    if session_id and event.get("session_id") and session_id != event.get("session_id"):
        return
    phase = phase_of(state)
    if phase in TERMINAL_PHASES:
        return
    if awaiting_confirmation_expired(state):
        set_terminal(workspace, state, "cancelled", "confirmation_ttl_expired")
        return
    if subagent_active(workspace):
        return
    if graph_is_critic_approved(workspace):
        state["current_phase"] = "pending_approval"
        state["phase"] = "pending_approval"
        state["status"] = "pending_approval"
        state["awaiting_confirmation"] = True
        state["awaiting_confirmation_set_at"] = now_iso()
        write_state(workspace, state)
        print(json.dumps({"systemMessage": approval_prompt(state)}, ensure_ascii=False))


def main() -> int:
    event = load_json_stdin()
    name = str(event.get("hook_event_name") or "").lower()
    if name in {"userpromptsubmit", "user_prompt_submit"}:
        handle_user_prompt_submit(event)
    elif name == "stop":
        handle_stop(event)
    elif name in {"subagentstart", "subagent_start"}:
        track_subagent(event, active=True)
    elif name in {"subagentstop", "subagent_stop"}:
        track_subagent(event, active=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
