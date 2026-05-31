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
2. 调用 `planner` subagent：基于需求制定或修订 roadmap，并直接更新 `.trae/rules/graph.mdc`。首轮使用 `planner_mode=initial_rewrite`；Critic `ITERATE` 后使用 `planner_mode=feedback_revision`，只针对 findings 做窄修。
3. Planner 完成后，调用 `architect` subagent：只读审查 `.trae/rules/graph.mdc`，输出架构审查报告。
4. Architect 完成后，调用 `critic` subagent：读取 `.trae/rules/graph.mdc` 和 Architect 报告，输出 `APPROVE` / `ITERATE` / `REJECT` verdict。
5. 如果 Critic verdict 是 `ITERATE`，把 findings 交回 Planner 修订；最多迭代 5 轮。
6. 如果 Critic verdict 是 `APPROVE`，先将最终完成态写入/确认到 `.trae/rules/graph.mdc`（`design_status=critic_approved`、`approved_for_execution=true`），再重新读取并执行 Final Check；检查通过后停止等待用户确认是否进入实现。
7. 如果 Critic verdict 是 `REJECT`，更新 `Design Loop Status` 为 `rejected`，并向用户说明需要澄清的问题。

## Final Check

Critic `APPROVE` 后必须确认：

- `.mdc` frontmatter 存在且包含 `description`、`alwaysApply`、`globs`。
- `design_status: critic_approved`、`last_critic_verdict: APPROVE`、`approved_for_execution: true` 三者一致。
- `execution_status` 合法；设计已批准或执行中时，`planner_mode` / `revision_scope` 可为 `null`。
- Roadmap 包含当前 Phase、目标/根因、修改文件候选、验收标准。
- `graph.mdc` 未嵌入完整 Architect/Critic 报告或历史 diff。

## 禁止事项

- Critic 未 `APPROVE` 前，不得开始修改业务代码。
- 不要把完整审查报告塞进默认上下文；报告只在本轮 loop 中传递，最终状态写入 `Design Loop Status`。
- 不要跳过 Architect 或 Critic。
- 不要让 Planner 使用 Edit/MultiEdit 追加或局部修改 `graph.mdc`；Planner 必须 Write 完整合法文件。
