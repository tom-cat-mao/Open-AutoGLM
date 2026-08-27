# J 批次共享契约：Guidance（指导性意见）字段形状冻结

J1（生产者）与 J2（渲染器）并行开发，**双方唯一接口就是本契约的字段形状**。
任何一方不得偏离；发现契约不够用时在 handoff 里记录，由主 agent 裁决。

## 设计红线（不可违反）

1. 代码只产出**机制级**指导（字段名/类型/范围/坐标/mark_id/action 名/app 名/错误码/
   机制建议），**绝不产出内容级指导**（不引用屏幕文本内容、不说"点哪个按钮"）。
2. `found` 白名单渲染：只允许 `field/type/range/value(数值或坐标)/mark_id/action/app`。
   任何命中 `PRIVATE_CONTEXT_TEXT_KEYS` 的键（text/message/hint/answer 等）一律
   降级为 `{"redacted": true, "length": N}`。
3. 语义级判词只来自模型（reflect/judge），代码建议与模型建议在 context 中分区呈现，
   命名上严格区分（`mechanism_suggestion` vs 模型的 `suggested_strategy`）。
4. 建议永远 advisory；fail-closed 路径不受建议内容影响。
5. 所有新 state 键必须在 `state.py` AgentState 显式声明（run-G 教训：未声明即静默丢弃）。
6. 每个新 state 字段**单写者**（下方标明），避免多节点互相覆盖。

## 契约字段

### C1. `ActionValidationError` / adapter 错误扩展（J1）
```python
class ActionValidationError(ValueError):
    def __init__(self, code, message, *, expected: dict | None = None, found: dict | None = None)
```
- `expected` 例：`{"field": "text", "type": "string"}`、`{"field": "element", "range": "0..1000"}`
- `found` 例：`{"field": "element[1]", "value": 1234}`、`{"field": "text", "value": None}`
- found 的 value 只允许数值/坐标/None/mark_id/action 名/app 名；**禁止 text/message/hint 实际值**。

### C2. state `parse_failure`（写者：plan，唯一）
```python
{
  "code": "missing_field",            # validation/adapter/grounding 错误码
  "layer": "validation",              # parse|adapter|validation|grounding
  "expected": {...} | None,           # C1 形状
  "found": {...} | None,              # C1 形状（已白名单过滤）
}
```
- plan 的**所有** parse/validation/grounding 失败分支（含 recovery 分支）写入；
  成功路径写 `None` 清除。
- J2 渲染进 `last_action_outcome` section 一行。

### C3. `last_action_outcome` 增补字段（写者：plan/execute 现有 outcome 写者，渲染 J2）
- 增加 `error_layer: str`、`retry_policy: str`（来自现有 `_error_fields`/`_retry_policy_for_layer`）。
- J2 在 `last_action_outcome` section 渲染这两个字段。

### C4. state `mechanism_suggestion`（写者：plan，唯一）
- `str | None`，≤120 字符，英文机制级建议，由 `phone_agent/graph/guidance.py` 的
  `mechanism_suggestion_for(code, layer) -> str | None` 产出（J1 新建该模块，集中现有
  `_retry_policy_for_layer`/`_screenshot_error_fields` 的映射，行为保持不变）。
- 失败分支写入；成功路径写 `None`。
- J2 渲染进新 `system_guidance` section。

### C5. state `acceptance_verdicts`（写者：acceptance `_rejected`，唯一）
```python
{ "<criterion_name>": {"status": "unknown|contradicted", "reason": "<reason code 字符串>"} }
```
- 来自 `fold["per_criterion"]`，**只投影 status + reason code**，绝不投影
  `observed_value`/`screen_reference` 的内容。
- 成功路径（acceptance success）清除为 `{}`。
- J2 渲染进现有 `acceptance_rejection` section（追加行）。

### C6. judge 判词行（渲染 J2，无新 state）
- `acceptance_rejection` section 末尾追加一行 `judge: <state["reflection"] 截断 ≤100 字符>`，
  仅当 `finish_validation_status` 为拒绝态时渲染（生命周期与 `acceptance_rejection_feedback`
  一致；写入侧已 sanitize，渲染侧复用现有防御性 re-sanitize）。

### C7. state `validation_replan_count`（写者：plan，唯一）
- int，默认 0。validation/adapter 失败且 repair 用尽后：若 count==0 → count+1 并
  **replan（不 terminal）**，`parse_failure`/`mechanism_suggestion` 已写入 state 供下轮
  plan 消费；若 count>=1 → 维持现状 terminal。
- grounding/screenshot/safety 的既有重试与终止语义**不变**。

### C8. 步内重试文本增强（J1）
- `_build_parse_retry_messages`：有具体 validator 消息时带上（消息本身是形式级，trace-safe），
  不再只带错误类别。

## 预算与裁剪（J2）
- 新渲染行都进 `_SECTION_BUDGETS` 与 `DEFAULT_CONTEXT_BUDGET`；
  `system_guidance` 独立小 budget（≤160 字符）；裁剪顺序放尾部；agenda 永不裁剪。

## 测试纪律（双方）
- 只用真实数据形状（契约形状）构造测试 state；禁止 mock 模型判断；
  FakeModel 作为 driver 断言形式可以。
- 测试命名 `test_j1_*`（J1）/ `test_j2_*`（J2），放对应既有测试目录。
- 双方 suite 必须独立绿：J2 不 import J1 的新模块（mechanism_suggestion 由 J1 写进
  state，J2 只渲染 state 形状）。
