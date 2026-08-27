# 执行文档 D：测试大清算 + 真机形态 fixture 回放

> 读者：pi 执行 agent。自包含任务书。
> 工作区：git worktree（分支 wt/test-reckoning），基点 = 主线最新 HEAD。
> 测试：`PYTHONPATH=<worktree绝对路径> /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 先验证 import 路径；先跑基线（当前 1279 全绿）。
> 禁止：git commit/push；本任务**不写任何新 mock 判断测试**。

## 1. 背景与刀法

1186→1279 的测试大半测的是"代码理解世界"的机器（匹配器/决策表/span），
这些机器已被删或重写；其余大量是 FakeModel 喂预设输出再断言系统**判断**的
演戏测试——它们全绿却拦不住任何一次真机失败。用户裁定：只保留单测。

**逐文件过刀，三问定生死**（答不出就删，并在交接节给一行理由）：
1. 它防的是哪次真实失败 / 哪条 P0？
2. 它测的是代码的形式/管道逻辑，还是模型的内容判断？
3. 删掉它，哪条活代码路径会失去唯一覆盖？

## 2. 三层教义

**保留（管道/形式，确定性）**：账本追加/有界/脱敏、引用形式校验、state 频道
接线、封缄/撤缄机制、效果守卫（action_had_effect/连续无效果）、HITL resume、
launch 解析链、边守卫、reducer 语义、ActionIR 管线、坐标转换、grounding
fail-closed、消息三段式结构、注册表 inventory/学习缓存、重复/locate 守卫。

**删除（判断演戏/死机器）**：
- FakeModelClient/预设模型输出 → 断言系统"判断对不对"的测试（parse 契约除外）
- 测已删机制（span/匹配器/决策表/E 级）残留的
- 与保留测试覆盖重复的（同一路径同等断言）
- `rg -l "FakeModel" tests/` 的 12 个文件是重点审查对象；`test_plan_reflect.py`
  （105 个）、`test_provenance_validation.py`、`test_goal_evaluator.py`、
  `test_task_plan.py`、`test_goal_compiler_chain.py` 逐个过

**新建（fixture 回放，数据驱动单测）**：从真机 run 形态提炼**合成最小化**
数据（脱敏、不含原始任务文本），回放折叠代码路径：
1. run G 面板观察→缺口清单✅→judge 引用通过（已有 test_model_delegated_evidence，
   检查保留并补齐反例）
2. 滑块生产性重复：同 key×5 带效果→放行；穿插无效果→拦截（tests/graph/
   test_effect_guards.py 已有基础，检查充分性）
3. HITL 登录墙：takeover→resume→续跑状态完整（tests/agent/test_hitl_resume.py
   已有，检查充分性）
4. 残留启动：首屏即满足部分判据→观察入账→finish 时 judge 拿轨迹摘要判因果
   （ledger 折叠路径，合成数据）
5. 编译自查：契约缺参数判据→self_check 修复循环一次→补齐（若已是 mock 判断
   形式则改写为纯结构断言）

## 3. 步骤

1. 基线全绿后，先产出**删除清单**（文件+用例名+一行理由）写在文档"## 刀法记录"
   节，再动手；每删一批跑全量。
2. 重点审查 FakeModel 12 文件：parse 契约类保留（"模型返回X形态→代码形式处理"），
   判断类删除。
3. 新建/补齐 fixture 回放（上文 5 形态，缺啥补啥，全部合成数据）。
4. 目标区间 **400-600 个测试**（质量优先，不硬凑数；若某文件全部值得保留，
   说明理由）。
5. 交接节：删除统计（按理由分类）、保留率最高的文件 Top5 及理由、新增 fixture
   清单、最终测试数。

## 4. 硬性约束

- **不碰 phone_agent/ 源码**（发现源码 bug 只记录在交接节，不修）。
- P0 衍生测试一律保留（边守卫/reducer/HITL/隐私脱敏/coordinate/grounding
  fail-closed/ActionIR/Safety Gate）。
- 删除后全量必须全绿；不得通过改断言让烂测试复活。
- 不 commit、不 push；用 rg。
