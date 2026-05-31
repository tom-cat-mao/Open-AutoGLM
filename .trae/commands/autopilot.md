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
3. 每个 stage 完成后，必须单独输出完成信号行：

```text
AUTOPILOT_SIGNAL: PIPELINE_<STAGE>_COMPLETE
```

4. 若 stop hook 已启用，它会根据 `.trae/autopilot/state.json` 和 transcript 自动推进下一 stage。
5. `--execution=team` 时，execution stage 应先拆分任务并使用 subagent 并行探索/设计/执行，主 Agent 负责合并和最终一致性。

## 当前 Stage 初始指令

若 hook 尚未注入 stage prompt，请按 Autopilot 协议从 `ralplan` stage 开始；若用户传入 `--use-current-plan` 且 `graph.mdc` 已 approved，或传入 `--planning=direct|false`，可从 `execution` stage 开始。

## 管理子命令

| 命令 | 行为 |
|---|---|
| `/autopilot status` | 显示当前 state |
| `/autopilot cancel` | 标记当前 Autopilot cancelled |
| `/autopilot resume` | 绑定当前 session 并恢复 active |
| `/autopilot reset` | 等价 cancel，下一次 `/autopilot <task>` 重新初始化 |
