"""Taxonomy classification + drift-contract tests (ROUND2-D1 §5.1).

Two things are verified:

1. **Classification** — every taxonomy prefix classifies the expected tool
   return into the expected class + report category, order (most-specific-first)
   is honored, and unmatched text is ``unknown``.
2. **Drift contract** — every ``prefix`` (and ``contains``) literal still appears
   verbatim in the v2 source file the rule points at. Tool returns are a *loose*
   contract; if a tool silently rewords its return string this test fails (rather
   than the diagnosis silently misclassifying at runtime).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sourcemap import resolve_repo_root
from taxonomy import (
    CATEGORY_OF,
    RESULT_CLASSES,
    SOURCE_OF,
    UNKNOWN_CLASS,
    category_of,
    classify_result,
)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected_cls", "expected_category"),
    [
        ("OK. tap 登录 at (100,200)\n[OBS] app=x", "success", "success"),
        ("[OBS] app=com.android.settings screen#3\nmarks (5): ...", "observation", "success"),
        ("[OBS] (re-observation failed: ScreenshotError boom)", "obs_capture_failed", "observation"),
        (
            "error: pass only one of target_mark_id or target_description, not both",
            "addressing_conflict",
            "grounding_addressing",
        ),
        (
            "error: one of target_mark_id or target_description is required",
            "addressing_missing",
            "grounding_addressing",
        ),
        ("stale mark: 'ax_9' is no longer on the current screen", "stale_mark", "grounding_addressing"),
        ("ambiguous: 登录; 登陆 — refine the description", "ambiguous_resolve", "grounding_addressing"),
        ("未定位: 找不到该元素（请细化描述）", "locate_no_match", "grounding_addressing"),
        ("定位失败: provider unavailable", "locate_provider_error", "grounding_addressing"),
        ("error: start must be [x, y] in 0-1000 relative coords", "bad_coords", "actuation_arg"),
        ("error: end must be [x, y] in 0-1000 relative coords", "bad_coords", "actuation_arg"),
        ("error: unknown direction 'sideways'; use up|down", "bad_direction", "actuation_arg"),
        ("ambiguous app '微信': 微信, 企业微信 — be more specific", "ambiguous_app", "launch"),
        ("denied: '支付宝' is not launch-authorized", "launch_denied", "launch"),
        ("error: '淘宝' 未安装在这台设备上（com.taobao.taobao），无法启动。", "app_not_installed", "launch"),
        ("error: 未能启动 '微信'（com.tencent.mm）——设备返回启动失败", "launch_failed", "launch"),
        ("unknown app 'zzz': not in registry/inventory — cannot launch", "unknown_app", "launch"),
        ("未写入（输入无效）：item id 缺失", "taskdoc_input_invalid", "taskdoc"),
        ("未写入（校验失败）：至多一个 in_progress 项", "taskdoc_validation_failed", "taskdoc"),
        ("已更新任务板。\n## 目标 ...", "taskdoc_ok", "taskdoc"),
        (
            "error: finish requires non-empty evidence — list what you observed",
            "finish_no_evidence",
            "finish_gate",
        ),
        ("路线仍有未完成项：s2:付款[pending]。请先完成", "finish_blocked_open_items", "finish_gate"),
        ("已记录完成声明", "finish_ok", "finish_gate"),
        ("[ASK_USER] 请问要用哪张卡？", "ask_user", "hitl"),
        ("已请求人工接管: 需要人工输入验证码", "takeover_requested", "hitl"),
    ],
)
def test_classify_prefix(text: str, expected_cls: str, expected_category: str):
    assert classify_result(text) == expected_cls
    assert category_of(expected_cls) == expected_category
    assert CATEGORY_OF[expected_cls] == expected_category


def test_leading_whitespace_is_tolerated():
    # classify_result lstrips: a leading newline/space must not defeat matching.
    assert classify_result("\n  OK. back") == "success"


def test_unknown_and_empty():
    assert classify_result("") == UNKNOWN_CLASS
    assert classify_result("something a tool would never return") == UNKNOWN_CLASS
    assert category_of("nonexistent-class") == UNKNOWN_CLASS


def test_app_not_installed_requires_contains():
    # Bare ``error: `` without the ``未安装`` marker must NOT be
    # classified as app_not_installed (it should fall through to unknown here).
    assert classify_result("error: something unrelated") == UNKNOWN_CLASS


def test_obs_failed_beats_obs_ok_ordering():
    # The failure variant is listed before the plain [OBS] rule; a re-observation
    # failure must classify as obs_capture_failed, not observation.
    assert classify_result("[OBS] (re-observation failed: boom)") == "obs_capture_failed"


# --------------------------------------------------------------------------
# drift contract: every prefix literal still lives in its v2 source file
# --------------------------------------------------------------------------
def _source_text(rel: str) -> str:
    path = resolve_repo_root() / rel
    assert path.exists(), f"taxonomy source file missing: {rel}"
    return path.read_text(encoding="utf-8")


def test_every_prefix_literal_present_in_source():
    """Contract: each rule's prefix (and contains) literal must still appear."""

    # Cache each source file once.
    cache: dict[str, str] = {}
    for rc in RESULT_CLASSES:
        src = cache.setdefault(rc.source, _source_text(rc.source))
        assert rc.prefix in src, (
            f"prefix {rc.prefix!r} (class {rc.cls}) no longer found in {rc.source} "
            "— a tool reworded its return; update taxonomy.py"
        )
        if rc.contains is not None:
            assert rc.contains in src, (
                f"contains {rc.contains!r} (class {rc.cls}) no longer found in "
                f"{rc.source} — update taxonomy.py"
            )


def test_source_of_maps_every_class():
    for rc in RESULT_CLASSES:
        assert SOURCE_OF[rc.cls] == rc.source
