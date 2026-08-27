# 执行文档 E：Plan 上下文真三段式（缓存友好，杀滑动窗口）

> 读者：pi 执行 agent。自包含任务书。
> 工作区：git worktree（分支 wt/plan-context-cache），基点 commit 17a7e25。
> 测试：`PYTHONPATH=<worktree绝对路径> /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 先验证 `import phone_agent` 打印 worktree 内路径。
> 禁止：git commit/push；禁止 FakeModel 式 mock 判断测试（只写确定性单测）。

## 1. 问题（代码实证）

prefix 缓存只命中"逐字节一致的最长前缀"。plan 每步请求被三个设计抵消：
- **滑动窗口**：`graph/context.py:46 request_recent_messages: 6`，`context.py:2432
  _bound_request_messages` 超过 6 条即砍最旧消息——每步 prefix 移位，只有
  system 一条能稳定命中。
- **胖历史**：每步用户消息带当前屏全量 marks（实测 7231 字符）+objects+context；
  `context.py:2513 _compact_message` 只剥图片不剥文本——历史步 marks 文本全部
  留在请求里直到被窗口砍掉。旧 marks 对决策无意义（每步重新观察）。
- **契约块每步重发**：`graph/nodes/plan.py:1010` 每步 append 新契约块消息
  （因旧的会被窗口砍掉），永远进不了稳定前缀。

reflect（reflect.py:1125-1138）与 judge（acceptance.py:1166-1173）已是
"静态 system+契约块 / 动态尾"的正确结构，**本任务不动它们**。

## 2. 目标结构

```
[永久 prefix]  system + 契约块 + task            ← 只发一次，任何裁剪不得触碰
[瘦轨迹]      每步一行 "sN: <action摘要> → <结果摘要>"（append-only，永不丢）
[当前 tail]   当前截图 + 当前 marks/objects + context_block + screen_info
```
- 历史步 marks/objects/context **不再出现在请求**；轨迹一行保留"做过什么"。
- 契约块只在 step 0 注入一次；删除每步重发逻辑。
- `_bound_request_messages` 删除（瘦轨迹下无存在理由）；`_compact_message`
  剥图片保留（P0#3 双保险）。

## 3. 实现要点（已核实的现状，直接依赖）

1. **瘦消息生成**：plan 的每步新消息仍是"胖 tail"（模型需要当前 marks 决策，
   不变）；**进入 state 前把上一步的胖 tail 替换为瘦行**——在
   `plan.py` 现有 `remove_images_from_message` 同款位置（plan.py:1044 附近
   state_messages 构造）与 execute 全量重建路径（P0#6 reducer 语义不动：
   plan 只追加、execute 全量重建）实施。瘦行格式：
   `s{N}: {action_type} {target摘要} → {result摘要}`，从 state 的
   action_parsed/action_result 构造，长度硬上限 ~200 字符，脱敏后写入。
   assistant 动作消息保留（动作记录，几百字符可接受）。
2. **契约块**：step 0 注入一次；删除 plan.py:1010 的每步重发分支。
3. **task**：step 0 放入 prefix（与契约块同区）；screen_info 是变量留在 tail。
4. **删窗口**：`_bound_request_messages` 及 `request_recent_messages` 配置删除
   （rg 全部引用点，含测试）；`compact_messages_for_request` 保留剥图片。
5. **context_block/marks/objects**：只出现在当前 tail（现状已是，确认不回归）。
6. trace：`prompt_block_chars` 键保持（add-only），新增
   `trajectory_skinny_chars`/`prefix_pinned: true` 字段。
7. **保持一致性纪律**：任何带步号/时间戳/计数器的文本不得进 prefix
   （system 的"今天的日期"按天稳定，保留）。

## 4. 测试（全部确定性单测，禁止 mock 模型）

- 消息结构：step 0 后请求 prefix=[system, 契约, task]；step N≥1 时历史只含
  瘦行（无 marks_block 文本残留）；契约块全请求仅出现一次
- 瘦行生成：从 action_parsed/action_result 合成，超长截断、脱敏生效
- reducer 语义：plan 返回值只含新消息；execute 全量重建后旧胖 tail 已瘦化
- `_bound_request_messages` 引用清零；20+ 步合成会话请求 token 估算显著下降
  （断言字符数阈值）
- P0#3：请求中仅最新用户消息含 image_url（回归）

## 5. 硬性约束

- P0#3 图片剥离、P0#6 reducer 语义、P0#10 脱敏不可破。
- reflect/judge/locate 的消息构造一律不动。
- CN/EN 相关文本同步（瘦行模板两种语言）。
- 不 commit、不 push；用 rg。
- 完成标准：全量测试绿；文档末尾写"## 交接"（变更文件、测试数变化、
  20 步合成会话前后 token 估算对比、遗留风险）。

## 交接

### 变更文件（worktree wt/plan-context-cache，基点 17a7e25）

| 文件 | 变更 |
|---|---|
| `phone_agent/graph/context.py` | 删除 `_bound_request_messages` 与 `DEFAULT_CONTEXT_BUDGET["request_recent_messages"]`（滑动窗口彻底移除）；新增瘦轨迹工具：`build_skinny_trajectory_line`（`s{N}: action target → result`，inject 脱敏 + 200 字符硬上限）、`is_fat_tail_message`（按 Screen Info/Marks/Objects 稳定标记识别胖 tail）、`replace_fat_tails_with_skinny`（按 assistant 前缀数推步号，append-only 语义）；`compact_messages_for_request` 仅保留剥图（P0#3 双保险） |
| `phone_agent/graph/nodes/plan.py` | step 0 拆分永久 prefix：`[system, 契约, task]` 各一条消息 + 当前 tail（screen_info+objects+marks+context+截图，统一 `** Screen Info **` 头）；删除 step N≥1 契约块每步重发分支；请求构造时对 state 历史做胖 tail 安全网替换（不改 state，只改请求副本，reducer append 语义不变）；trace 新增 `prefix_pinned: true` 与 `trajectory_skinny_chars`（`prompt_block_chars` 保持 add-only） |
| `phone_agent/graph/nodes/execute.py` | 全量重建路径（P0#6 replace 语义）把当前步胖 tail 替换为瘦行（`_strip_and_append` 增 `skinny_line` 参数，`_skinny_for_step` 从 `action_parsed`+本地 result 合成，步号取历史上 assistant 条数）；confirm 首过写"待确认/awaiting confirmation"占位行，二次 dispatch 成功后原地替换为真实结果；无用户消息时 no-op |
| `tests/graph/test_plan_reflect.py` | 更新 4 个结构断言：step0 消息 3→4（system/契约/task/tail）、契约每步重发改为"全请求仅一次"、task 文本移入 prefix 消息、compaction 计数 5→4 |
| `tests/graph/test_plan_context_cache.py` | 新增 22 个确定性单测（无模型判断断言，plan-node 用录制式 client 只断请求结构） |

### 测试数变化

- 基线（改前）：1185 passed + 1 failed = 1186
- 改后：1207 passed + 1 failed = 1208（净增 22 个确定性单测）
- 唯一失败 `tests/graph/test_locate_resolution_tier.py::test_observation_fallback_path_keeps_960_tier` 为**改前即存在的环境性失败**（LocateAnything MLX 模型文件 `model_path.exists()` 为 False，机器未下载模型），与本改动无关，未新增任何失败。

### 20 步合成会话前后 token 估算对比（`compact_messages_for_request` 实测）

合成 20 步会话（prefix + 20×(fat tail/瘦行 + assistant) + 当前 tail，44 条消息），marks 块按任务书实测量级 ~7.2k 字符/步：

| 指标 | 改前（胖 tail 历史 + 契约每步重发） | 改后（瘦轨迹 + 永久 prefix） | 降幅 |
|---|---|---|---|
| 请求字符数（指标口径，超长串按 2000 封顶） | 42,128 | 2,488 | ~17×（5.9%） |
| 估算 token（chars/4） | ~10,532 | ~622 | ~17× |
| 实际 wire 字符（不封顶：7.25k×20 步） | ~152,152 | ~7,472 | ~20× |
| 历史消息条数 | 44（窗口已删，全保留） | 44（append-only，永不丢） | 1× |

prefix（system+契约+task）在改后每次请求逐字节一致 → prefix-caching provider 全量命中；历史 marks/objects/context 文本彻底不再进入请求。

### 设计要点与取舍

- **替换发生在 execute 全量重建**（结果已知处）；**plan 请求侧安全网**兜底 confirm-reject/中断等 execute 未替换的路径（只清请求副本，state 保持 reducer 语义）。
- **confirm 流程**：首过写占位瘦行（`→ 待确认`），接受后二次 dispatch 原地换真实结果——避免任何路径残留胖 tail；拒绝路径历史直接就是瘦行。
- **每步"任务："续行保留**（P1#2/roadmap I1 既有测试锁定，位于动态 tail 区，不影响 prefix 字节稳定）。
- **task prefix 消息保留原始任务文本**（与改前 step-0 tail 一致；脱敏在 trace/checkpoint 出口执行，P0#10 出口原则不变），瘦行一律 inject 脱敏后写入。
- reflect/judge/locate 消息构造零改动。

### 遗留风险

1. `messages_reducer` 的 replace 判定仍靠"首条 role+content 匹配"启发式；真实运行首条恒为 system，安全；若未来首条可变需重审。
2. 瘦行内容依赖 `action_parsed`/`action_result` 结构，action 字段新增时需同步 `_action_target_summary`。
3. 胖 tail 识别基于屏幕块标记字符串；若 marks/objects/screen_info 标题文案改动需同步 `_TRAJECTORY_TAIL_MARKERS`。
4. 契约块在 goal 编译失败（空块）时 prefix 降为 `[system, task]`，仍稳定。
5. 环境性失败（LocateAnything 模型缺失）需在有模型的机器上复跑确认。
