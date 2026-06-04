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
    screen_id: Optional[str]  # 当前屏幕快照 ID
    screen_hash: Optional[str]  # 当前屏幕 hash 摘要
    observation: Optional[dict]  # 当前 observation 脱敏元数据
    mark_registry: Optional[dict]  # 当前 screen_id 绑定的 Mark Registry

    # === Plan 节点输出 ===
    thinking: str  # 模型思考过程
    action_raw: str  # 模型原始 action 文本
    action_parsed: Optional[dict]  # parse_action() 解析结果
    intent_raw: Optional[dict]  # grounding 前的 IntentIR，仅供 trace/debug，不能进入 executor
    grounding_error: Optional[str]  # IntentIR grounding 失败原因
    grounding_result: Optional[dict]  # provider/mark grounding result, trace-safe metadata only
    grounding_provider: Optional[str]  # grounding provider name
    grounding_latency_ms: Optional[int]  # grounding latency in milliseconds
    grounding_failure_code: Optional[str]  # structured grounding failure code
    grounding_screen_hash: Optional[str]  # raw screenshot hash bound to grounding request/result
    grounding_observation: Optional[dict]  # bounded grounding context section payload
    grounding_candidates: list[dict]  # trace-safe grounding candidate summaries
    grounding_candidate_count: int  # count of provider candidates
    selected_grounding_candidate_id: Optional[int]  # selected candidate index when exactly one valid candidate succeeds

    # === Layered error taxonomy ===
    error_layer: Optional[str]  # parse / adapter / validation / grounding / safety / execution / reflection / context
    error_code: Optional[str]  # stable machine-readable error code
    recoverable: Optional[bool]  # whether automatic recovery may continue
    retry_policy: Optional[str]  # none / parse_retry / reobserve / wait / takeover

    # === Execute 节点输出 ===
    action_result: Optional[dict]  # ActionResult 序列化为 dict

    # === Reflect 节点输出 ===
    reflection: Optional[str]  # 反思结论
    action_succeeded: bool  # 上一步动作是否生效
    reflection_verdict: Optional[str]  # succeeded / failed / partial
    failure_cause: Optional[str]  # 结构化失败原因分类
    suggested_strategy: Optional[str]  # 建议恢复策略
    retry_count: int  # 失败/部分成功反思累计次数

    # === Context & Observability Harness (Phase 11) ===
    context_mode: str  # off / observe / inject
    context_strategy: str  # off / observe_only / inject_redacted_block
    prompt_version: str  # prompt renderer version for trace/eval comparison
    selected_sections: list[str]  # selected context section IDs, not raw text
    screen_belief: dict  # 短期屏幕信念，非事实来源
    action_outcome_summary: Optional[dict]  # 最近一次动作结果摘要
    failure_memory: list[dict]  # 当前 run 内最近失败摘要
    summarized_history: str  # 当前 run 内压缩历史
    short_term_memory: dict  # bounded request-time memory sections
    action_ledger: list[dict]  # bounded action ledger for recent steps
    context_budget: dict  # context 裁剪预算
    context_truncated: bool  # context 是否被裁剪
    context_block_chars: int  # 注入 context block 字符数
    messages_before: int  # model request message count before compaction
    messages_after: int  # model request message count after compaction
    message_chars_before: int  # approximate request chars before compaction
    message_chars_after: int  # approximate request chars after compaction
    approx_tokens_before: int  # rough char/4 token estimate before compaction
    approx_tokens_after: int  # rough char/4 token estimate after compaction
    failure_memory_hit_count: int  # failure memory 命中次数
    repeated_failure_count: int  # 重复失败计数
    gui_memory: dict  # GUI 短期记忆：visited_screens/tried_actions/scroll_memory/task_progress

    # === Deterministic verifier ===
    verifier_result: Optional[dict]
    verifier_status: Optional[str]
    verifier_failure_cause: Optional[str]

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
