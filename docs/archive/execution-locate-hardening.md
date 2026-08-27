# 执行文档：locate 硬化（H1-H5）

> 基线：934 passed（commit 15d41a5）。来源：20260803 携程真机运行（25 步手动终止）
> 双方分析结论。核心教训：**mark 绑定是"由构造成立的不变量"，不是"像素指纹门禁"**——
> 两帧 hash 精确比较在动态页面无解（视频页/状态栏实时信息必灭），本次 locate 10/10
> 失败全死于这道门。全程测试绿，不 commit/push，CN/EN 同步。

## H1：locate 原子化 observe+query（删除两帧 hash 门禁）

现状：`tools/locate.py` 在 execute 时重新截图并比较 `captured_hash != binding.raw_screenshot_hash`
→ 拒绝（screen_changed）。plan 截图与 execute 截图隔模型推理 5-30s，动态页面必不匹配。

改为**原子化**（不再有比较，不再有 screen_changed 失败码）：
1. execute 时当场截图 F；
2. 用 F 构造/更新 ScreenBinding（hash_F）；
3. 把 F 喂给 LA → 得框；
4. mark 绑定 hash_F（`with_extra_marks` 语义不变：保留 screen_id 只重算 mark_set_version）；
5. mark 永远绑在 LA 实际看到的帧上（P0#9 由构造成立，而非比较成立）。

注意：
- 若当前 registry 的 observation 截图与 F 不同（屏幕变了），registry 里原有 marks 可能已陈旧——
  不拒绝 locate，但 trace 记录 `observation_drifted=True` 供诊断；
- 截图失败走既有 screenshot_failure 路径；
- 删除/改造 screen_changed 相关测试为原子化语义（截图 F 绑定、drift 标记、单框/多框/0 框三分支不变）。

## H2：locate 预算计尝试 + 预算可见

- `execute.py`：locate_count 自增移到**所有分支之前**（成功/失败都计），保持 LOCATE_MAX_PER_RUN=3；
  耗尽拒绝 `locate_budget_exhausted`（现有）。
- `context.py build_budget_section`：追加 "locate 剩余 x/3"（与步数/续命三件套并列）。

## H3：locate 失败反馈可见

现状：locate 失败走 replan（跳过 reflect），execute 只写 failure_cause，plan 渲染链断——
模型连失败原因都看不到，于是连刷 locate。
- execute 的 locate 失败分支：写 `action_outcome_summary`（含 failure_code/message/attempt 计数），
  使 plan context 的 `last_action_outcome` section 下一轮自然渲染；
- trace `locate_result` 已有，保持。

## H4：repeat guard 覆盖 locate

- `context.py repeated_action_key`：新增 Locate 分支——key = `(locate, surface, hint_text_digest)`
  （hint 原文经 sanitize 后取短 hash）；同一 query 在同一 surface 重复 ≥ 阈值同样被拒绝；
- `update_gui_memory` 的 tried_actions 记录放开 `_metadata=="do"` 限制（Locate 也要入列，
  否则计数源缺失——参考 P3 swipe 修复的同款处理）。

## H5：续命凭据排除 auto 标准

现状：`new_latch`/`judge_near_miss` 分支被恒真的 app 前台 auto 标准（ctrip_foreground）主导，
首个窗口续命近乎自动放行。
- `context.py continuation_credential`：分支 2/3 只统计 **judge 型标准**（verification=vlm_judge 或
  predicate 非 auto 类；读契约元数据区分），auto 标准（app_or_activity_match 等）不计入；
- 分支 1（criterion movement）不受影响；
- 测试：auto-only latch 不授予、judge latch 授予。

## 不在本轮（明确排除）

- 日历格子合成 mark（F5，需独立设计：网格识别+日期标注）
- LA 输出 OCR 对齐校验（F6）
- perceptual hash 换 dHash（P3，仅当未来用于稳定性判断）
- graph.mdc/README 同步（Phase 收尾统一做）

## 测试要求

- H1：原子化语义（无 screen_changed 码、F 绑定正确、drift 标记、LA 收到 F 的图）
- H2：失败也计数、预算段含 locate 行
- H3：失败后下一轮 plan block 的 last_action_outcome 含 failure_code
- H4：同 query 同 surface 重复 locate 被拒绝；tried_actions 含 Locate
- H5：auto/judge 标准区分的授予矩阵
- 全量 `.venv/bin/pytest tests/ -q` 绿（934 基线+新增）
