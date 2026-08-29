"""NiceGUI application for live thin-loop watch and HITL steering (v2).

Design contract: ``docs/web-ui-design.md`` §10. Adds over v1: config drawer
(per-run overrides), expandable step details, screenshot history, per-role
token breakdown, soft stop, and an App-KB tab with dream.
"""

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
    "takeover": "已接管/已停止",
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

_USAGE_ROLE_TEXT = {
    "actor": "主模型",
    "compact": "压缩",
    "verifier": "验收器",
    "reviewer": "安全复核",
    "distill": "蒸馏",
}

_KIND_TEXT = {
    "device": "设备",
    "alias": "别名",
    "learned": "学习",
    "user": "用户",
}


def _display(value: Any, fallback: str = "—") -> str:
    text = str(value or "").strip()
    return text or fallback


def _choose_frame(screens: list[dict], selected: dict) -> dict | None:
    """Main-frame choice: follow the newest frame unless the user pinned one.

    ``selected`` is ``{"seq": int|None, "pinned": bool}`` mutated in place.
    A pinned frame that rolled out of the history cap releases the pin.
    """

    latest = screens[-1] if screens else None
    if not selected["pinned"]:
        selected["seq"] = latest.get("seq") if latest else None
        return latest
    if latest is not None and selected["seq"] not in {s.get("seq") for s in screens}:
        selected["pinned"] = False
        selected["seq"] = latest.get("seq")
    return next((s for s in screens if s.get("seq") == selected["seq"]), latest)


def _pin_toggle(selected: dict, seq: Any) -> None:
    """Click a thumbnail: pin it; click the pinned one again: follow latest."""

    if selected["pinned"] and selected["seq"] == seq:
        selected.update(seq=None, pinned=False)
    else:
        selected.update(seq=seq, pinned=True)


def _mask_url(url: str) -> str:
    text = str(url or "")
    if len(text) <= 28:
        return text
    return text[:18] + "…" + text[-8:]


class _ConfigPanel:
    """Right-drawer config: per-run overrides (never written back to .env)."""

    def __init__(self, config: V2Config) -> None:
        self._config = config
        with ui.drawer("right", bordered=True).classes("p-4 gap-3 w-80") as drawer:
            ui.label("运行配置（本次生效，不写回 .env）").classes("text-base font-semibold")
            self.device_id = ui.input(
                "设备 serial（留空=自动）", value=config.device_id or ""
            ).props("outlined dense").classes("w-full")
            self.model_name = ui.input("模型", value=config.model_name).props(
                "outlined dense"
            ).classes("w-full")
            self.safety_mode = ui.select(
                ["wary", "off", "hard", "reviewer"],
                value=getattr(config, "safety_mode", "wary"),
                label="安全模式",
            ).props("outlined dense").classes("w-full")
            self.lang = ui.select(
                ["cn", "en"], value=getattr(config, "lang", "cn"), label="语言"
            ).props("outlined dense").classes("w-full")
            self.max_steps = ui.number(
                "最大步数（保险丝）", value=getattr(config, "max_model_calls", 100), min=1
            ).props("outlined dense").classes("w-full")
            self.token_budget = ui.number(
                "Token 预算", value=getattr(config, "token_budget", 1_000_000), min=1000
            ).props("outlined dense").classes("w-full")
            self.grounding_provider = ui.select(
                ["hybrid", "accessibility", "locateanything"],
                value=getattr(config, "grounding_provider", "hybrid"),
                label="Grounding",
            ).props("outlined dense").classes("w-full")
            self.app_kb = ui.switch(
                "App-KB 记忆", value=bool(getattr(config, "app_kb_enabled", True))
            )
            ui.separator()
            ui.label("当前生效（只读）").classes("text-sm font-medium text-slate-500")
            ui.label(f"网关：{_mask_url(getattr(config, 'base_url', ''))}").classes(
                "text-xs text-slate-500 break-all"
            )
            ui.label(
                f"图片保留 {getattr(config, 'image_keep', 2)} 张 · "
                f"compact {getattr(config, 'compact_warn_ratio', 0.75)}/"
                f"{getattr(config, 'compact_trigger_ratio', 0.92)} · "
                f"记忆目录 {getattr(config, 'memory_dir', 'memory')}"
            ).classes("text-xs text-slate-500")
            ui.button("恢复默认", icon="restart_alt", on_click=self.reset).props(
                "flat dense"
            )
        self.drawer = drawer

    def reset(self) -> None:
        cfg = self._config
        self.device_id.value = cfg.device_id or ""
        self.model_name.value = cfg.model_name
        self.safety_mode.value = getattr(cfg, "safety_mode", "wary")
        self.lang.value = getattr(cfg, "lang", "cn")
        self.max_steps.value = getattr(cfg, "max_model_calls", 100)
        self.token_budget.value = getattr(cfg, "token_budget", 1_000_000)
        self.grounding_provider.value = getattr(cfg, "grounding_provider", "hybrid")
        self.app_kb.value = bool(getattr(cfg, "app_kb_enabled", True))
        ui.notify("已恢复为当前 .env 生效值", type="positive")

    def overrides(self) -> dict[str, Any]:
        return {
            "device_id": str(self.device_id.value or "").strip() or None,
            "model_name": str(self.model_name.value or "").strip() or None,
            "safety_mode": self.safety_mode.value,
            "lang": self.lang.value,
            "max_model_calls": int(self.max_steps.value or 100),
            "token_budget": int(self.token_budget.value or 1_000_000),
            "grounding_provider": self.grounding_provider.value,
            "app_kb_enabled": bool(self.app_kb.value),
        }


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
        .thumb { cursor: pointer; border: 2px solid transparent; border-radius: .4rem; }
        .thumb:hover { border-color: #93c5fd; }
        .task-board { max-height: 46vh; overflow-y: auto; white-space: pre-wrap; }
        .step-detail { white-space: pre-wrap; word-break: break-all; }
        """)

    panel = _ConfigPanel(config)

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
        stop_button = ui.button("停止", icon="stop", color="negative").props("outline")
        status_badge = ui.badge("待命", color="grey").classes("text-sm")
        ui.button(icon="settings", on_click=panel.drawer.toggle).props(
            "flat round dense"
        )

    with ui.column().classes("w-full max-w-[1500px] mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-stretch gap-4 flex-wrap lg:flex-nowrap"):
            with ui.card().classes("panel w-full lg:w-[38%] p-4"):
                ui.label("手机画面").classes("text-lg font-semibold")
                screen_meta = ui.label("等待画面").classes("text-sm text-slate-500")
                with ui.element("div").classes(
                    "phone-frame w-full flex justify-center items-start mt-2"
                ):
                    # Plain <img> (not Quasar q-img): the box hugs the bitmap
                    # (height-anchored), so no cropping and no empty black slab.
                    screen_image = ui.element("img").style(
                        "max-height: 62vh; max-width: 100%; width: auto;"
                        " height: auto; display: block; margin: 0 auto;"
                        " border-radius: .8rem;"
                    )
                    screen_image.set_visibility(False)
                no_screen = ui.label("运行后将在这里显示最新截图").classes(
                    "text-sm text-slate-400 self-center mt-3"
                )
                thumbs = ui.row().classes(
                    "w-full gap-2 mt-2 overflow-x-auto flex-nowrap"
                )

            with ui.column().classes("w-full lg:w-[62%] gap-4"):
                with ui.card().classes("panel w-full p-4"):
                    with ui.tabs().classes("w-full") as tabs:
                        tab_steps = ui.tab("steps", label="步骤")
                        tab_board = ui.tab("board", label="任务板")
                        tab_kb = ui.tab("appkb", label="应用库")
                    with ui.tab_panels(tabs, value=tab_steps).classes("w-full"):
                        with ui.tab_panel(tab_steps).classes("p-0 pt-2"):
                            with ui.row().classes(
                                "w-full items-center justify-between"
                            ):
                                step_count = ui.badge("0 步", color="grey")
                                usage_line = ui.label("").classes(
                                    "text-xs text-slate-500"
                                )
                            timeline = ui.column().classes(
                                "w-full gap-2 max-h-[46vh] overflow-y-auto"
                            )
                            with timeline:
                                ui.label("尚无执行步骤").classes(
                                    "text-sm text-slate-400"
                                )
                        with ui.tab_panel(tab_board).classes("p-0 pt-2"):
                            task_board = ui.markdown("_等待 TaskDoc…_").classes(
                                "task-board w-full text-sm"
                            )
                        with ui.tab_panel(tab_kb).classes("p-0 pt-2"):
                            with ui.row().classes(
                                "w-full items-center justify-between"
                            ):
                                kb_count = ui.badge("0 条", color="grey")
                                dream_button = ui.button(
                                    "立即整理 (dream)", icon="cleaning_services"
                                ).props("outline dense")
                            kb_table = ui.table(
                                columns=[
                                    {"name": "label", "label": "名称", "field": "label"},
                                    {"name": "package", "label": "包名", "field": "package"},
                                    {"name": "kind", "label": "类型", "field": "kind"},
                                    {"name": "success_count", "label": "成功次数", "field": "success_count"},
                                    {"name": "stale", "label": "状态", "field": "stale"},
                                ],
                                rows=[],
                                row_key="package",
                            ).classes("w-full max-h-[40vh]")

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
    selected: dict[str, Any] = {"seq": None, "pinned": False}
    last_run_id: dict[str, Any] = {"id": None}

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
            bridge.start(
                str(task_input.value or ""),
                overrides=panel.overrides(),
            )
            ui.notify("任务已启动", type="positive")
        except (ValueError, RuntimeError) as exc:
            ui.notify(str(exc), type="warning")

    start_button.on("click", start_run)
    task_input.on("keydown.enter", start_run)

    def stop_run() -> None:
        if bridge.request_stop():
            ui.notify("已请求停止（当前步完成后收尾）", type="warning")
        else:
            ui.notify("当前没有可停止的运行", type="warning")

    stop_button.on("click", stop_run)

    def run_dream() -> None:
        summary = bridge.run_dream()
        ui.notify(f"dream 整理：{summary}", type="info", multi_line=True)

    dream_button.on("click", run_dream)

    def _render_steps(steps: list[dict[str, Any]]) -> None:
        timeline.clear()
        with timeline:
            if not steps:
                ui.label("尚无执行步骤").classes("text-sm text-slate-400")
                return
            for step in steps:
                color = _STEP_COLOR.get(step["status"], "grey")
                is_closing = not step.get("tool") and not step.get("result")
                if is_closing:
                    # A final model turn with no tool call is the wrap-up, not a
                    # work step — label it as such instead of "（未声明意图）".
                    head = f"#{step['step']} 模型收尾（无工具调用）"
                    color = _STEP_COLOR["success"]
                else:
                    head = (
                        f"#{step['step']} "
                        f"{_display(step['intent'], '（未声明意图）')} → "
                        f"{_display(step['tool'])}"
                        + (f"<{step['target']}>" if step.get("target") else "")
                    )
                with ui.expansion(head).classes("w-full").style(
                    f"border-left: 4px solid "
                    f"{color}; border-radius: .4rem; background: #fff"
                ).props("dense") as expansion:
                    with ui.column().classes("gap-1 p-1"):
                        badge_text = (
                            "收尾"
                            if is_closing
                            else _STEP_TEXT.get(step["status"], step["status"])
                        )
                        ui.badge(badge_text, color=color)
                        if step.get("args"):
                            ui.label("参数").classes("text-xs font-medium text-slate-500")
                            ui.label(str(step["args"])).classes(
                                "text-xs step-detail text-slate-600"
                            )
                        if step.get("result"):
                            ui.label("结果").classes("text-xs font-medium text-slate-500")
                            ui.label(str(step["result"])).classes(
                                "text-sm step-detail"
                            )
                        ui.label(
                            f"模型 {step.get('model_latency_ms', 0)}ms · "
                            f"工具 {step.get('tool_latency_ms', 0)}ms"
                        ).classes("text-xs text-slate-400")
                if step["status"] == "running" and not is_closing:
                    expansion.set_value(True)

    def _render_kb() -> None:
        entries = bridge.kb_entries()
        rows = [
            {
                "label": entry.get("label", ""),
                "package": entry.get("package", ""),
                "kind": _KIND_TEXT.get(entry.get("kind", ""), entry.get("kind", "")),
                "success_count": entry.get("success_count", 0),
                "stale": "已失效" if entry.get("stale") else "有效",
            }
            for entry in entries
        ]
        kb_table.rows = rows
        kb_table.update()
        kb_count.set_text(f"{len(rows)} 条")

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
                    step.get("tool_latency_ms"),
                )
                for step in state["steps"]
            ),
            state["current_app"],
            len(state["screens"]),
            state["task_board"],
            state["pending_hitl_prompt"],
            state["tokens"],
            tuple(sorted((state["usage"] or {}).items())),
            result.get("reason"),
        )
        if signature == last_signature:
            return
        last_signature = signature

        status = state["status"]
        status_text = _STATUS_TEXT.get(status, status)
        status_badge.set_text(status_text)
        status_badge.props(f"color={_STATUS_COLOR.get(status, 'grey')}")
        running = status in {"starting", "running", "waiting_hitl"}
        start_button.set_enabled(not running)
        stop_button.set_enabled(bool(running))
        step_count.set_text(f"{len(state['steps'])} 步")

        usage = state["usage"] or {}
        if usage:
            parts = " · ".join(
                f"{_USAGE_ROLE_TEXT.get(role, role)} {tokens:,}"
                for role, tokens in sorted(usage.items())
            )
            usage_line.set_text(parts)
        else:
            usage_line.set_text("")

        # --- phone screen + history -------------------------------------
        screens = state["screens"]
        latest = screens[-1] if screens else None
        if state["run_id"] != last_run_id["id"]:
            # New run: drop any pinned historical frame.
            last_run_id["id"] = state["run_id"]
            selected["seq"] = None
            selected["pinned"] = False
        shown = _choose_frame(screens, selected)
        if shown and shown.get("image"):
            screen_image.props(f'src="{shown["image"]}"')
            screen_image.set_visibility(True)
            no_screen.set_visibility(False)
        else:
            screen_image.set_visibility(False)
            no_screen.set_visibility(True)
        screen_meta.set_text(
            f"应用：{_display(state['current_app'])} · screen#{_display(state['screen_seq'])}"
            + (
                f"（历史帧 #{selected['seq']}，再点一次该缩略图回到最新）"
                if selected["pinned"] and shown is not latest
                else ""
            )
        )

        thumbs.clear()
        with thumbs:
            for item in screens[-12:]:
                seq = item.get("seq")
                thumb_cls = "thumb w-14"
                if selected["pinned"] and seq == selected["seq"]:
                    thumb_cls += " border-blue-500"
                ui.image(item["image"]).classes(thumb_cls).props("fit=contain").on(
                    "click", lambda _e, s=seq: _pin_toggle(selected, s)
                )

        _render_steps(state["steps"])
        task_board.set_content(state["task_board"] or "_等待 TaskDoc…_")

        prompt = state["pending_hitl_prompt"]
        hitl_prompt.set_text(prompt or "")
        hitl_panel.set_visibility(bool(prompt))

    ui.timer(refresh_seconds, render)
    ui.timer(2.0, _render_kb)


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
