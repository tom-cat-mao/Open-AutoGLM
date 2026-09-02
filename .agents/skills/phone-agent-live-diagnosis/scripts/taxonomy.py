"""Result-prefix taxonomy: tool return string -> class -> report category.

Per ``outputs/design-council/ROUND2-D1.md`` §2. The v2 thin-loop tools return a
result *string*; the leading text of that string is a stable (if loose) contract
that tells us what happened. This module is the canonical, ordered classifier.

``RESULT_CLASSES`` is ordered **most-specific first**: :func:`classify_result`
returns the first matching rule's class. Each rule carries the v2 source file +
symbol it originates from so the report can point a finding at real code, and a
contract test (``tests/skill/test_taxonomy.py``) asserts every ``prefix`` literal
still appears in that source file — a tool that silently rewords its return
string breaks the test instead of the diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass

# v2 source files (relative to repo root) the prefixes originate from.
SRC_ACTUATION = "phone_agent/v2/tools/actuation.py"
SRC_PERCEPTION = "phone_agent/v2/tools/perception.py"
SRC_OBS = "phone_agent/v2/tools/_obs.py"
SRC_CONTROL = "phone_agent/v2/tools/control.py"
SRC_TASKDOC = "phone_agent/v2/tools/taskdoc.py"
SRC_DELIVERABLE = "phone_agent/v2/tools/deliverable.py"


@dataclass(frozen=True)
class ResultClass:
    """One classification rule.

    ``prefix``: the leading literal the tool return starts with.
    ``contains``: optional extra literal that must also be present (used to
    disambiguate the several ``error: ...`` returns that share a prefix).
    ``cls``: the machine class name. ``category``: the report bucket.
    ``source``: ``path`` the prefix literal must still appear in (contract test).
    """

    prefix: str
    cls: str
    category: str
    source: str
    contains: str | None = None


# Ordered most-specific first. classify_result returns the first match.
RESULT_CLASSES: tuple[ResultClass, ...] = (
    # --- success / observation ------------------------------------------
    ResultClass(
        "OK. 已创建文档",
        "deliverable_created",
        "deliverable",
        SRC_DELIVERABLE,
    ),
    ResultClass(
        "OK. 已更新文档",
        "deliverable_updated",
        "deliverable",
        SRC_DELIVERABLE,
    ),
    ResultClass("OK. ", "success", "success", SRC_ACTUATION),
    ResultClass(
        "[OBS] 此屏被系统级保护（登录/支付页）。",
        "secure_screenshot_blocked",
        "secure_screenshot",
        SRC_OBS,
    ),
    ResultClass("[OBS] (re-observation failed:", "obs_capture_failed", "observation", SRC_OBS),
    ResultClass("[OBS] app=", "observation", "success", SRC_OBS),
    # --- grounding / addressing -----------------------------------------
    ResultClass(
        "error: pass only one of target_mark_id",
        "addressing_conflict",
        "grounding_addressing",
        SRC_ACTUATION,
    ),
    ResultClass(
        "error: one of target_mark_id or target_description is required",
        "addressing_missing",
        "grounding_addressing",
        SRC_ACTUATION,
    ),
    ResultClass("stale mark:", "stale_mark", "grounding_addressing", SRC_ACTUATION),
    ResultClass("ambiguous:", "ambiguous_resolve", "grounding_addressing", SRC_ACTUATION),
    ResultClass("未定位:", "locate_no_match", "grounding_addressing", SRC_PERCEPTION),
    ResultClass("定位失败:", "locate_provider_error", "grounding_addressing", SRC_PERCEPTION),
    # --- actuation argument errors --------------------------------------
    ResultClass("error: start must be [x, y]", "bad_coords", "actuation_arg", SRC_ACTUATION),
    ResultClass("error: end must be [x, y]", "bad_coords", "actuation_arg", SRC_ACTUATION),
    ResultClass("error: unknown direction", "bad_direction", "actuation_arg", SRC_ACTUATION),
    # --- launch ----------------------------------------------------------
    ResultClass(
        "ambiguous app ",
        "ambiguous_app",
        "launch",
        SRC_ACTUATION,
        contains="rank_score=",
    ),
    ResultClass(
        "ambiguous app ",
        "ambiguous_app",
        "launch",
        SRC_ACTUATION,
        contains="score=",
    ),
    ResultClass("ambiguous app ", "ambiguous_app", "launch", SRC_ACTUATION),
    ResultClass("denied:", "launch_denied", "launch", SRC_ACTUATION),
    ResultClass("error: 未能启动", "launch_failed", "launch", SRC_ACTUATION),
    ResultClass(
        "error: ",
        "app_not_installed",
        "launch",
        SRC_ACTUATION,
        contains="未安装",
    ),
    ResultClass(
        "unknown app ",
        "unknown_app",
        "launch",
        SRC_ACTUATION,
        contains="排序候选：",
    ),
    ResultClass("unknown app ", "unknown_app", "launch", SRC_ACTUATION),
    # --- run-bound deliverable ------------------------------------------
    ResultClass(
        "error: deliverable already exists; use update_document",
        "deliverable_exists",
        "deliverable",
        SRC_DELIVERABLE,
    ),
    ResultClass(
        "error: document was not written (",
        "deliverable_write_failed",
        "deliverable",
        SRC_DELIVERABLE,
    ),
    ResultClass(
        "error: document was not updated (",
        "deliverable_update_failed",
        "deliverable",
        SRC_DELIVERABLE,
    ),
    # --- taskdoc ---------------------------------------------------------
    ResultClass("未写入（输入无效）：", "taskdoc_input_invalid", "taskdoc", SRC_TASKDOC),
    ResultClass("未写入（校验失败）：", "taskdoc_validation_failed", "taskdoc", SRC_TASKDOC),
    ResultClass("已更新任务板。", "taskdoc_ok", "taskdoc", SRC_TASKDOC),
    # --- finish gate -----------------------------------------------------
    ResultClass(
        "error: finish requires non-empty evidence",
        "finish_no_evidence",
        "finish_gate",
        SRC_CONTROL,
    ),
    ResultClass(
        "路线仍有未完成项：",
        "finish_blocked_open_items",
        "finish_gate",
        SRC_CONTROL,
    ),
    ResultClass("已记录完成声明", "finish_ok", "finish_gate", SRC_CONTROL),
    # --- hitl ------------------------------------------------------------
    ResultClass("[ASK_USER] ", "ask_user", "hitl", SRC_CONTROL),
    ResultClass("已请求人工接管:", "takeover_requested", "hitl", SRC_CONTROL),
)

# class -> report category (derived; single source is RESULT_CLASSES).
CATEGORY_OF: dict[str, str] = {rc.cls: rc.category for rc in RESULT_CLASSES}
# class -> v2 source file (for findings / source-map).
SOURCE_OF: dict[str, str] = {rc.cls: rc.source for rc in RESULT_CLASSES}

UNKNOWN_CLASS = "unknown"

# Underlying exception (session/resolver) -> emitted prefix, for the causal chain.
EXCEPTION_PREFIX_MAP: dict[str, str] = {
    "StaleMarkError": "stale mark:",
    "ResolveAmbiguousError": "ambiguous:",
    "LocateAmbiguousError": "ambiguous:",  # actuation path; perception -> 未定位:
    "ScreenshotError": "[OBS] (re-observation failed:",
}


def classify_result(text: str) -> str:
    """Classify a tool return string into a taxonomy class.

    Returns :data:`UNKNOWN_CLASS` for text that matches no rule (a new/renamed
    tool return). Empty text is ``unknown``.
    """

    if not text:
        return UNKNOWN_CLASS
    head = text.lstrip()
    for rc in RESULT_CLASSES:
        if head.startswith(rc.prefix):
            if rc.contains is not None and rc.contains not in head:
                continue
            return rc.cls
    return UNKNOWN_CLASS


def category_of(cls: str) -> str:
    """Report category for a class (``unknown`` -> ``unknown``)."""

    return CATEGORY_OF.get(cls, UNKNOWN_CLASS)


__all__ = [
    "ResultClass",
    "RESULT_CLASSES",
    "CATEGORY_OF",
    "SOURCE_OF",
    "EXCEPTION_PREFIX_MAP",
    "UNKNOWN_CLASS",
    "classify_result",
    "category_of",
]
