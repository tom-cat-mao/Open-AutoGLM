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
    task_goal_contract: Optional[
        dict
    ]  # legacy/deprecated: trace payload alias for goal_contract; new code should read goal_contract via ensure_goal_contract()
    goal_contract: Optional[
        dict
    ]  # declarative GoalContract dict (compiled by goal_node)
    goal_contract_status: Optional[str]  # pending / compiled / failed / user_override
    goal_compile_source: Optional[
        str
    ]  # llm / heuristic / heuristic_fallback / external
    goal_compile_attempts: int  # number of compile attempts
    task_requirement_set: Optional[dict]  # privacy-safe independent raw-task requirements
    contract_adequacy_status: Optional[str]  # adequate / inadequate / needs_clarification
    contract_adequacy_reasons: list[str]  # stable reason codes only
    needs_recompile: bool  # True only when reflect flags stage stall (W2 T6); goal_node recompiles and clears it
    step_count: int  # 当前步数
    max_steps: int  # 最大步数
    lang: str  # 语言

    # === F1 locate tool 预算 ===
    locate_count: int  # 本 run 已执行 locate 次数（上限 LOCATE_MAX_PER_RUN）
    invalidated_mark_ids: list[
        str
    ]  # S4: locate_* marks invalidated after a failed tap; filtered from marks_block and rejected by grounding

    # === F2 窗口预算（earned continuation） ===
    continuation_count: int  # 已授予的续命窗口次数（上限 CONTINUATION_MAX_GRANTS）
    continuation_last_latch_count: int  # 上一窗口边界时锁存（ever_matched）标准数
    continuation_last_stage_index: Optional[
        int
    ]  # W2 T4: 上一窗口边界时 task_plan 当前阶段序号（无 plan 时 None）
    absolute_max_steps: int  # 本 run 硬性步数上限（初始窗口 * 3）

    # === 屏幕 ===
    screen_width: int  # 设备屏幕宽度（像素）
    screen_height: int  # 设备屏幕高度（像素）
    screenshot_b64: Optional[str]  # deprecated compatibility field; always None
    current_app: str  # 当前前台 app 名
    screen_id: Optional[str]  # 当前屏幕快照 ID
    screen_hash: Optional[str]  # 当前屏幕 hash 摘要
    observation: Optional[dict]  # 当前 observation 脱敏元数据
    mark_registry: Optional[dict]  # 当前 screen_id 绑定的 Mark Registry
    screen_structure: Optional[dict]  # 当前 screen_id 绑定的结构 sidecar
    object_registry: Optional[dict]  # 当前 observation-local object registry
    screen_structure_summary: Optional[dict]  # trace-safe structure summary
    object_registry_summary: Optional[dict]  # trace-safe object summary
    object_registry_binding: Optional[dict]  # object registry binding/version digest
    object_set_version: Optional[str]  # current object registry version
    structure_topology_digest: Optional[str]  # current structure topology digest
    object_trace_summary: Optional[dict]  # selected/compiled object trace summary

    # === Plan 节点输出 ===
    thinking: str  # 模型思考过程
    progress_note: Optional[
        str
    ]  # P4: 模型一句话自述（上轮完成+下一步意图），下轮 plan 注入“上轮意图”；inject 脱敏后存储
    action_raw: str  # 模型原始 action 文本
    action_parsed: Optional[dict]  # validated canonical ActionIR
    intent_raw: Optional[
        dict
    ]  # grounding 前的 IntentIR，仅供 trace/debug，不能进入 executor
    grounding_error: Optional[str]  # IntentIR grounding 失败原因
    grounding_result: Optional[
        dict
    ]  # provider/mark grounding result, trace-safe metadata only
    grounding_provider: Optional[str]  # grounding provider name
    grounding_latency_ms: Optional[int]  # grounding latency in milliseconds
    grounding_failure_code: Optional[str]  # structured grounding failure code
    grounding_screen_hash: Optional[
        str
    ]  # raw screenshot hash bound to grounding request/result
    grounding_observation: Optional[dict]  # bounded grounding context section payload
    grounding_candidates: list[dict]  # trace-safe grounding candidate summaries
    grounding_candidate_count: int  # count of provider candidates
    selected_grounding_candidate_id: Optional[
        int
    ]  # selected candidate index when exactly one valid candidate succeeds
    expected_outcome: Optional[
        dict
    ]  # sibling postcondition contract for verifier, never executor payload
    expected_transition: Optional[
        dict
    ]  # privacy-safe typed shadow; runtime values omitted
    parse_failure: Optional[dict]  # J1 guidance failure contract, Plan-owned
    mechanism_suggestion: Optional[str]  # J1 mechanism-only advisory, Plan-owned
    validation_replan_count: int  # J1 adapter/validation one-shot guidance replan count

    # === Layered error taxonomy ===
    error_layer: Optional[
        str
    ]  # parse / adapter / validation / grounding / safety / execution / reflection / context
    error_code: Optional[str]  # stable machine-readable error code
    recoverable: Optional[bool]  # whether automatic recovery may continue
    retry_policy: Optional[str]  # none / parse_retry / reobserve / wait / takeover

    # === Execute 节点输出 ===
    action_receipt: Optional[
        dict
    ]  # dispatch-only receipt; never transition/Goal success
    action_result: Optional[dict]  # ActionResult 序列化为 dict
    pending_finish: bool  # finish claim pending reflect validation
    finish_claim: Optional[
        dict
    ]  # trace-safe finish claim summary
    finish_source: Optional[
        str
    ]  # who asked for the acceptance: model_claim / budget_forced
    budget_acceptance_done: bool  # budget-forced acceptance already ran this run
    finish_validation_status: Optional[str]  # pending / success / failure / unknown
    finish_validation_evidence: Optional[dict]  # trace-safe final goal evidence
    goal_evidence_ledger: list[dict]  # bounded privacy-safe criterion evidence; Reflect-owned
    goal_agenda: list[dict]  # criterion descriptions and folded statuses; no evidence text
    criterion_gap_list: Optional[
        dict
    ]  # per-criterion gap list for plan injection; folded from model_observation ledger entries by reflect (declared channel: previously absent so the reflect return was silently dropped by LangGraph)
    task_plan_status: Optional[
        dict
    ]  # W2 T3: reflect 推导的阶段状态（current_stage_index + per_stage）；无 plan 时 None；永不当门槛
    stage_stall_windows: int  # W2 T6: 连续 stuck 且阶段未推进的 reflect 窗口数
    stage_stall_grace_windows: int  # P3: 重编译后的免疫窗口余数（期间不累计 stall）

    # === Reflect 节点输出 ===
    reflection: Optional[str]  # 反思结论
    action_succeeded: bool  # 上一步动作是否生效
    reflection_verdict: Optional[str]  # succeeded / failed / partial
    failure_cause: Optional[str]  # 结构化失败原因分类
    suggested_strategy: Optional[str]  # 建议恢复策略
    disputed: Optional[
        bool
    ]  # P3: verifier 高置信 success 与模型 failed 冲突仲裁为 partial；disputed 步不写 failure_memory
    reflection_directive_filtered: bool  # 反思 message 命中指令模式被过滤（保险丝）
    model_skipped: Optional[bool]  # P5: 确定性验证路径跳过 reflect 模型调用
    model_skip_reason: Optional[str]  # P5: 跳过原因（仅稳定机器码，不包含原始屏幕文本）
    observation_retry_count: int  # consecutive observation infrastructure failures
    acceptance_round_count: int  # rejected finish claims requiring replanning
    acceptance_rejection_feedback: Optional[
        dict
    ]  # Stage-Sealing: structured rejection feedback {missing:[{criterion, stage_id, hint}]} for the next plan
    acceptance_verdicts: dict | None  # J1 projected acceptance verdicts, Acceptance-owned

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
    repeated_action_detected: bool  # 同一目标在同一 surface 上重复操作（与成败无关）
    repeat_rejected: bool  # 重复守卫拒绝最近一次动作（系统决策，非动作失败）
    gui_memory: (
        dict  # GUI 短期记忆：visited_screens/tried_actions/scroll_memory/task_progress
    )

    # === Deterministic verifier ===
    verifier_result: Optional[dict]
    verifier_status: Optional[str]
    verifier_failure_cause: Optional[str]
    verifier_evidence: Optional[dict]

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
