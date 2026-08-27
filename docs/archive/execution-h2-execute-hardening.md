# 执行文档 H2：confirm reobserve + hash 对齐 + 残留清理 + 断档 + answer 上限（执行侧）

> 读者：reasonix 执行 agent。自包含任务书，问题均已由主 agent 代码验证。
> 工作区：/Users/bytedance/Open-AutoGLM-fixH2（worktree，分支 wt/fix-h2-execute，基点 b822126）。
> 测试：`PYTHONPATH=/Users/bytedance/Open-AutoGLM-fixH2 /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 基线 **1304 全绿**。禁止：git commit/push；FakeModel 式 mock 判断测试；改任务书外代码；用 rg。
> 设计哲学：代码=形式/管道，模型=内容判断。P0#5（陈旧 pending 不得路由）、P0#6
> （plan 只 append/execute 可 replace）、P0#9（grounding fail-closed）不可破。

## Fix C：confirm 接受后的 mark 新鲜度校验（P0#9 精神）

**背景**：confirm 中断插入任意长人工延迟；resume 后 pending_execute 分支
（execute.py:830-945）拿 plan 帧的旧 mark_registry 直接 dispatch，屏幕可能已变。
**改动**（execute.py pending_execute 分支，dispatch 之前）：
1. 通过 configurable 的 device_factory 抓一帧新截图，与 mark_registry 绑定的
   screen_hash 比对（先读 mark_registry/grounding 现有 stale/hash 校验模式，复用其
   hash 来源与比较方式，不新造概念）。
2. 一致 → 照现有路径 dispatch（无 mark 的动作如 Launch/back/wait 跳过校验直接执行）。
3. 不一致/截图失败 → **fail-closed**：不 dispatch；返回：
   `pending_execute=False, pending_interrupt=None, interrupt_result=None` +
   skinny 化 messages（瘦行 `sN: <action> → 屏幕已变化，需重新观察`）+
   gui_memory 记录（had_effect=False 不写——动作未执行，不产生 tried_actions 条目；
   读现有代码确认正确字段）+ context 备注。路由走 replan（对照 repeat 拒绝分支的路由方式）。
4. trace 增加 `confirm_stale_reobserve` 事件（add-only）。
**测试**：hash 一致→dispatch 执行；不一致→不 dispatch+replan+pending 全清；无 mark
动作→直通；截图异常→fail-closed。

## Fix D：verifier hash 算法对齐（一行级+消费方核对）

**背景**：verifier.py:338 `before_hash = before_state.get("screen_hash") or ...screen_id`
（raw sha256）vs after `build_screen_id`（语义 identity）→ 恒不等 → `screen_changed`
弱信号恒真，`content_shifted` 期望恒 unknown，reflect prompt 每步被注入系统噪声。
**改动**：before 侧优先取 `before_state.get("screen_id")`（plan 帧 build_screen_id 输出，
同算法；先确认 plan 确实写顶层 screen_id——rg `_observation_state_fields`）。raw
screen_hash 兜底逻辑保留或移除视 state 实际可用字段定（以同算法为唯一标准）。
reflect 的 had_effect（raw-vs-raw）**不动**——两侧语义分工保持。
**测试**：同语义屏→screen_changed False；换页→True；content_shifted 期望不再恒 unknown。
核对 verifier 消费方（skip 门控/disputed）行为无回归（它们只吃 success，已确认）。

## Fix E：pending 残留清理（3 处）

1. execute.py repeat 拒绝分支（469-490 区域）return 增加：
   `pending_execute=False, pending_interrupt=None, interrupt_result=None`。
2. execute.py `confirmation_required` 分支（844-854 区域）同样补清。
3. takeover.py（35-38 区域）清 `interrupt_result=None`（先读该节点现有返回字段，
   保持 add-only 风格）。
**测试**：各分支返回 dict 断言三字段清空；现有路由测试不回归。

## Fix G：终局路径轨迹断档收口（execute 统一 skinny）

**背景**：plan 4 条失败路径的 fat tail（~13-16K 字符）进 state 后无人瘦化
（execute passthrough 不碰 messages）；execute 自身 4 条终局分支同样不瘦化；
resume 后 plan 兜底误替换（`sN: unknown → failed` 语义丢失，多残留时全部同一行）。
**改动**（全部在 execute.py，replace 是它的权利）：
1. 新增 `_terminal_messages(state, skinny_line)` 辅助：对 `state["messages"]` 做
   `replace_fat_tails_with_skinny` + 追加失败瘦行（格式 `sN: <action|unknown> → failed:
   <error_code>`，error_code 取现有 _layered_error 的 code 字段）。
2. 应用于：passthrough 分支（execute.py:314-337，finished and error）、validation
   error（352-375）、safety rejected（509-546）、capability_missing（1030-1045）。
   原本不返回 messages 的分支改为返回瘦化后的 messages；原本返回 messages 拷贝的
   分支改为返回瘦化版本。
3. 非终局路径一律不动。
**测试**：终局后 state.messages 无胖 tail 残留、失败瘦行在场且编号正确；resume 场景
plan 兜底不再触发误替换；非终局路径消息不变。

## Fix H：assistant answer 上限（并入剥 think 通道）

**背景**：`_strip_think_from_history`（execute.py:115-131）剥 think 后，answer
（action_raw JSON 200-800+字符，无上限）成为长任务唯一未设防膨胀源。
**改动**：同一函数遍历历史 assistant 时加：**历史 answer 超 ~500 字符截断+省略标记**
（如 `…[truncated]`）；**最新一条 assistant 保持完整**。截断破坏历史 JSON 无妨
（adapter 只解析最新响应；历史消息纯上下文）。docstring 注明与瘦行 200 上限同款
形式机制。
**测试**：20 步合成会话 assistant 历史字符数有界；最新 assistant 逐字节完整；
无 think 块消息行为不变。

## 完成标准

- 五项全落地，全量测试绿（基线 1304，新增用例另计）。
- 每项至少 2 个确定性单测；P0#5/#6/#9 不破；trace add-only；CN/EN 同步（若动提示文本）。
- 文档末尾写"## 交接"：改动文件:行、测试数变化、与任务书不符的事实。
- 未 commit/push。
