# 执行文档 F：测试保守瘦身（env 层 + 变体合并）

> 读者：pi 执行 agent。自包含任务书。
> 工作区：git worktree（分支 wt/test-conservative-cut），基点 f5c3845。
> 测试：`PYTHONPATH=<worktree绝对路径> /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 基线 1276 全绿。禁止：git commit/push；**不碰 phone_agent/ 源码**；
> 不写 mock 模型判断的新测试。

## 1. 刀法（用户已批准的保守档，只两刀）

### 刀 1：环境依赖层 ~80 个
主要是 `tests/actions/test_grounding_provider.py`（59 个，加载真
`models/LocateAnything-3B-4bit`）+ `tests/graph/test_locate_resolution_tier.py`
中模型文件依赖用例。规则：
- 测试逻辑是 **grounding fail-closed 管道**（bad bbox/低置信/hash 不匹配/多候选
  → 拒绝）的：把"跑真模型"换成**合成输入**（构造 marks/bbox 字典直接走判定
  函数），保留全部判定逻辑覆盖。
- 测试本质是"模型能识别 XX 图"的**模型集成冒烟**：直接删除（bench/grounding
  套件与实机 e2e 已覆盖该功能）。
- 附带修复：pi-D 发现的 `test_locate_resolution_tier` 一用例 monkeypatch 后仍
  依赖模型文件（CWD 依赖）——改合成或删。
- 完成后 `rg -l "models/" tests/` 应为 0，套件在任何无模型机器上全绿。

### 刀 2：纯单测变体合并 ~120 个
已查明的增殖簇（同函数同路径的输入变体）：
- `tests/actions/test_adapter.py`: test_adapt_json×12、test_adapt_tool×6、
  test_validate_action×6、test_validator_rejects×4
- `tests/graph/test_goal_evaluator.py`: test_vlm_judge×11、
  test_accessibility_text×3、test_object_rank×3、test_external_probe×3
- `tests/model/test_client.py`: test_parse_response×6、test_model_config×5、
  test_ttft_breaker×5、test_build_model×4
合并规则：**只有"同一路径、同一断言结构、仅输入值不同"的变体**才合并
（用 `@pytest.mark.parametrize`）；每个变体防的是不同失败模式的必须保留，
并在刀法记录说明。宁少砍不多砍。

**绝对不动**：node_driver 201（接线/形式测试，state 频道事故的唯一回归网）、
graph 集成 18（HITL resume）、其余纯单测。

## 2. 步骤

1. 基线全绿 → 先写"## 刀法记录"（每个删除/合并一行理由）再动手，每批跑全量。
2. 刀 1 再刀 2。目标 ~1080（1276 - 80± - 120±；质量优先不硬凑）。
3. 交接节：删除/合并统计、最终数、`rg -l "models/" tests/` 为 0 的验证、
   保留的变体及理由。

## 3. 硬性约束

- P0#9 grounding fail-closed 的**判定逻辑覆盖**不得因删模型测试而减少
  （换合成输入保留）。
- 全量必须始终全绿；不得改断言让烂测试复活。
- 不 commit/push；用 rg。
