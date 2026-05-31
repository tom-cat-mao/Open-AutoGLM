from __future__ import annotations
from typing import TypedDict, Annotated, Optional


def messages_reducer(existing: list[dict], new: list[dict]) -> list[dict]:
    """Custom reducer for messages field.

    Dual-mode:
    - Append mode: new is a single new message (plan_node) → append to existing.
    - Replace mode: new is a full rebuilt list (execute_node) → replace existing.

    Heuristic: if new is non-empty and its first element is already present
    in existing (by role+content), treat as a replace (execute rebuilt list).
    Otherwise, append new to existing (plan added one message).
    """
    if not new:
        return existing

    # Replace mode: if new list looks like a full rebuilt list
    # (first message matches existing first message, or existing is empty)
    if existing and len(new) >= len(existing):
        first_match = existing[0].get("role") == new[0].get("role")
        content_match = existing[0].get("content") == new[0].get("content")
        if first_match and content_match:
            return new

    # Append mode: just append new messages
    return existing + new


class AgentState(TypedDict):
    """Agent state for LangGraph StateGraph.

    Contains all persistent state across the Plan-Execute-Reflect loop.
    """

    # === 对话上下文 ===
    messages: Annotated[
        list[dict], messages_reducer
    ]  # OpenAI chat 格式，替代 PhoneAgent._context

    # === 任务 ===
    task: str  # 用户原始任务
    step_count: int  # 当前步数
    max_steps: int  # 最大步数
    lang: str  # 语言

    # === 屏幕 ===
    screen_width: int  # 设备屏幕宽度（像素）
    screen_height: int  # 设备屏幕高度（像素）
    screenshot_b64: Optional[str]  # 当前截图 base64
    current_app: str  # 当前前台 app 名

    # === Plan 节点输出 ===
    thinking: str  # 模型思考过程
    action_raw: str  # 模型原始 action 文本
    action_parsed: Optional[dict]  # parse_action() 解析结果

    # === Execute 节点输出 ===
    action_result: Optional[dict]  # ActionResult 序列化为 dict

    # === Reflect 节点输出 ===
    reflection: Optional[str]  # 反思结论
    action_succeeded: bool  # 上一步动作是否生效
    reflection_verdict: Optional[str]  # succeeded / failed / partial
    failure_cause: Optional[str]  # 结构化失败原因分类
    suggested_strategy: Optional[str]  # 建议恢复策略
    retry_count: int  # 失败/部分成功反思累计次数

    # === Human-in-the-Loop (Phase 2) ===
    pending_interrupt: Optional[str]  # 待处理的中断类型: "confirmation" / "takeover"
    interrupt_message: Optional[str]  # 中断消息
    interrupt_result: Optional[bool]  # 中断结果: confirmation 的用户选择

    # === Pending execute (Phase 5 BUG 2 fix) ===
    pending_execute: bool  # 待执行的确认动作（confirm后dispatch）
    action_confirmed: bool  # 当前动作已通过确认

    # === Eval metrics (Phase 7) ===
    hitl_count: int  # HITL interrupt routing count within this run

    # === 控制 ===
    finished: bool  # 任务是否完成
    error: Optional[str]  # 错误信息
    device_id: Optional[str]  # 设备 ID（可选）
