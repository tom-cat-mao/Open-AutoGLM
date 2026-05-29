---
description: 按 Planner → Architect → Critic 顺序为需求生成并审查 graph.mdc roadmap
argument-hint: <task-or-requirement>
disable-model-invocation: true
---

# Design Loop Orchestrator

针对以下需求运行 Plan-Architect-Critic 设计循环：

```text
$ARGUMENTS
```

## 执行规则

1. 先读取 `.trae/rules/design-loop.mdc` 和 `.trae/rules/graph.mdc`。
2. 调用 `planner` subagent：基于需求制定或修订 roadmap，并直接更新 `.trae/rules/graph.mdc`。
3. Planner 完成后，调用 `architect` subagent：只读审查 `.trae/rules/graph.mdc`，输出架构审查报告。
4. Architect 完成后，调用 `critic` subagent：读取 `.trae/rules/graph.mdc` 和 Architect 报告，输出 `APPROVE` / `ITERATE` / `REJECT` verdict。
5. 如果 Critic verdict 是 `ITERATE`，把 findings 交回 Planner 修订；最多迭代 5 轮。
6. 如果 Critic verdict 是 `APPROVE`，更新 `.trae/rules/graph.mdc` 的 `Design Loop Status` 为 `critic_approved`，并停止等待用户确认是否进入实现。
7. 如果 Critic verdict 是 `REJECT`，更新 `Design Loop Status` 为 `rejected`，并向用户说明需要澄清的问题。

## 禁止事项

- Critic 未 `APPROVE` 前，不得开始修改业务代码。
- 不要把完整审查报告塞进默认上下文；报告只在本轮 loop 中传递，最终状态写入 `Design Loop Status`。
- 不要跳过 Architect 或 Critic。
