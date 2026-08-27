# 模型放权重构计划（Model-Delegation Refactor Plan）

> 来源：20260727 限速摩卡真机 e2e 失败的架构层根因分析（双 Agent 联合调查）。
> 状态：Phase 1/2 待实施；Phase 0 需真机；Phase 3 待决策确认。

## 0. 背景：失败事实（全部经 trace 验证）

- 运行 `outputs/live-diagnosis/20260727-105106-...限速摩卡...银石赛道`：20 步预算耗尽、模型从未发 finish claim、acceptance 节点零触发、`failure_cause=unknown`。
- 直接根因（commit `92ff6c8` 已修但未真机验证）：
  1. `state_before_observation_payload`（`observation_capture.py:243`）暴露裸 activity 类，after 侧携带 `device_signals.top_activity`（package/activity 组件）→ surface 比较两侧形状永不相等 → `selected_object_surface_changed=True` 恒真（trace 12/12 步）。
  2. repeat guard 只报告不拒绝 → type"限速摩卡"×3、重复点搜索框零拦截。
  3. 旧 liveness 基于去重流 → stuck 结构性不可达（trace 全程 exploring）。
- 架构层根因（本次重构的出发点）：**权威倒挂**——`merge_verifier_with_reflection`（`verifier.py:336-430`）5 个分支可用程序化信号覆盖模型视觉判断；而 trace 证明多数步骤模型的 partial/failed 比 verifier 的假 success 更准。传感器 bug 变成模型信念 bug。
- 辅助事实：
  - `context.py:935` failure_memory 预算 3 条但只渲染 `[-1:]` 1 条，模型看不到 repeated_failure_count=7。
  - acceptance 不依赖 reflect 的 named_evidence（自有 `_run_semantic_judge`，`acceptance.py:360`）→ 删除 reflect 模型调用不会饿死验收。
  - repeat 拒绝后动作仍走 reflect、写 failure_memory（`execute.py:251-290` → `after_execute` → reflect）→ 系统决策被记成动作失败，污染记忆。
  - 每步 2 次 VLM 调用（plan+reflect），reflect 是冗余感知：plan 每步开头自行截屏并注入上轮 reflection_context。

## 1. 放权总原则：新权威表

| 层 | 现状 | 目标 |
|---|---|---|
| 单步成败判断 | verifier 5 分支可覆盖模型 verdict | 仅 hard_failure 保留覆盖；其余降级为证据句注入下轮 prompt |
| 重复动作 | 拒绝后仍走 reflect、污染失败记忆 | 拒绝=系统决策：跳过 reflect/失败记忆，直接 replan 并注入拒绝原因+计数；但**必须仍计入 tried_actions**（否则 guard 计数失效） |
| 轨迹活性 | stuck 路由 takeover/replan（edges.py:44-46） | 降级为 context 顶部一句自然语言提示；路由兜底只保留 max_steps / observation_retry |
| finish | 唯一入口=模型主动 claim | 双通道：模型 claim + 预算触底系统强制验收一次 |
| 记忆 | 程序化拼接 summarized_history；failure_memory 只渲染 1 条 | 渲染预算 3 条+重复计数行（P3 再改为模型自述 progress_note） |
| 不动的硬线 | 坐标转换、grounding fail-closed、safety/HITL、隐私脱敏、acceptance hard veto + auto 标准程序化验收 | 全部保留 |

**一句话：程序化只产"证据"和"护栏"，判断权（这步成没成、是否绕圈、该不该完成）交还模型。**

## 2. 分阶段计划

### Phase 0 — 验证门（半天，需真机，不在本次实施范围）
用 92ff6c8 HEAD 真机重跑限速摩卡任务，建立基线。

### Phase 1 — 权威修正（1-2 天，4 个独立可回滚小改）

| # | 改动 | 位置 | 要点 |
|---|---|---|---|
| 1.1 | merge 收窄 | `verifier.py:336-430 merge_verifier_with_reflection` | 删除：①success+conf≥0.9→succeeded ②selected-object 组合→succeeded ③failure+conf≥0.7→failed ④⑤unknown+missing/no-matched 且模型 succeeded→failed。仅保留 hard_failure 覆盖。verifier 结果以 advisory 字段（如 `verifier_advisory`）挂在 reflection dict 上，供 context 渲染成证据句 |
| 1.2 | repeat 拒绝语义 | `execute.py:236-290`、`edges.py:after_execute` | 拒绝返回置标志位（如 `repeat_rejected=True`）；after_execute 检查并直接路由 replan；不走 reflect、不写 failure_memory；**仍写 gui_memory.tried_actions**（保 guard 计数）；拒绝原因+repeat_count 经 avoid_repeating/action_outcome 注入下轮 plan |
| 1.3 | failure_memory 渲染 | `context.py:935` | `[-1:]` → 按预算 `failure_memory_items`(3) 渲染，并附 repeated_failure_count 计数行 |
| 1.4 | context 砍 section | `context.py:CONTEXT_SECTION_IDS` 及渲染/选择逻辑 | 删 `screen_belief`、`short_term_memory`、`action_ledger`（与 tried_actions 重复）；注意 `REFLECT_CONTEXT_SECTION_IDS` 引用 screen_belief 需同步；预算让给 avoid_repeating/goal_agenda；更新相关单测与 trace 指标 |

### Phase 2 — 结构改造（2-3 天）

| # | 改动 | 设计 |
|---|---|---|
| 2.1 | 预算触发式验收 | `edges.py:should_continue`：finished/error 检查仍在最前（P0#5）；`step_count >= max_steps` 分支改为：若合约已编译、未 finished、且未做过预算验收（新 state 标志 `budget_acceptance_done`）→ 置 `pending_finish=True` + `finish_source="budget_forced"`，路由 "acceptance"。acceptance 走现有三层权威（hard veto → hard confirm → `_run_semantic_judge`）：模型语义判官有机会认出"实际已完成"（本次 run 内容已打开）；判不通过则 `failure_cause=goal_not_satisfied` 结束，替代 unknown 零信息结局。`after_acceptance` 的 max_steps→end 分支保持不变。agent.py run_end 归因同步 |
| 2.2 | liveness 提示化 | `edges.py` 删 stuck→replan/takeover 路由（保留 observation_retry takeover）；`context.py` 把 liveness state + novelty_streak + repeat 计数渲染为一句自然语言放 context block 首行；`trajectory_liveness` 计算保留（遥测+prompt），config/policy 阈值保留 |
| 2.3 | judge 标准可判定化 | goal compiler LLM prompt：vlm_judge 标准必须生成"屏幕可观察的具体内容"描述；`goal_requirements.py ContractAdequacyValidator` 加一条 **degraded**（非 inadequate）启发式：抽象描述无具体可观察内容 → `judge_description_not_observable`，仅诊断不硬拒 |

### Phase 3 — 模型中心（3-5 天，**本次不实施**，待 P1/P2 效果确认后单独分支）

- 3.1 每步单模型调用：reflect 节点删模型调用，仅跑 observation_capture+verifier 折叠证据；verdict 决策移交下轮 plan。
- 3.2 prompt 三块化：静态 system（前缀缓存）+ user（截图+任务+goal_agenda+压缩 marks）+ ≤300 字符自然语言 observation；删防御性文本；finish 条款显著化。
- 3.3 模型自述 progress_note 替代程序化 summarized_history。

### Phase 4 — trace-replay 回归工装（可选，与 P1 并行）
`tests/replay/`：加载录制 trace.jsonl，mock 模型响应，重放 verifier/merge/context/路由纯函数，断言关键步骤信号。形式化 92ff6c8 "录制 trace 重放验证 18/20 步"的做法。

## 3. P0 约束冲突检查

- #5 edge terminal guard：2.1 改 should_continue 时 finished/error 必须最先返回 end。
- #13 reflect 只答单步：reflect 节点保留，verdict 来源变化不违反。
- #13a finish fail-closed：预算触发只是多一个入口，验收标准不降；unknown 永不 success。
- #13b liveness 不读单步 verdict：提示化后更不参与路由，符合。
- #1 坐标 / #8-9 grounding / #4 HITL / #10 隐私：不在改动面，不动。

## 4. 验证策略

- 每个 Phase 1 小改配新单测；全程 `.venv/bin/pytest tests/ -q` 保持全绿（基线 721 passed）。
- 关键断言：1.1 后模型 verdict 不再被 conf≥0.9/selected-object 覆盖；1.2 后拒绝动作无 reflect verdict、无 failure_memory 写入、tried_actions 仍计数；2.1 后 max_steps 耗尽触发一次 acceptance 且 failure_cause≠unknown。
- 不 commit、不 push（AGENTS.md #15）；改动留工作区由主 Agent 验收。
