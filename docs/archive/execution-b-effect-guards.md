# 执行文档 B：守卫效果化（重复守卫 + locate 额度 + liveness 统一效果信号）

> 读者：pi 执行 agent。自包含任务书。
> 工作区：git worktree（分支 wt/effect-guards），基点 commit 17a7e25。
> 测试：`PYTHONPATH=<worktree绝对路径> /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 先验证导入路径打印的是 worktree 内 phone_agent。
> 禁止：git commit/push；禁止 FakeModel 式 mock 判断测试（只写确定性单测）。

## 1. 问题（真机实证）

同程机票 run：agent 在筛选面板拖时间滑块，每次拖拽都有效（面板值 08:00→07:00
逐步逼近 06:00），但：
- 重复动作守卫按**动作指纹**计数（swipe key=方向+50单位起止格），第 3 次同指纹
  拖拽被硬拒（`nodes/execute.py:275`，阈值=2，`config/policy.py:303`）
- locate 全局额度 `LOCATE_MAX_PER_RUN=3`（`config/policy.py:20`）**成功也计数**
  （`nodes/execute.py:427,484`；`graph/tools/locate.py:449` 硬门），3 次成功
  locate 后额度耗尽 fail-closed → agent 认为无可执行路径 → takeover → 任务死亡。
- `graph/context.py:1128-1151` 还在提示词里渲染"locate 剩余 X/3"，稀缺暗示
  本身诱导放弃。

核心逻辑错误：**守卫数次数，不看效果**。人监工只看"情况有没有在变化"。

## 2. 代码事实（已核实）

- `graph/context.py:1175 repeated_action_key`：tap=20 单位几何桶、
  swipe=(direction, 50 单位起止格)、locate=`_locate_repeat_key`(hint 摘要+surface)。
- `graph/context.py:671 detect_repeated_action`：供 avoid_repeating 注入使用；
  docstring 自述"对效果全盲"。
- `nodes/execute.py:275-345`：prior>=threshold → 硬拒 + 写 gui_memory 保持计数
  递增（此写入逻辑保留）。
- 效果信号全部现成：reflect 每步产 `screen_hash/screen_id` 变化、
  `model_observation` 账本条目（判据观察）、reflect verdict。
- gui_memory.tried_actions 是计数来源（reflect/execute 都会写，形状一致）。

## 3. 设计

1. **统一效果判定**（context.py 或新模块，纯函数）：
   ```python
   def action_had_effect(*, before_screen_hash, after_screen_hash,
                         new_observation_count: int, verdict: str | None) -> bool:
       # 屏幕变化 OR 有新判据观察 OR verdict=="succeeded" → 生产性
   ```
2. **重复计数改造**：tried_actions 条目写入时附带 `had_effect: bool`（reflect 尾部
   判定写入；execute 拒绝路径的写入保持 had_effect=False）。
   守卫判定改为：同一 key **连续 had_effect=False** 的条目数 >= threshold 才拦截；
   任何 had_effect=True 的条目重置该 key 的连续计数。滑块场景（每次拖值都变）
   永不触发；真死循环（同目标、屏幕不变）2 次后仍被拦截。
   `detect_repeated_action`（avoid_repeating 注入）同步改为连续无效果语义。
3. **locate 额度**：`LOCATE_MAX_PER_RUN` 从 3 提到 **20**（纯 runaway 保险丝，
   正常任务碰不到）；失败/无效果的重复 locate 由上面的连续无效果守卫接管。
   删除 context 中"locate 剩余 X/3"倒计时渲染（CN/EN 两处在 context.py 内，
   不是 prompts 文件）；execute.py:427/484 注释与计数语义更新。
4. **liveness**：已是观察驱动（17a7e25）；确认其"新观察/新屏幕"信号与
   `action_had_effect` 概念一致，docstring 互相引用即可，不强求代码合并。
5. trace：重复拦截事件增加 `consecutive_no_effect` 字段（add-only）。

## 4. 步骤

1. `action_had_effect` 纯函数 + 单测（真值表全覆盖）。
2. tried_actions 写入路径加 had_effect（reflect 尾部 + execute 拒绝路径）；
   读取处兼容旧条目（无字段视为 False）。
3. 守卫判定改造（execute 门 + detect_repeated_action）。
4. locate 额度调整 + 删倒计时 + 注释更新。
5. 测试（全部确定性单测）：
   - 滑块形态：同 key×5 但每条 had_effect=True → 不拦截
   - 真循环：同 key×2 had_effect=False → 第 3 次拦截；插入一条 True 后计数重置
   - locate：成功 locate（had_effect=True）不计入守卫计数；保险丝 20 仍在
   - context block 不再含 locate 倒计时
   - 旧格式 tried_actions 条目兼容
6. 全量测试绿，写交接节。

## 5. 硬性约束

- P0#8/#9（grounding fail-closed）、Safety Gate、ActionIR 管线不动。
- gui_memory/tried_actions 的写入者与 reducer 语义不动（只加字段）。
- 提示词文本改动仅限删除 locate 倒计时；CN/EN 同步。
- 禁止 mock 模型判断的测试；tried_actions/账本全部用合成数据直造。
- 不 commit、不 push。用 rg。
- 完成标准：全量测试绿；交接节含变更文件、测试数变化、遗留风险。

## 交接

### 变更文件

- `phone_agent/graph/context.py`：新增纯函数 `action_had_effect`（before/after screen_hash、new_observation_count、verdict=="succeeded" 三信号，None hash 视为无信号、fail-closed）与 `consecutive_no_effect_count`（从尾部回扫同 key 条目，had_effect=True 重置、异 key 不重置、旧条目视为 False）；`detect_repeated_action` 改为连续无效果语义；`update_gui_memory` 新增 `had_effect` 参数（默认 `_derive_had_effect`：failure_cause/成功=False 结果→False，verdict=="succeeded"→True）；`_build_avoid_repeating` 与 liveness note 的 repeat_count 改用无效果连续计数；`build_budget_section` 删除 locate 倒计时（CN/EN）；`trajectory_liveness` 与 `action_had_effect` docstring 互相引用。
- `phone_agent/graph/nodes/execute.py`：守卫门改 `consecutive_no_effect_count`；重复拦截 trace 增加 `consecutive_no_effect`（add-only）；拒绝路径、locate 失败/registry_missing 写 `had_effect=False`，locate 成功写 `had_effect=True`；locate 预算注释更新（纯保险丝）。
- `phone_agent/graph/nodes/reflect.py`：reflect 尾部用 `action_had_effect` 判定（before=state.observation.screen_hash、after=本次 snapshot.screen_hash、new_obs=len(criteria_observations)、verdict）写入 had_effect；repeat_count 改用无效果连续计数。
- `phone_agent/config/policy.py`：`LOCATE_MAX_PER_RUN` 3→20（纯 runaway 保险丝）+ 注释。
- `phone_agent/config/prompts_zh.py` / `prompts_en.py`：删除 "全程限 3 次（预算段显示 locate 剩余 x/3）"/ "max 3 per run (see the budget block...)"（仅删 locate 倒计时/限额表述）。
- 测试：`tests/graph/test_effect_guards.py`（新增 22 个确定性单测）；`tests/graph/test_execute_locate.py`（原"第 3 次同查询拒绝"改为"成功 locate 连续放行" + 新增失败 locate 循环拦截测试）；`tests/graph/test_continuation.py`（倒计时测试改为断言倒计时消失）。

### 测试数变化

起始 1186（1185 通过 + 1 环境失败）→ 完成 1209 全绿（+23：test_effect_guards.py +22，test_execute_locate.py 净 +1）。全部为确定性合成数据直造，无 FakeModel 式 mock 判断。

### 环境说明

基线中 `tests/graph/test_locate_resolution_tier.py::test_observation_fallback_path_keeps_960_tier` 在 worktree 失败：`models/` 是 gitignored 目录，worktree 未物化，`LocateAnythingMLXProvider.model_path`（相对 CWD）不存在 → `model_not_found`。已建符号链接 `models -> /Users/bytedance/Open-AutoGLM/models`（不被 git 跟踪，无提交影响）恢复全绿。

### 语义要点

- 重复判定只看"连续无效果"：同 key 任一条 had_effect=True 即重置该 key 计数；旧条目（无 had_effect 字段）读时按 False 处理（兼容）。
- 成功 locate 是进步（注册了新可执行 mark）→ had_effect=True，不计入守卫；失败/无效果 locate 由连续无效果守卫在阈值（2）拦截；locate_count 仍每尝试 +1，但 20 的保险丝正常任务碰不到。
- 拒绝路径仍写 gui_memory（计数递增逻辑保留），had_effect=False。

### 遗留风险

- 真机"每步都 succeeded 且屏幕不变"的循环（旧 `detect_repeated_action` 覆盖场景）：新语义下 verdict=="succeeded" 视为有效果，守卫不再拦截——此类循环改由 `trajectory_liveness`（novelty 耗尽→stuck→replan）接管，未在本次改动中合并两套信号，需真机回归确认。
- 屏幕 hash 在"内容轻微变化但目标未变"时仍会判定有效果（hash 级变化即信号），理论上可被内容抖动刷掉连续计数；粒度细化（如 semantic_screen_id）留待后续。
- `partial` verdict 不重置计数（严格判定），死循环场景按设计仍会在 2 次无效果后被拦截，代价是"部分进展但屏幕未变"的步骤会累积计数——可接受，真机观察后如需放宽再调。
