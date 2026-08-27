# 执行文档：locate 通路闭环（F-A 继承 / F-B 阶梯 / F-C 工具描述）

> 基线：949 passed（未 commit 的 H1-H5 已验收）。来源：20260803-193716 高铁任务真机运行
> （locate("无锡东站") 成功注册 locate_1 → 下一步 plan 重建 registry 将其丢弃 →
> unknown_mark → plan 侧直接 finished 终局）。双方独立审查（trace×代码）已确证。
> 哲学：**代码保证工具真实可靠+成本诚实；用不用、何时用全归模型**（不写行为规定）。
> 全程测试绿，不 commit/push，CN/EN 同步。

## F-A：locate 标记同屏继承（核心）

**事实**：`build_observation`（observation.py:543）每步 `MarkRegistry.from_marks` 从零重建，
从不读旧 registry；plan 六处返回整体写回 `mark_registry`（无 reducer）。execute 的 locate
成功分支（execute.py:527-560）写入的 locate_N 必被覆盖。真机证据：s11→s12 复合
screen_id/mark_set_version/topology_digest 完全一致（屏幕结构没变），继承本可救场。

**改法**（落点经审查确认，勿放 plan.py:812 裸合并——object_registry/screen_binding
在之前已绑旧 mark_set_version，会引入 object_stale 回归）：
1. `build_observation` 增加可选参数 `previous_registry`（dict 或 MarkRegistry）；
2. 在 `from_marks` 之后、`bound_structures`/`build_object_registry` 之前：
   若 previous_registry 存在，取其 marks 中 `mark_id` 以 `locate_` 开头者，
   用 `registry.with_extra_marks(locate_marks)` 合并——`with_extra_marks` 已有同屏门控
   （screen_id 不同自动丢弃，fail-closed）与 mark_set_version 重算，直接复用；
3. plan.py 调用 `build_observation` 处（~770-778）传 `previous_registry=state.get("mark_registry")`；
4. marks_block 渲染自然带上继承的 locate_N（模型能在 marks 列表里看到它——这次模型
   引用 locate_1 时 marks_block 里没有，只能从 last_action_outcome 间接推断）。

**测试**：跨重建用例（execute 注册 locate → build_observation 同 screen_id 重建 →
locate_N 仍在且可 grounding；screen_id 不同 → 丢弃）；附属账簿版本一致性
（object_registry.mark_set_version == 合并后版本，不触发 object_stale）。

## F-B：plan 侧 grounding 失败真走重试（消灭死元数据）

**事实**：plan 期 grounding 错误（unknown_mark 等 GROUNDING_ERROR_CODES）→
`_error_fields` 标了 `recoverable=True, retry_policy="reobserve"` 但**无任何路由消费**；
parse 失败分支无条件 `finished=True`（plan.py:1443-1474）。parse retry 仅覆盖
{"parse","adapter"} 层（plan.py:1189-1190）。

**改法**：把现有 parse-retry 机制扩展到 grounding 层——
1. grounding 层错误纳入可重试层集合（与 parse/adapter 同机制、同上限；
   `parse_retry_count` 计数或独立 `grounding_retry_count`，复用现有 policy 常量）；
2. 重试时给模型的反馈必须含：**哪个 mark_id 不存在 + 当前 marks 概要**（让模型知道
   "那个编号不存在，这些是现存的"），反馈走现有 parse-retry 的反馈通道；
3. 重试上限耗尽 → 才走 finished/失败归因（保留现有终局路径作为兜底）；
4. P0#5：edges 不动，终端守卫语义不变。

**测试**：unknown_mark → 重试一次（反馈含缺失 id）→ 模型改引用合法 mark → 成功；
重试耗尽 → 终局且归因正确；locate_N 继承后引用（F-A 场景）不再触发 unknown_mark。

## F-C：locate prompt 改为中性工具描述（CN/EN 同步）

**现状**："仅当 Screen marks 中没有目标的可执行 mark 时使用"——门禁式措辞，替模型做决定。
**改为**（语义要点，措辞可润色）：
- 能力：传入目标的视觉描述，返回一个可执行 mark（注册进 Screen marks，下一步可用）；
- 成本：约 2s 延迟；全程限 3 次（预算段已有 "locate 剩余 x/3"）；同一屏幕同一描述
  重复调用会被拒绝；
- 失败语义：0 框/多框会失败且不产生 mark；失败原因会出现在上一步结果里；
- **不写"什么时候必须用/只能用"**——判断归模型。可保留一句中性提示如
  "当 marks 不能覆盖你的目标时可以考虑它"。

**测试**：prompt 快照/关键词测试同步更新；不再出现"仅当"类门禁措辞。

## 明确不做

- 收缩/移除 observation 阶段 auto-LA fallback（下一轮单独讨论：数据显示自动注入的
  LA marks 质量差——整句查询产宽条带、错位框致 10.4 误选；模型主动 locate 查询质量高）
- 日历格子合成 mark、LA OCR 对齐校验
- graph.mdc/README 同步（Phase 收尾统一）

## 交付要求

①改动文件+内容 ②F-A 继承的版本一致性处理方式 ③F-B 重试反馈的确切形式 ④新测试清单
⑤最终 pytest 末尾 ⑥偏差说明。
