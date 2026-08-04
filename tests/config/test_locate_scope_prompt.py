"""Scope-semantics completion of the Locate prompt entry (CN/EN parity).

C1: the Locate scope paragraph in ACTION_SCHEMA explains the *spatial*
semantics of scope (search region only, target outside fails), spatial
containment vs semantic relevance (titles are not containers), the
tighter-is-better / larger-container guidance, and the interval form as an
interface lesson (two text anchors bracket a block, calendar example).
Pure documentation — no behavior rules, no validation changes.
"""

from phone_agent.config import prompts_en, prompts_zh

ZH_ACTION_SCHEMA = prompts_zh.ACTION_SCHEMA
EN_ACTION_SCHEMA = prompts_en.ACTION_SCHEMA

# Key sentences that must appear in both languages (paired one-to-one).
CN_EN_PAIRS = [
    # scope 只在区域内搜索、目标不在区域内必失败
    ("目标不在区域内必然失败", "outside the region is guaranteed to fail"),
    ("只在该区域内搜索", "searches only inside that region"),
    # 空间包含 ≠ 语义相关，标题不是容器
    ("空间包含≠语义相关", "spatial containment is not semantic relevance"),
    ("文字标签/标题不是容器", "text labels/titles are not containers"),
    ("2026年10月", "2026年10月"),
    # 松紧原则
    ("搜索区域越紧，定位准确度越高", "Tighter regions give higher accuracy"),
    ("拿不准时选更大的容器", "when unsure, choose a larger container"),
    ("最大≈全屏，合法", "full screen, which is valid"),
    # 区间形态教学（日历例）
    ("当目标位于两个文字锚点之间时", "When the target lies between two text anchors"),
    ("用 start/end 夹出区间", "use start/end to bracket"),
    ("两个月份标题做区间即可圈出整个月块", "two month titles as start/end bracket the whole month block"),
    ("无需知道目标在第几行", "without needing to know which row the target is in"),
]


def test_zh_locate_scope_carries_spatial_semantics() -> None:
    assert "目标不在区域内必然失败" in ZH_ACTION_SCHEMA
    assert "空间包含" in ZH_ACTION_SCHEMA
    assert "空间包含≠语义相关" in ZH_ACTION_SCHEMA
    assert "文字标签/标题不是容器" in ZH_ACTION_SCHEMA
    assert "2026年10月" in ZH_ACTION_SCHEMA


def test_zh_locate_scope_carries_region_guidance() -> None:
    assert "搜索区域越紧，定位准确度越高" in ZH_ACTION_SCHEMA
    assert "拿不准时选更大的容器" in ZH_ACTION_SCHEMA
    assert "最大≈全屏，合法" in ZH_ACTION_SCHEMA


def test_zh_locate_scope_carries_interval_lesson() -> None:
    assert "当目标位于两个文字锚点之间时" in ZH_ACTION_SCHEMA
    assert "用 start/end 夹出区间" in ZH_ACTION_SCHEMA
    assert "两个月份标题做区间即可圈出整个月块" in ZH_ACTION_SCHEMA
    assert "无需知道目标在第几行" in ZH_ACTION_SCHEMA


def test_en_locate_scope_carries_spatial_semantics() -> None:
    assert "outside the region is guaranteed to fail" in EN_ACTION_SCHEMA
    assert "spatially contain" in EN_ACTION_SCHEMA
    assert "spatial containment is not semantic relevance" in EN_ACTION_SCHEMA
    assert "text labels/titles are not containers" in EN_ACTION_SCHEMA
    assert "2026年10月" in EN_ACTION_SCHEMA


def test_en_locate_scope_carries_region_guidance() -> None:
    assert "Tighter regions give higher accuracy" in EN_ACTION_SCHEMA
    assert "when unsure, choose a larger container" in EN_ACTION_SCHEMA
    assert "full screen, which is valid" in EN_ACTION_SCHEMA


def test_en_locate_scope_carries_interval_lesson() -> None:
    assert "When the target lies between two text anchors" in EN_ACTION_SCHEMA
    assert "use start/end to bracket" in EN_ACTION_SCHEMA
    assert "bracket the whole month block" in EN_ACTION_SCHEMA
    assert "without needing to know which row the target is in" in EN_ACTION_SCHEMA


def test_locate_scope_cn_en_key_sentence_parity() -> None:
    """Each CN key sentence has its EN counterpart and vice versa."""
    for cn, en in CN_EN_PAIRS:
        assert cn in ZH_ACTION_SCHEMA, f"missing CN sentence: {cn}"
        assert en in EN_ACTION_SCHEMA, f"missing EN sentence: {en}"


def test_locate_scope_keeps_interface_forms_in_both_langs() -> None:
    assert "scope_mark_id" in ZH_ACTION_SCHEMA
    assert "scope_start_mark_id" in ZH_ACTION_SCHEMA
    assert "scope_end_mark_id" in ZH_ACTION_SCHEMA
    assert "scope_mark_id" in EN_ACTION_SCHEMA
    assert "scope_start_mark_id" in EN_ACTION_SCHEMA
    assert "scope_end_mark_id" in EN_ACTION_SCHEMA
