from __future__ import annotations
from typing import TypedDict, Annotated, Optional
from operator import add


class AgentState(TypedDict):
    """Agent state for LangGraph StateGraph.

    Contains all persistent state across the Plan-Execute-Reflect loop.
    """
    # === 对话上下文 ===
    messages: Annotated[list[dict], add]      # OpenAI chat 格式，替代 PhoneAgent._context

    # === 任务 ===
    task: str                                  # 用户原始任务
    step_count: int                            # 当前步数
    max_steps: int                             # 最大步数
    lang: str                                  # 语言

    # === 屏幕 ===
    screen_width: int                          # 设备屏幕宽度（像素）
    screen_height: int                         # 设备屏幕高度（像素）
    screenshot_b64: Optional[str]              # 当前截图 base64
    current_app: str                           # 当前前台 app 名

    # === Plan 节点输出 ===
    thinking: str                              # 模型思考过程
    action_raw: str                            # 模型原始 action 文本
    action_parsed: Optional[dict]              # parse_action() 解析结果

    # === Execute 节点输出 ===
    action_result: Optional[dict]              # ActionResult 序列化为 dict

    # === Reflect 节点输出 ===
    reflection: Optional[str]                  # 反思结论
    action_succeeded: bool                     # 上一步动作是否生效

    # === Human-in-the-Loop (Phase 2) ===
    pending_interrupt: Optional[str]           # 待处理的中断类型: "confirmation" / "takeover"
    interrupt_message: Optional[str]           # 中断消息
    interrupt_result: Optional[bool]           # 中断结果: confirmation 的用户选择

    # === 控制 ===
    finished: bool                             # 任务是否完成
    error: Optional[str]                       # 错误信息
    device_id: Optional[str]                   # 设备 ID（可选）
