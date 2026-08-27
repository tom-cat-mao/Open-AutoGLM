# 执行文档 H1：门禁退役 + 账本锚点 + 引用 step 存在性（验收侧）

> 读者：reasonix 执行 agent。自包含任务书，问题均已由主 agent 代码验证。
> 工作区：/Users/bytedance/Open-AutoGLM-fixH1（worktree，分支 wt/fix-h1-acceptance，基点 b822126）。
> 测试：`PYTHONPATH=/Users/bytedance/Open-AutoGLM-fixH1 /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 基线 **1304 全绿**。禁止：git commit/push；FakeModel 式 mock 判断测试；改任务书外代码；用 rg。
> 设计哲学（必须遵守）：代码=形式/管道，模型=内容判断。不得新增任何"代码理解内容"的逻辑。

## Fix A：finish claim 命名门禁彻底退役（用户已拍板）

**背景**：旧架构中终局声称是唯一证词，故 P0#13a 要求"判据未在 matched_terminal_evidence
命名=missing"。pi-26 观察驱动架构后，证据在每步入账（seal/账本 observed），judge 的
satisfied 又必须携带合法 evidence_step 引用——命名门禁的承诺与防幻觉功能均已被继承，
字面恢复只会在正确场景误拒。用户裁决：**彻底退役**。

**改动**：
1. `goal_evaluator.py fold_acceptance_verdicts`：删除 `finish_claim_matched` 参数（签名
   + docstring），函数体本就没用它；`evaluation_from_acceptance_fold`（1220/1248 区域）
   保留它在 evidence 记录中的诊断用途。
2. legacy 双标准消除：`AggregatingGoalEvaluator._check_vlm_judge`（811-812 区域）与
   `PureGoalEvaluator`（250-252 区域）删除 Part1"named in finish claim"检查，保留其余
   核验（evidence 核验逻辑不动）。
3. `acceptance.py:601-605`：claim 名单提取保留，但只用于 trace/evidence 记录，不再传入 fold。
4. adapter/validator 不动（`matched_terminal_evidence` 仍是合法 finish 字段，作诊断 trace）。
5. **规范文本更新**：AGENTS.md P0#13a 与 .trae/rules/graph.mdc 中"vlm_judge 判据未在
   finish claim 中命名=missing"改为："判据由账本逐层坐实（seal > 账本 observed > judge
   引用）；judge 的 satisfied 必须携带合法 evidence_step 引用；未坐实且无引用=unknown
   →goal_not_satisfied→replan，永不升级为成功"。
6. **测试调整**：tests/graph/test_acceptance*.py、test_provenance_validation.py、
   test_goal_evaluator.py 中断言"未命名=missing"的用例改为新语义断言：
   - 账本 observed 但 claim 未命名 → satisfied（门禁退役）
   - 未坐实 + judge satisfied 但无合法引用 → unknown（引用门禁仍在）
   - 未坐实 + judge satisfied + 合法引用 → satisfied

## Fix B：账本锚点（治"守卫周期失效"+"长任务证据丢失"两病）

**背景**：`MODEL_OBSERVATION_LIMIT=48` FIFO 驱逐。6 判据每步全读 → 8 步驱逐一轮：
(a) 被驱逐判据在 `latest_status_by_criterion` 中消失 → fresh_observation_count 误判
"首见即 fresh" → had_effect=True → 死循环守卫每 8 步被重置一次；
(b) 早期 observed 证据被驱逐 → fold tier2 读不到 → finish 被错误拒绝。

**改动**（全部在 goal_evidence.py，纯键控形式机制）：
1. 新增**每判据锚点**：维护 `criterion → 最新一条 model_observation` 的映射。
   - `append_model_observations` 时同步更新锚点（同 (screen,step,criterion) 替换语义与
     现有 append 一致）；
   - 锚点**不参与** FIFO 驱逐（窗口继续服务轨迹摘要/liveness 的"新近"需求）；
   - 撤缄/revoke 扫描照常作用于锚点（contradicted 为最新时锚点=contradicted）；
   - 按 contract_id 隔离（复用现有过滤，重编译后旧锚点自然失效）；
   - 序列化：锚点可并入账本结构或作为派生结构重建——选实现上最简且不破坏
     state 序列化（JSON-safe）的方式，保持 ledger schema add-only。
2. `latest_status_by_criterion` 改读锚点（fresh 判定不再因驱逐误报）。
3. `latest_model_observation`（fold tier2 用）改读锚点（早期 observed 证据不再丢失）。
4. 锚点上界=判据数；entry 脱敏复用现有写入路径。
**测试**：驱逐后同 status 观察**不**误报 fresh；首见仍 fresh；status 翻转仍 fresh；
驱逐后 tier2 仍能 satisfied（长任务场景合成账本）；contradicted 锚点阻断 finish。

## Fix F：引用校验补"step 存在性"（纯形式维度）

**背景**：`_evidence_step_valid`（goal_evaluator.py:952-973）只查 `0≤step≤current_step`，
模型可引用账本/摘要中根本不存在的 step。
**改动**：
1. acceptance.py 构建轨迹摘要时收集**摘要中实际出现的 step 集合**（effect_event/
   model_observation 桶的 step），随 fold 调用传入。
2. `_evidence_step_valid` 增加 `allowed_steps` 参数：非 `final_screen` 的引用必须 ∈
   allowed_steps（成员=形式检查，不读内容）。
3. 调用点同步更新；旧调用（若有测试直调）兼容处理。
**测试**：引用摘要内 step → 通过；引用空洞 step（范围内但摘要无）→ unknown；
final_screen 常量仍通过。

## 完成标准

- 三项全落地，全量测试绿（基线 1304，新增用例另计）。
- 每项至少 2 个确定性单测；不改任务书外代码；trace schema add-only。
- 文档末尾写"## 交接"：改动文件:行、测试数变化、与任务书不符的事实。
- 未 commit/push。
