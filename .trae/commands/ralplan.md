---
description: 启动/管理 RALPLAN 共识规划：Planner → Architect → Critic，pending approval 前严禁执行
argument-hint: "[status|cancel|resume|reset|approve|reject] [--interactive] [--deliberate] [--architect <provider>] [--critic <provider>] <task>"
---

# RALPLAN Orchestrator

任务：

```text
$ARGUMENTS
```

## 执行规则

1. 读取 `.trae/rules/ralplan.mdc`、`.trae/rules/graph.mdc`。
2. 启动 Planner → Architect → Critic 串行共识循环；最多 5 轮 ITERATE。
3. Planner 只允许整体覆写 `.trae/rules/graph.mdc`；Critic 未 `APPROVE` 前不得修改业务代码。
4. `--deliberate` 或高风险任务必须包含 pre-mortem 与 expanded test plan。
5. APPROVE + Final Check 后进入 `pending_approval`，等待用户明确批准执行。

## 管理子命令

| 命令 | 行为 |
|---|---|
| `/ralplan status` | 显示当前 state 摘要 |
| `/ralplan cancel` | 取消当前 RALPLAN |
| `/ralplan resume` | 绑定当前 session 并恢复 |
| `/ralplan reset` | 清理 runtime state，保留 `graph.mdc` |
| `/ralplan approve` | 将 pending approval 交接为 approved handoff |
| `/ralplan reject` | 拒绝当前计划并终止 RALPLAN |

若 hook 尚未注入 continuation prompt，请严格按 RALPLAN 协议执行，不要进入实现阶段。
