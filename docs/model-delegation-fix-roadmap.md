# 模型放权修复路线（执行版）

> 合并自主 Agent 草案（docs/fix-roadmap-draft-main-agent.md）与外部架构评审（pi-6）。
> 前置：模型放权重构 Phase 1/2 已完成（merge 收窄/repeat 拒绝/agenda/liveness 提示化/预算验收）。
> 总原则：**代码约束环境，模型负责判断**。模型自由文本只能向下（解释自己），不能向上（指挥下一步）。
> 仅存的代码判断（安全轨非决策）：hard_failure 覆盖、acceptance 终态验收。
> debug 模式：不做隐私脱敏收窄；恢复 think 捕获（诊断资产）。

## 信息环境不变量（每阶段交付后用 trace 断言验证）

- I1 任务原文每次 plan 请求都在场
- I2 无 reflect 自由文本/策略注入 plan prompt（通道物理删除）
- I3 里程碑钉住、不因瞬态回退；预算不截里程碑
- I4 verdict 与记忆写入隔离（一致失败才写 failure_memory）
- I5 冲突显式化（disputed 可见、可计数）
- I6 progress_note 意图连续
- I7 护栏覆盖全动作类型（含 swipe）

## P1 拆毒（最高杠杆，纯 prompt 层）

1. **删除 reflect 自由文本注入**：`plan.py` 非 step0 分支删 `_build_reflection_context` 调用（约 868-876 行）。
   ⚠️ `state["reflection"]` 字段写入必须保留——`build_mark_provider_hints`（plan.py ~790）消费它做 grounding hint。
2. **task 每步注入**：`plan.py` 非 step0 分支 text_content 首行加 `任务：{task}`（截断 200 chars，走 sanitize_context_payload inject）。
3. **reflect 角色修正**：`reflect.py:61-110` REFLECT_SYSTEM_PROMPT CN+EN 删"并给出下一步建议"类措辞；message 字段语义改为"只描述当前屏幕客观观察，禁止行动指令/目标名/输入建议"。suggested_strategy 字段保留（trace 用）但不再注入 plan（随 1 一起消失）。
4. **指令过滤保险丝**：`parse_reflection_action` 后处理：message 命中指令模式（可以输入/请点击/请搜索/建议（你|你可）/should (type|tap|search)|please (type|tap) 等，CN/EN）→ 置空并置 `reflection_directive_filtered=True`。
5. 测试：plan_prompt_debug 无 Reflection 块；task 每步在场；指令句被过滤；CN/EN prompt 同步。

## P2 换记忆（milestone + 锁存）

1. **milestone ledger 投影**：从 `goal_evidence_ledger`（goal_evidence.py）折叠 `ever_matched(criterion)`：任一 entry matched 且 `target_app_entered=True` → 里程碑"已满足（stepN 曾观察）"；`contradicted`（确定性反证）→ 解锁。渲染进 goal_agenda section。
2. **agenda 锁存**：`_render_goal_agenda`（context.py:1063）读锁存态而非当前屏态；**预算 400→800**（否则锁存信息被 vlm_judge 待验收行挤掉——评审坑#2）。acceptance 终态语义（goal_evaluator freshness=current_observation）**不动**。
3. **summarized_history 退出 prompt**：不再注入 plan（`build_plan_context_block` 移除该 section）；写入保留为 trace-only；先 rg 确认 evals/ 消费点；`REFLECT_CONTEXT_SECTION_IDS` 同步移除；`update_summarized_history` 保留写（trace 兼容）。
4. 测试：锁存三态（matched→unknown 保持；matched→contradicted 解锁；跨屏保持）；里程碑不截断；summarized_history 不出现在 plan block。

## P3 裁决与守卫

1. **冲突三层裁决**（verifier.py merge 后或 reflect 内）：
   - hard_failure → 覆盖（现状保留）
   - verifier success（置信且有 matched postconditions）vs 模型 failed/wrong_page → **disputed**：verdict=partial、failure_cause=unknown、`disputed=True`；**不写 failure_memory**；advisory 保留
   - 一致失败（或 verifier failure + 模型 failed）→ 才写 failure_memory
   - wrong_page 窄否决并入上条：模型 wrong_page 但 top_activity 已迁移（observation before/after app/activity 变化）→ 同样按 disputed 处理
2. **failure_memory 写入隔离**：reflect.py 写入路径按上规则改造；`repeated_failure_count` 语义同步。
3. **swipe repeat key**：`context.py repeated_action_key`/`action_target_center`：Swipe 无 center 时用 `(swipe, surface, direction, start/50 网格, end/50 网格)` 组 key（`_swipe_direction` 已有）。
4. **selected_object 跨页降级**：verifier selected_object 比较前置条件：before/after semantic_screen_id（或 mark_set_version）一致才比较；跨页 → 该信号 unknown 并注明 `page_changed_object_check_skipped`（评审坑#4）。
5. 测试：裁决三组合矩阵；swipe×4 触发拒绝；跨页不再出 selected_object 假 failure/假 success。

## P4 连续性（progress_note + think 恢复）

1. **progress_note**：放 `expected_outcome` **同层**（envelope 顶层会被 `_extract_provider_action_payload` 剥掉——评审坑#6）；adapter/schema 可选字段；plan 写 `state["progress_note"]`；下轮 plan prompt 注入"上轮意图：…"（sanitize 后）。
2. **think 恢复**：`model/client.py:_consume_stream` 把 reasoning_content 累加进原始内容/返回 thinking；assistant 历史消息 `<think...>` 占位符替换为真实 think。
   ⚠️ 评审坑#5：`messages_reducer`（state.py）append/replace 启发式对 assistant 格式敏感——改格式必须跑 reducer 相关测试，防 token 爆炸（P0#6）。
3. 测试：envelope 解析 progress_note；think 被捕获进 history；reducer 语义不变；stepN prompt 含 stepN-1 progress_note。

## P5 提速 ✅（已完成）

1. **reflect 模型调用默认跳过**：扩展 `_reflection_from_verifier`（reflect.py:125-142）：verifier 高置信 success（含 surface_changed/selected_object_match 路径）且无待收集 vlm_judge 证据时跳过模型，直接产 ReflectionResult；配置开关 `skip_reflect_on_high_confidence` 默认开。⚠️ 依赖 P3.4 跨页降级先落地（坑#8）。
2. **prompt cache**：goal contract block 独立成静态 message（plan 侧，参照 reflect.py:714-719）；`prompt_cache_key`/`enable_cache_control` 默认开。
3. **TTFT 熔断**：model client 统计连续 TTFT>60s 次数，≥3 次抛 model_request_failed 终止 run（不白烧 50 分钟）；指标进 trace。
4. 测试：reflect 调用数<步数；cache 标记存在；熔断单测（mock client）。

> ✅ 落地记录：`_reflection_from_verifier` 扩展为 status=success 且 confidence≥0.9（或同页 `selected_object_match`）且无待收集 vlm_judge criterion（goal_agenda 同源折叠，P2 锁存态可满足）且非 stuck → 直接产 ReflectionResult（succeeded/continue），trace `model_skipped=True`+code-only 原因；开关 `skip_reflect_on_high_confidence`（env `PHONE_AGENT_SKIP_REFLECT_ON_HIGH_CONFIDENCE`，默认 true）关闭时回到旧路径。plan 侧 goal contract block 独立为静态 user message（step0 在 system 后、非 step0 在动态消息前），evals 默认 `enable_cache_control=True` + run 级稳定 `prompt_cache_key`（`autoglm-eval:{task.id}`），main.py 同源 env 配置，provider 不兼容可经 env 关闭。TTFT 熔断：`ModelClient` 连续 N（默认 3）次 TTFT>阈值（默认 60s，非流式用 total_time 代理）抛 `TTFTCircuitBreakerError`，走 plan 现有 model 失败路径终止 run，`ttft_circuit_breaker` trace 事件携带最近 TTFT 序列；`reset_run_state()` 在 `run_structured` 开头调用，多 run 不串扰。

## 全局约束

- 每阶段：`.venv/bin/pytest tests/ -q` 全绿（基线 756）；新增/更新单测；CN/EN prompt 同步；不 commit/push。
- P0 兼容：#5 边界守卫、#13/#13a（finish 仍 fail-closed）、#6 reducer、#3 剥图、#10 隐私（非 debug 路径不脱敏收窄）全部不得破坏。
- 验收人：主 Agent 逐阶段读 diff + 独立跑测试 + 核对本文件逐项。
