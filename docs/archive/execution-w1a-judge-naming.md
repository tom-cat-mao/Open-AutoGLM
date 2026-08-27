# 执行文档 W1-A：vlm_judge 验收命名对齐修复

> 基线：1013 passed（commit 3d39681）。来源：20260804-135901 真机运行——任务行为层
> 全通（日历/滑块/筛选全对），finish 声明规范，但验收 failure：
> `flight_search_parameters / cheapest_flight_result` 均 `missing`，
> reason=`no_named_evidence_from_reflect`（goal_evaluator.py:745）。
> 全程测试绿，不 commit/push，CN/EN 同步。

## 根因（已双证核实）

1. acceptance 有自己的判官：`_run_semantic_judge`（acceptance.py:437），finish 声明时
   调 VLM 输出 `{completed, message, named_evidence:[{criterion, screen_reference, ...}]}`；
2. 评估器（goal_evaluator.py:274-298, 745）按 **criterion 名字符串精确匹配**契约标准名
   建索引；判官模型写的是自由文本（大小写/空格/改写漂移）→ 查无此名 → missing；
3. 叠加：reflect 侧 prompt（reflect.py:75/96）规定 named_evidence 仅在 reflect 自报
   finish 时产出——历史证据通道近乎永远为空。**本伦只修 acceptance 判官链路**，
   reflect 闸门留给 task_plan 轮收窄。

## A1：判官 prompt 强约束标准名（acceptance.py，CN/EN 同步）

- 判官 prompt 已列出标准（81-102 附近）。加固为：
  - 逐条给出标准的**精确名称**（白名单形式："以下标准名必须逐字使用：
    flight_search_parameters / cheapest_flight_result"）；
  - 明确"named_evidence 的 criterion 字段必须逐字等于上述名称之一，禁止改写、
    翻译、大小写变化"；
  - completed=true 时每个 required 的 [judge] 标准都必须有一条 named_evidence，
    缺一不可（缺=视为未完成）。

## A2：评估器规范化容错匹配（goal_evaluator.py）

- 建 named_evidence_map 时对 criterion 键做规范化：`casefold + 空白/连字符/下划线归一`
  （"Flight Search Parameters" / "flight search parameters" → flight_search_parameters）；
- **保持 fail-closed**：规范化只修格式漂移，不做语义模糊匹配；对不上的名字依然 missing；
- 判官输出了契约外标准名 → 忽略并记 trace（不可用来满足标准）。

## A3：可诊断性

- acceptance 的判官原始回复（completed/message/named_evidence 名称列表）进 trace
  payload（只增字段；observed_value 等文本按 P0#10 脱敏规则处理）——现在看不到判官
  说了什么，无法归因。

## 测试要求

- 判官返回精确名 → 通过；大小写/空格漂移 → 规范化后通过；错误名/缺条 → 仍 missing
  （fail-closed 锁）；
- prompt 快照测试同步（CN/EN 含白名单表述）；
- 回归：现有 acceptance/finish-gate 测试全绿；
- 全量 .venv/bin/pytest tests/ -q 绿（1013 基线±W1-B 并行改动）。

## 交付

①改动文件+内容 ②规范化函数形式与不误放论证 ③新测试清单 ④最终 pytest 末尾 ⑤偏差说明。
