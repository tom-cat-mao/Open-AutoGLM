"""v2 source map: report category -> v2 source files + on-disk line anchors.

Per ``outputs/design-council/ROUND2-D1.md`` §5. Replaces the v1 ``SOURCE_RULES``
(which pointed at the deleted ``phone_agent/graph/*``). Each category maps to the
v2 file(s) that own that behavior so a finding can render a clickable
``path:line`` anchor. ``add_line_numbers`` / ``find_anchors`` resolve real
def/class line numbers from the working tree (kept from the v1 helper — still
correct against any tree).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def resolve_repo_root() -> Path:
    """Resolve the TaskWizard repo root.

    The skill ships under
    ``<repo>/.agents/skills/phone-agent-live-diagnosis/scripts`` so ``parents[4]``
    is the repo root. ``PHONE_AGENT_REPO_ROOT`` overrides for symlinked/copied
    trees (the ``.claude/skills`` symlink resolves here).
    """

    override = os.getenv("PHONE_AGENT_REPO_ROOT")
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_dir():
            return path
    return Path(__file__).resolve().parents[4]


# category -> v2 source rule. severity/title/suggestion/verify drive the report.
V2_SOURCE_RULES: dict[str, dict[str, Any]] = {
    "grounding_addressing": {
        "layer": "grounding",
        "severity": "P0",
        "title": "标记寻址 / 定位失败（stale / ambiguous / no-match）",
        "files": [
            "phone_agent/v2/tools/actuation.py",
            "phone_agent/v2/tools/perception.py",
            "phone_agent/v2/resolver.py",
            "phone_agent/v2/session.py",
        ],
        "suggestion": (
            "marks-first：tap 必须绑定唯一 mark。stale 说明动作前未 read_screen 刷新；"
            "ambiguous 说明描述不唯一——细化描述或改用 target_mark_id；no-match 说明"
            "accessibility + LocateAnything 都未命中，核对 grounding provider 与 max_marks。"
        ),
        "verify": "对 native 页与 WebView/自绘页各跑一次，确认 resolver 三级匹配与 locate fallback 行为。",
    },
    "actuation_arg": {
        "layer": "actuation",
        "severity": "P1",
        "title": "执行工具参数非法（坐标 / 方向）",
        "files": ["phone_agent/v2/tools/actuation.py", "phone_agent/v2/coords.py"],
        "suggestion": "swipe 需要 [x,y] 0-1000 相对坐标；scroll 只接受 up|down|left|right。坐标换算只在工具内做。",
        "verify": "构造越界/缺项坐标与未知方向 case，确认 fail-closed 且不触发设备动作。",
    },
    "launch": {
        "layer": "launch",
        "severity": "P1",
        "title": "应用启动被拒 / 未安装 / 歧义 / 未知",
        "files": [
            "phone_agent/v2/tools/actuation.py",
            "phone_agent/v2/names.py",
            "phone_agent/config/apps.py",
            "phone_agent/config/policy.py",
        ],
        "suggestion": "核对 app registry / launch policy：denied 走安全策略，not_installed/unknown 走清单，ambiguous 需更精确的名字。",
        "verify": "分别用受限 app、未安装 app、歧义名运行 launch_app，确认返回码与提示准确。",
    },
    "resolver": {
        "layer": "resolver",
        "severity": "P1",
        "title": "应用名解析未决（排序候选 / margin）",
        "files": [
            "phone_agent/v2/names.py",
            "phone_agent/v2/tools/actuation.py",
            "phone_agent/v2/middleware/trace.py",
        ],
        "suggestion": (
            "核对 names.py 的 exact/lexical/pinyin/embedding 四路候选、按 package 去重排序、"
            "match_type 类型化决策与 legacy 分支；结合 resolution_attempt trace 检查候选来源、"
            "match_type、decision_basis 和分差。"
        ),
        "verify": (
            "构造弱证据、强证据并列和 legacy 回退应用名，确认 unknown/ambiguous/resolved "
            "回执与 resolution_attempt 的 decision/winner/candidates/match_type 一致。"
        ),
    },
    "deliverable": {
        "layer": "deliverable",
        "severity": "P1",
        "title": "运行绑定产出物写入 / 更新",
        "files": [
            "phone_agent/v2/tools/deliverable.py",
            "phone_agent/v2/capabilities.py",
        ],
        "suggestion": (
            "核对 run_id 派生的唯一 HTML 路径、256 KiB 上限以及 create/update 状态；"
            "既有文件用 update_document，缺失文件先 write_document。"
        ),
        "verify": "分别覆盖首次创建、重复创建、缺失更新、超限和 symlink，确认失败不改变原文档。",
    },
    "finish_gate": {
        "layer": "finish",
        "severity": "P0",
        "title": "完成门（finish gate）：证据缺失 / 路线未闭合",
        "files": [
            "phone_agent/v2/tools/control.py",
            "phone_agent/v2/taskdoc.py",
        ],
        "suggestion": (
            "finish 要求非空 evidence 且 TaskDoc 无 open 项。被 open items 拦截说明路线未闭合——"
            "先完成、标 blocked（带 reason）或用 update_task_doc 修正路线，绝不放宽 gate。"
        ),
        "verify": "构造空 evidence 与仍有 pending 项的 finish，确认均被拒并回到路线。",
    },
    "taskdoc": {
        "layer": "taskdoc",
        "severity": "P2",
        "title": "任务板写入被拒（输入无效 / 校验失败）",
        "files": [
            "phone_agent/v2/tools/taskdoc.py",
            "phone_agent/v2/taskdoc.py",
        ],
        "suggestion": "校验：至多一个 in_progress、路线 ≤15 项、blocked 必带 reason、事实 ≤10 条且每条 ≤120 字。",
        "verify": "构造多 in_progress / 超限 / blocked 缺 reason 的写入，确认不落盘并返回原因。",
    },
    "hitl": {
        "layer": "safety",
        "severity": "P1",
        "title": "人机协同（ask_user / take_over）",
        "files": [
            "phone_agent/v2/tools/control.py",
            "phone_agent/v2/middleware/safety.py",
        ],
        "suggestion": (
            "HITL 硬门只在 safety 中间件。ask_user 走 respond，take_over 必中断。"
            "误触发/漏触发核对 SafetyPolicyRegistry 分类与 SENSITIVE_APP_KEYWORDS。"
        ),
        "verify": "运行敏感 tap、支付类 launch、登录/验证码 case，确认中断与恢复路径正确。",
    },
    "observation": {
        "layer": "observation",
        "severity": "P1",
        "title": "再观测失败（非保护页截图 / 采样异常）",
        "files": [
            "phone_agent/v2/tools/_obs.py",
            "phone_agent/v2/session.py",
            "phone_agent/adb/screenshot.py",
        ],
        "suggestion": "auto_observation 把非 secure 的 ScreenshotError 只记为 note——动作成功但再观测失败会掩盖后续 stale mark，关注连续 obs_capture_failed。",
        "verify": "模拟非 secure 的截图/采样失败，确认 [OBS] (re-observation failed:) 出现且不伪装成新状态。",
    },
    "secure_screenshot": {
        "layer": "observation",
        "severity": "P0",
        "title": "系统保护页 / 黑屏保护阻断截图",
        "files": [
            "phone_agent/v2/tools/_obs.py",
            "phone_agent/v2/session.py",
            "phone_agent/adb/screenshot.py",
        ],
        "suggestion": (
            "secure_screenshot_blocked 是 fail-closed 的受保护页事实：不使用占位黑图，也不沿用旧 mark；"
            "若 accessibility 无可用 mark，登录/支付流程应 take_over。"
        ),
        "verify": (
            "用系统拒绝 screencap 与 RGB maxima <=4 两条路径触发保护，确认回执无 image、批次失效，"
            "关闭 PHONE_AGENT_BLACK_SCREEN_DETECT 时仅绕过像素黑屏检测。"
        ),
    },
    "context": {
        "layer": "context",
        "severity": "P2",
        "title": "上下文卫生（图像剪裁 / TaskDoc 钉入）",
        "files": [
            "phone_agent/v2/middleware/images.py",
            "phone_agent/v2/middleware/taskdoc.py",
        ],
        "suggestion": "历史截图逐轮剪裁（只留最新一张带图消息）；TaskDoc 每轮重钉，压缩免疫。峰值图像消息数应恒为 1。",
        "verify": "多步任务后检查 evidence 的 image_message_count 是否稳定为 1、taskdoc_present 是否每步为真。",
    },
    "visual": {
        "layer": "visual",
        "severity": "P0",
        "title": "视觉回流（工具返回携带截图 image 块）",
        "files": [
            "phone_agent/v2/tools/_obs.py",
            "phone_agent/v2/tools/actuation.py",
            "phone_agent/v2/middleware/images.py",
        ],
        "suggestion": "D2 视觉回流：工具返回应带 [OBS 文本 + 截图 image 块]。若 tool_results_with_image=0，说明截图未随工具返回回流，模型在盲操作。",
        "verify": "跑一次真机任务，确认 evidence 的 tool_observation.image.present 至少一次为真。",
    },
    "model": {
        "layer": "model",
        "severity": "P1",
        "title": "模型调用（延迟 / 错误）",
        "files": ["phone_agent/v2/model.py", "phone_agent/v2/agent.py"],
        "suggestion": "网关在 Cloudflare 后需浏览器式 UA；采样上限按模型强制。高延迟核对 prompt 前缀缓存与图像剪裁。",
        "verify": "对比连续 step 的 model_request.context_chars 是否受控，确认无缓存击穿。",
    },
    "recall": {
        "layer": "memory",
        "severity": "P2",
        "title": "分榜召回 / 增量索引 / 召回指标",
        "files": ["phone_agent/v2/recall.py"],
        "suggestion": (
            "分别核对确定性 App mention 榜与 episode 语义榜；top_k 只约束 episode。"
            "索引漂移查 incremental_upsert/reconcile_index，指标漂移查 schema-v2 显式分母。"
        ),
        "verify": (
            "覆盖 app_alias 不占 episode quota、增量写后可检索、reconcile 清理缺失项，"
            "并核对 hit@1、conditional hit、package precision/recall 的分母。"
        ),
    },
    "capabilities": {
        "layer": "assembly",
        "severity": "P1",
        "title": "能力挂载与释放",
        "files": ["phone_agent/v2/capabilities.py"],
        "suggestion": (
            "核对 middleware/tools/prompt/start-end hooks/CLI 五个挂载缝及 cap_id 所有权；"
            "pending/off 不应用，mode 变化先 release 再 apply。"
        ),
        "verify": "逐能力切换 on/shadow/off，确认顺序稳定、重复 reconcile 幂等且 release 后零残留。",
    },
}


def rule_for(category: str) -> dict[str, Any] | None:
    """Return the source rule for a report category (or None)."""

    return V2_SOURCE_RULES.get(category)


def find_anchors(path: Path) -> list[dict[str, Any]]:
    """Return up to 8 ``{line, symbol}`` def/class anchors from a source file."""

    anchors: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001 - a missing/binary file yields no anchors
        return anchors
    pattern = re.compile(r"^\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for index, line in enumerate(lines, start=1):
        match = pattern.match(line)
        if match:
            anchors.append({"line": index, "symbol": match.group(2)})
        if len(anchors) >= 8:
            break
    return anchors


def add_line_numbers(files: list[str], repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Resolve each relative source path to ``{path, exists, anchors}``."""

    root = repo_root or resolve_repo_root()
    result: list[dict[str, Any]] = []
    for rel in files:
        path = root / rel
        result.append(
            {
                "path": rel,
                "exists": path.exists(),
                "anchors": find_anchors(path) if path.exists() else [],
            }
        )
    return result


__all__ = [
    "resolve_repo_root",
    "V2_SOURCE_RULES",
    "rule_for",
    "find_anchors",
    "add_line_numbers",
]
