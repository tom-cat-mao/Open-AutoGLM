# Budget 重构执行文档：资源保险丝 + 进展声明-校验

> 状态：已批准执行。来源：2026-08-12 三轮设计讨论 + 携程实机 run（`outputs/live-diagnosis/20260812-151859-*`）诊断。
> 本文档是唯一执行依据。实现者不得偏离"设计决策"节；遇到本文未覆盖的情况，按"设计原则"裁决并在报告中说明。

## 1. 背景与目标

当前 budget 是 14 套机制的混用词（见 §3 盘点），其中 `max_steps` 窗口、budget-forced acceptance、continuation grant（+10 ≤2 次）、`absolute_max_steps=×3`、novelty 一票否决构成一个" harness 排定死期 + harness 审判缓刑"的补丁栈，违背架构基因（模型声明/提议 → harness 证据校验 → fail-closed）。

携程 run 实证：轨迹在 step 18-19 已恢复活力（locate 成功 + 新状态），仍在 step 20 被窗口边界以 `novelty_exhausted` 杀死，`run_end` 文案 "Max steps reached" + `failure_cause=goal_not_satisfied` 双重失真。

**目标**：把 budget 重构为两个正交概念——
- **资源保险丝**（用户域）：`step_cap` + 可选 `wall_clock_cap_seconds`，用户直接设置、全程披露、诚实熔断；
- **进展声明-校验**（证据域）：无窗口、无死期。证据枯竭时模型必须声明（finish / take_over / progress_claim），harness 用证据校验声明，空口反复 + 持续枯竭才结束。

## 2. 设计原则（裁决依据）

1. **模型决定，证据把关**：继续/收尾是模型的声明；harness 不信文本，只认账本结构化证据（与 P0 #13a 同哲学）。
2. **保险丝不是判决**：`step_cap`/wall-clock 熔断只代表资源政策，不伪装成任务成败。
3. **harness 不喧宾夺主**：披露事实、校验证据、拉保险丝；不排定死期、不替模型判断任务策略。
4. **fail-closed**：声明解析失败、证据缺失、检测器不确定时，一律偏向"不接受声明/结束"，绝不偏向放行。

## 3. 现状盘点（删/留依据）

| 机制 | 处置 |
|---|---|
| `max_steps` 窗口语义 + `should_continue` 到界转 acceptance | **删**，改为纯 step_cap fuse |
| budget-forced acceptance（伪造 pending_finish，`acceptance.py:535-580`） | **删**。finish gate 恢复为纯 claim 触发（P0 #13a）。这是刻意的语义决策：模型从未声明完成 = 未完成；临近保险丝的披露文本会提示模型"已完成请 finish" |
| continuation grant / `CONTINUATION_*` 常量 / `_continuation_decision` | **删** |
| `absolute_max_steps = max_steps × 3`（`policy.py:102`、`agent.py:629`） | **删**。硬上限即用户 `step_cap`，不再派生 |
| novelty negation 一票否决（`context.py:1262`） | **删**（否决门）；novelty 保留为枯竭检测输入之一 |
| `budget_acceptance_done`、`continuation_count` 决策语义 | **删**；`continuation_count` 字段保留为 deprecated 兼容输出（恒 0），eval 不崩 |
| "Max steps reached" 结局文案 | **删**，替换为诚实 end reason（§7） |
| finish gate / ledger fold / acceptance 三层权威 | **留，不动** |
| `trajectory_liveness`（`context.py:899-955`） | **留**，降级为枯竭检测输入，不再承担任何路由 |
| `LOCATE_MAX_PER_RUN`、no-effect guard、observation retry、wait≤60s、context/ledger 字符预算、model timeout/TTFT | **留，全是健康保险丝** |
| budget section / liveness_note 披露（`context.py:1328-1371`、`2039-2094`） | **改写**（§6），"预算耗尽≠失败…续命 0/2 次"等旧措辞全部删除 |

## 4. 新增概念定义

### 4.1 资源保险丝

| 配置 | env | 默认 | 语义 |
|---|---|---|---|
| `step_cap` | `PHONE_AGENT_STEP_CAP` | 未设时回退 `PHONE_AGENT_MAX_STEPS` 的值（兼容期）；AgentConfig 默认 100 | 运行最大步数，撞丝硬停 |
| `wall_clock_cap_seconds` | `PHONE_AGENT_WALL_CLOCK_CAP_SECONDS` | None（关闭） | 从 run 开始的墙钟秒数上限，`should_continue` 检查 |

`AgentConfig` 新增两字段；`max_steps` 字段保留但语义变为 step_cap 的兼容来源。state 初始化写 `step_cap` / `wall_clock_cap_started_at`（epoch），不再写 `absolute_max_steps`（保留字段名输出 None 兼容，或彻底删除——由实现者按测试影响面选择，报告说明）。

### 4.2 枯竭检测器 `progress_exhaustion()`

新增纯函数于 `phone_agent/graph/context.py`，与 `trajectory_liveness` 同族（纯函数、不读单步 verdict、守 P0 #13b）。输入：state（账本、gui_memory、criterion 历史）。输出 `{"streak": int, "dry": bool, "reasons": [...]}`。

**单步 dry 定义**（最近一步同时满足）：
- 无 fresh criterion/model observation（`fresh_observation_count()` 口径 + §4.4 的 value digest 补充）
- 无 criterion rank 净升 / 无新 judge latch / 无 stage advance（有 task_plan 时）
- 无目标相关 effect event（succeeded/partial 且与目标页面/判据相关；纯"点开再点回"不算）
- 无语义状态扩展（新 `(surface, screen_id)`）；**注意：novel state 只能阻止 dry 中的"状态重复"子项，不能单独使一步变非 dry**——长列表无限滚动 mint 新屏幕不算进展（对齐 `tests/graph/test_continuation.py:112` fake-exploring 判例）
- 最新目标 no-effect 重复未被重置

**触发**：`exhaustion_streak >= 4`（常量 `PROGRESS_EXHAUSTION_TRIGGER=4`，`PROGRESS_EVIDENCE_HORIZON=6`，放 `policy.py`）→ state 置 `progress_declaration_due=true`。

**单屏填表豁免**：Type/Tap 产生 effect event、screen text digest 差异、observation 值变化 → 非 dry。

### 4.3 模型声明与校验

**声明入口**：plan 输出 JSON 新增可选字段（adapter/validator 白名单，**解析失败 fail-open 丢弃字段**，绝不阻塞主动作）：

```json
{
  "action": {...},
  "progress_claim": {
    "summary": "我在推进什么（一句话）",
    "evidence_refs": ["criterion:task_completed", "screen:FlightListActivity", "effect:locate_success@18"],
    "next_actions": ["点查询", "按时间筛选", "读最低价"]
  }
}
```

- finish / take_over 仍是既有动作路径，**不重复造字段**；`progress_claim` 仅表达"未完成但在推进"。
- 平时可省略；`progress_declaration_due=true` 时 plan context 明确要求模型必须表态（§6 tier 文本）。

**校验器 `validate_progress_claim()`**（纯函数，`context.py`）：
- 不信 `summary` 文本；只认结构化证据。
- **通过条件（满足其一强证据）**：近 HORIZON 步内 ① criterion rank 净升（ABA 振荡不算）② 新 judge-type latch ③ fresh observation 状态或值变化 ④ 目标相关 effect event ⑤ stage advance。
- 语义新状态单独**不构成**通过（反滚动作弊）。
- 输出 `{"status": "accepted"|"rejected", "missing": [...], "reason": "..."}`。

**结果流转**（plan 节点内调用校验器并写 state；结束经 `state.finished` 由边守卫路由，守 P0 #5）：
- `accepted` → `progress_exhaustion_streak=0`、`progress_declaration_due=false`、`progress_claim_round_count` 不变、继续运行
- `rejected` → 反馈（缺什么证据）写入下轮 plan context（复用 rejection feedback 通道模式）；`progress_claim_round_count+1`；给 `PROGRESS_CLAIM_GRACE_STEPS=3` 步宽限，宽限内须出现策略切换 + 新证据
- 以下任一 → `finished=true`、`failure_cause=progress_evidence_exhausted`：
  - declaration due 后模型**未给任何声明**且继续枯竭达宽限步数
  - `progress_claim_round_count >= 2`（`PROGRESS_CLAIM_MAX_ROUNDS=2`）且仍无新证据
- 常量均放 `policy.py`。

### 4.4 配套补丁：`observed_value_digest_changed`

`goal_evidence.py::fresh_observation_count()`（`:598`）当前只比 status 不比值，表单任务漏判。新增：同 criterion 连续 `model_observation` 的脱敏值 digest 变化也计为 fresh。**只存 digest，不落原值**（P0 #10）。

## 5. 状态机与路由改动

```
running（plan→execute→reflect 循环，每步 reflect 计算 progress_exhaustion 遥测）
  ├─ finished/error                    → end            （既有守卫，P0 #5）
  ├─ step_count >= step_cap            → end(resource_fuse_exhausted)
  ├─ wall clock 超时（若启用）          → end(resource_fuse_exhausted)
  ├─ pending_interrupt / obs retry     → 既有 HITL 路径  （不动）
  ├─ finish claim                       → acceptance     （不动，claim 唯一入口）
  ├─ progress_declaration_due           → plan context 索求声明（§6）
  │     ├─ 模型 finish                  → acceptance
  │     ├─ 模型 take_over               → takeover
  │     ├─ 模型 progress_claim          → plan 内校验（§4.3 流转）
  │     └─ 无声明 + 宽限耗尽            → end(progress_evidence_exhausted)
  └─ 其他                               → 继续循环
```

**`edges.py`**：
- `should_continue()`：删 `step_count >= max_steps → acceptance/end` 分支（`:53-75`），改为 terminal guard → fuse 检查 → HITL/obs retry → 否则 replan/继续。
- `after_acceptance()`：删 `step_count >= max_steps → end`（`:160-170`）；finish 被拒后只受 fuse/HITL 影响，否则 replan。
- 所有改动先查 `state.finished`/`state.error`（P0 #5）。

**`acceptance.py`**：删除 budget_forced 分支（`:539-580`）与 `_continuation_decision`（`:1050-1130`）及其调用；节点恢复纯 finish-claim 入口。删除 `budget_acceptance_done` 写入。

**`plan.py`**：
- 解析 `progress_claim` 可选字段；declaration due 时 context 注入索求文本；调用 `validate_progress_claim()` 并按 §4.3 写 state/发 trace。
- 守 P0 #6：plan 只 append 新消息；declaration 相关不得把大段文本塞进 `messages`（走 context block，不落 messages）。

## 6. 披露文本（lang=cn，进 `build_plan_context_block` budget section，不进 system 前缀——保 prompt 缓存）

改写 `build_budget_section()`（`context.py:1328-1371`），删除"续命/预算耗尽≠失败/只是触发系统验收"全部旧措辞。渐进分层（每层只追加，低层保留）：

```
Tier0（常态，剩余>50%）:
预算：已用 X/Y 步，剩余 Z 步。

Tier1（剩余<=50%）:
+ 步数用完后本次运行会停止（资源上限，不代表任务失败）。若任务实际已完成，请及时 finish 并点名成功标准。

Tier2（剩余<=25% 或 trajectory stuck）:
+ 轨迹提示：最近 M 步证据停滞（exhaustion_streak=N）/ 在相同页面间往返。这只是提示，请对照截图自行判断；若确卡住，换目标/换操作方式/返回上级。

Tier3（progress_declaration_due 或 剩余<=2）:
+ 系统已连续多步未观察到任务进展证据。请在本次输出中表态：(a) 已完成 → finish；(b) 无法完成 → take_over；(c) 仍在推进 → 附 progress_claim（说明推进内容+可核对的证据+接下来 1-3 个决定性动作）。自述不证明进展，系统只认账本证据。
```

liveness_note（`context.py:2039-2094`）保留"仅为提示请自行判断"定位，文本可微调与 Tier2 去重。

## 7. 结局语义与观测

- `run_end`/`finish_source`：新增 `resource_fuse_exhausted`、`progress_evidence_exhausted`；删除 `budget_forced`、`absolute_budget_exhausted`。"Max steps reached" 文案删除。
- 新 trace 事件：`resource_fuse_disclosed`（step0 一次）、`resource_fuse_exhausted`、`progress_exhaustion_observed`、`progress_claim_validation_result`、`progress_claim_rejected`、`progress_evidence_exhausted`。claim 自由文本经 `sanitize_context_payload` 脱敏（P0 #10）。
- state 新字段：`step_cap`、`wall_clock_cap_started_at`、`progress_exhaustion_streak`、`progress_declaration_due`、`progress_claim_round_count`、`progress_validation_status`/`progress_claim_feedback`。
- eval（`run_eval.py`）：`continuation_count` 保留输出恒 0（deprecated）；新增 `resource_fuse_exhausted_count`、`progress_claim_count`、`progress_claim_accepted/rejected` 聚合。
- live-diagnosis 脚本 `run_diagnosis.py:296` 的 decision-loop 信号引用旧 budget 事件，同步更新。

## 8. 测试改造

- `tests/graph/test_continuation.py` → 改写为 `validate_progress_claim()` 单测（强证据通过/振荡不算/滚动不算/空口驳回/宽限与轮次上限）
- `tests/graph/test_context_trajectory.py` → 保留 AB 振荡 stuck、long novel exploring；新增"长 novel 列表无目标证据不清零 exhaustion"
- edges/acceptance/plan 相关测试同步：删 budget-forced 用例，改 fuse/declaration 路由用例
- HITL/checkpoint 测试：新 state 字段进出 `AgentState`；进程内 checkpointer 不落盘现状不变；serde 清空 ledger 的既有行为不改（文档标注）
- **验收线**：`.venv/bin/pytest tests -q` 全绿

## 9. 文档同步

实现完成后同步：`docs/future-roadmap.md`（本节状态）、`AGENTS.md`（P0 #13b 附近补一句新语义）、`CLAUDE.md`/`README.md` lockstep。**不创建 git commit**（P0 #15）。

## 10. 携程 run 重放预期（验收对照）

新规则下该 run 应在 step ~16 触发 declaration review → 模型空口声明被驳回 + 反馈 → 宽限内策略切换（locate）→ 强证据入账 → 清零继续 → 后续正常推进直至 finish 或撞 `step_cap`（20，用户所设）→ `resource_fuse_exhausted` 诚实收尾。结局更早、更诚实，且循环期被模型可见的反馈打断。

## 11. 风险清单（实现必须守住）

1. 声明解析失败必须 fail-open，不阻塞主动作。
2. 弱模型无视披露 → 保险丝硬停兜底，披露不承担责任。
3. `messages` reducer 语义（P0 #6）、边守卫（P0 #5）、liveness 纯函数（P0 #13b）、acceptance finish-only、脱敏（P0 #10）——逐条自查。
4. heuristic 合约下 stage 分支缺席是已知边界，不算缺陷；文档注明。
5. `run_live` interrupt 循环只认 HITL marker，新流程不得引入新 interrupt 类型。
