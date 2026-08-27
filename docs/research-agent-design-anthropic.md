# 研究对照：Anthropic agent 设计文献 vs Open-AutoGLM 架构决策

> 2026-08-03。来源：Anthropic《Building Effective Agents》(2024-12)、
> 《Effective Context Engineering for AI Agents》(2025-09)、
> 《Measuring AI Agent Autonomy in Practice》(2026-02)。
> 目的：用外部权威实践校验本项目"代码约束环境、判断还给模型"的改革主线。

## 一、逐条对照

| Anthropic 指导 | 我们的对应实践 | 评 |
|---|---|---|
| **Workflows vs Agents**：agent = 模型动态决定流程、工具、何时停止；"you define constraints: what tools are available, how much the model can spend, when to stop; the model observes, reasons, chooses" | model-delegation 改革：模型决定 finish（验收 fail-closed 兜底）、模型选择工具（locate）、窗口预算="how much can spend"（耗尽=验收点+凭据续命，非硬终止） | ✅ 定义级吻合 |
| **System prompt "right altitude"**：避免两个极端——硬编码脆弱 if-else 规则 / 模糊高层废话；够具体给信号，够灵活用启发式 | 我们删掉 authority 反转的代码裁判（narrowed_merge 只留 hard_failure）；F-C 初版"精确目标不在 marks 就必须 locate"正是他们警告的 brittle rule，被用户当场纠回，改为中性工具描述 | ✅ 方向一致，且亲身验证了失败模式 |
| **工具设计 = ACI（Agent-Computer Interface）**：像给能干的新队友写文档——能力、成本、边界、错误语义；"花在工具优化上的时间有时超过 prompt 本身" | locate 工具中性描述（能力+约2s+3次预算+失败可见）；H3 失败反馈进 last_action_outcome = 工具透明度 | ✅ 吻合 |
| **Just-in-time 检索 > 预注入**：轻量引用+按需工具加载，而非开头倾倒一切（context 是有限注意力预算，最小高信号 token 集） | **模型主动 locate（JIT grounding，查询精准）vs auto-LA fallback（预注入，整句查询产宽条带/错位框）**。实证：auto-LA 是 10.4 误选元凶，模型 locate 两次全对 | ✅ 强烈支持收缩 auto-LA（用户判断"已过时"与文献一致） |
| **工具集最小且不重叠** | 现状有重叠：auto-LA 注入 marks + locate 工具两个通道（重叠直接导致 la_1_1/locate_1 混淆） | ⚠️ 待办：下一轮收敛到单通道 |
| **长任务三件套：Compaction / 结构化笔记 / 子代理** | goal_agenda 里程碑锁存（ever_matched）= 结构化笔记；summarized_history 改 trace-only（自由文本摘要不如结构化议程）；未来 task_planner 层 = orchestrator-workers 模式 | ✅ 基本吻合 |
| **Context window 管理**：最小高信号 token 集；历史工具输出清理是安全的压缩形式 | 6 消息窗口 + 历史剥图（只留当前截图）+ 任务/契约每步注入 + 动态 block（agenda/预算/记忆） | ✅ 吻合 |
| **自主性是共建的**（2026-02）：模型能力×用户信任×产品设计（监控+选择性监督）；80% 使用带 safeguards；"模型能承担的自主比它们实际行使的多" | HITL（支付/登录 interrupt）、验收 fail-closed、续行凭据、trace 全量归因 | ✅ 吻合，且支持继续放权 |

## 二、文献对我们下一轮的指引

1. **auto-LA 收缩有据可依**：JIT 原则 + 工具不重叠原则 + 我们的实证（auto-LA 全错、
   模型 locate 全对）三重支持。方向：LA 唯一入口 = 模型主动 locate；observation 阶段
   不再自动注入 LA marks（或降级为不可执行的参考标注）。
2. **"right altitude"检验 prompt 存量**：逐条审查 prompts_zh/en 里的行为规定，
   凡"代码式行为规则"（什么时候必须做什么）都是 brittle-rule 失败模式候选，
   应改为能力/成本/边界描述。
3. **monitoring 投资**：Anthropic 强调 post-deployment monitoring 是放权的前提——
   我们的 trace/diagnosis 体系就是这件事，继续投入优先级高于新功能。

## 三、核心文献：《The new rules of context engineering for Claude 5 generation models》
（2026-07-24，Thariq Shihipar，claude.com/blog）—— 用户指定篇目，最直接的对照

**头条事实**：Claude Code 为 Claude 5 代模型（Opus 5 / Fable 5）**删除了超过 80% 的
system prompt，编码评测无任何可测损失**。他们称之为 "Unhobbling Claude"——之前的
system prompt/CLAUDE.md/skills 层层叠加的约束互相冲突，模型要在冲突信息间反复权衡；
这些约束曾为防老模型的最坏情况而设，新模型的判断力已不需要。

### Then → Now 六条新规则（与我们逐条对照）

| 旧规则（已作废） | 新规则 | 我们的对应 |
|---|---|---|
| 给 Claude 立规则 | **让 Claude 用判断**（"never write comments" → "match surrounding code's comment density"） | model-delegation 改革主线；用户纠回 F-C 精确匹配规则正是此条的实战 |
| 给 Claude 示例 | **设计接口**（示例会把模型约束在某个探索空间；用富表达力的参数暗示用法，如 Todo 工具的 status 枚举） | ⚠️ 待审：prompts 里的 few-shot 示例可能过度约束；正面证据：locate 无示例、纯能力描述，模型首次即用对 |
| 全部前置 | **渐进披露**（verification/code review 移入 skills 按需加载；工具 deferred-loading + ToolSearch；文件树按需加载） | locate = grounding 能力的渐进披露；auto-LA 预注入收缩方向再获背书 |
| 反复重复 | **简单工具描述**（用法写进 tool description，不在 system prompt 重复） | F-C 中性描述定位一致；注意 Locate 规则目前写在 system prompt 段，可评估下沉到 action schema 描述 |
| CLAUDE.md 记忆 | 自动记忆 | goal_agenda 锁存 / gui_memory 即自动结构化记忆 |
| 简单 specs | 富引用（代码即 spec、rubric + verifier agents 验证品味） | GoalContract criterion = rubric；reflect/acceptance = verifier 模式 |

### 对存量 prompt 的审计方向（新待办）
按此文标准，prompts_zh/en 需要一轮"unhobbling 审计"：
1. 凡是"防最坏情况的行为规则"——逐条问：这个最坏情况新模型还会犯吗？不会就删；
2. 凡是 few-shot 示例——问：它是在教格式（保留）还是在限定探索空间（删/改接口暗示）；
3. 凡重复表述（system prompt 与 action 描述里各说一遍）——只留 tool/action 描述处的一份；
4. 保留的合法残留：环境约束（预算、fail-closed 语义、HITL 边界）——这些不是行为规则，
   是接口设计，正是"设计接口"那条鼓励的。

## 四、怎么写出更好的 system prompt（2026 实操层）

用户痛点：原则都懂，但"具体怎么写"缺方法。三份最新材料互补：

### 1. 《A field guide to Claude Fable 5: Finding your unknowns》（2026-07-06）
核心重构：**写 prompt 不是一次性的写作问题，是 unknowns 发现过程**——
"工作质量的瓶颈在于我澄清 unknowns 的能力"。四象限：known knowns（已写进 prompt）/
known unknowns（知道没说清）/ unknown knowns（显而易见忘了写）/ unknown unknowns（没意识到）。
方法清单：
- **Blind spot pass**：直接让模型帮你找你的未知（"我对这个领域不懂，帮我做 blind spot pass"）
- **Interview**："一次一个问题地采访我，优先问答案会改变架构的问题"
- **Brainstorm/prototype**：对"看到才知道要什么"的 unknown knowns，先要 4 个迥异方案再反应
- **References**：最好的参考是源代码而非描述/截图
- **Implementation notes**：让 agent 记录偏离决策，供下轮迭代
- **Quiz**：让模型出题考你，确认你真正理解了改动

### 2. 2026 实操指南（kay-rottmann / OpenAI cookbook / Anthropic docs 综合）
- system prompt 六要素：角色一句话 / 目标一句话 / 工具及使用指引 / 策略边界 /
  不确定性如何表达（何时升级求助）/ 如何宣告"完成"
- **像代码一样对待 prompt：先建 eval 集（30-100 例：常规/困难/边界/对抗）再定稿；
  每改一行对照 eval；版本化管理**
- 优先级堆叠的规则（严格降序），工具文档化（when to use / when NOT / pitfalls）
- 常见坑：工具 >8 个、描述模糊、无反思回路、prompt 过长、假设模型能推断边界、无 eval

### 3. 与 Claude 5 新规则的张力与统一
表面矛盾：实操指南说"写清规则/示例/优先级"，Claude 5 规则说"删 80%/别给示例"。
统一解：**删的是行为微规则（怎么做），留的是环境契约（是什么/什么价/什么边界）**。
对新一代模型，system prompt 的正确内容 = 产品语境 + 工具接口契约 + 边界（预算/安全/HITL）
+ 不确定性出口；错误内容 = 行为剧本、重复表述、限定探索空间的示例。

### 4. 落到本项目的方法论（可直接执行）
我们的 trace/diagnosis 体系就是 eval 集——每轮真机运行 = 一次对照测试：
1. **最小草稿起步** → 真机跑 → 按 trace 观察到的失败模式逐行增删（已在做）
2. **让模型审计自己的 prompt**：把 prompt + 失败 trace 喂给强模型，问"哪句话导致了
   这个行为/哪句是死的"（= blind spot pass 的逆向用法）
3. **interview 法用于 prompt 作者**：写 prompt 前让模型采访你（"关于这个 agent 的
   行为，我有哪些该说没说的"）
4. 每条 prompt 规则标注类型：环境契约（留）/ 行为规则（审）/ 示例（审是否限定探索空间）

## 五、关键引文（备查）

- "Find the smallest possible set of high-signal tokens that maximize the likelihood of
  the desired outcome."（context engineering 核心原则）
- "Workflows offer predictability... whereas agents are the better option when flexibility
  and model-driven decision-making are needed at scale."
- "We removed over 80% of Claude Code's system prompt... with no measurable loss on our
  coding evaluations."（2026-07-24）
- "Giving examples actually constrains them to a certain exploration space."（2026-07-24）
- "We can delete many of them and let the model use surrounding context and judgement instead."
  （2026-07-24，Unhobbling）
- "Models are capable of more autonomy than they currently exercise"（2026-02 实测发现）
