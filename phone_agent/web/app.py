"""NiceGUI application for live thin-loop watch and HITL steering (v2).

Console v3: designed dark UI — timeline steps with status nodes, click-to-pin
frames, per-role token bars, memory/capability tab, command-bar header.
"""

from __future__ import annotations

import re
import time
from typing import Any

from nicegui import ui

from phone_agent.v2.config import V2Config, load_project_env
from phone_agent.web.bridge import WebRunBridge

# ---------------------------------------------------------------- design tokens

_ACCENT = "#8b5cf6"
_BG = "#070b14"
_PANEL = "#0d1526"
_PANEL_SOFT = "#111c33"
_BORDER = "rgba(148,163,184,.10)"
_TEXT = "#e2e8f0"
_MUTED = "#64748b"

_CSS = f"""
:root {{ color-scheme: dark; }}
body {{ background: {_BG}; color: {_TEXT};
       font-family: -apple-system, "SF Pro Text", "PingFang SC", "Segoe UI", sans-serif;
       -webkit-font-smoothing: antialiased; }}
.mono {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; }}

/* header */
.tw-header {{ background: rgba(13,21,38,.82); backdrop-filter: blur(12px);
  border-bottom: 1px solid {_BORDER}; }}
.tw-mark {{ width: 26px; height: 26px; border-radius: 8px;
  background: linear-gradient(135deg, #8b5cf6, #6366f1);
  box-shadow: 0 0 14px rgba(139,92,246,.45); }}
.tw-cmd {{ border-radius: 10px; }}
.tw-cmd .q-field__control {{ border-radius: 10px; background: rgba(148,163,184,.06); }}
.tw-cmd.q-field--focused .q-field__control {{ box-shadow: 0 0 0 2px rgba(139,92,246,.5); }}

/* status pill */
.tw-pill {{ display:inline-flex; align-items:center; gap:7px; padding:4px 12px;
  border-radius:999px; font-size:12.5px; font-weight:600;
  border:1px solid {_BORDER}; background:{_PANEL_SOFT}; }}
.tw-dot {{ width:8px; height:8px; border-radius:50%; }}
.tw-dot.live {{ animation: tw-pulse 1.6s ease-in-out infinite; }}
@keyframes tw-pulse {{ 0%,100% {{ opacity:1; box-shadow:0 0 0 0 currentColor; }}
  50% {{ opacity:.55; }} }}

/* panels */
.panel {{ background:{_PANEL}; border:1px solid {_BORDER}; border-radius:14px;
  box-shadow: 0 1px 2px rgba(2,6,23,.4); }}
.section-title {{ font-size:12px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; color:{_MUTED}; }}

/* device bezel */
.phone-frame {{ background: linear-gradient(160deg,#0b1224,#020617);
  border:1px solid rgba(148,163,184,.16); border-radius:26px; padding:12px;
  box-shadow: 0 18px 40px -18px rgba(2,6,23,.9), inset 0 1px 0 rgba(255,255,255,.04); }}
.phone-frame img {{ border-radius:16px; display:block; }}
.filmstrip {{ scroll-snap-type:x mandatory; scrollbar-width:thin; }}
.thumb {{ scroll-snap-align:start; cursor:pointer; border-radius:8px;
  border:2px solid transparent; opacity:.55; transition:all .15s ease; }}
.thumb:hover {{ opacity:.9; }}
.thumb.sel {{ border-color:{_ACCENT}; opacity:1; }}

/* timeline */
.tl {{ position:relative; }}
.tl-item {{ position:relative; padding-left:46px; padding-bottom:10px; }}
.tl-item::before {{ content:''; position:absolute; left:16px; top:38px; bottom:-2px;
  width:2px; background:{_BORDER}; }}
.tl-item:last-child::before {{ display:none; }}
.tl-node {{ position:absolute; left:0; top:6px; width:34px; height:34px;
  border-radius:11px; display:flex; align-items:center; justify-content:center;
  background:{_PANEL_SOFT}; border:1px solid {_BORDER}; }}
.tl-node .q-icon {{ font-size:17px; }}
.tl-item[data-st="running"] .tl-node {{ border-color:{_ACCENT};
  box-shadow:0 0 12px rgba(139,92,246,.35); animation:tw-pulse 1.6s infinite; }}
.tl-item[data-st="success"] .tl-node {{ border-color:rgba(52,211,153,.45); color:#34d399; }}
.tl-item[data-st="error"] .tl-node {{ border-color:rgba(248,113,113,.5); color:#f87171; }}
.tl-item[data-st="warning"] .tl-node {{ border-color:rgba(251,191,36,.5); color:#fbbf24; }}
.tl-card {{ background:{_PANEL_SOFT}; border:1px solid {_BORDER}; border-radius:11px;
  transition:border-color .15s ease; }}
.tl-card:hover {{ border-color:rgba(139,92,246,.4); }}
.tl-card .q-item {{ padding:9px 14px; min-height:0; }}
.tl-card .q-expansion-item__content {{ padding:0; }}
.chip {{ display:inline-flex; align-items:center; gap:4px; padding:2px 9px;
  border-radius:6px; font-size:11.5px; font-weight:600;
  background:rgba(139,92,246,.13); color:#c4b5fd; border:1px solid rgba(139,92,246,.25); }}
.chip.grey {{ background:rgba(148,163,184,.09); color:#94a3b8;
  border-color:rgba(148,163,184,.18); }}
.latbar {{ height:4px; border-radius:2px; background:rgba(148,163,184,.12);
  overflow:hidden; }}
.latbar > div {{ height:100%; border-radius:2px; }}

/* tabs */
.tw-tabs .q-tab {{ text-transform:none; font-weight:600; color:{_MUTED}; }}
.tw-tabs .q-tab--active {{ color:{_TEXT}; }}
.q-tab-panels {{ background:transparent; }}

/* tables */
.q-table__container, .q-table {{ background: transparent; box-shadow: none;
  border: none; }}
.q-table__card {{ background: transparent; box-shadow: none; }}
.q-table th {{ font-size:11px; letter-spacing:.07em; text-transform:uppercase;
  color:{_MUTED}; border-bottom:1px solid {_BORDER}; background:transparent; }}
.q-table td {{ border-bottom:1px solid rgba(148,163,184,.06); font-size:13px;
  background:transparent; }}
.q-table tbody tr:hover {{ background:rgba(148,163,184,.05); }}
.q-table__bottom {{ border-top:1px solid {_BORDER}; color:{_MUTED}; }}

/* task board */
.board-goal {{ background:{_PANEL_SOFT}; border:1px solid {_BORDER};
  border-radius:11px; padding:12px 16px; }}
.board-item {{ display:flex; gap:10px; padding:7px 4px; align-items:flex-start;
  border-bottom:1px solid rgba(148,163,184,.06); }}
.board-flow {{ font-family:ui-monospace,Menlo,monospace; font-size:11.5px;
  color:{_MUTED}; padding:3px 0; border-bottom:1px dashed rgba(148,163,184,.08);
  white-space:pre-wrap; word-break:break-all; }}

/* stat cards */
.stat-card {{ background:{_PANEL_SOFT}; border:1px solid {_BORDER};
  border-radius:12px; padding:12px 16px; min-width:120px; }}
.stat-num {{ font-size:22px; font-weight:700; font-family:ui-monospace,Menlo,monospace; }}

/* empty states */
.empty {{ display:flex; flex-direction:column; align-items:center; gap:8px;
  padding:36px 0; color:{_MUTED}; }}

/* hitl banner */
.hitl {{ border:1px solid rgba(251,191,36,.4); background:rgba(251,191,36,.07);
  border-radius:14px; }}

/* drawer */
.q-drawer {{ background:{_PANEL}; border-left:1px solid {_BORDER}; }}
.q-drawer .q-field .q-field__control {{ background:rgba(148,163,184,.06);
  border-radius:9px; }}
.step-detail {{ white-space:pre-wrap; word-break:break-all; }}
"""

_STATUS_META = {
    "idle": ("待命", "#64748b", False),
    "starting": ("启动中", "#38bdf8", True),
    "running": ("运行中", _ACCENT, True),
    "waiting_hitl": ("等待人工", "#fbbf24", True),
    "succeeded": ("已完成", "#34d399", False),
    "failed": ("未完成", "#f87171", False),
    "takeover": ("已接管/停止", "#fbbf24", False),
    "budget_exhausted": ("预算耗尽", "#f87171", False),
    "loop_fuse": ("保险丝触发", "#f87171", False),
    "error": ("运行错误", "#f87171", False),
}

_STEP_META = {
    "running": ("执行中", _ACCENT),
    "success": ("成功", "#34d399"),
    "warning": ("预警", "#fbbf24"),
    "error": ("失败", "#f87171"),
}

_TOOL_ICON = {
    "tap": "touch_app",
    "long_press": "touch_app",
    "type_text": "keyboard",
    "launch_app": "rocket_launch",
    "locate": "my_location",
    "swipe": "swipe",
    "scroll": "unfold_more",
    "wait": "hourglass_empty",
    "finish": "flag",
    "update_task_doc": "edit_note",
    "ask_user": "help_outline",
    "take_over": "pan_tool",
    "read_screen": "visibility",
    "press_key": "smart_button",
    "home": "home",
    "back": "arrow_back",
}

_USAGE_ROLE_TEXT = {
    "actor": "主模型",
    "compact": "压缩",
    "verifier": "验收器",
    "reviewer": "安全复核",
    "distill": "蒸馏",
}

_VERIFIER_TEXT = {"pass": "通过", "fail": "未通过", "skipped": "跳过"}

_BOARD_ITEM_RE = re.compile(r"^- \[(?P<status>\w+)\] (?P<ident>\S+): (?P<rest>.*)$")
_BOARD_NOTE_RE = re.compile(r"（(?:证据|原因)：(?P<note>.*)）$|\((?:evidence|reason): (?P<note_en>.*)\)$")
_BOARD_SECTIONS = {
    "goal": ("目标", "Goal"),
    "items": ("路线", "Plan"),
    "flow": ("流程线", "Flow"),
}


def _parse_board(text: str) -> dict[str, Any]:
    """Parse the pinned TaskDoc block into structured sections for the 任务板 tab.

    Input format (see ``taskdoc.render`` + ``TaskDocMiddleware._flow_block``):
    ``## 目标/Goal`` → ``base: …`` + ``- amendment``; ``## 路线/Plan`` →
    ``- [status] id: content（证据/原因：…）``; ``## 流程线/Flow…`` → ``#N …`` lines.
    Unknown shapes land in ``raw`` so nothing is ever lost.
    """

    out: dict[str, Any] = {"goal": "", "amendments": [], "items": [], "flow": [], "raw": ""}
    if not text or not text.strip():
        return out
    lines = [ln.rstrip() for ln in str(text).splitlines()]
    section: str | None = None
    in_amendments = False
    for ln in lines:
        stripped = ln.strip()
        if not stripped or stripped == "[TASK_DOC]":
            continue
        header = re.match(r"^##\s*(.+)$", stripped)
        if header:
            title = header.group(1)
            section = None
            for key, names in _BOARD_SECTIONS.items():
                if any(title.startswith(name) for name in names):
                    section = key
                    break
            in_amendments = False
            if section is None:
                out["raw"] += ln + "\n"
            continue
        if section == "goal":
            if stripped.startswith(("base:", "Base:")):
                out["goal"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith(("补充", "Amendments")):
                in_amendments = True
            elif in_amendments and stripped.startswith("- "):
                out["amendments"].append(stripped[2:])
            else:
                out["raw"] += ln + "\n"
        elif section == "items":
            match = _BOARD_ITEM_RE.match(stripped)
            if match:
                rest = match.group("rest")
                note = ""
                note_match = _BOARD_NOTE_RE.search(rest)
                if note_match:
                    note = note_match.group("note") or note_match.group("note_en") or ""
                    rest = rest[: note_match.start()].rstrip()
                out["items"].append(
                    {
                        "status": match.group("status"),
                        "id": match.group("ident"),
                        "content": rest,
                        "note": note,
                    }
                )
            else:
                out["raw"] += ln + "\n"
        elif section == "flow":
            if stripped.startswith("#"):
                out["flow"].append(stripped)
            else:
                out["raw"] += ln + "\n"
        else:
            out["raw"] += ln + "\n"
    out["raw"] = out["raw"].strip()
    return out

_KIND_TEXT = {"device": "设备", "alias": "别名", "learned": "学习", "user": "用户"}

_CAP_STATE_STYLE = {
    "active": ("#34d399", "生效"),
    "shadow": (_ACCENT, "影子"),
    "off": ("#64748b", "关闭"),
    "pending": ("#fbbf24", "待岗"),
}


def _display(value: Any, fallback: str = "—") -> str:
    text = str(value or "").strip()
    return text or fallback


def _choose_frame(screens: list[dict], selected: dict) -> dict | None:
    """Main-frame choice: follow the newest frame unless the user pinned one."""

    latest = screens[-1] if screens else None
    if not selected["pinned"]:
        selected["seq"] = latest.get("seq") if latest else None
        return latest
    if latest is not None and selected["seq"] not in {s.get("seq") for s in screens}:
        selected["pinned"] = False
        selected["seq"] = latest.get("seq")
    return next((s for s in screens if s.get("seq") == selected["seq"]), latest)


def _pin_toggle(selected: dict, seq: Any) -> None:
    """Click a thumbnail/step: pin it; click the pinned one again: follow latest."""

    if selected["pinned"] and selected["seq"] == seq:
        selected.update(seq=None, pinned=False)
    else:
        selected.update(seq=seq, pinned=True)


def _mask_url(url: str) -> str:
    text = str(url or "")
    if len(text) <= 28:
        return text
    return text[:18] + "…" + text[-8:]


def _tokens_fmt(n: int | float) -> str:
    n = int(n)
    return f"{n / 1000:.1f}k" if n >= 10000 else f"{n:,}"


def _stat_card(label: str, accent: str = _ACCENT) -> ui.label:
    with ui.element("div").classes("stat-card"):
        ui.label(label).classes("section-title")
        value = ui.label("—").classes("stat-num").style(f"color:{accent}")
    return value


class _ConfigPanel:
    """Right-drawer config: per-run overrides (never written back to .env)."""

    def __init__(self, config: V2Config) -> None:
        self._config = config
        with ui.drawer("right", bordered=True, value=False).classes(
            "p-5 gap-3 w-80"
        ) as drawer:
            ui.label("运行配置").classes("text-lg font-bold")
            ui.label("本次生效，不写回 .env").classes("text-xs").style(
                f"color:{_MUTED}; margin-top:-8px"
            )
            self.device_id = ui.input(
                "设备 serial（留空=自动）", value=config.device_id or ""
            ).props("outlined dense dark").classes("w-full")
            self.model_name = ui.input("模型", value=config.model_name).props(
                "outlined dense dark"
            ).classes("w-full")
            self.safety_mode = ui.select(
                ["wary", "off", "hard", "reviewer"],
                value=getattr(config, "safety_mode", "wary"),
                label="安全模式",
            ).props("outlined dense dark").classes("w-full")
            self.lang = ui.select(
                ["cn", "en"], value=getattr(config, "lang", "cn"), label="语言"
            ).props("outlined dense dark").classes("w-full")
            self.max_steps = ui.number(
                "最大步数（保险丝）", value=getattr(config, "max_model_calls", 100), min=1
            ).props("outlined dense dark").classes("w-full")
            self.token_budget = ui.number(
                "Token 预算", value=getattr(config, "token_budget", 1_000_000), min=1000
            ).props("outlined dense dark").classes("w-full")
            self.grounding_provider = ui.select(
                ["hybrid", "accessibility", "locateanything"],
                value=getattr(config, "grounding_provider", "hybrid"),
                label="Grounding",
            ).props("outlined dense dark").classes("w-full")
            self.app_kb = ui.switch(
                "App-KB 记忆", value=bool(getattr(config, "app_kb_enabled", True))
            ).props("dark")
            ui.separator()
            ui.label("当前生效（只读）").classes("section-title")
            ui.label(f"网关 {_mask_url(getattr(config, 'base_url', ''))}").classes(
                "text-xs mono"
            ).style(f"color:{_MUTED}")
            ui.label(
                f"图片保留 {getattr(config, 'image_keep', 2)} 张 · "
                f"compact {getattr(config, 'compact_warn_ratio', 0.75)}/"
                f"{getattr(config, 'compact_trigger_ratio', 0.92)}"
            ).classes("text-xs").style(f"color:{_MUTED}")
            ui.button("恢复默认", icon="restart_alt", on_click=self.reset).props(
                "flat dense no-caps"
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
            "device_id": str(self.device_id.value or "") or None,
            "model_name": str(self.model_name.value or "") or None,
            "safety_mode": self.safety_mode.value,
            "lang": self.lang.value,
            "max_model_calls": int(self.max_steps.value or 100),
            "token_budget": int(self.token_budget.value or 1_000_000),
            "grounding_provider": self.grounding_provider.value,
            "app_kb_enabled": bool(self.app_kb.value),
        }


# ------------------------------------------------------------------ main UI


def create_ui(
    bridge: WebRunBridge,
    *,
    config: V2Config,
    refresh_seconds: float = 0.5,
) -> None:
    """Build the single-page UI and attach it to ``bridge``."""

    ui.colors(primary=_ACCENT, positive="#34d399", negative="#f87171")
    ui.dark_mode().enable()
    ui.add_css(_CSS)

    panel = _ConfigPanel(config)

    # --- header ---------------------------------------------------------
    with ui.header().classes("tw-header items-center gap-3 px-5 py-2.5"):
        ui.element("div").classes("tw-mark")
        with ui.column().classes("gap-0"):
            ui.label("TaskWizard").classes("text-base font-bold leading-5")
            ui.label("thin-loop 实时控制台").classes("text-[11px] leading-4").style(
                f"color:{_MUTED}"
            )
        task_input = (
            ui.input(placeholder="输入手机任务，回车运行 — 例如：打开设置并进入 WLAN")
            .props("outlined dense clearable dark")
            .classes("grow min-w-64 tw-cmd")
        )
        start_button = ui.button("运行", icon="play_arrow").props(
            "unelevated no-caps"
        ).style(
            f"background:{_ACCENT}; box-shadow:0 4px 16px -4px rgba(139,92,246,.5);"
            " border-radius:10px; font-weight:600"
        )
        stop_button = ui.button("停止", icon="stop").props("flat no-caps text-negative")
        with ui.element("div").classes("tw-pill") as status_pill:
            status_dot = ui.element("span").classes("tw-dot").style(
                "background:#64748b; color:#64748b"
            )
            status_text = ui.label("待命").classes("text-[12.5px]")
        tokens_chip = ui.label("").classes("mono text-xs").style(f"color:{_MUTED}")
        ui.button(icon="tune", on_click=panel.drawer.toggle).props(
            "flat round dense"
        ).style(f"color:{_MUTED}")

    # --- main stage ------------------------------------------------------
    with ui.column().classes("w-full max-w-[1460px] mx-auto p-5 gap-5"):
        with ui.row().classes("w-full items-stretch gap-5 flex-wrap lg:flex-nowrap"):

            # device rail
            with ui.element("div").classes("panel w-full lg:w-[370px] p-4"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("设备").classes("section-title")
                    screen_meta = ui.label("—").classes("mono text-[11px]").style(
                        f"color:{_MUTED}"
                    )
                with ui.element("div").classes(
                    "phone-frame w-full flex justify-center items-start mt-3"
                ) as phone_frame:
                    screen_image = ui.element("img").style(
                        "max-height: 62vh; max-width: 100%; width: auto;"
                        " height: auto; margin: 0 auto;"
                    )
                phone_frame.set_visibility(False)
                with ui.element("div").classes("empty") as no_screen:
                    ui.icon("smartphone").style("font-size:34px")
                    ui.label("运行后这里显示实时画面").classes("text-xs")
                thumbs = ui.row().classes(
                    "filmstrip w-full gap-2 mt-3 overflow-x-auto flex-nowrap pb-1"
                )

            # stage
            with ui.element("div").classes("panel grow p-4").style(
                "min-height: 72vh"
            ):
                with ui.tabs().classes("w-full tw-tabs") as tabs:
                    tab_steps = ui.tab("steps", label="步骤")
                    tab_board = ui.tab("board", label="任务板")
                    tab_kb = ui.tab("appkb", label="应用库")
                    tab_memory = ui.tab("memory", label="记忆")
                ui.separator().style(f"background:{_BORDER}")
                with ui.tab_panels(tabs, value=tab_steps).classes("w-full"):

                    with ui.tab_panel(tab_steps).classes("p-0 pt-3"):
                        with ui.row().classes(
                            "w-full items-center justify-between mb-2"
                        ):
                            step_count = ui.label("0 步").classes(
                                "mono text-xs font-bold"
                            ).style(f"color:{_MUTED}")
                            usage_total = ui.label("").classes("mono text-xs").style(
                                f"color:{_MUTED}"
                            )
                        usage_bars = ui.row().classes(
                            "w-full gap-2 items-center flex-wrap mb-2"
                        )
                        timeline = ui.column().classes(
                            "tl w-full gap-0 max-h-[52vh] overflow-y-auto pr-1"
                        )

                    with ui.tab_panel(tab_board).classes("p-0 pt-3"):
                        board_box = ui.column().classes(
                            "w-full gap-3 max-h-[52vh] overflow-y-auto pr-1"
                        )

                    with ui.tab_panel(tab_kb).classes("p-0 pt-3"):
                        with ui.row().classes(
                            "w-full items-center justify-between mb-2"
                        ):
                            kb_count = ui.label("0 条").classes(
                                "mono text-xs font-bold"
                            ).style(f"color:{_MUTED}")
                            dream_button = ui.button(
                                "立即整理", icon="auto_fix_high"
                            ).props("outline dense no-caps")
                        kb_table = ui.table(
                            columns=[
                                {"name": "label", "label": "名称", "field": "label"},
                                {"name": "package", "label": "包名", "field": "package"},
                                {"name": "kind", "label": "类型", "field": "kind"},
                                {
                                    "name": "success_count",
                                    "label": "成功",
                                    "field": "success_count",
                                },
                                {"name": "stale", "label": "状态", "field": "stale"},
                            ],
                            rows=[],
                            row_key="package",
                        ).classes("w-full max-h-[42vh]")

                    with ui.tab_panel(tab_memory).classes("p-0 pt-3"):
                        ui.label("能力状态").classes("section-title")
                        caps_row = ui.row().classes("w-full gap-2 flex-wrap mt-1 mb-3")
                        with ui.row().classes("w-full gap-3 flex-wrap"):
                            stat_eps = _stat_card("任务档案", _ACCENT)
                            stat_evals = _stat_card("回想评估", "#38bdf8")
                            stat_hit = _stat_card("命中率", "#34d399")
                            stat_false = _stat_card("误命中率", "#fbbf24")
                        memory_table = ui.table(
                            columns=[
                                {"name": "time", "label": "时间", "field": "time"},
                                {"name": "goal", "label": "任务", "field": "goal"},
                                {
                                    "name": "outcome",
                                    "label": "结果",
                                    "field": "outcome",
                                },
                                {"name": "steps", "label": "步数", "field": "steps"},
                                {"name": "tokens", "label": "Token", "field": "tokens"},
                                {
                                    "name": "verifier",
                                    "label": "验收",
                                    "field": "verifier",
                                },
                            ],
                            rows=[],
                            row_key="time",
                        ).classes("w-full max-h-[36vh] mt-3")
                        ui.label(
                            "回想处于 shadow 模式：只观测不注入；命中率由每次运行的实际行为自动对答案。"
                        ).classes("text-xs mt-2").style(f"color:{_MUTED}")

        # HITL banner
        with ui.element("div").classes("hitl w-full p-4") as hitl_panel:
            with ui.row().classes("items-center gap-2 mb-2"):
                ui.icon("front_hand", color="warning").style("font-size:20px")
                ui.label("需要人工决定").classes("text-base font-bold")
            hitl_prompt = ui.label().classes("text-sm whitespace-pre-wrap")
            with ui.row().classes("w-full gap-2 mt-3 items-center"):
                hitl_answer = (
                    ui.input(placeholder="也可以输入文本回答")
                    .props("outlined dense clearable dark")
                    .classes("grow tw-cmd")
                )
                approve_button = ui.button("同意", icon="check").props(
                    "unelevated no-caps color=positive"
                )
                reject_button = ui.button("拒绝", icon="close").props(
                    "flat no-caps text-negative"
                )
                answer_button = ui.button("提交", icon="send").props(
                    "outline no-caps"
                )
        hitl_panel.set_visibility(False)

    last_signature: tuple[Any, ...] | None = None
    selected: dict[str, Any] = {"seq": None, "pinned": False}
    last_run_id: dict[str, Any] = {"id": None}

    # --- actions ---------------------------------------------------------
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
            bridge.start(str(task_input.value or ""), overrides=panel.overrides())
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

    # --- renderers -------------------------------------------------------
    def _render_steps(steps: list[dict[str, Any]]) -> None:
        timeline.clear()
        with timeline:
            if not steps:
                with ui.element("div").classes("empty"):
                    ui.icon("route").style("font-size:30px")
                    ui.label("尚无执行步骤").classes("text-xs")
                return
            for step in steps:
                status_key = step["status"]
                _, color = _STEP_META.get(status_key, ("", _MUTED))
                is_closing = not step.get("tool") and not step.get("result")
                icon = _TOOL_ICON.get(step.get("tool", ""), "bolt")
                if is_closing:
                    head_text = f"#{step['step']} 模型收尾"
                    icon = "check_circle"
                    status_key = "success"
                else:
                    head_text = _display(step["intent"], "（未声明意图）")
                with ui.element("div").classes("tl-item w-full").props(
                    f'data-st="{status_key}"'
                ):
                    with ui.element("div").classes("tl-node"):
                        ui.icon(icon).style(f"font-size:17px; color:{color}")
                    with ui.expansion().classes("tl-card w-full").props("dense") as ex:
                        with ex.add_slot("header"):
                            with ui.row().classes(
                                "w-full items-center gap-2 no-wrap"
                            ):
                                ui.label(f"#{step['step']}").classes(
                                    "mono text-[11px]"
                                ).style(f"color:{_MUTED}")
                                ui.label(head_text).classes(
                                    "text-[13.5px] font-semibold ellipsis"
                                ).style("max-width:46%")
                                if step.get("tool"):
                                    ui.label(step["tool"]).classes("chip mono")
                                if step.get("target"):
                                    ui.label(
                                        str(step["target"])[:26]
                                    ).classes("chip grey mono ellipsis")
                                ui.space()
                                lat_total = step.get("model_latency_ms", 0) + step.get(
                                    "tool_latency_ms", 0
                                )
                                if lat_total:
                                    ui.label(f"{lat_total / 1000:.1f}s").classes(
                                        "mono text-[11px]"
                                    ).style(f"color:{_MUTED}")
                        with ui.column().classes("gap-2 px-4 py-3"):
                            badge_text = (
                                "收尾"
                                if is_closing
                                else _STEP_META.get(step["status"], ("",))[0]
                            )
                            if badge_text:
                                ui.label(badge_text).classes("chip").style(
                                    f"color:{color}; border-color:{color}55;"
                                    f" background:{color}18"
                                )
                            if step.get("args"):
                                ui.label("参数").classes("section-title")
                                ui.label(str(step["args"])).classes(
                                    "mono text-[11.5px] step-detail"
                                ).style(f"color:{_MUTED}")
                            if step.get("result"):
                                ui.label("结果").classes("section-title")
                                ui.label(str(step["result"])).classes(
                                    "text-[13px] step-detail"
                                )
                            model_lat = step.get("model_latency_ms", 0)
                            tool_lat = step.get("tool_latency_ms", 0)
                            total = model_lat + tool_lat
                            if total:
                                with ui.element("div").classes("latbar w-full"):
                                    ui.element("div").style(
                                        f"width:{model_lat / total * 100:.0f}%;"
                                        f" background:{_ACCENT}"
                                    )
                                ui.label(
                                    f"模型 {model_lat}ms · 工具 {tool_lat}ms"
                                ).classes("mono text-[10.5px]").style(
                                    f"color:{_MUTED}"
                                )
                    if step["status"] == "running" and not is_closing:
                        ex.set_value(True)
                    if step.get("screen_seq") is not None:
                        ex.on(
                            "click",
                            lambda _e, s=step["screen_seq"]: _pin_toggle(selected, s),
                        )

    _BOARD_STATUS_ICON = {
        "completed": ("check_circle", "#34d399"),
        "in_progress": ("radio_button_checked", _ACCENT),
        "pending": ("radio_button_unchecked", "#64748b"),
        "blocked": ("block", "#f87171"),
    }

    def _render_board(text: str) -> None:
        """任务板：结构化渲染 TaskDoc——目标卡 + 路线检查单 + 紧凑流程线。"""

        board_box.clear()
        parsed = _parse_board(text)
        with board_box:
            if not any(
                [parsed["goal"], parsed["amendments"], parsed["items"], parsed["flow"]]
            ):
                with ui.element("div").classes("empty"):
                    ui.icon("assignment").style("font-size:30px")
                    ui.label("等待任务板…").classes("text-xs")
                return
            if parsed["goal"] or parsed["amendments"]:
                ui.label("目标").classes("section-title")
                with ui.element("div").classes("board-goal w-full"):
                    if parsed["goal"]:
                        ui.label(parsed["goal"]).classes("text-[14px] font-semibold")
                    for amendment in parsed["amendments"]:
                        ui.label(f"· {amendment}").classes("text-xs mt-1").style(
                            f"color:{_MUTED}"
                        )
            if parsed["items"]:
                ui.label("路线").classes("section-title")
                with ui.column().classes("w-full gap-0"):
                    for item in parsed["items"]:
                        icon, color = _BOARD_STATUS_ICON.get(
                            item["status"], ("radio_button_unchecked", "#64748b")
                        )
                        with ui.row().classes("board-item w-full no-wrap"):
                            ui.icon(icon).style(f"font-size:17px; color:{color}")
                            ui.label(item["id"]).classes("mono text-[11px]").style(
                                f"color:{_MUTED}; min-width:18px"
                            )
                            with ui.column().classes("gap-0 grow"):
                                ui.label(item["content"]).classes("text-[13px]")
                                if item["note"]:
                                    ui.label(item["note"]).classes(
                                        "text-[11.5px]"
                                    ).style(f"color:{_MUTED}")
            if parsed["flow"]:
                ui.label("流程线").classes("section-title mt-1")
                with ui.column().classes("w-full gap-0"):
                    for line in parsed["flow"]:
                        ui.label(line).classes("board-flow")
            if parsed["raw"]:
                ui.label(parsed["raw"]).classes(
                    "text-xs whitespace-pre-wrap"
                ).style(f"color:{_MUTED}")

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

    def _render_caps(caps: list[dict[str, Any]]) -> None:
        caps_row.clear()
        with caps_row:
            if not caps:
                ui.label("首次运行后显示能力状态").classes("text-xs").style(
                    f"color:{_MUTED}"
                )
                return
            for cap in caps:
                state_key = str(cap.get("state", ""))
                color, text = _CAP_STATE_STYLE.get(state_key, ("#64748b", state_key))
                missing = cap.get("missing_deps") or []
                label = f"{cap.get('title', cap.get('cap_id'))} · {text}"
                if missing:
                    label += f"（缺 {', '.join(missing)}）"
                with ui.element("span").classes("chip grey"):
                    ui.element("span").classes("tw-dot").style(
                        f"background:{color}; color:{color}"
                    ).classes("tw-dot" + (" live" if state_key == "shadow" else ""))
                    ui.label(label)

    def _render_memory() -> None:
        snapshot = bridge.memory_snapshot()
        stats = snapshot.get("recall_stats") or {}
        evaluations = int(stats.get("evaluations", 0) or 0)
        hits = int(stats.get("hits", 0) or 0)
        false_hits = int(stats.get("false_hits", 0) or 0)
        stat_evals.set_text(str(evaluations))
        stat_hit.set_text(f"{hits / evaluations:.0%}" if evaluations else "—")
        stat_false.set_text(f"{false_hits / evaluations:.0%}" if evaluations else "—")
        episodes = snapshot.get("episodes") or []
        stat_eps.set_text(str(len(episodes)))
        rows = []
        for episode in episodes:
            ts = episode.get("ts_start")
            time_text = (
                time.strftime("%m-%d %H:%M", time.localtime(float(ts))) if ts else "—"
            )
            goal = str(episode.get("goal_text", ""))
            rows.append(
                {
                    "time": time_text,
                    "goal": goal[:28] + ("…" if len(goal) > 28 else ""),
                    "outcome": ("✓ " if episode.get("success") else "✗ ")
                    + str(episode.get("reason", "")),
                    "steps": episode.get("steps", 0),
                    "tokens": _tokens_fmt(episode.get("tokens_total", 0)),
                    "verifier": _VERIFIER_TEXT.get(
                        str(episode.get("verifier", "")), "—"
                    ),
                }
            )
        memory_table.rows = rows
        memory_table.update()

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
                    step.get("screen_seq"),
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
            tuple(
                (cap.get("cap_id"), cap.get("state"))
                for cap in (state.get("capabilities") or [])
            ),
        )
        if signature == last_signature:
            return
        last_signature = signature

        status = state["status"]
        text, color, live = _STATUS_META.get(status, (status, "#64748b", False))
        status_text.set_text(text)
        status_dot.style(f"background:{color}; color:{color}")
        status_dot.classes("tw-dot" + (" live" if live else ""), remove="tw-dot")
        status_pill.style(f"border-color:{color}44")
        running = status in {"starting", "running", "waiting_hitl"}
        start_button.set_enabled(not running)
        stop_button.set_enabled(bool(running))
        step_count.set_text(f"{len(state['steps'])} 步")

        usage = state["usage"] or {}
        total_usage = sum(usage.values())
        usage_total.set_text(f"共 {_tokens_fmt(total_usage)}" if usage else "")
        tokens_chip.set_text(f"⏱ {_tokens_fmt(state['tokens'])} tokens")
        usage_bars.clear()
        if usage:
            with usage_bars:
                for role, tokens in sorted(
                    usage.items(), key=lambda pair: -pair[1]
                ):
                    ui.label(_USAGE_ROLE_TEXT.get(role, role)).classes(
                        "chip grey"
                    ).style("font-size:10.5px; padding:1px 7px")
                    with ui.element("div").classes("latbar").style("width:64px"):
                        ui.element("div").style(
                            f"width:{tokens / total_usage * 100:.0f}%;"
                            f" background:{_ACCENT}"
                        )
                    ui.label(_tokens_fmt(tokens)).classes("mono text-[10.5px]").style(
                        f"color:{_MUTED}"
                    )

        # --- device frame + filmstrip ------------------------------------
        screens = state["screens"]
        latest = screens[-1] if screens else None
        if state["run_id"] != last_run_id["id"]:
            last_run_id["id"] = state["run_id"]
            selected["seq"] = None
            selected["pinned"] = False
        shown = _choose_frame(screens, selected)
        if shown and shown.get("image"):
            screen_image.props(f'src="{shown["image"]}"')
            phone_frame.set_visibility(True)
            no_screen.set_visibility(False)
        else:
            phone_frame.set_visibility(False)
            no_screen.set_visibility(True)
        screen_meta.set_text(
            f"{_display(state['current_app'])} · #{_display(state['screen_seq'])}"
            + (" · 已钉住历史帧" if selected["pinned"] and shown is not latest else "")
        )

        thumbs.clear()
        with thumbs:
            for item in screens[-12:]:
                seq = item.get("seq")
                cls = "thumb w-14"
                if selected["pinned"] and seq == selected["seq"]:
                    cls += " sel"
                ui.image(item["image"]).classes(cls).props("fit=contain").on(
                    "click", lambda _e, s=seq: _pin_toggle(selected, s)
                )

        _render_steps(state["steps"])
        _render_board(state["task_board"] or "")

        prompt = state["pending_hitl_prompt"]
        hitl_prompt.set_text(prompt or "")
        hitl_panel.set_visibility(bool(prompt))

        _render_caps(state.get("capabilities") or [])

    ui.timer(refresh_seconds, render)
    ui.timer(2.0, _render_kb)
    ui.timer(2.0, _render_memory)


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
        title="TaskWizard 实时控制台",
        show=False,
        reload=False,
    )


__all__ = ["create_ui", "run"]
