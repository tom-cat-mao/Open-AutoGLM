#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGE_ORDER = ["ralplan", "execution", "ralph", "qa"]
SIGNALS = {
    "ralplan": "PIPELINE_RALPLAN_COMPLETE",
    "execution": "PIPELINE_EXECUTION_COMPLETE",
    "ralph": "PIPELINE_RALPH_COMPLETE",
    "qa": "PIPELINE_QA_COMPLETE",
}
MAX_STAGE_ITERATIONS = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def write_state(workspace: Path, state: dict[str, Any]) -> None:
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(prompt: str) -> tuple[set[str], str]:
    text = prompt.strip()
    if text.startswith("/autopilot"):
        text = text[len("/autopilot") :].strip()
    elif text.startswith("/auto-pilot"):
        text = text[len("/auto-pilot") :].strip()
    flags: set[str] = set()
    parts: list[str] = []
    for part in text.split():
        if part.startswith("--"):
            flags.add(part)
        else:
            parts.append(part)
    return flags, " ".join(parts).strip()


def parse_command(prompt: str) -> tuple[str, str]:
    text = prompt.strip()
    for prefix in ("/autopilot", "/auto-pilot"):
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            if not rest:
                return "start", ""
            head, _, tail = rest.partition(" ")
            if head in {"cancel", "status", "resume", "reset"}:
                return head, tail.strip()
            return "start", rest
    return "", text


def should_start(prompt: str) -> bool:
    stripped = prompt.lstrip()
    return stripped.startswith("/autopilot") or stripped.startswith("/auto-pilot")


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


def build_stages(first_stage: str, *, no_verification: bool, no_qa: bool) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    activated = False
    for stage in STAGE_ORDER:
        skipped = (stage == "ralph" and no_verification) or (stage == "qa" and no_qa)
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
    use_current = "--use-current-plan" in flags or not task
    direct = "--direct" in flags
    if direct:
        first_stage = "execution"
    elif use_current and graph_is_approved(workspace):
        first_stage = "execution"
    else:
        first_stage = "ralplan"
    state = {
        "version": 1,
        "status": "active",
        "session_id": event.get("session_id"),
        "task": task,
        "flags": sorted(flags),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "stages": build_stages(
            first_stage,
            no_verification="--no-verification" in flags,
            no_qa="--no-qa" in flags,
        ),
    }
    write_state(workspace, state)
    return state


def summarize_state(state: dict[str, Any] | None) -> str:
    if not state:
        return "Autopilot is idle."
    current = current_stage(state)
    stage_text = current.get("id") if current else "none"
    return (
        f"Autopilot status={state.get('status', 'unknown')}, "
        f"stage={stage_text}, task={state.get('task') or 'current plan'}"
    )


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


def stage_prompt(stage_id: str, state: dict[str, Any]) -> str:
    task = state.get("task") or "当前 approved roadmap"
    signal = SIGNALS[stage_id]
    common = (
        "<autopilot-pipeline-continuation>\n"
        f"[AUTOPILOT PIPELINE - STAGE: {stage_id} | SIGNAL: {signal}]\n"
        "读取 `.trae/rules/autopilot.mdc` 获取协议；完成本 stage 后单独输出：\n"
        f"AUTOPILOT_SIGNAL: {signal}\n\n"
    )
    if stage_id == "ralplan":
        body = f"""
## Stage: RALPLAN

任务：{task}

执行：
1. 读取 `.trae/rules/ralplan.mdc` 与 `.trae/rules/graph.mdc`。
2. 调用 planner subagent 整体覆写 `.trae/rules/graph.mdc`。
3. 串行调用 architect subagent 只读审查。
4. 串行调用 critic subagent 输出 APPROVE / ITERATE / REJECT。
5. 若 ITERATE，最多 5 轮回到 planner 窄修。
6. APPROVE 后执行 Final Check，确认 `design_status=critic_approved`、`last_critic_verdict=APPROVE`、`approved_for_execution=true`。
7. Critic 未 APPROVE 前不得修改业务代码。
"""
    elif stage_id == "execution":
        body = f"""
## Stage: Execution

任务：{task}

执行：
1. 读取 `.trae/rules/graph.mdc` 的 approved roadmap 与约束 Checklist。
2. 按当前规划 Phase 实现代码/配置/文档变更。
3. 使用 TodoWrite 跟踪多步骤执行。
4. 不主动 commit，不清理用户已有改动。
5. 完成实现后输出完成信号；测试失败可留给 verification stage 继续修复。
"""
    elif stage_id == "ralph":
        body = """
## Stage: RALPH / Verification

执行：
1. 运行相关测试；Python/pytest/pip 优先使用 `.venv/bin/*`。
2. 检查新增配置语法、hook 脚本语法、关键路径行为。
3. 修复发现的问题并重跑目标测试。
4. 仅清理本 stage 产生的临时产物，不清理用户已有改动。
"""
    else:
        body = """
## Stage: QA

执行：
1. 检查 git diff 范围，确认没有过度清理或无关改动。
2. 确认 `.trae/rules/graph.mdc` 与 Autopilot 状态边界未混淆。
3. 如本次完成 roadmap phase，按项目规则更新状态；否则不要伪造完成态。
4. 汇总变更、测试结果、剩余风险。
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
        print(json.dumps({"decision": "block", "reason": "Autopilot cancelled."}, ensure_ascii=False))
        return
    if command == "resume":
        state = read_state(workspace)
        if state:
            state["status"] = "active"
            state["session_id"] = event.get("session_id")
            state["updated_at"] = now_iso()
            write_state(workspace, state)
        print(json.dumps({"decision": "block", "reason": summarize_state(state)}, ensure_ascii=False))
        return
    state = init_state(event)
    if state is None:
        return
    stage = current_stage(state)
    stage_id = stage.get("id") if stage else "unknown"
    print(json.dumps({"systemMessage": f"Autopilot initialized: active stage={stage_id}"}, ensure_ascii=False))


def handle_stop(event: dict[str, Any]) -> None:
    workspace = workspace_from_event(event)
    state = read_state(workspace)
    if not state or state.get("status") != "active":
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
        if next_stage is None:
            print(json.dumps({"systemMessage": "AUTOPILOT COMPLETE"}, ensure_ascii=False))
            return
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
    if stage["iterations"] > MAX_STAGE_ITERATIONS:
        state["status"] = "blocked"
        state["blocked_reason"] = f"stage {stage_id} exceeded {MAX_STAGE_ITERATIONS} continuation iterations"
        write_state(workspace, state)
        print(json.dumps({"continue": False, "stopReason": state["blocked_reason"]}, ensure_ascii=False))
        return
    write_state(workspace, state)
    print(json.dumps({"decision": "block", "reason": stage_prompt(stage_id, state)}, ensure_ascii=False))


def main() -> int:
    event = load_json_stdin()
    name = str(event.get("hook_event_name") or "").lower()
    if name == "userpromptsubmit" or name == "user_prompt_submit":
        handle_user_prompt_submit(event)
    elif name == "stop":
        handle_stop(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
