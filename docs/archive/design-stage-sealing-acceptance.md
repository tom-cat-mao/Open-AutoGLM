# 设计文档：Stage-Sealing 验收层（task_plan × validator 融合）

> 状态：待实施。基线：1134 passed（e940d0b）。来源：20260805-112945 航班运行
> （行为完成但 finish 两次被拒，pi-16/pi-20 根因分析）+ 用户拍板的融合方向。
> 实施者须知：全程测试绿、不 commit/push、P0 约束不破（清单见 §10）。

## 1. 根因（必须理解的三条运行证据）

1. **验证时机错配**：判官在结果页找字面"2026年10月1日"，但年份只在日历页可见。
   任务完成是**轨迹的属性**，不是最后一帧的属性。
2. **假独立**：acceptance 不信 reflect 的自证，另起一轮判官——但判官与 reflect
   是同一个 VLM 看同一块屏，独立性为零，只是多一次被 UI 简写挡住的机会。
   真正独立的证据是**无障碍树的程序提取文本**（机械事实）。
3. **全有或全无 + 证据作废**：reflect 已给出合格证据（"起飞时间均在 06:00-12:00"）
   但 acceptance 不采信；judge 一条 missing 拖垮全部；重编译换名清空 latch。

## 2. 设计原则

- **证据在哪里产生，就在哪里采集**：日历屏采"年份"，筛选屏采"时段"，
  终屏只做一致性检查。
- **机械证据 > 效果事件 > 模型判断**：三级权威递减，判官范围收缩到最小。
- **逐条裁决替代全有或全无**：satisfied / unknown / contradicted 三态；
  unknown → 可执行取证指引；contradicted（正面反证）→ 拒绝并 replan。
- **stage 即验收单元**：`TaskStage.done_criteria` 声明了"哪条判据在哪个阶段
  被验证"——这就是编译期的验证上下文。

## 3. 三层证据模型

### L1 机械证据（每步自动采集，零模型成本）
每步 observation 后，从 ax marks 提取 `(text_summary, mark_id, screen_id)` 文本
摘要写入证据账本（`goal_evidence.py` 新增 entry kind `"screen_text_digest"`）。
有界：每屏 top-40 文本、账本保留最近 30 屏（policy 常量，env 可配）。
隐私：text_summary 已过 ax 脱敏；账本 entry 继续走既有 regex redaction。
→ "2026年10月"在日历屏被机械记录后，"年份"子目标永久结清。

### L2 效果事件（reflect 升格）
reflect 验证动作生效后（verdict=succeeded/partial 且 ExpectedOutcome 已过前置
校验），把效果升格为子目标事件入账（entry kind `"effect_event"`）：
`{action, target, observed_after, screen_id, step}`。reflect 的 named_evidence
（现有字段）一并入账，权威级低于 L1 但可被 L3 判官作为上下文采信。
单步语义（P0#13）不变：reflect 仍只判"这步生效没"，入账是副作用。

### L3 判官（范围收缩）
判官不再裸看终屏做全量判断，只做两件事：
1. **矛盾检查**：终屏/账本是否有正面反证（如结果页 10-04 vs 账本 10-01）
2. **unknown 裁决**：带着账本摘要（L1 digest + L2 事件）+ 当前截图，
   对仍未结清的判据逐条裁决。判官能看到日历屏的机械文本记录——
   不需要终屏出现字面"2026年"。

## 4. Stage Sealing（核心机制）

### 4.1 判据→stage 绑定
编译期由 `TaskStage.done_criteria` 反解 criterion→stage 映射；不属于任何
stage 的判据默认 terminal（终屏验收）。编译期可行性约束（goal_node 警告级，
不阻断）：terminal 判据的 description 若包含**带年份的完整日期字面量**或
**区间字面量**（正则 `\d{4}年\d{1,2}月\d{1,2}日`、`\d{2}:\d{2}-\d{2}:\d{2}`），
警告"该字面量可能不出现在终屏，建议挂到产生它的 stage"——给 trace 可见的
结构信号，不做硬拒（LLM 编译产物不可完全预判）。

### 4.2 SealRecord（账本新 entry kind `"stage_seal"`）
```
{stage_id, criteria_sealed: [name...], evidence_refs: [...],
 screen_id, step, sealed_at, semantic_key}
```
`semantic_key` = stage 判据描述内容的归一化哈希（与名字无关）——
重编译换名后语义键不变，seal 继承。

### 4.3 封存触发（急切封存）
每次 reflect 后（reflect_node 尾部），复用 `stage_status_from_ledger` 归约
当前 stage：全部判据在账本中 satisfied → 立即写 SealRecord。封存是幂等的
（同 semantic_key 已有 seal 则跳过）。已封存 stage 的判据在后续
`GoalEvaluator` 归约中直接判 satisfied（authoritative，不再重复求证）。

### 4.4 封存撤销（仅正面反证，P0#13a）
L3 矛盾检查或后续步骤的 typed predicate 对某 seal 判据产生**正面反证**
（positive counter-observation，非 absence）→ seal 撤销（账本写
`"stage_unseal"` entry），stage 回 open，plan 收到通知。existential absence
永远只判 unknown，不撤销 seal。

## 5. Acceptance 重写（nodes/acceptance.py）

finish 声明到达后的新流程：
```
1. 账本归约（逐条裁决，替代全有或全无）：
   - 已 seal 的 stage 判据 → satisfied（出示 seal 凭证）
   - L1/L2 已结清 → satisfied
   - 其余 → unknown
2. typed predicate 矛盾检查（现有 hard_veto 保留，扩展读 seal）
3. 有 unknown → 调 L3 判官：输入 = 账本摘要(有界) + 当前截图 +
   判据白名单(现有 W1-A 机制保留)；判官逐条回答（新输出契约，见 §6）
4. 裁决汇总：
   - 全部 satisfied → finished=True
   - 有 unknown → 拒绝 + 【stage 定位的可执行反馈】（§7）
   - 有 contradicted → 拒绝 + replan 信号（现有路径）
```

## 6. 判官输出契约变更（CN/EN 同步）

从 `{completed: bool, message, named_evidence[]}` 扩展为：
```
{verdicts: [{criterion: <白名单名>, status: satisfied|unknown|contradicted,
             observed_value: str|null}], message: str}
```
- 白名单逐字匹配机制（W1-A）原样保留
- 判官被告知账本摘要的存在与权威级："屏幕文本记录是程序提取的事实，
  可直接采信；你的任务是对记录未覆盖的判据给出判断"
- 旧 completed 字段保留兼容解析（既有测试不炸），新字段优先

## 7. 可执行拒绝反馈

拒绝时 message 结构化：`{missing: [{criterion, stage_id, hint}]}`，
hint 由代码按 stage_id 生成中性指引（"该判据属于阶段 S3（应用筛选），
其证据未入账——请回到对应页面让证据可被观察"）。注入下轮 plan 的
reflect 反馈通道（现有 acceptance_rejection 反馈路径），模型据此定向
取证而非盲目重做。

## 8. 重编译继承

重编译（现有 stage_stall/needs_recompile 路径）后：
- 新契约判据按 description 归一化哈希计算 semantic_key
- 账本中 seal/effect_event/screen_text_digest 按 semantic_key 重映射继承
- 名字变了但语义相同的判据不丢证据（修今天"换名清空 latch"）

## 9. 分期交付（每期测试绿）

- **A 期（证据层）**：L1 screen_text_digest 采集 + L2 effect_event 入账 +
  账本 bounded 裁剪。acceptance 行为不变。
- **B 期（封存层）**：SealRecord + 急切封存 + seal 权威归约 + 撤销 +
  重编译 semantic_key 继承。
- **C 期（验收层）**：acceptance 归约重写 + 判官新契约（带账本上下文）+
  逐条裁决 + 可执行反馈 + 编译期字面量警告。
- （D 期，本轮不做）：stage 末就地判官。

## 10. P0 兼容性清单（实施者逐条自查）

- #4 HITL / #5 边守卫 / #6 reducer 语义：acceptance 返回值结构变更时
  messages 只增不重建
- #10 隐私：L1 digest/L2 事件/seal 全部过 regex redaction；trace 只落
  redacted 形态
- #13 reflect 单步语义不变；L2 入账是 reflect 尾部副作用，不改 verdict 逻辑
- #13a finish gate fail-closed 不降级：unknown 永远≠satisfied；
  撤销只认正面反证
- 判官白名单逐字匹配（W1-A）不破

## 11. 测试要求

- L1：digest 入账、有界裁剪、redaction、不含 locate/la 文本噪声
- L2：succeeded/partial 才入账、failed 不入账、幂等
- seal：归约触发、幂等、authoritative、撤销仅正面反证、absence 不撤销
- 重编译：semantic_key 继承（换名不换描述→证据保留）
- acceptance：逐条裁决三态、seal 出示即过、判官带账本上下文、
  unknown→反馈带 stage_id、旧契约兼容解析
- 端到端模拟：构造"日历屏有年份+终屏无年份"场景 → finish 通过
  （这是今天失败场景的回归测试，必须有）
- CN/EN 判官 prompt 配对测试

## 12. 明确不做

D 期就地判官；judge 多模态多帧输入；判据自动改写；task_plan 编译策略变更。

## 13. 交付

①改动文件+内容摘要 ②分期交付确认（A/B/C 各自测试绿）③新测试清单
④最终 pytest 输出 ⑤P0 自查表 ⑥偏差说明
