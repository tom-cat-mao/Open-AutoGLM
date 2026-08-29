"""NiceGUI application for live thin-loop watch and HITL steering."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from phone_agent.v2.config import V2Config, load_project_env
from phone_agent.web.bridge import WebRunBridge

_STATUS_TEXT = {
    "idle": "待命",
    "starting": "启动中",
    "running": "运行中",
    "waiting_hitl": "等待人工",
    "succeeded": "已完成",
    "failed": "未完成",
    "takeover": "等待接管",
    "budget_exhausted": "Token 预算耗尽",
    "loop_fuse": "步骤保险丝触发",
    "error": "运行错误",
}

_STATUS_COLOR = {
    "idle": "grey",
    "starting": "blue-grey",
    "running": "primary",
    "waiting_hitl": "orange",
    "succeeded": "positive",
    "failed": "negative",
    "takeover": "orange",
    "budget_exhausted": "negative",
    "loop_fuse": "negative",
    "error": "negative",
}

_STEP_COLOR = {
    "running": "blue",
    "success": "green",
    "warning": "orange",
    "error": "red",
}

_STEP_TEXT = {
    "running": "执行中",
    "success": "成功",
    "warning": "预警",
    "error": "失败",
}


def _display(value: Any, fallback: str = "—") -> str:
    text = str(value or "").strip()
    return text or fallback


def create_ui(
    bridge: WebRunBridge,
    *,
    config: V2Config,
    refresh_seconds: float = 0.5,
) -> None:
    """Build the single-page UI and attach it to ``bridge``."""

    ui.colors(primary="#2563eb", positive="#16a34a", negative="#dc2626")
    ui.add_css("""
        body { background: #f4f6f8; color: #172033; }
        .panel { border: 1px solid #e5e7eb; box-shadow: none; }
        .phone-frame { background: #111827; border-radius: 1.3rem; padding: .65rem; }
        .phone-frame img { border-radius: .8rem; max-height: 68vh; object-fit: contain; }
        .task-board { max-height: 33vh; overflow-y: auto; white-space: pre-wrap; }
        """)

    with ui.header().classes(
        "items-center gap-3 px-5 py-3 bg-white text-slate-900 border-b"
    ):
        ui.label("Open-AutoGLM 实时控制台").classes(
            "text-xl font-semibold whitespace-nowrap"
        )
        task_input = (
            ui.input(placeholder="输入手机任务，例如：打开设置并进入 WLAN")
            .props("outlined dense clearable")
            .classes("grow min-w-64")
        )
        start_button = ui.button("开始运行", icon="play_arrow").props("unelevated")
        ui.label(
            f"设备：{config.device_id or '自动选择'} · 模型：{config.model_name}"
        ).classes("text-xs text-slate-500 whitespace-nowrap")

    with ui.column().classes("w-full max-w-[1500px] mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-stretch gap-4 flex-wrap lg:flex-nowrap"):
            with ui.card().classes("panel w-full lg:w-[38%] p-4"):
                ui.label("手机画面").classes("text-lg font-semibold")
                screen_meta = ui.label("等待画面").classes("text-sm text-slate-500")
                with ui.element("div").classes(
                    "phone-frame w-full flex justify-center mt-2"
                ):
                    screen_image = ui.image().classes("w-full")
                    screen_image.set_visibility(False)
                no_screen = ui.label("运行后将在这里显示最新截图").classes(
                    "text-sm text-slate-400 self-center mt-3"
                )

            with ui.column().classes("w-full lg:w-[62%] gap-4"):
                with ui.card().classes("panel w-full p-4"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("步骤").classes("text-lg font-semibold")
                        step_count = ui.badge("0 步", color="grey")
                    timeline = ui.column().classes(
                        "w-full gap-2 max-h-[44vh] overflow-y-auto"
                    )
                    with timeline:
                        ui.label("尚无执行步骤").classes("text-sm text-slate-400")

                with ui.card().classes("panel w-full p-4"):
                    ui.label("任务板").classes("text-lg font-semibold")
                    task_board = ui.markdown("_等待 TaskDoc…_").classes(
                        "task-board w-full text-sm"
                    )

        with ui.card().classes("panel w-full p-4"):
            with ui.row().classes("w-full items-center gap-3"):
                status_badge = ui.badge("待命", color="grey").classes("text-sm")
                status_line = ui.label("步骤 0 · Token 0 · 终局：—").classes(
                    "text-sm text-slate-600"
                )

        with ui.card().classes("panel w-full p-4 border-orange-300") as hitl_panel:
            ui.label("需要人工决定").classes("text-lg font-semibold text-orange-700")
            hitl_prompt = ui.label().classes("text-sm whitespace-pre-wrap")
            hitl_answer = (
                ui.input(placeholder="也可以输入文本回答")
                .props("outlined dense clearable")
                .classes("w-full")
            )
            with ui.row().classes("gap-2"):
                approve_button = ui.button("同意", icon="check", color="positive")
                reject_button = ui.button("拒绝", icon="close", color="negative")
                answer_button = ui.button("提交回答", icon="send").props("outline")
        hitl_panel.set_visibility(False)

    last_signature: tuple[Any, ...] | None = None

    def submit_hitl(answer: str) -> None:
        try:
            bridge.submit_hitl(answer)
            hitl_answer.value = ""
            ui.notify("已提交人工决定", type="positive")
        except (ValueError, RuntimeError) as exc:
            ui.notify(str(exc), type="warning")

    approve_button.on("click", lambda: submit_hitl("approve"))
    reject_button.on("click", lambda: submit_hitl("reject"))
    answer_button.on("click", lambda: submit_hitl(str(hitl_answer.value or "")))

    def start_run() -> None:
        try:
            bridge.start(str(task_input.value or ""))
            ui.notify("任务已启动", type="positive")
        except (ValueError, RuntimeError) as exc:
            ui.notify(str(exc), type="warning")

    start_button.on("click", start_run)
    task_input.on("keydown.enter", start_run)

    def render() -> None:
        nonlocal last_signature
        state = bridge.snapshot()
        result = state["final_result"] or {}
        signature = (
            state["status"],
            state["screen_seq"],
            len(state["steps"]),
            tuple(
                (
                    step["step"],
                    step["intent"],
                    step["tool"],
                    step["target"],
                    step["status"],
                    step["result"],
                    step["latency_ms"],
                )
                for step in state["steps"]
            ),
            state["current_app"],
            state["current_screen"],
            state["task_board"],
            state["pending_hitl_prompt"],
            state["tokens"],
            result.get("reason"),
        )
        if signature == last_signature:
            return
        last_signature = signature

        status = state["status"]
        status_badge.set_text(_STATUS_TEXT.get(status, status))
        status_badge.props(f"color={_STATUS_COLOR.get(status, 'grey')}")
        start_button.set_enabled(status not in {"starting", "running", "waiting_hitl"})
        step_count.set_text(f"{len(state['steps'])} 步")
        status_line.set_text(
            f"步骤 {len(state['steps'])} · Token {state['tokens']:,} · "
            f"终局：{_display(result.get('reason'))}"
        )

        if state["current_screen"]:
            screen_image.set_source(state["current_screen"])
            screen_image.set_visibility(True)
            no_screen.set_visibility(False)
        else:
            screen_image.set_visibility(False)
            no_screen.set_visibility(True)
        screen_meta.set_text(
            f"应用：{_display(state['current_app'])} · screen#{_display(state['screen_seq'])}"
        )

        timeline.clear()
        with timeline:
            if not state["steps"]:
                ui.label("尚无执行步骤").classes("text-sm text-slate-400")
            for step in state["steps"]:
                color = _STEP_COLOR.get(step["status"], "grey")
                with (
                    ui.card()
                    .classes("step-card w-full p-3 bg-white")
                    .style(f"border-left: 4px solid {color}")
                ):  # compact ledger card
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(
                            f"#{step['step']} {_display(step['intent'], '（未声明意图）')}"
                        ).classes("font-medium")
                        ui.badge(
                            _STEP_TEXT.get(step["status"], step["status"]), color=color
                        )
                    ui.label(
                        f"工具：{_display(step['tool'])} · 目标：{_display(step['target'])}"
                    ).classes("text-xs text-slate-500")
                    if step["result"]:
                        ui.label(step["result"]).classes(
                            "text-sm whitespace-pre-wrap break-all"
                        )

        task_board.set_content(state["task_board"] or "_等待 TaskDoc…_")
        prompt = state["pending_hitl_prompt"]
        hitl_prompt.set_text(prompt or "")
        hitl_panel.set_visibility(bool(prompt))

    ui.timer(refresh_seconds, render)


def run(
    *,
    device_id: str | None = None,
    model: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    """Resolve configuration, build the UI, and start NiceGUI."""

    load_project_env()
    overrides = {"device_id": device_id, "model_name": model}
    config = V2Config.from_env(overrides)
    bridge = WebRunBridge(overrides)

    @ui.page("/")
    def _index() -> None:
        # Explicit page registration: the auto-index page would re-execute
        # ``sys.argv[0]`` per request, which breaks ``python -m phone_agent.web``.
        create_ui(bridge, config=config)

    ui.run(
        host=host,
        port=port,
        title="Open-AutoGLM 实时控制台",
        show=False,
        reload=False,
    )


__all__ = ["create_ui", "run"]
