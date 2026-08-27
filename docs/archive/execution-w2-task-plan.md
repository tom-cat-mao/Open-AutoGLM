# 执行文档 W2：task_plan 任务级规划层原生融入

> 基线：W1 合入后的绿基线。来源：docs/future-plan-task-planner.md（构想）+
> pi-3 架构评估（挂载点已核实）。本文件为实施定稿。
> 哲学红线：**阶段是信念不是授权**——只进 prompt 不进 gate；tracker 账本推导，
> 不认模型自报；阶段信号只挂 auto 标准。全程测试绿，不 commit/push，CN/EN 同步，
> trace/checkpoint schema 只增、脱敏语义不破。

## T1：数据模型（goal.py）

- 新增 `TaskStage` dataclass：`stage_id: str`、`objective: str`（页面级目标一句话）、
  `done_criteria: tuple[str, ...]`（引用契约 success_criteria 的名字）、
  `fallback: str`（一句兜底策略）、`index: int`；
- `GoalContract` 新增 `task_plan: tuple[TaskStage, ...] | None`；
- 序列化三件套同步：to_dict/from_dict；**state/trace payload 只存脱敏元数据**
  （objective 过 redact_context_text、done_criteria 只存标准名），全量留 runtime_goal
  引用（P0#10 不破）；
- 校验：done_criteria 的名字必须存在于契约 success_criteria；不存在的名字 →
  编译视为失败回退（见 T2 Heuristic）。

## T2：编译（goal_compiler.py）

- LLMGoalCompiler 的 JSON schema 加 `task_plan` 段（**同一次调用**，schema 扩展示例
  同步进编译 prompt，CN/EN）：
  ```
  "task_plan": [{"objective":"页面级目标","done_criteria":["契约标准名"],
                 "fallback":"卡住怎么办"}]  // 3-6 阶段，按执行顺序
  ```
- 编译 prompt 约束（写进编译器指令）：页面级表述、禁止控件/坐标级描述；
  **每阶段至少含一条非恒真 auto 标准**（app.foreground_identity 类恒真项不得单独
  构成 done_criteria——防阶段虚推进，H5 同款护栏）；
- HeuristicGoalCompiler：产出 `task_plan=None`（退化为无计划，全契约即全程）——
  fail-closed，无计划不影响任何现有行为；
- External 契约源若带 task_plan 则校验后采用。

## T3：阶段状态推导（goal_evidence.py + nodes/reflect.py）

- 新纯函数 `stage_status_from_ledger(ledger, task_plan)`：每阶段 done_criteria 做
  `ever_matched` 折叠 → `pending/satisfied`，当前阶段 = 首个非 satisfied；
  全 satisfied → current=None；
- reflect_node：现有账本折叠后调用该函数，写 `state["task_plan_status"]`
  （{current_stage_index, per_stage: [...]}）——**零新增模型调用**；
- reflect 职责不变（不判定阶段完成；只收证据+推导状态）；
- `_judge_evidence_pending`（reflect.py:202）保持现状（vlm_judge 收窄依赖 W1-A
  验收判官修复后的效果评估，不在本轮动）。

## T4：续行凭据第 4 分支（context.py）

- `continuation_credential` 增加 `stage_advance`：`task_plan_status.current_stage_index`
  与上一窗口边界快照比较，前进 → True；
- 存快照的位置复用现有窗口快照机制；无 task_plan 时分支恒 False（不影响现有契约）；
- 分支优先级/组合逻辑与现有三分支一致（any → grant）。

## T5：prompt 承载（nodes/plan.py + context.py）

- **静态块**（goal_contract_block，可缓存）：task_plan 全量（阶段序号+目标+完成信号名），
  附标注"参考路径，以截图为准 / reference path; the screenshot prevails"；
- **动态块**（build_plan_context_block 新 section `task_plan_status`）：
  `当前阶段：2/5（目标摘要）+ 本阶段信号满足情况`；
- 红线：动态阶段信息绝不进静态块；无 task_plan 时两个块都不出现该内容。

## T6：重编触发

- `needs_recompile` 目前无写入者。本轮只接**一个**写入点：阶段停滞——
  `stage_stall`（当前阶段连续 K 个窗口未推进且 trajectory_liveness=stuck）时
  reflect/acceptance 侧置 `needs_recompile=True`（走既有 replan→goal 路由）；
- K 放 policy.py（如 `STAGE_STALL_RECOMPILE_WINDOWS = 2`）；
- 其余重编触发（验收拒绝等）沿用现状。

## 明确不做（外挂反模式清单，验收逐条查）

1. ❌ task_plan 独立 state 键+独立编译+独立 prompt block（脱离契约设施）
2. ❌ 新增图节点（stage_tracker 等）
3. ❌ 新谓词/新证据源（CORE_PROVIDER_PREDICATES 闭包不动）
4. ❌ "所有阶段 satisfied 才允许 finish"（阶段永不当门槛）
5. ❌ task_plan 全量进 checkpoint（只存脱敏元数据）
6. ❌ tracker 用模型自报推进

## 测试要求

- T1：序列化 roundtrip；脱敏 payload 不含原始 objective 文本；非法 done_criteria 名拒绝
- T2：LLM 编译产出合法 plan；缺段/非法段 → Heuristic 退化 None；恒真-only 阶段被拒
- T3：stage_status 折叠（全 pending/部分 satisfied/全 satisfied/振荡不回退——
  ever_matched 锁存）；零模型调用验证
- T4：stage_advance 触发/不触发/无 plan 恒 False；与现有三分支组合
- T5：静态块含全量+标注、动态块含当前焦点、缓存边界（动态不进静态）
- T6：阶段停滞置位 needs_recompile；正常推进不置位
- 反模式回归：finish 不受阶段状态影响（阶段未走完也能 finish——终态契约唯一权威）
- 全量绿

## 交付

①改动文件+内容 ②关键决策（stage 归属/编译 schema/停滞参数）③新测试清单
④最终 pytest 末尾 ⑤反模式逐条自查 ⑥偏差说明。
