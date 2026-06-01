#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple


MAX_STAGE_ITERATIONS = 10
SUBAGENT_FRESH_SECONDS = 5
RESUME_TTL_HOURS = 24
MODE_NAME = "autopilot"
MODE_EXCLUSIVE_PEERS = {"ralplan", "team", "ralph"}
COMMAND_PREFIXES = ("/autopilot", "/auto-pilot")
RALPLAN_AGENT_CHAIN = ("planner", "architect", "critic")
AUTOPILOT_STAGE_AGENTS = (
    "executor",
    "debugger",
    "test-engineer",
    "designer",
    "code-reviewer",
    "security-reviewer",
)


class PipelineAdapter(NamedTuple):
    id: str
    name: str
    completion_signal: str


ADAPTERS: tuple[PipelineAdapter, ...] = (
    PipelineAdapter("ralplan", "RALPLAN", "PIPELINE_RALPLAN_COMPLETE"),
    PipelineAdapter("execution", "Execution", "PIPELINE_EXECUTION_COMPLETE"),
    PipelineAdapter("ralph", "RALPH / Verification", "PIPELINE_RALPH_COMPLETE"),
    PipelineAdapter("qa", "QA", "PIPELINE_QA_COMPLETE"),
)
ADAPTER_BY_ID = {adapter.id: adapter for adapter in ADAPTERS}
STAGE_ORDER = [adapter.id for adapter in ADAPTERS]
SIGNALS = {adapter.id: adapter.completion_signal for adapter in ADAPTERS}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    cwd = event.get("cwd") or os.getcwd()
    return Path(str(cwd)).resolve()


def state_path(workspace: Path) -> Path:
    return workspace / ".trae" / "autopilot" / "state.json"


def ralplan_state_path(workspace: Path) -> Path:
    return workspace / ".trae" / "ralplan" / "state.json"


def mode_registry_path(workspace: Path) -> Path:
    return workspace / ".trae" / "modes" / "state.json"


def subagent_tracking_path(workspace: Path) -> Path:
    return workspace / ".trae" / "autopilot" / "subagent-tracking.json"


def transition_log_path(workspace: Path) -> Path:
    return workspace / ".trae" / "autopilot" / "transitions.jsonl"


def graph_path(workspace: Path) -> Path:
    return workspace / ".trae" / "rules" / "graph.mdc"


def read_state(workspace: Path) -> dict[str, Any] | None:
    path = state_path(workspace)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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


def write_state(workspace: Path, state: dict[str, Any]) -> None:
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def active_mode_conflict(workspace: Path, session_id: Any) -> str | None:
    registry = read_json(mode_registry_path(workspace)) or {}
    active = registry.get("active")
    if not isinstance(active, dict):
        return None
    mode = str(active.get("mode") or "")
    if not mode or mode == MODE_NAME or mode not in MODE_EXCLUSIVE_PEERS:
        return None
    return mode


def phase_of(state: dict[str, Any] | None) -> str:
    if not state:
        return ""
    return str(state.get("current_phase") or state.get("phase") or state.get("status") or "")


def can_handoff_from_ralplan(workspace: Path, flags: dict[str, str | bool], task: str) -> bool:
    if not (truthy(flags.get("use-current-plan")) or not task):
        return False
    state = read_json(ralplan_state_path(workspace)) or {}
    return phase_of(state) == "pending_approval" and graph_is_approved(workspace)


def consume_ralplan_handoff(workspace: Path) -> None:
    state = read_json(ralplan_state_path(workspace)) or {}
    if phase_of(state) != "pending_approval":
        return
    state["active"] = False
    state["current_phase"] = "handoff"
    state["phase"] = "handoff"
    state["status"] = "handoff"
    state["deactivated_reason"] = "autopilot_use_current_plan"
    state["completed_at"] = now_iso()
    state["updated_at"] = now_iso()
    write_json(ralplan_state_path(workspace), state)


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


def log_transition(workspace: Path, event: str, stage: str | None, state: dict[str, Any]) -> None:
    path = transition_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": now_iso(),
        "event": event,
        "stage": stage,
        "session_id": state.get("session_id"),
        "task": state.get("task") or "current plan",
        "status": state.get("status"),
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


def parse_args(prompt: str) -> tuple[dict[str, str | bool], str]:
    text = prompt.strip()
    for prefix in COMMAND_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    flags: dict[str, str | bool] = {}
    parts: list[str] = []
    for part in text.split():
        if not part.startswith("--"):
            parts.append(part)
            continue
        key_value = part[2:]
        if "=" in key_value:
            key, value = key_value.split("=", 1)
            flags[key] = value
        else:
            flags[key_value] = True
    return flags, " ".join(parts).strip()


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def falsey(value: Any) -> bool:
    return str(value).strip().lower() in {"0", "false", "no", "off", "none"}


def int_flag(flags: dict[str, str | bool], key: str, default: int) -> int:
    value = flags.get(key)
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def build_pipeline_config(flags: dict[str, str | bool]) -> dict[str, Any]:
    planning = str(flags.get("planning") or "ralplan").lower()
    execution = str(flags.get("execution") or "solo").lower()
    verification_flag = flags.get("verification")
    qa_flag = flags.get("qa")

    if truthy(flags.get("direct")):
        planning = "direct"
    if truthy(flags.get("no-verification")) or falsey(verification_flag):
        verification: dict[str, Any] | bool = False
    else:
        verification = {
            "engine": "ralph",
            "max_iterations": int_flag(flags, "max-verification-iterations", 100),
        }
    qa = not truthy(flags.get("no-qa")) and not falsey(qa_flag)
    if planning not in {"ralplan", "direct", "false"}:
        planning = "ralplan"
    if execution not in {"solo", "team"}:
        execution = "solo"
    return {
        "planning": False if planning == "false" else planning,
        "execution": execution,
        "verification": verification,
        "qa": qa,
        "max_stage_iterations": int_flag(flags, "max-stage-iterations", MAX_STAGE_ITERATIONS),
    }


def parse_command(prompt: str) -> tuple[str, str]:
    text = prompt.strip()
    for prefix in COMMAND_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            if not rest:
                return "start", ""
            head, _, tail = rest.partition(" ")
            if head in {"cancel", "status", "resume", "reset"}:
                return head, tail.strip()
            return "start", rest
    return "", text


def effective_word_count(prompt: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_./#:-]+|[\u4e00-\u9fff]", prompt))


def has_task_anchor(prompt: str) -> bool:
    text = prompt.strip()
    patterns = [
        r"`{3}[\s\S]*?`{3}",
        r"(?:^|\s)[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|md|yaml|yml|json)(?::\d+)?",
        r"#[0-9]+",
        r"\b(pytest|npm test|pnpm test|yarn test|go test|cargo test)\b",
        r"(^|\n)\s*\d+[.)]\s+",
        r"验收标准|acceptance criteria|expected|actual|traceback|stack trace|error:",
    ]
    return any(re.search(pattern, text, re.I | re.M) for pattern in patterns)


def has_explicit_autopilot_invocation(prompt: str) -> bool:
    text = prompt.strip().lower()
    if text.startswith(COMMAND_PREFIXES):
        return True
    if re.search(r"自动持续执行|端到端流水线|自动执行流水线", prompt):
        return True
    if "autopilot" not in text and "auto-pilot" not in text:
        return False
    return bool(re.search(r"(^|\s)(run|use|start|invoke|执行|启动|使用|进入|用)\s+auto-?pilot\b", text) or text.startswith(("autopilot", "auto-pilot")))


def task_size_allows_autopilot(prompt: str) -> bool:
    stripped = prompt.strip()
    if stripped.startswith(COMMAND_PREFIXES):
        return True
    return effective_word_count(stripped) >= 8 or has_task_anchor(stripped)


def should_start(prompt: str) -> bool:
    stripped = prompt.lstrip()
    return has_explicit_autopilot_invocation(stripped) and task_size_allows_autopilot(stripped)


def state_is_resumeable(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    ts = parse_dt(state.get("updated_at") or state.get("created_at"))
    return bool(ts and now() - ts <= timedelta(hours=RESUME_TTL_HOURS))


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
        status[key.strip()] = value.strip().strip('"\'')
    return status


def graph_is_approved(workspace: Path) -> bool:
    status = graph_status(workspace)
    return (
        status.get("design_status") == "critic_approved"
        and status.get("last_critic_verdict") == "APPROVE"
        and status.get("approved_for_execution") == "true"
    )


def adapter_should_skip(stage_id: str, config: dict[str, Any]) -> bool:
    if stage_id == "ralplan":
        return config.get("planning") is False
    if stage_id == "ralph":
        return config.get("verification") is False
    if stage_id == "qa":
        return not bool(config.get("qa", True))
    return False


def default_pipeline_config(*, no_verification: bool = False, no_qa: bool = False) -> dict[str, Any]:
    flags: dict[str, str | bool] = {}
    if no_verification:
        flags["no-verification"] = True
    if no_qa:
        flags["no-qa"] = True
    return build_pipeline_config(flags)


def build_stages(
    first_stage: str,
    *,
    no_verification: bool = False,
    no_qa: bool = False,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    pipeline_config = config or default_pipeline_config(no_verification=no_verification, no_qa=no_qa)
    stages: list[dict[str, Any]] = []
    activated = False
    for stage in STAGE_ORDER:
        skipped = adapter_should_skip(stage, pipeline_config)
        if skipped:
            status = "skipped"
        elif stage == first_stage and not activated:
            status = "active"
            activated = True
        elif not activated and STAGE_ORDER.index(stage) < STAGE_ORDER.index(first_stage):
            status = "skipped"
        else:
            status = "pending"
        item: dict[str, Any] = {"id": stage, "status": status, "iterations": 0}
        if status == "active":
            item["started_at"] = now_iso()
        stages.append(item)
    return stages


def first_stage_for(workspace: Path, flags: dict[str, str | bool], config: dict[str, Any], task: str) -> str:
    use_current = truthy(flags.get("use-current-plan")) or not task
    planning = config.get("planning")
    if planning in {False, "direct"}:
        return "execution"
    if use_current and graph_is_approved(workspace):
        return "execution"
    return "ralplan"


def current_stage(state: dict[str, Any]) -> dict[str, Any] | None:
    for stage in state.get("stages", []):
        if isinstance(stage, dict) and stage.get("status") == "active":
            return stage
    return None


def next_pending_stage(state: dict[str, Any]) -> dict[str, Any] | None:
    for stage in state.get("stages", []):
        if isinstance(stage, dict) and stage.get("status") == "pending":
            return stage
    return None


def init_state(event: dict[str, Any]) -> dict[str, Any] | None:
    prompt = str(event.get("prompt") or event.get("user_prompt_submit", {}).get("prompt") or "")
    if not should_start(prompt):
        return None
    workspace = workspace_from_event(event)
    flags, task = parse_args(prompt)
    conflict = active_mode_conflict(workspace, event.get("session_id"))
    if conflict == "ralplan" and can_handoff_from_ralplan(workspace, flags, task):
        consume_ralplan_handoff(workspace)
        conflict = None
    if conflict:
        return {"status": "blocked", "blocked_reason": f"Cannot start Autopilot while {conflict} mode is active."}
    pipeline_config = build_pipeline_config(flags)
    first_stage = first_stage_for(workspace, flags, pipeline_config, task)
    state = {
        "version": 2,
        "status": "active",
        "session_id": event.get("session_id"),
        "task": task,
        "flags": flags,
        "pipeline_config": pipeline_config,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "stages": build_stages(first_stage, config=pipeline_config),
    }
    write_state(workspace, state)
    acquire_mode(workspace, event)
    log_transition(workspace, "on_enter", first_stage, state)
    return state


def summarize_state(state: dict[str, Any] | None) -> str:
    if not state:
        return "Autopilot is idle."
    current = current_stage(state)
    stage_text = current.get("id") if current else "none"
    return (
        f"Autopilot status={state.get('status', 'unknown')}, "
        f"stage={stage_text}, task={state.get('task') or 'current plan'}, "
        f"{format_pipeline_hud(state)}"
    )


def format_pipeline_hud(state: dict[str, Any]) -> str:
    stages = state.get("stages") if isinstance(state.get("stages"), list) else []
    total = len([stage for stage in stages if isinstance(stage, dict) and stage.get("status") != "skipped"])
    active_index = 0
    parts: list[str] = []
    seen_runnable = 0
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id"))
        status = str(stage.get("status"))
        adapter = ADAPTER_BY_ID.get(stage_id)
        name = adapter.name if adapter else stage_id
        if status != "skipped":
            seen_runnable += 1
        if status == "complete":
            marker = "OK"
        elif status == "active":
            marker = ">>"
            active_index = seen_runnable
        elif status == "skipped":
            marker = "SKIP"
        else:
            marker = ".."
        suffix = f" (iter {stage.get('iterations', 0)})" if status == "active" else ""
        parts.append(f"[{marker}] {name}{suffix}")
    return f"Pipeline {active_index or total}/{total}: " + " | ".join(parts)


def track_subagent(event: dict[str, Any], active: bool) -> None:
    workspace = workspace_from_event(event)
    path = subagent_tracking_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "active": active,
        "updated_at": now_iso(),
        "session_id": event.get("session_id"),
        "agent": event.get("agent") or event.get("subagent") or event.get("name"),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def subagent_active(workspace: Path) -> bool:
    path = subagent_tracking_path(workspace)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(data.get("updated_at")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not data.get("active"):
        return False
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age <= SUBAGENT_FRESH_SECONDS


def collect_assistant_text(obj: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(obj, dict):
        role = obj.get("role") or obj.get("type") or obj.get("event")
        if role in {"assistant", "assistant_message"}:
            texts.append(json.dumps(obj, ensure_ascii=False))
        for value in obj.values():
            texts.extend(collect_assistant_text(value))
    elif isinstance(obj, list):
        for value in obj:
            texts.extend(collect_assistant_text(value))
    return texts


def transcript_has_signal(transcript_path: str | None, signal: str) -> bool:
    if not transcript_path:
        return False
    path = Path(transcript_path)
    if not path.exists():
        return False
    marker = f"AUTOPILOT_SIGNAL: {signal}"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if any(marker in text for text in collect_assistant_text(obj)):
            return True
    if os.environ.get("AUTOPILOT_ALLOW_RAW_SIGNAL_SCAN") == "1":
        return any(marker in line for line in lines)
    return False


def execution_prompt(task: str, state: dict[str, Any]) -> str:
    config = state.get("pipeline_config") if isinstance(state.get("pipeline_config"), dict) else {}
    mode = config.get("execution", "solo")
    if mode == "team":
        return f"""
## Stage: Execution / Team

任务：{task}

执行：
1. 读取 `.trae/rules/graph.mdc` 的 approved roadmap 与约束 Checklist。
2. 使用 TodoWrite 拆分任务，并按风险/文件边界分派项目级 subagent：
   - designer：复杂实现前做局部设计与边界确认，只读。
   - executor：执行已批准 roadmap 内的聚焦实现任务。
   - debugger：定位失败根因并给出最小修复路径。
   - test-engineer：补充/调整测试并运行验证。
3. 主 Agent 负责合并结果、编辑文件、解决冲突、保持整体一致性。
4. 不主动 commit，不清理用户已有改动。
5. 完成实现后输出完成信号；测试失败可留给 RALPH stage 继续修复。
"""
    return f"""
## Stage: Execution / Solo

任务：{task}

执行：
1. 读取 `.trae/rules/graph.mdc` 的 approved roadmap 与约束 Checklist。
2. 使用 TodoWrite 跟踪多步骤执行。
3. 必要时调用 designer / executor / debugger / test-engineer 辅助局部设计、实现、调试或测试，但主 Agent 负责最终编辑与合并。
4. 不主动 commit，不清理用户已有改动。
5. 完成实现后输出完成信号；测试失败可留给 RALPH stage 继续修复。
"""


def stage_prompt(stage_id: str, state: dict[str, Any]) -> str:
    task = state.get("task") or "当前 approved roadmap"
    signal = SIGNALS[stage_id]
    stage = current_stage(state) or {}
    max_iterations = state.get("pipeline_config", {}).get("max_stage_iterations", MAX_STAGE_ITERATIONS)
    common = (
        "<autopilot-pipeline-continuation>\n"
        f"{format_pipeline_hud(state)}\n\n"
        f"[AUTOPILOT PIPELINE - STAGE: {stage_id} | ITERATION {stage.get('iterations', 0)}/{max_iterations} | SIGNAL: {signal}]\n"
        "读取 `.trae/rules/autopilot.mdc` 获取协议；完成本 stage 后单独输出：\n"
        f"AUTOPILOT_SIGNAL: {signal}\n\n"
    )
    if stage_id == "ralplan":
        body = f"""
## Stage: RALPLAN

任务：{task}

执行：
1. 读取 `.trae/rules/ralplan.mdc` 与 `.trae/rules/graph.mdc`。
2. 串行调用 planner subagent 整体覆写 `.trae/rules/graph.mdc`。
3. 串行调用 architect subagent 只读审查。
4. 串行调用 critic subagent 输出 APPROVE / ITERATE / REJECT。
5. 若 ITERATE，最多 5 轮回到 planner 窄修。
6. APPROVE 后执行 Final Check，确认 `design_status=critic_approved`、`last_critic_verdict=APPROVE`、`approved_for_execution=true`。
7. Critic 未 APPROVE 前不得修改业务代码。
"""
    elif stage_id == "execution":
        body = execution_prompt(task, state)
    elif stage_id == "ralph":
        body = """
## Stage: RALPH / Verification

执行：
1. 并行调用只读审查类 subagent：code-reviewer（代码质量）、security-reviewer（安全边界）、architect（架构一致性）、critic（质量门/遗漏项）。
2. 必要时调用 debugger 定位失败根因，调用 test-engineer 设计/运行验证。
3. 主 Agent 汇总 findings，只修复高置信问题。
4. 运行相关测试；Python/pytest/pip 优先使用 `.venv/bin/*`。
5. 检查新增配置语法、hook 脚本语法、关键路径行为。
6. 修复发现的问题并重跑目标测试。
7. 仅清理本 stage 产生的临时产物，不清理用户已有改动。
"""
    else:
        body = """
## Stage: QA

执行：
1. 调用 test-engineer 辅助确认测试覆盖和验证命令，调用 code-reviewer / security-reviewer 做最终只读抽查。
2. 主 Agent 检查 git diff 范围，确认没有过度清理或无关改动。
3. 确认 `.trae/rules/graph.mdc` 与 Autopilot runtime state 边界未混淆。
4. 如本次完成 roadmap phase，按项目规则更新状态；否则不要伪造完成态。
5. 主 Agent 汇总变更、测试结果、剩余风险，并输出完成信号。
"""
    return common + body.strip() + "\n</autopilot-pipeline-continuation>"


def advance_state(state: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
    current = current_stage(state)
    if current is None:
        state["status"] = "completed"
        state["updated_at"] = now_iso()
        return state, None, None
    current["status"] = "complete"
    current["completed_at"] = now_iso()
    previous = str(current.get("id"))
    nxt = next_pending_stage(state)
    if nxt is None:
        state["status"] = "completed"
        state["completed_at"] = now_iso()
        state["updated_at"] = now_iso()
        return state, previous, None
    nxt["status"] = "active"
    nxt["started_at"] = now_iso()
    state["updated_at"] = now_iso()
    return state, previous, str(nxt.get("id"))


def handle_user_prompt_submit(event: dict[str, Any]) -> None:
    prompt = str(event.get("prompt") or event.get("user_prompt_submit", {}).get("prompt") or "")
    command, _ = parse_command(prompt)
    workspace = workspace_from_event(event)
    if command == "status":
        print(json.dumps({"decision": "block", "reason": summarize_state(read_state(workspace))}, ensure_ascii=False))
        return
    if command in {"cancel", "reset"}:
        state = read_state(workspace) or {}
        state.update({"status": "cancelled", "updated_at": now_iso(), "cancelled_at": now_iso()})
        write_state(workspace, state)
        release_mode(workspace)
        print(json.dumps({"decision": "block", "reason": "Autopilot cancelled."}, ensure_ascii=False))
        return
    if command == "resume":
        state = read_state(workspace)
        conflict = active_mode_conflict(workspace, event.get("session_id"))
        if conflict:
            print(json.dumps({"decision": "block", "reason": f"Cannot resume Autopilot while {conflict} mode is active."}, ensure_ascii=False))
            return
        if state and state_is_resumeable(state):
            state["status"] = "active"
            state["session_id"] = event.get("session_id")
            state["updated_at"] = now_iso()
            write_state(workspace, state)
            acquire_mode(workspace, event)
        elif state:
            state["status"] = "expired"
            state["updated_at"] = now_iso()
            write_state(workspace, state)
            release_mode(workspace)
        print(json.dumps({"decision": "block", "reason": summarize_state(state)}, ensure_ascii=False))
        return
    state = init_state(event)
    if state is None:
        return
    if state.get("status") == "blocked":
        print(json.dumps({"decision": "block", "reason": state.get("blocked_reason")}, ensure_ascii=False))
        return
    stage = current_stage(state)
    stage_id = stage.get("id") if stage else "unknown"
    print(json.dumps({"systemMessage": f"Autopilot initialized: active stage={stage_id}"}, ensure_ascii=False))


def handle_stop(event: dict[str, Any]) -> None:
    workspace = workspace_from_event(event)
    state = read_state(workspace)
    if not state or state.get("status") != "active":
        return
    if subagent_active(workspace):
        return
    session_id = state.get("session_id")
    if session_id and event.get("session_id") and session_id != event.get("session_id"):
        return
    stage = current_stage(state)
    if not stage:
        state["status"] = "completed"
        state["updated_at"] = now_iso()
        write_state(workspace, state)
        return
    stage_id = str(stage.get("id"))
    signal = SIGNALS[stage_id]
    if transcript_has_signal(event.get("transcript_path"), signal):
        state, previous, next_stage = advance_state(state)
        write_state(workspace, state)
        log_transition(workspace, "on_exit", previous, state)
        if next_stage is None:
            release_mode(workspace)
            log_transition(workspace, "pipeline_complete", None, state)
            print(json.dumps({"systemMessage": "AUTOPILOT COMPLETE"}, ensure_ascii=False))
            return
        log_transition(workspace, "on_enter", next_stage, state)
        reason = (
            "<autopilot-pipeline-transition>\n"
            f"Stage complete: {previous} -> {next_stage}\n\n"
            f"{stage_prompt(next_stage, state)}\n"
            "</autopilot-pipeline-transition>"
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return
    stage["iterations"] = int(stage.get("iterations") or 0) + 1
    state["updated_at"] = now_iso()
    write_state(workspace, state)


def main() -> int:
    event = load_json_stdin()
    name = str(event.get("hook_event_name") or "").lower()
    if name == "userpromptsubmit" or name == "user_prompt_submit":
        handle_user_prompt_submit(event)
    elif name == "stop":
        handle_stop(event)
    elif name in {"subagent_start", "subagentstart"}:
        track_subagent(event, True)
    elif name in {"subagent_stop", "subagentstop"}:
        track_subagent(event, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
