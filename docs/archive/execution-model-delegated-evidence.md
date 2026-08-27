# 执行文档：模型委派证据架构（Model-Delegated Evidence）

> 读者：pi 执行 agent。本文档是自包含任务书。
> 仓库：/Users/bytedance/Open-AutoGLM（分支 feature/langgraph-refactor）
> 测试：`.venv/bin/pytest tests/ -q`（当前 1222 passed，必须保持全绿）
> 禁止：git commit / push；禁止调用外部 agent；禁止放宽 P0 约束。

## 0. 背景：为什么做这轮重构

七轮真机 e2e（携程机票任务）暴露的所有 bug 有一个共同根源：**代码试图理解 UI/语言长什么样**——
字面摘要比对、span 提取、interval_contains 匹配器、至/- 归一化、兄弟控件拆分，每一个匹配器
都是对世界的豪赌，世界每次都掀桌。与此同时，模型从未判断失误：reflect 每步都准确读出屏幕
内容（包括"筛选面板显示06:00至12:00"），但这些观察被 P0#13 隔离，看完就蒸发了。

最新一轮（run 20260806-162530）的实证：
- agent 亲手拖滑块把起飞时段设为 06:00-12:00（4 次手势，reflect 逐步确认），任务真实完成；
- 但证据链全断：①区间值拆在两个兄弟 TextView（ax_32="06:00"/ax_33="12:00"），原子文本匹配器
  看不见；②reflect 看到了但不进账本；③重编译把判据冲掉（liveness 误判）；④finish judge 只看
  最后一帧（面板已关）；⑤**判据缺口清单从未接线**——`AgentState` 没有声明 `criterion_gap_list`
  频道，reflect 每步算好被 LangGraph 静默丢弃，plan 全程读到空。

## 1. 核心设计原则（不可妥协）

**代码管证据的"形式"，模型管证据的"内容"。**

- 代码职责：账本管道（追加/有界/脱敏）、给模型喂对的信息（判据清单+轨迹摘要+屏幕）、
  结构 fail-closed（无"已观察"记录=未满足；矛盾=阻断）、引用存在性校验（只验 verdict 引用的
  步骤号存在，不验内容）、动作归因时间戳。
- 模型职责：全部内容判断——读屏（这条判据现在屏幕上可见吗，值是什么）、因果（这个状态是
  本跑动作造成的还是残留）、合同充分性自查。
- **禁止**新增任何"代码理解屏幕文本内容"的机制（匹配器/归一化/span 提取/决策表）。
  唯一保留的代码传感器：app 包名/前台比对（精确且免费）。
- 提示词只定义输出格式与状态语义（form），不写行为规则；模型自行判断内容。

## 2. 实施步骤（按序执行，每步跑全量测试保持绿；预算不够时在绿点停下写交接）

### S1 reflect 判据观察（新传感器）+ 缺口清单接线

1. `phone_agent/graph/nodes/reflect.py`：reflect 的模型调用 schema 增加 `criteria_observations`：
   ```json
   {"criterion": "name", "status": "observed|not_visible|contradicted", "observed_value": "..."}
   ```
   reflect prompt（CN/EN 同步，`phone_agent/config/prompts_zh.py` + `prompts_en.py`）注入
   **未满足的判据清单**（name + description + provenance 注释 + control_hint），状态语义：
   - `observed`：当前屏幕上直接读到该判据的内容，observed_value 填读到的值；
   - `not_visible`：当前屏幕读不到（不推断）；
   - `contradicted`：屏幕上读到与该判据矛盾的值（须给 observed_value）。
   P0#13 保持：verdict 字段仍只回答"本动作是否生效"，criteria_observations 是**读屏**，
   不是进度判断。reflect 没有任何判据上下文时（无契约/无 task_plan）跳过该段。
2. 账本新 entry kind `model_observation`（`phone_agent/graph/goal_evidence.py`）：
   `{kind, criterion, status, observed_value(脱敏), step, screen_id, observation_epoch, contract_id}`。
   复用现有 redaction 管线与 bounded ledger。
3. **`phone_agent/graph/state.py`：声明 `criterion_gap_list` 频道**（本轮最重要的一行——
   此前 reflect 返回它被静默丢弃）。
4. `criterion_gap_status` 改为直接折叠 `model_observation`（+ app 前台代码检查）：
   每条判据取最新观察：observed→✅、其余→⏳；confirmed 判据渲染 `[需确认]` 标记。
   删除对 `ever_matched`/`confirmed_criterion_satisfied`/`_gap_row_status` 的依赖。
   渲染器 `_render_criterion_gap_list`（context.py）已存在，确认接通即可。
5. 测试：reflect→state→plan **全链路测试**（构造 state 走 reflect_node 返回值合入 state，
   再验证 plan 上下文含缺口清单——这是此前缺失、导致接线断裂无人发现的测试类型）。

### S2 封缄与折叠切换到观察证据

1. `seal_satisfied_stages`：阶段全部 done_criteria 最新观察为 observed → 封缄（保留
   semantic_key 幂等）；观察到 contradicted → 撤缄（P0#13a：仅正面矛盾撤缄，缺失不撤）。
2. `stage_status_from_ledger` 同步改读 model_observation。
3. `provenance_level`（E0-E5）、`confirmed_criterion_satisfied` 决策表、
   `_confirmed_judge_control_bound`：**删除**。provenance 从代码决策表变成判据上的注释文本
   （编译器已输出 provenance 字段，保留该字段，但它的消费方式改为"注入 judge/reflect 提示词"）。
4. `goal_evaluator.py` 的 fold 相应简化：satisfied 的依据 = model_observation(observed)
   或 judge verdict（S3）或 app 前台代码检查。
5. 更新受影响测试（test_stage_sealing/test_acceptance_stage_sealing/test_provenance_validation
   等），删除测死代码的用例，新增观察驱动封缄/撤缄用例。

### S3 Acceptance：judge 信息给足 + 引用校验

1. finish judge prompt（CN/EN）注入**轨迹摘要**（代码从账本构建，有界 ~12 步）：
   每步行 `sN: 动作类型 → reflect verdict；观察: criterion=value`。
   这是 judge 判断因果（本跑行为 vs 残留）的唯一信息来源——run G 的 judge 只看最后一帧，
   信息饥饿导致无法确认。
2. judge 输出 schema 增加引用：`{criterion, status, observed_value, evidence_step}`。
   satisfied 必须给 evidence_step（轨迹步号或 "final_screen"）。
3. 代码校验（只验形式）：status=satisfied 但无 evidence_step → 按 unknown 处理；
   evidence_step 数字 > 当前步数 → 按 unknown 处理。unknown/missing → 拒绝并回传
   阶段定位反馈（保留现有 acceptance_rejection_feedback 结构）。
4. 删除 finish 处的 provenance 门控决策表（被"轨迹摘要 + judge 判断 + 引用校验"取代）。
5. 测试：引用缺失/越界降级、矛盾阻断、轨迹摘要内容有界性、全满足通过路径。

### S4 Liveness：观察驱动

1. `trajectory_liveness` 输入改为：最近 N 步是否有**新的 model_observation 或新 screen_id**。
   有→advancing/exploring；持续无→stuck。
2. 基于"stage 指针停滞"的重编译触发器移除或收敛：仅当完全无新观察且无新屏幕时才允许
   stuck→replan/recompile。run G 中 agent 在面板连续产生新观察时被误判 stuck 触发了两次
   重编译——该场景必须回归测试。
3. P0#13b 保持：liveness 不读单步 verdict。

### S5 编译器充分性自查（删除 span 机制）

1. 编译器 prompt（CN/EN）增加自查段：输出契约前自查"任务中每个参数（时间/日期/路线/排序等）
   是否有独立判据覆盖；缺则补上"。解析后可做一次自我修复循环（发现缺漏则要求模型补判据，
   最多 1 次）。
2. **删除**：`parameter_spans`/`constraint_spans`/`_normalize_parameter_span`/`_coverage_digests`/
   `_ROUTE_*`/`parameter_constraint_uncovered` 门控/`_parameters_covered` 及其全部测试。
   `ContractAdequacyValidator` 收缩为结构校验（判据名在 task_plan 中存在、id 唯一等纯形式）。
   TaskRequirementSet 保留 app 识别等元数据（LaunchPolicy 依赖），删除 coverage 相关字段。
3. takeover 触发条件相应更新：仅编译结构失败或模型明示任务不可验证时。

### S6 死代码清理

1. reflect 中 `collect_facts`/fact_providers 证据采集路径已被 S1 取代：删除调用；
   `fact_providers.py`/`predicates.py` 中仅服务证据匹配的谓词目录随之下线或收缩
   （先全仓 rg 确认无其他消费者，包括 goal_node/goal_compiler/checkpoint/bench）。
2. 契约 schema 中 `predicate_id`/`matcher_id` 字段变为可选并逐步停止输出（编译器 prompt
   不再要求；解析容忍缺失；trace 保留字段位，add-only 原则）。
3. `verification` 词汇保持不变（app_or_activity_match 仍由代码检查；accessibility_text_match
   与 vlm_judge 现在统一由模型观察传感，区别仅作为提示词注释）。
4. 更新 AGENTS.md 项目布局描述与受影响 `.trae/rules/*.mdc` 段落（只改描述，不改 P0 表）。

### S7 回归 fixture（用 run G 的形态）

新增测试模拟 run G 关键形态：s15 面板观察（departure_time observed=06:00-12:00）入账 →
缺口清单显示 ✅ → finish judge 带轨迹摘要+引用 → 通过；以及反例：无观察 + judge 无引用 →
拒绝。fixture 用合成数据（脱敏），不依赖真实截图。

## 3. 硬性约束

- **P0 全表不可违反**，特别注意：#4 HITL 路由不动；#5 边守卫先查 finished/error；
  #6 messages reducer 语义不动；#10 observed_value 入账前脱敏；#13 reflect verdict 仍只判单步；
  #13a finish 默认 fail-closed、撤缄仅凭正面矛盾；#13b liveness 不读单步 verdict。
- trace schema **add-only**：新 event/字段可加，已有事件字段名不删不改。
- 提示词 CN/EN 必须成对同步修改。
- 每步完成跑 `.venv/bin/pytest tests/ -q` 全绿再进下一步。
- 不 commit、不 push、不动 git 历史。
- 文件搜索用 rg，不用 grep/find。

## 4. 完成标准

1. 全量测试绿，且总数允许下降（删死代码的测试）但核心路径覆盖率不降。
2. reflect→state→plan 缺口清单全链路测试存在且通过（此前从未有过）。
3. 无任何"代码理解屏幕文本内容"的匹配器残留（rg 验证 interval_contains/parameter_spans/
   _coverage_digests/confirmed_criterion_satisfied 等符号全删）。
4. 交接说明：每步完成情况、删除/新增文件清单、测试数变化、遗留风险。

## 5. 交接要求（预算不足时）

停在任一全绿步骤边界，在本文档末尾追加"## 交接"节：已完成步骤、当前测试数、
下一步入口文件与注意事项。

## 交接

本轮重构执行于分支 `feature/langgraph-refactor`，当前全绿：**1186 passed**（起始 1222，净 -36：
删除测死代码的用例 + 新增观察驱动/全链路用例）。未 commit / push，未改历史。

### 每步完成情况

| 步骤 | 状态 | 说明 |
|---|---|---|
| S1 | ✅ | reflect 模型调用新增 `criteria_observations` 传感器（CN/EN 提示词成对）；账本新 entry kind `model_observation`（写前脱敏、有界窗口 48）；**`AgentState` 声明 `criterion_gap_status` 缺失的 `criterion_gap_list` 频道**（本轮最关键修复）；`criterion_gap_status` 改为直接折叠 model_observation + app 前台代码检查（`satisfied_by_code`），删除对 `ever_matched`/`confirmed_criterion_satisfied`/`_gap_row_status` 的依赖；reflect→state→plan 全链路测试 `test_reflect_state_plan_gap_list_full_chain` 通过 |
| S2 | ✅ | `seal_satisfied_stages`：阶段全部 done_criteria 最新观察为 observed → 封缄（semantic_key 幂等），撤缄仅凭正面矛盾；`stage_status_from_ledger` 改读 model_observation；删除 `provenance_level`(E1-E5)/`derive_provenance_level`/`confirmed_criterion_satisfied`/`grounded_effect_evidence`/`_confirmed_judge_control_bound`/`_fold_confirmed_criterion`/`criterion_satisfied_by_digest`；`fold_acceptance_verdicts` 简化（satisfied = model_observation observed ∥ judge verdict ∥ 代码传感器），新增 `_code_settled_criterion` 分层；更新 test_stage_sealing / test_acceptance_stage_sealing / test_provenance_validation / test_goal_latch / test_task_plan |
| S3 | ✅ | finish judge 提示词（CN/EN）注入**轨迹摘要**（账本构建、有界 ~12 步 `sN: 动作 → verdict; 观察: criterion=value`，`_trajectory_summary_for_judge`）；judge schema 新增 `evidence_step`（步号或 final_screen）；`_evidence_step_valid` 形式校验：satisfied 无引用或越界 → unknown；删除 finish 处 provenance 门控决策表；测试覆盖引用缺失/越界降级、轨迹摘要内容有界 |
| S4 | ✅ | `trajectory_liveness` 观察驱动：`criterion_history_from_ledger` 并入 model_observation，`_criterion_moved_toward_satisfaction` 改为"任意新读屏=运动"；stuck 仅在完全无新观察且无新屏幕时成立（run G 面板连续产生观察不再误判） |
| S5 | ✅ | 编译器 prompt（CN/EN）新增 `self_check` 自查段（模型侧参数覆盖自查，代码零文本比对）+ 最多 1 次自我修复循环（`_repair_prompt`，测试 `test_llm_compiler_self_repair_loop_adds_missing_parameter_criterion`）；**删除** `parameter_spans`/`constraint_spans`/`_normalize_parameter_span`/`_coverage_digests`/`_parameters_covered`/`_ROUTE_*`/`_PARAMETER_PATTERNS`/`_DATE_LITERAL_RE`/`parameter_constraint_uncovered`/`constraints_uncovered`/`terminal_state_uncovered`/`judge_description_not_observable`/`raw_text_binding_is_observable` 及全部对应测试；`ContractAdequacyValidator` 收缩为结构校验（含 task_plan 判据名/id 纯形式检查）；`TaskRequirementSet` 删除 coverage 字段，保留 app/ordinal/entities 元数据；heuristic 编译器 constraints 置空 |
| S6 | 部分 | reflect 中 `collect_goal_facts` 事实采集路径**已删除**（S1 传感器取代），`in_target_app` 走 registry 代码检查；删除 `interval_contains` matcher / `ui.parameter_value` 谓词 / `parse_interval_literal`（provider 与 catalog 同步收缩）；`CriterionEvidenceEntry.provenance_level` 字段删除；AGENTS.md 项目布局描述更新。**未做**：acceptance.py 仍经 `collect_goal_facts` 供 programmatic（代码可自证）层证据（app 前台/toggle/rank 等精确设备态传感器，P0#8 允许保留）；`.trae/rules/*.mdc` 段落描述未同步；`predicate_id`/`matcher_id` 已在 schema 中可选且 compiler 不输出，trace 字段位保留（add-only） |
| S7 | ✅ | 新增 `tests/graph/test_model_delegated_evidence.py`：run G 形态正向（s15 面板观察入账 → 缺口清单 ✅/封缄 → judge 带轨迹摘要+evidence_step 通过）与反例（无观察+judge 无引用 → 拒绝）；含 model_observation 脱敏/有界、parse 表单校验等。合成数据，无真实截图 |

### 新增/删除文件

- 新增：`tests/graph/test_model_delegated_evidence.py`（8 个用例）
- 无删除文件；大量测试被改写（删除测死代码用例集中在 test_provenance_validation.py / test_goal_requirements.py / test_stage_sealing.py / test_acceptance_stage_sealing.py / test_task_plan.py / test_goal_latch.py / test_predicate_catalog_closure.py / test_finish_gate_e2e.py）

### 测试数变化

起始 1222 → 当前 1186（净 -36：删除参数/provenance 决策表死代码用例约 44，新增观察驱动 + 全链路 + run G 回归 + 编译器自修复用例约 8）。

### 遗留风险与下一步

1. **acceptance 的 fact-provider 层**：`fold_acceptance_verdicts` 的 programmatic 分支仍读 `resolve_programmatic_criteria` 写入的 criterion entries（精确设备态传感器）。若想彻底下线 fact_providers，需把 app 前台判据改为由 acceptance 直接以 `current_app`/`in_target_app` 判定并写 ledger（入口文件 `phone_agent/graph/nodes/acceptance.py` + `fact_providers.py`）。
2. **screen_text_digest (L1)** 仍作为 judge 提示词上下文（机械提取，非证据门）。如需彻底移除，改 `_ledger_digest_for_judge`（acceptance.py）。
3. **`.trae/rules/*.mdc`** 与 AGENTS.md 中关于 ledger/provenance 的描述段落尚未全量同步（本轮只改了 AGENTS.md 布局描述，未动 P0 表）。
4. `ever_matched` / `MilestoneLatch` 仍服务于 F2 continuation 的 `_ever_matched_latch`（context.py）——属预算窗口机制，非判据证据，暂保留；若按 S6 精神收紧可在后续移除并改读 model_observation。
5. 真机回归（run G 形态 e2e）尚未在设备上验证，仅有合成 fixture 覆盖。
