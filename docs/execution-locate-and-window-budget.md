# 执行文档：F1 locate 工具 + F2 窗口预算

> 基线：869 passed（commit c54f232）。两个工作流独立可验证，按 F1→F2 顺序实施。
> 全程 `.venv/bin/pytest tests/ -q` 必须绿；不 commit/push；CN/EN prompt 同步；trace 只增不删。

---

## F1：locate 工具（LA 小工具化）

> 设计来源：docs/grounding-usability-fix-plan.md 后续讨论（pi-15 调研结论）。
> 目标：模型发现目标不在 Screen marks 里时，可主动输出 locate 动作，系统用当前截图+聚焦描述调 LA，把格子级 box 注册为 mark，replan 后模型用真实 target_mark_id 执行。

### F1.1 动作定义
- `{"type":"intent","action":"locate","target_text_hint":"10月1日"}`。
- `adapter.py`：ACTION_ALIASES 加 `"locate":"Locate"`；`ALLOWED_PROVIDER_FIELDS_BY_ACTION` 加 Locate → 公共 intent 字段 + `target_text_hint`。
- `validator.py`：Locate 要求 `target_text_hint` 非空；硬上限沿用 240（prompt 建议 ≤64）。
- `safety.py`：benign（side_effect=none，不进 confirm/takeover）。
- `capability.py`：`ToolCapability("Locate", implemented, side_effect_kind="none", observation_effect="none", can_advance_goal=False, retry_safety="safe", required_postconditions=())` → `requires_reobservation=False` → after_execute 现有 replan 路由（edges.py 无需改，验证即可）。

### F1.2 执行链（关键落点）
- 新文件 `phone_agent/graph/tools/locate.py`：输入 state + config；取当前 observation 的 screenshot（base64/宽高）+ 当前 screen_id/raw_screenshot_hash 构造 ScreenBinding；**单 hint 单 query**（`MarkProviderHint(text=target_text_hint, source="locate")`）调 LA provider（从 config/device_factory 的 grounding providers 中取，注入方式读现有代码选合理路径）；`structure_mode=off` 语义（多框 → grounding_ambiguous fail-closed）。
- `tools/__init__.py` dispatch_tool：intent+Locate 分支，不落 device。
- `execute.py`：**内部能力分发点必须在 safety gate 之后、未知动作终态分支之前**（pi-15 坑#3：`_metadata != "do"` 会被 `unknown_action_type` 拦截）。分发到 locate 工具；成功 → 用 `MarkRegistry.with_extra_marks()` 合并 mark 进 state 的 mark_registry；失败 → 直接写 `failure_cause`（replan 跳过 reflect，必须 execute 写，pi-15 坑#5）+ 返回 replan 语义。
- `marks.py` 新增 `MarkRegistry.with_extra_marks(extra)`：**保留原 screen_id**（截图未变，P0#9 哈希绑定不失配），只重算 mark_set_version；新 mark_id 递增 `locate_N`（state 计数）；mark 字段 role=None、source=LA 名、confidence=1.0。
- 预算：`state["locate_count"]`，上限 `LOCATE_MAX_PER_RUN=3`（policy.py）；耗尽 → 拒绝 `failure_cause="locate_budget_exhausted"` → replan。每屏 locate mark ≤5。

### F1.3 prompt（prompts_zh.py + prompts_en.py 同步）
- ACTION_SCHEMA 加 Locate 规则：仅当 Screen marks 中**没有目标的可执行 mark** 时使用；`target_text_hint` 只写可见元素的聚焦短描述（建议 ≤64 字符）；禁止整句任务、禁止隐私原文。
- JSON/TOOL_CALLS 示例加 `{"type":"intent","action":"locate","target_text_hint":"10月1日"}`。

### F1.4 测试
- 单测：adapter 解析（含缺 hint 拒绝）、validator、capability 路由语义、marks merge（screen_id 不变/mark_set_version 变/幂等）、execute 三分支（mock LA：1 框成功注入 / 0 框 no_candidate / 2 框 ambiguous）、预算耗尽拒绝。
- 集成：fake LA provider 注入，全图"目标不在 ax marks → locate → 新 mark → tap 落到 LA 坐标"链路。

---

## F2：窗口预算（earned continuation + 预算可见 + eval interrupt 前置修复）

> 设计来源：pi-16 调研 + 主 Agent 分析。max_steps 从终局硬顶改为窗口预算；
> 代码只在窗口边界组织验收+按 Goal 进展事实发续命，完成/冲刺/放弃判断还给模型。

### F2.0 前置：eval interrupt 处理（pi-16 坑#5，必须先做）
- `evals/run_eval.py` 目前无 resume 处理，take_over → GraphInterrupt → 归因混乱。
- 处理：eval 模式下 takeover interrupt 转为干净终局（`success=False`、`failure_cause="takeover"`/`model_declared_infeasible`、final_message=接管原因），不再落 run_error。agent.py run_structured 的 GraphInterrupt 捕获路径同步核查。

### F2.1 窗口预算核心
- 语义：`max_steps` = 当前窗口大小。窗口耗尽 → 强制验收（现有 budget_forced）→ 被拒 → 判续行凭据 → 有凭据：`max_steps += 10`、`continuation_count += 1`、复位 `budget_acceptance_done`（新窗口可再强制验收）；无凭据：走现有 after_acceptance max_steps→end（goal_not_satisfied）。
- **edge 纯函数不写 state**（pi-16 坑#4）：授予逻辑落在节点内（acceptance 节点被拒分支或 goal 节点，读代码选合理位置并说明）；edges 只读新状态。
- 凭据纯函数 `continuation_credential(state)` 放 `context.py`，分支：
  1. criterion movement：最近 6 步 ledger 中任一 criterion status rank 上升（matched>unknown>missing）
  2. 新增锁存：`ever_matched` latched 计数较上一窗口边界新增
  3. judge near-miss：本次强制验收的 semantic judge 返回非空 named_evidence 或 hard confirm ≥1
  - 否定：最近 6 步 novelty_streak≥4 且无 1/3 → 拒绝；凭据 2（Goal 事实）不受否定
- 常量进 policy.py：`continuation_grant_steps=10`、`continuation_max_grants=2`、`continuation_window_steps=6`、`absolute_max_steps=max_steps*3`；绝对上限到 → 强制验收 → end，`finish_source="absolute_budget_exhausted"`。
- telemetry：trace 事件 `continuation_granted`/`continuation_denied`（含命中分支），先 telemetry 后调阈值。

### F2.2 预算可见（context.py plan block 新增 budget 段）
- 内容三件套：剩余 X/Y 步、已续命 K/2 次、"预算耗尽≠失败，只是触发系统验收；已实际完成请立即 finish 点名标准；结构性无法完成请 take_over 说明"。
- **只进动态 context block**，严禁进 system/goal block（P5 前缀缓存，pi-16 坑#8）。
- 预算 ≥75% 加"将尽"提示。

### F2.3 take_over 词表放宽（prompts_zh/en 同步）
- 硬约束改为：登录/验证码/需人工/**结构性无法完成（说明原因）** → take_over。
- 明确"预算耗尽≠失败"句式，防恐慌性提前 finish（pi-16 坑#1）。

### F2.4 归因
- goal_not_satisfied（验收拒绝）/ takeover（模型主动）/ goal_not_satisfied+finish_source=absolute_budget_exhausted（绝对上限）。
- 基础设施错误烧步问题：截图/parse 失败步单独计数（trace 标记，不从窗口扣除——可选简化：先只 trace 标记，不改计数语义，说明理由）。

### F2.5 测试
- 凭据纯函数全分支（含 A-B-A-B 振荡不授予、feed 刷新假探索不授予、新 latch 不受否定否决）。
- edges：窗口续命路由/续命上限/绝对上限/P0#5 守卫仍最先。
- acceptance：窗口语义 budget_forced（模型 claim 不设标志、续命后新窗口可再验收）。
- eval：take_over interrupt 干净归因；continuation 指标进结果。
- run_diagnosis 归因文案同步（耗尽≠失败）。