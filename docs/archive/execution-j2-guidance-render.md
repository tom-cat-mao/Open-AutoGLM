# Execution J2: Guidance 渲染侧（traex）

## Mission

实现 Guidance 方案的**渲染侧**：把契约字段渲染进 plan 每轮的 context block。
生产者侧（plan.py/validator.py/acceptance.py/guidance.py）由 J1 并行开发——
**你只许改 `phone_agent/graph/context.py` + 新增测试**，其余文件不许动。

共享契约：**先读 `docs/execution-j-guidance-contract.md`**，字段形状以它为准。
你按契约形状构造合成 state 做测试，**不 import J1 的新模块**（`mechanism_suggestion`
是 J1 写进 state 的字符串，你只渲染 state）。

Worktree：`/tmp/oa-j2`，分支 `review/j2-guidance-render`，base = main `0375cd6`。

## 环境

```bash
cd /tmp/oa-j2
PYTHONPATH=/tmp/oa-j2 /Users/bytedance/Open-AutoGLM/.venv/bin/python -m pytest tests/ -q
```

## 任务清单（全部在 context.py）

### R1（契约 C2/C3）：last_action_outcome section 增强
- 渲染 `state["parse_failure"]`：一行 `parse_failure: layer=validation code=missing_field
  expected={...} found={...}`；found 渲染前再过一次白名单防御（契约 §2），
  命中隐私键降级 `{"redacted":true,"length":N}`。
- 渲染 outcome 里新增的 `error_layer`/`retry_policy` 字段（存在才渲染）。

### R2（契约 C4）：新 section `system_guidance`
- 渲染 `state["mechanism_suggestion"]`（非空才出现）。
- section 命名/措辞必须与模型的 `suggested_strategy` 明显区分（这是代码的机制级建议），
  例：`[system_guidance] (mechanism-level hint, advisory only): ...`。
- 独立 budget ≤160 字符；裁剪顺序尾部；agenda 永不裁剪。

### R3（契约 C5/C6）：acceptance_rejection section 增强
- 追加渲染 `state["acceptance_verdicts"]`：每判据一行
  `verdict: <criterion> status=<unknown|contradicted> reason=<code>`（有界，条数≤criteria 数）。
- 末尾追加 judge 判词行：`judge: <state["reflection"] 截断≤100字符>`，仅当
  `finish_validation_status` 为拒绝态时（生命周期跟随 `acceptance_rejection_feedback`）；
  复用现有防御性 re-sanitize。

### R4：预算与裁剪
- 新行/section 全部进 `_SECTION_BUDGETS` + `DEFAULT_CONTEXT_BUDGET`；
  `_trim_plan_block_preserving_agenda` 裁剪顺序中新 section 放尾部。

### R5：测试（`test_j2_*`）
- 按契约形状构造合成 state（真实形状！）断言：R1/R2/R3 各渲染行出现与格式；
  隐私键降级；budget 裁剪不丢 agenda；`acceptance_verdicts={}`/`mechanism_suggestion=None`
  时不渲染空 section；非拒绝态不渲染 judge 行。
- 全 suite 绿（你的 worktree 里没有 J1 的新 state 字段生产者，渲染器对缺字段必须
  容错——字段不存在=不渲染，这也是契约的一部分）。

## 硬约束

- 遵守 AGENTS.md P0 + 契约红线；只许改 context.py + 新增测试文件。
- 不 commit。完成后写 `/tmp/oa-j2/HANDOFF_J2.md`（改动清单+测试结果+契约偏离记录+挂账）。

## Output

handoff 文件路径 + 测试结果 + 需要主 agent 验收特别注意的点。
