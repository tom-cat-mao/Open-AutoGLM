---
name: ralplan
description: 当用户要求 ralplan、共识规划、先规划再执行、规划模式、Planner/Architect/Critic 设计循环，或模糊执行请求需要先澄清计划时使用。
---

# RALPLAN Skill

RALPLAN 是纯规划模式：Planner → Architect → Critic 迭代共识，Critic APPROVE 且用户明确批准前严禁执行代码变更。

## 使用方式

- 显式规划：`/ralplan <task>`。
- 查看/控制：`/ralplan status`、`/ralplan cancel`、`/ralplan resume`、`/ralplan reset`、`/ralplan approve`、`/ralplan reject`。
- 高风险或深度审议：`/ralplan --deliberate <task>`。

## 必须遵守

- 读取 `.trae/rules/ralplan.mdc`。
- 计划产物只写 `.trae/rules/graph.mdc`。
- Planner 只规划；Architect / Critic 只读。
- Architect 和 Critic 必须串行，不可并行。
- APPROVE 后默认停在 pending approval，除非用户明确批准执行。
