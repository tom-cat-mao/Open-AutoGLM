# Execution J1: Guidance 生产者侧（traex）

## Mission

实现 Guidance 方案的**生产者侧**：validator/adapter 结构化错误、plan 失败分支写入契约字段、
G1 机制建议模块、validator 失败改"带指导 replan 一次再死"、acceptance 判词投影。
渲染侧（context.py）由 J2 并行开发——**你不许改 `phone_agent/graph/context.py`**。

共享契约：**先读 `docs/execution-j-guidance-contract.md`**，字段形状以它为准。

Worktree：`/tmp/oa-j1`，分支 `review/j1-guidance-producers`，base = main `0375cd6`。

## 环境

```bash
cd /tmp/oa-j1
PYTHONPATH=/tmp/oa-j1 /Users/bytedance/Open-AutoGLM/.venv/bin/python -m pytest tests/ -q
```

## 任务清单

### T1（契约 C1）：ActionValidationError 结构化 expected/found
- `phone_agent/actions/validator.py`：`ActionValidationError.__init__` 增加可选
  `expected`/`found`；**全部 raise 点**补上数据（missing_field→期望字段+类型；
  unknown_action→found=动作名；坐标越界→expected range + found 数值；unsafe_value 的
  extras→found=多余字段名列表；等等）。found 值只允许契约白名单类型。
- `phone_agent/actions/adapter.py`：可机械化的 raise 点同样补（mark_required、
  unknown_action、missing_field；invalid_json 无字段可不给）。
- 白名单过滤函数 `_whitelist_found(found)` 放 validator.py（或 actions/__init__ 公共处），
  命中 `PRIVATE_CONTEXT_TEXT_KEYS` 的键降级 `{"redacted": True, "length": N}`。

### T2（契约 C2/C3/C7）：plan 失败分支写入 + validator 失败 replan 一次
- `phone_agent/graph/nodes/plan.py` 所有 parse/adapter/validation/grounding 失败分支
  （含 recovery 分支 plan.py:1540-1598、terminal 分支 1599-1645）写 state：
  - `parse_failure`（C2 形状，found 先过白名单）
  - `mechanism_suggestion`（C4，来自 guidance.py）
  - `last_action_outcome`/`action_result` 增补 `error_layer`/`retry_policy`（C3，数据已存在于
    `_error_fields`，只需带进 outcome dict）
  - 成功路径把 `parse_failure=None`、`mechanism_suggestion=None` 清除。
- **C7 行为变更**：validation/adapter 失败且 repair 用尽后，`validation_replan_count==0`
  时 → 不 terminal，写 `validation_replan_count+1`，返回 replan（不 finished），让下轮 plan
  带着 parse_failure+mechanism_suggestion 重新决策；`>=1` 时维持 terminal。
  注意：相应 terminal 分支测试要更新为新语义。grounding/screenshot/safety 语义不动。
- recovery 分支（wrong_page+go_back）把错误码写进 `action_result.message`（W3，只 code 不全文）。

### T3（契约 C4）：新建 `phone_agent/graph/guidance.py`
- `mechanism_suggestion_for(code: str, layer: str) -> str | None`：错误码→机制级英文建议
  （≤120 字符）。集中现有 `_retry_policy_for_layer`（plan.py:378-393）与
  `_screenshot_error_fields`（plan.py:411-421）的映射，plan.py 改为从 guidance.py import，
  **行为保持相等**（现有测试不许变红）。
- 建议文案风格对齐 acceptance `_missing_feedback` 的机制 hint 风格（只谈机制，不谈内容）。
- 示例：`missing_field/validation → "Re-emit the action with all required fields populated."`、
  `unknown_mark/grounding → "Reference a mark from the current screen or emit locate first."`

### T4（契约 C5）：acceptance 判词投影
- `phone_agent/graph/nodes/acceptance.py` `_rejected`：从 `fold["per_criterion"]` 投影
  `acceptance_verdicts = {criterion: {"status":..., "reason": <reason code>}}`——只 status+reason，
  绝不投影 observed_value/screen_reference 内容；写入前过 `sanitize_context_payload`。
- acceptance 成功路径清除 `acceptance_verdicts={}`。

### T5（契约 C8）：步内重试文本增强
- `_build_parse_retry_messages`：有具体 validation 消息时带上（消息形式级、trace-safe）。

### T6：state.py 声明
- `parse_failure`、`mechanism_suggestion`、`acceptance_verdicts`、`validation_replan_count`
  全部在 `phone_agent/graph/state.py` AgentState 显式声明（overwrite 语义，类型注解放宽为
  `dict | None` / `str | None` / `int`）。

### T7：测试
- 新增 `test_j1_*`：T1 各 raise 点的 expected/found 形状+白名单降级；T2 失败分支契约字段
  写入/清除、replan-once 新语义（用编译 mini-graph 或真实形状 driver，禁止假形状——
  "测试自我欺骗"是本仓库已命名的 bug 类别）；T3 映射表完整性+旧行为相等；
  T4 投影只含 status+reason；T7 state 声明存在性。
- 全 suite 绿。

## 硬约束

- 遵守 AGENTS.md P0 + `docs/execution-j-guidance-contract.md` 红线。
- **不许改 `phone_agent/graph/context.py`**（J2 的地盘）。其余文件可改。
- trace schema 只增不改；CN/EN prompt 若动到 config/prompts_*.py 必须双语言同步。
- 不 commit。完成后写 `/tmp/oa-j1/HANDOFF_J1.md`（改动清单+测试结果+契约偏离记录+挂账）。

## Output

handoff 文件路径 + 测试结果 + 你认为需要主 agent 验收特别注意的点。
