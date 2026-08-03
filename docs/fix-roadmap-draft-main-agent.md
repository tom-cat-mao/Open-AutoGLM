# 修复路线设计草案（主 Agent 独立版）

> 基于两次真机运行（0727 限速摩卡 / 0731 Chester117）的全部已验证问题。
> 核心主张：不做补丁堆砌，定义并保证**信息环境不变量**。

## 0. 设计原点

模型每步决策质量 = f(模型能力, 它看到的信息环境)。两次失败都不是模型"疯了"：
- 0727：verifier 假 success 覆盖模型正确判断（旧 merge 分支）→ 世界模型污染。
- 0731：reflect 指令性文本 + agenda 回退 + 任务原文被裁 + 历史关键段截断 →
  模型服从被误导的信息环境，理性地做错事。

结论：**系统必须保证模型每步看到"最小完备信息环境"，且任何子系统产物不得以指令形式进入该环境。**

## 1. 信息环境不变量（每条可断言、可在 trace 自动验证）

| # | 不变量 | 负责方 | 验证方式 |
|---|---|---|---|
| I1 | **任务原文**在每一次 plan 请求中完整出现（不受消息窗口影响） | plan.py 消息组装：每轮 user 消息首行注入 task | trace 断言：每个 plan_prompt_debug 的 request_messages 含 task 原文 |
| I2 | **模型自身意图**连续可见：每步 plan 输出含 progress_note（一句话"我在做什么/下一步意图"），下一步注入 | adapter schema 增加可选 progress_note；state 保存；context 注入 | trace 断言 step>1 时存在 |
| I3 | **进展里程碑**永不丢失：milestone ledger（activity 迁移 + goal 标准状态迁移），钉在 context block 头部，不参与尾部截断 | context.py 新增 milestone section（从 state.observation/current_app 历史与 goal_agenda 迁移派生） | trace 断言存在且含全部里程碑 |
| I4 | **观察-指令分离**：任何子模型（reflect）产物不得含行动指令。reflect 输出 schema 收紧为 verdict + observation（纯屏幕状态描述），suggested_strategy 保留在 trace 但不再注入 plan（FAILURE_RECOVERY_MAP 已在系统提示覆盖该职能） | reflect.py prompt+schema；plan.py _build_reflection_context 只渲染 verdict+observation | 指令模式过滤器（祈使句/“可以输入/请点击”检测）+ 单测 |
| I5 | **证据冲突显式化**：verifier 与模型 verdict 冲突时不静默覆盖也不静默接受——verdict 保留模型的，但标记 conflict 并在下轮注入"系统证据与你判断冲突：……请复核"；wrong_page 窄硬否决：top_activity 已按预期迁移时 wrong_page 不成立（强制改判 partial+conflict） | reflect.py merge 后处理；context.py 渲染冲突句 | 单测冲突矩阵；trace 双写 |
| I6 | **护栏对所有动作类型有效**：repeat guard 的 key 覆盖 swipe（start/end 坐标摘要）；verifier selected_object 只在 before/after 同 semantic_screen_id 时比较 | context.py repeated_action_key/action_target_center；verifier.py selected_object 分支前置条件 | 单测 swipe 重复拒绝；跨页不比对象 |

## 2. 逐问题映射

| 问题（0731 运行） | 不变量 | 修复点 |
|---|---|---|
| reflect 文本"可以输入博主名称 Chester117"误导 | I4 | 祸根是角色定义（reflect.py:61 "判断动作是否生效，**并给出下一步建议**"=制度性越权）；reflect prompt 已含任务原文（reflect.py:391）仍产出指令 → 角色定义+schema 必须一起改（verdict+纯观察 observation），plan 不再注入其自由文本与 strategy |
| 任务原文 step3 起消失 | I1 | plan.py:826-900 else 分支 text_content 首行加 task |
| 历史 think 抹除、意图丢失 | I2 | plan 输出 progress_note（保留在 assistant answer JSON 与 state，双通道注入） |
| summarized_history 截断丢掉 step6-9 关键进展 | I3 | milestone ledger 替代 summarized_history 头部地位；history 降为可选尾部 |
| goal_agenda 不锁存回退 | I3 配套 | agenda 里程碑型标准"曾满足即锁存（标注曾观察）"；acceptance 终态当前屏语义不动（锁存只影响 plan 侧展示，不影响验收） |
| 模型 s2-4 wrong_page 误报长驱直入 | I5 | 冲突标记 + wrong_page 窄硬否决（activity 已迁移） |
| s5 verifier 跨页误判 failure | I6 | selected_object 比较前置 semantic_screen_id 一致 |
| swipe×4 漏检 | I6 | swipe key = hash(start,end,app/screen) |
| TTFT 155-161s（外部） | — | model client 加 TTFT 熔断：连续 N 次超阈值（如 60s）→ 报错终止 run（model_request_failed），不白烧 50 分钟；运维侧换端点/关 thinking |

## 3. reflect 去留结论（明确）

**保留调用，阉割文本。**理由：
- 0731 中 reflect 的 verdict 本身没错（s10 succeeded 正确），毒在自由文本指令；
- verifier 同样有噪声（s5 跨页误判），删 reflect 模型调用等于让 verifier 单通道判决；
- 但 reflect 的"建议下一步"职能是越权的（P0#13 本来就规定它只答单步生效）——
  FAILURE_RECOVERY_MAP 已把 failure_cause→动作映射放在 plan 系统提示里，suggested_strategy
  注入 plan 是重复且有害的通道。
- 远期（单模型调用/步改造）再整体废除 reflect 调用；本次不动调用拓扑，风险最小。

## 4. 成本评估

每步新增注入：task(38) + progress_note(~40) + milestones(~150) + conflict(0~100) ≈ **+330 chars**；
对比 marks block 3.4-12.9k + objects 3.9k，增幅 <3%，可忽略。prefix cache 只依赖 system 前缀，不受影响。

## 5. 阶段计划

- **S1（prompt 完整性，1-2 天）**：I1 task 行、I2 progress_note、I4 reflect 文本阉割、I3 milestone 渲染（数据源已有）。纯 prompt/context 层，不动路由。
- **S2（语义正确性，1-2 天）**：I5 冲突标记+wrong_page 窄否决、I6 swipe key+跨页前置、agenda 锁存。
- **S3（运维，0.5 天）**：TTFT 熔断 + 端点/thinking 配置。
- **S4（验证工装，1 天）**：run_diagnosis 增加不变量断言（每请求检查 I1-I4），0731 录制 trace 重放回归。
- 每阶段独立可回滚；P0 兼容：I4 是 P0#13 的强化而非违反；I5 不恢复静默覆盖；acceptance fail-closed 不动。

## 6. 自挖的坑

1. progress_note 可能自我强化错误意图——它是模型自述信念，需标注"仅为自述"，截图优先。
2. I4 的指令过滤器对中文祈使句脆弱——以角色重定义为主、schema 为辅、过滤兜底；只改 schema 不改 prompt 角色是无效的（reflect 模型不是任务盲，错在角色授权它给建议）；
3. agenda 锁存对瞬态标准（toast/键盘可见）可能错误固化——锁存只适用里程碑型（导航/实体到达），状态型（toggle）不锁；需要在 predicate 元数据区分。
4. task 每轮注入对超长任务文本需截断保护（如 200 chars）。
