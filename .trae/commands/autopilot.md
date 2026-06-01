---
description: 启动/管理 Autopilot：可配置 RALPLAN → execution → RALPH/verification → QA 持续执行流水线
argument-hint: "[status|cancel|resume|reset] [--planning=ralplan|direct|false] [--execution=solo|team] [--use-current-plan|--direct] [--no-verification] [--no-qa] <task>"
---

# Autopilot Orchestrator

任务：

```text
$ARGUMENTS
```

## 启动规则

1. 读取 `.trae/rules/autopilot.mdc`、`.trae/rules/ralplan.mdc`、`.trae/rules/graph.mdc`。
2. 若未显式 `--use-current-plan`、`--direct`、`--planning=direct` 或 `--planning=false`，默认先执行 RALPLAN。
3. 保持 TraeCLI-native：不迁移 `.omc` runtime，不修改 `.trae/traecli.yaml`，runtime state 仍在 `.trae/autopilot/state.json`，mode registry 仍在 `.trae/modes/state.json`。
4. 尊重 RALPLAN handoff、mode registry 与 subagent tracking；不抢占其他 active mode。
5. 不主动 commit，不清理用户已有改动，不修改本任务批准范围外的业务代码。
6. 每个 stage 完成后，必须单独输出完成信号行：

```text
AUTOPILOT_SIGNAL: PIPELINE_<STAGE>_COMPLETE
```

7. 若 stop hook 已启用，它会根据 `.trae/autopilot/state.json`、`.trae/modes/state.json` 和 transcript 自动推进下一 stage。
8. RALPLAN stage 复用并串行调用 `planner` → `architect` → `critic`；Critic APPROVE 前不得实现。
9. `--execution=team` 时，execution stage 应先拆分任务并使用 `designer` / `executor` / `debugger` / `test-engineer` 并行设计、执行、调试和验证，主 Agent 负责最终编辑、合并和一致性。
10. RALPH/verification stage 使用 `code-reviewer`、`security-reviewer`、`architect`、`critic` 只读审查，必要时调用 `debugger` / `test-engineer`。
11. QA stage 可调用 `test-engineer` / `code-reviewer` / `security-reviewer` 辅助，最终状态汇总和完成信号由主 Agent 负责。
12. 自然语言触发必须足够明确；过短且无文件/错误/测试/验收锚点的请求不自动进入 Autopilot。

## 当前 Stage 初始指令

若 hook 尚未注入 stage prompt，请按 Autopilot 协议从 `ralplan` stage 开始；若用户传入 `--use-current-plan` 且 `graph.mdc` 已 approved，或传入 `--planning=direct|false`，可从 `execution` stage 开始。

## 管理子命令

| 命令 | 行为 |
|---|---|
| `/autopilot status` | 显示当前 state |
| `/autopilot cancel` | 标记当前 Autopilot cancelled |
| `/autopilot resume` | 24 小时内绑定当前 session 并恢复 active |
| `/autopilot reset` | 等价 cancel，下一次 `/autopilot <task>` 重新初始化 |
