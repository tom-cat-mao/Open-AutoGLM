---
name: autopilot
description: 当用户要求 autopilot、自动持续执行、从 RALPLAN 到实现验证 QA 的端到端流水线时使用。
---

# Autopilot Skill

Autopilot 将复杂任务拆成持续流水线：

```text
RALPLAN -> execution -> RALPH/verification -> qa -> complete
```

## 使用方式

- 推荐用户手动调用 `/autopilot <task>`。
- 已有 approved roadmap 且明确要复用时，调用 `/autopilot --use-current-plan`。
- 需要多 subagent 执行时，调用 `/autopilot --execution=team <task>`。
- 可配置规划与验证：`--planning=ralplan|direct|false`、`--verification=false`、`--qa=false`。
- 不建议对架构级变更使用 `--direct`。
- 自然语言触发仅用于明确的 autopilot/自动持续执行/端到端流水线请求；模糊短请求不自动启动。
- 管理当前 pipeline：`/autopilot status`、`/autopilot cancel`、`/autopilot resume`（24 小时内）、`/autopilot reset`。

## 必须遵守

- 读取 `.trae/rules/autopilot.mdc` 获取 pipeline 协议。
- RALPLAN 产物以 `.trae/rules/graph.mdc` 为准，不使用 `.omc/plans`。
- TraeCLI-native：不迁移 `.omc` runtime，不修改 `.trae/traecli.toml`，不引入第二套 runtime state。
- 尊重 RALPLAN handoff、mode registry 与 subagent tracking；hook 只从 assistant transcript 识别 completion signal。
- 每个 stage 完成后单独输出 `AUTOPILOT_SIGNAL: <SIGNAL>`。
- RALPLAN stage 复用并串行调用 `planner` → `architect` → `critic`，Critic APPROVE 前不得实现。
- execution=team 时先拆任务并调度 `designer` / `executor` / `debugger` / `test-engineer`，主 Agent 负责最终编辑、合并与一致性。
- RALPH/verification stage 并行调用 `code-reviewer`、`security-reviewer`、`architect`、`critic` 做只读审查，必要时调用 `debugger` / `test-engineer`。
- QA stage 可调用 `test-engineer`、`code-reviewer`、`security-reviewer` 辅助，但主 Agent 负责最终汇总和完成信号。
- prompt/context harness 任务的 QA 必须验证 `off|observe|inject`、`prompt_version=context_harness_v1`、request-only compaction、trace/eval 隐私指标和 Safety/HITL 不变式；旧 text DSL prompt 回滚路径不得恢复。
- 尊重 `.trae/modes/state.json` 的互斥锁，不抢占 active RALPLAN/team/RALPH。
- 不主动 commit，不清理用户已有改动，不修改本任务批准范围外的业务代码。
