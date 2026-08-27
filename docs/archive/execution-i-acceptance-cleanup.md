# 执行文档 I：验收收尾批次（9 项）

> 读者：reasonix 执行 agent。自包含任务书，问题均经主 agent 代码验证。
> 工作区：/Users/bytedance/Open-AutoGLM-fixI（worktree，分支 wt/fix-i-cleanup，基点 bbbcf7e）。
> 测试：`PYTHONPATH=/Users/bytedance/Open-AutoGLM-fixI /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 基线 **1335 全绿**。禁止：git commit/push；FakeModel 式 mock 判断测试；任务书外改动；用 rg。
> **CLAUDE.md 不要动**（本地未跟踪文件，由主 agent 另行处理）；HTML 归档文档不动。

## 1【P1】Fix D 真实路径未修复：after 侧补 marks（主 agent 已验证）

**证据**：plan 帧 `state["screen_id"]` 由 `observation.py:611-616` 的
`build_screen_id(..., marks=base_marks)` 生成（含 marks 拓扑 digest）；
`verifier.py:345-350` after 侧 `build_screen_id(...)` **不传 marks** → digest 恒为
`sha256("")[:16]` → 有 marks 的真实屏幕上 before≠after 恒真，`screen_changed`
依旧恒真、`content_shifted` 依旧恒 unknown。4 个新测试全用无 marks 的 before 构造，
绕开真实路径。
**修法**：after 侧传入当前帧 marks：`build_screen_id(..., marks=<当前 observation 的
marks>)`——先读 verifier.py 该函数签名，确认 after_observation / mark_registry 哪个
变量在作用域内、如何取 marks list（参考 observation.py 的用法）。两侧输入对称后，
同屏（含 marks 不变）→ screen_changed=False；换页/marks 结构变 → True。
**修测试**：`tests/graph/test_verifier_h2_hash_alignment.py` 4 个用例的 before 侧改为
**带 marks 的真实构造**（build_screen_id(..., marks=[...]) 模拟 plan 帧），断言：
同 marks 同屏→False；marks 拓扑变化→True；app 切换→True。**禁止再构造无 marks 的 before**。

## 2【P2】confirm reobserve 的 hash 缺失 fail-open → fail-closed

**证据**：`execute.py:985-990` `needs_mark_check = bool(grounded_mark_id) and registry
is not None and bool(registry.raw_screenshot_hash)`——registry 无 hash（旧 checkpoint/
手工构造）时跳过校验直接 dispatch，把"无法验证"当"新鲜"，违背 P0#9。
**修法**：动作有 grounded mark 且 registry 在、但 hash 缺失 → 走 stale 分支（与 hash
不匹配同路径）。即 needs_mark_check 只看"有 mark 且 registry 在"；校验内部 hash 缺失
→ stale。补测试：registry 无 hash → 不 dispatch + replan。

## 3【P3】Fix C 测试盲点补锁

1. `test_confirm_accept_without_mark_skips_freshness_check`：改断言 `get_screenshot`
   **不在** device 调用列表中（现断言 calls[-1]=="back" 无法区分跳过与多截一张）。
2. `test_confirm_accept_fresh_mark_dispatches`：补断言 `get_screenshot` 恰好调用 1 次。

## 4【P4】takeover/delegated 内联分支 pending 防御清理

**证据**：`execute.py:1173-1192`（takeover）与 `:1298-1310`（delegated）不清
pending_execute/interrupt_result（当前不可达，防御）。
**修法**：两分支 return 增加 `pending_execute: False, interrupt_result: None`（add-only，
读现有返回字段保持风格一致）。

## 5【一致性收尾】PureGoalEvaluator 命名判定降级为诊断

**证据**：`goal_evaluator.py:285-286` `unknown_claim_ids`（claim 点名契约外判据）→
`overall="failure"`——命名仍直接改变判定，与"claim 仅诊断"的退役裁决不一致（该
evaluator 无生产调用，但双标准不该留）。
**修法**：移除 unknown_claim_ids 对 overall 的影响（保留在 evidence 记录中作诊断）；
`test_pure_goal_evaluator_rejects_unknown_finish_claim_ids` 改为断言新语义
（记录但不再判 failure）。**注意**：对照 AggregatingGoalEvaluator 确认生产路径无同类残留。

## 6【P3】allowed_steps 与摘要截断同步 + 桶 kind 门控

**证据**：(a) `acceptance.py:382` 摘要 `[:2000]` 截断，但 `trajectory_summary_steps`
基于未截断 buckets——尾部 step 行被裁掉而集合仍含它（judge 可引用它看不到的 step）；
(b) `_trajectory_buckets` 在 kind 分支前 `buckets.setdefault(step,...)`——锚点/seal/
digest 条目也建桶，渲染空行 `sN: ?` 且撑宽 allowed_steps。
**修法**：(a) 摘要改为**逐行拼接直到 2000 上限**，同步收集实际入摘要的 step 集合
（集合=渲染行对应 step，严格同源）；(b) 只为 kind ∈ {effect_event, model_observation}
的条目建桶（锚点等不再产生空行/撑宽集合）。
**测试**：超长观察值触发截断时 allowed_steps ⊆ 实际渲染行；锚点条目不产生摘要行。

## 7【测试缺口】锚点隔离三锁

1. 锚点不进轨迹摘要/liveness 消费者（`_trajectory_buckets` 输出无锚点 step 行；
   `criterion_history_from_ledger` 不受锚点影响）——代码已如此，补测试锁定。
2. `remap_ledger_for_contract` 后旧契约锚点不污染新契约（重映射/失配行为断言）。
3. contradicted → observed 覆盖锚点 → tier2 恢复 satisfied（正向恢复断言）。

## 8【文档同步】.mdc/README/roadmap（全部在本批提交）

1. `.trae/rules/graph.mdc:37`："由 GoalEvaluator 对照 success_criteria 核验每个 name"
   → 新语义（claim 仅诊断 trace；判据由账本 seal>observed>judge 引用坐实）。
2. `.trae/rules/graph.mdc` 增补 H2 行为条目：confirm 接受后 dispatch 前 fresh-hash
   校验（stale→fail-closed replan）；终局分支历史瘦化；历史 assistant answer 500 截断。
3. `README.md:181-182` 旧硬门句 → 新语义（与 graph.mdc 一致）。
4. `docs/future-roadmap.md:17` 旧点名硬门句 → 新语义。
5. 顺手核对 docs/ 下无其他**非归档**文档残留旧门禁语义（HTML 归档不动）。

## 9【复核】全量回归

完成 1-8 后：rg 全仓 `not_named_in_finish_claim`（应只剩 docstring 退役说明）；
`rg observation_anchor` 消费点与验收清单一致；全量测试绿。

## 完成标准

- 9 项全落地，全量测试绿（基线 1335，新增用例另计）。
- 禁止 FakeModel 式 mock 判断测试；item 1 的测试必须构造真实带 marks 形状。
- 文档末尾写"## 交接"（改动文件:行、测试数变化、与任务书不符的事实）。
- 未 commit/push。
