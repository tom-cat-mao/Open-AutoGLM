# 执行文档：Goal/Plan/Validator 统一化（provenance 验收）

> 基线：1166 passed（084be2d）。来源：20260805-160431 运行残留放行事件 +
> pi-23 取证 + pi-24 设计 + 用户拍板的统一架构方向。
> 实施者须知：全程测试绿、不 commit/push、P0 约束不破。

## 0. 统一架构（目标形态）

```
GoalContract 树（objective → stage → criterion[provenance]）
   ├── Plan 每步读"判据缺口清单" → 决定执行层下一步
   └── Validator 每步把证据写回账本 → 缺口关闭
finish = 树全绿的形式确认（不再依赖终点判官拉锯）
```

## 1. 罪证（pi-23 取证，必读背景）

- `goal_compiler.py:660-683` `_quoted_span` 取最短引号片段 → 四要素判据塌缩成"上海"
- `goal_requirements.py::constraint_spans` 只收否定约束 → "早上6点到12点"不产生
  constraint hash，ContractAdequacyValidator 零约束
- `goal_evidence.py:583-624 ever_matched` 永久闩锁 + `reflect.py:1205-1231` 急切
  seal → Launch 后首页含"上海"即零动作封存
- `goal_evaluator.py:1032-1050` fold tier1 seal 无 freshness/当前屏检查
- 判官对时段筛选靠"航班时间在窗口内"派生放行（从未读筛选控件值）

## 2. 四层修复

### L1 编译充分性（goal_compiler.py / goal_requirements.py）
1. `constraint_spans`（或新增 `parameter_spans`）收**肯定参数约束**：时间区间
   （早上X点到Y点/X:XX-X:XX）、日期、路线（从A到B/飞往）、排序（最便宜/按…排序）、
   单程往返；TaskRequirementSet 增 `parameter_hashes`；ContractAdequacyValidator
   增结构级检查 `parameter_constraint_uncovered`（任务含参数约束但契约无同类
   独立判据 → inadequate，走 goal_node 现有 takeover/recompile 分支）
2. `_quoted_span` 改返回**全部**引号片段 → 多片段绑新谓词
   `semantic.attributes_present`（合取，同一控件子树）；单片段保留 entity_matches
3. 编译器 prompt（CN/EN）增规则：每个显式参数约束必须有独立判据且声明
   provenance=confirmed；vlm_judge 参数判据 description 必须指明读取的控件

### L2 provenance 语义（goal.py / goal_evidence.py / goal_evaluator.py / predicates.py）
1. `CriterionSpec` 增字段：
   ```python
   provenance: Literal["state","confirmed","caused"] = "state"
   control_hint: str | None = None
   ```
   语义：state=自显终态（App前台/开关/结果页）；confirmed=塑造答案的查询参数
   （时段/日期/路线/排序）——必须本轮在控件上精确读值；caused=动作效果。
   同步 to_dict/from_dict/to_prompt_block（confirmed 渲染 [确认] 标签）。
   `ambiguity_policy` 现有零消费，provenance 接管其语义（保留字段不动）。
2. 账本 `CriterionEvidenceEntry` 增 `provenance_level`（E1弱命中/E2残留/E3派生/
   E4本轮精确/E5本轮造成），由 (predicate.evidence_scope, source, epoch) 推导。
3. fold（`fold_acceptance_verdicts`）按决策表门控：
   - state 判据：E2 残留精确/E3 派生/E4/E5 → satisfied
   - confirmed 判据：仅 E4/E5 → satisfied；E2/E3 → **unknown（block）**
   - tier1 seal：仅当 seal 证据等级 ≥ 判据要求档
   - tier5 judge：对 confirmed 判据必须**控件绑定**——screen_reference 指向
     控件 mark 或 observed_value 本身体现区间/日期字面量；纯结果列表派生
     （读航班时间断言筛选生效）对 confirmed 判据降档 E3 → unknown
   - fail-closed 不动：unknown 永≠satisfied；contradicted 仅正面反证
4. 新谓词 `ui.parameter_value`（string, raw_text, element_scoped, matcher
   `interval_contains`）：解析 "06:00-12:00"/"6:00~12:00"/"早上6点到12点" 为
   区间，期望 ⊆ 观测。
5. 修 `_is_self_observable`：provenance≠state 且带 typed 谓词 → 机械通道；
   仅 state 的 raw_text 走 judge。
6. 编译期默认分派：app_or_activity_match/toggle_state_match→state；
   object_rank_match→confirmed；描述命中参数词表→confirmed，否则 state。

### L3 sealing 门控（goal_evidence.py / reflect.py）
1. **动作因果门**：stage 封存要求 ≥1 条 done criterion 的证据 epoch **严格晚于**
   前一 stage 的封存 epoch（单调推进）；首屏残留（epoch ≤ Launch effect epoch）
   不封存
2. **provenance 门**：SealRecord 记 `provenance_by_criterion`；confirmed/caused
   判据的证据等级未达标不封存
3. **同判据去重**：编译期/task_plan_validation_errors 禁止相邻 stage 的
   done_criteria 完全同集（堵 stage_2 搭便车；pi-23 发现的 key 碰撞伪影
   goal_evidence.py:841 一并修——同 key 多 stage 时 seal 归属取 index 最小者）

### L4 Plan 接线（nodes/plan.py / context.py / goal_evidence.py）
每步 plan 注入**判据级缺口清单**（细化现有 stage 焦点注入，同一消息块）：
```
当前阶段 S3 应用时段筛选
  ✅ search_parameters_match（S1 已确认）
  ⏳ time_filter_confirmed [需确认]：在筛选面板读取时段值（期望06:00-12:00；
     从航班列表推断不算数）
  ⏳ cheapest_identified [需观察]：结果页可见价格从低到高排序
```
- 数据来源：`stage_status_from_ledger` + fold 的判据级状态 + provenance 要求；
  生成中性缺口描述（未达标判据 + 需要的证据档 + control_hint）
- prompt 侧（CN/EN）只解释清单语义（⏳=未满足的验收条件，[需确认]=须读到
  控件实际值），不写行为指令
- 有界：当前 stage 的判据 + 全部已封存判据的 ✅ 一行式；脱敏走既有通道

## 3. 明确不做

D 期就地判官；判据自动改写；contract 热更新；多帧判官；caused 档的独立
prompt 渲染（本轮编译器只需正确分派 state/confirmed）。

## 4. 测试要求

- L1：肯定区间约束产生 parameter_hashes；无独立参数判据 → inadequate；
  多引号片段 → attributes_present 合取；单片段仍 entity_matches
- L2：provenance 默认分派正确；fold 决策表（E2/E3 对 confirmed → unknown，
  对 state → satisfied）；interval_contains 三种写法；judge 控件绑定降档；
  _is_self_observable 新规则
- L3：首屏残留不封存；epoch 单调推进；confirmed 判据低档证据不封存；
  同 done_criteria 相邻 stage 编译期报错；key 碰撞归属最小 index
- L4：plan prompt 含缺口清单；⏳/✅ 状态正确；confirmed 判据带 [需确认]；
  CN/EN 配对
- **端到端回归（必须有）**：复刻 160431 场景——残留筛选+排序、契约含
  confirmed 时段判据 → finish 被 block，反馈指引开筛选面板；确认动作
  （读控件值）入账后 finish 通过
- 全量 pytest 绿（1166 基线+新增）

## 5. P0 自查清单

#4 HITL、#5 边守卫、#6 reducer（plan 只 append）、#10 隐私（清单/provenance
字段过 redaction）、#13 reflect 单步、#13a fail-closed（unknown≠satisfied、
撤销仅正面反证）、#13b liveness 不读 verdict、W1-A 白名单逐字

## 6. 交付

①改动文件+内容 ②L1-L4 各自测试绿确认 ③新测试清单 ④最终 pytest 末尾
⑤P0 自查表 ⑥偏差说明
