# Open-AutoGLM Phone Agent 自进化研究报告

> 日期：2026-08-29  
> 范围：只读审查 `/Users/bytedance/Open-AutoGLM`，结合论文、官方项目与官方 API 文档。本文所说的“自进化”是**不更新基础模型权重、由经验驱动且受治理的运行时能力演化**，不是在线自动改核心代码。

## 摘要

Open-AutoGLM 已经具备一个很好的自进化地基：薄循环保持“一次模型调用—一个动作—一次新观测”，TaskDoc 保存当前任务状态，atomic observation 保证 mark 的时效性，finish verifier、安全中间件、token budget 和 trace 提供了可观测评价信号，App-KB 则验证了“追加事件日志 + 物化视图 + 有界注入 + dream 整理”的长期记忆骨架。

下一步不应把 App-KB 扩成一张万能记忆表，也不应直接让模型把一次反思写进永久提示词。更合适的架构是一个**经验编译器**：

```text
运行事件 -> 结构化评价 -> 轨迹蒸馏 -> 候选教训/工作流
        -> 多次独立验证 -> dry-run 提案 -> 人工晋升
        -> 有界、按条件注入 -> 持续监控 -> 降级/撤销/归档
```

最值得优先做的不是向量数据库或自动改代码，而是：

1. 建立隐私受控、可评价的 `ExperienceEvent`，并统一统计 actor、压缩器、verifier、reviewer、distiller 的成本与结果；
2. 先以 `observe/shadow` 模式验证“哪些经验真的能改变后续决策”，再开放已晋升规则的注入；
3. 将技能定义为带前置条件、后置条件和恢复分支的**工作流骨架**，每一步仍基于 fresh observation 绑定新 mark，绝不回放旧坐标；
4. 把上下文分成稳定前缀、追加式文本账本和易变尾部，先测 prefix-cache 首个分歧点，再改变 TaskDoc/图片裁剪结构；
5. 模型路由优先用于压缩、蒸馏、普通评审等旁路任务；主 actor 必须先 shadow 标注和离线校准，再允许弱模型处理低风险步骤。

---

## 一、调研发现

### 1. 手机 Agent 需要什么记忆

#### 1.1 外部研究给出的共同模式

- **Reflexion** 不改模型权重，而是把环境反馈转成语言反思，放入 episodic memory，供后续尝试使用；它证明了“评价信号 -> 文字经验 -> 下一次决策”的可行性，但单次自我反思本身并不等于可信的永久规则。[S2]
- **ExpeL** 同时保存成功和失败轨迹：按任务相似度召回成功经验，并从成功/失败对照中提取 insight，再通过 add/edit/vote 迭代精炼；这比“只总结失败”更能避免错误归因。[S3]
- **AWM** 把 workflow 定义为从具体样例上下文中抽象出来的常见子程序，既可从标注样例离线诱导，也可从历史经验在线诱导。[S1] 这非常适合手机任务，但 workflow 应抽象“意图和可验证状态迁移”，不能保存旧页面的 mark 或坐标。
- **Voyager** 的长期积累对象是可组合、可解释的技能，并通过环境反馈、执行错误和自验证迭代改进。[S4] 它的可执行代码库适合 Minecraft 这种稳定 API 环境；动态 Android UI 更适合保存声明式工作流，而不是直接移植代码动作序列。
- **dsh-self-evolving-agent** 提供了一个有用但非学术基准的治理模板：HOT/WARM/规则/COLD 四层、只注入 1–5 条相关经验、Rule of 3、默认 dry-run、人审，以及 30–90 天降级/归档。[S5] **dsh-auto-evolve** 进一步强调 baseline/trial 验证、封闭 mutation vocabulary、成本预算、cooldown 和回归回滚。[S6]
- **Claude Code 的抽取提示词**（第三方仓库，不是官方接口承诺）提供了两个值得借鉴的约束：即时记忆只保存 applicable、durable、legible 的教训，并把旧记忆当待核验快照；dream 则按 orient → gather → consolidate → prune 四阶段合并近重复、删除矛盾/过期项，并与项目级 `CLAUDE.md` 对账，索引限制在约 200 行/25KB。[S11] 对手机 Agent 的含义是“候选与事实源分离、当前代码/设备事实优先、索引必须有硬上限”，而不是照搬文件格式。

因此，手机 Agent 的“记忆”至少要拆成以下七类。App-KB 只是其中第一类长期记忆实例。

#### 1.2 建议的记忆分类

| 类型 | 内容形态 | 写入触发 | 读取/注入策略 | 衰减与撤销 |
|---|---|---|---|---|
| **App 事实记忆** | App label、package、alias、设备 scope、置信度、成功次数、版本/时间 | `pm get-application-label` 同步；launch 成功；用户明确纠正 | resolver 内确定性查询；run 开始只注入有界 App 列表 | 设备 inventory 对账；卸载/改名标 stale；低置信老条目清理 |
| **情节/结果记忆** | 一次 run/step 的结构化 capsule：目标签名、app/package、观测 epoch、意图、工具、结果码、verifier/safety/takeover、成本、证据引用 | 每个工具回执、终局、verifier/reviewer 结果 | **不直接注入 actor**；只供评价、蒸馏、审计和回放 | 原始事件按隐私/审计期限清理；保留聚合统计和已晋升对象的证据引用 |
| **失败/规避教训** | `适用条件 -> 不要做什么 -> 建议恢复动作 -> 可观察后置条件`，附 support/conflict 和来源 | 重复工具失败、stale/ambiguous mark、verifier reject、loop fuse、明确用户纠正 | 只注入 `promoted` 且结构条件命中的 1–3 条；错误发生后的精确 recovery hint 可在下一轮追加 | 30 天未命中标 stale，90 天归档；一旦导致相反错误立即 revoke |
| **任务工作流技能** | goal pattern、参数槽、前置条件、步骤的 intent/action-family/postcondition、恢复分支、终止证据、app/version scope | 多次成功且终局可信；成功/失败轨迹对照可抽象出稳定子程序 | 每次最多 1 个 workflow skeleton；用于构造/修订 TaskDoc 路线，不直接执行旧动作 | app 大版本、前置条件失败或成功率下降时降级；重新验证后再晋升 |
| **用户偏好记忆** | 用户明确表达的语言、默认选择、交互偏好和禁忌；带 provenance、last_confirmed、scope | 用户明确声明或纠正；禁止从点击行为猜测敏感偏好 | 仅在相关任务精确命中时注入；高敏偏好要求再次确认 | 用户新陈述立即覆盖；长期未确认的环境相关偏好提示复核 |
| **设备/环境画像** | Android/OEM、分辨率、能力、权限、provider 可用性、accessibility/MLX 延迟分位数、已知系统限制 | run 起始探测、配置变化、稳定重复测量 | 供 harness 选择观测/grounding 路径；actor 只看与当前决策有关的极少事实 | 每 run 刷新易变字段；系统升级/设备变化使旧 profile 失效 |
| **安全经验** | warning/hard gate/takeover 的结构化触发特征、人工裁决、false-positive/false-negative、policy version | 安全预警、人工 approve/reject、takeover、事后审查 | 用于 reviewer 校准和离线 policy 评测；**不得直接削弱 hard gate** | policy/version 绑定；冲突立即撤销；任何放宽都必须人审和回归测试 |

其中，“情节/结果记忆”是原料，不是给 actor 阅读的知识；“教训/工作流”是经过蒸馏与晋升后的产品。这一区分能阻止上下文退化成日志堆积。

#### 1.3 推荐 schema

经验事件应是结构化、隐私最小化的投影，而不是复制完整 transcript：

```json
{
  "schema_version": 1,
  "event_id": "...",
  "run_id": "...",
  "step": 12,
  "task_key": "hash-or-coarse-intent",
  "scope": {"app_package": "...", "app_version": "...", "device_class": "..."},
  "observation": {"epoch": 9, "screen_hash": "...", "mark_count": 23},
  "action": {"intent": "...", "tool": "tap", "target_features": {"role": "button"}},
  "outcome": {"ok": false, "code": "stale_mark", "latency_ms": 41},
  "evaluation": {"safety": "none", "finish_verdict": null, "terminal": null},
  "privacy": {"redaction_version": 1},
  "evidence_refs": ["trace://run/step"]
}
```

候选教训则必须显式携带适用边界与反证：

```json
{
  "lesson_id": "...",
  "kind": "avoidance|recovery|workflow_hint|preference|safety_calibration",
  "when": {"app": "...", "tool": "...", "error_code": "..."},
  "instruction": "...",
  "expected_observation": "...",
  "evidence": ["event-id-1", "event-id-2"],
  "support_count": 3,
  "distinct_task_count": 2,
  "conflict_count": 0,
  "status": "candidate|promoted|stale|revoked|archived",
  "last_validated_at": "...",
  "expires_at": "..."
}
```

### 2. 自进化闭环应该怎样运行

#### 2.1 Experience：从薄循环采集什么

当前代码已经暴露出足够多的评价信号：

| 信号 | 位置 | 能说明什么 | 不能直接说明什么 |
|---|---|---|---|
| `last_tool_ok`、工具错误、stale/ambiguous/unknown | `tools/actuation.py`、`session.py`、`review.py` | 某一步是否执行成功、grounding 是否失配 | 工具成功不代表任务成功 |
| safety warning/reviewer/hard gate | `middleware/safety.py` | 动作具有风险或不确定性 | warning 不等于 actor 做错；可能是正确的必要动作 |
| finish two-step、verifier APPROVE/REJECT、争议次数 | `tools/control.py`、`verify.py` | 当前最强的终局验收信号 | verifier fail-open 或未触发时，不能伪装成独立确认 |
| `ask_user` / `take_over` | `tools/control.py` | 自动化边界、登录/验证码/判断缺口 | takeover 可能是正确行为，不应一律算失败 |
| `finished`、`model_stopped`、`token_budget_exhausted`、`loop_fuse`、`hitl_resume_exhausted` | `agent.py` | run 终局与资源性失败 | `finished` 仍需区分证据强度 |
| model/tool latency、actor token usage | `trace.py`、`budget.py` | 性能与成本 | 当前账本未覆盖 summarizer/verifier/reviewer/distiller |

生产 trace 会把文本截到 64 字且脱敏，不应作为唯一的蒸馏输入。正确做法是新增结构化 `ExperienceEvent` 投影：只保留学习所需字段，敏感值在写入前分类、散列或删除；原始 screenshot/base64 不进入长期经验库。

#### 2.2 Evaluate：把“成功”分级，而不是一个布尔值

建议给 episode 建立证据等级：

- **A 级正例**：独立 verifier APPROVE，或人工明确确认；
- **B 级正例**：两段式 finish 成功、route 全闭合、无 hard doubt，但 verifier 未触发；
- **弱正例**：工具调用成功或到达某页面，只能验证局部 transition；
- **强负例**：verifier REJECT、执行错误后出现明确恢复、人工明确指出错误；
- **弱负例/告警**：safety warning、低 marks、一次 timeout、takeover；需要结合上下文，不能直接生成永久教训；
- **资源性失败**：token budget、loop fuse、模型停止；优先产生“路线/效率问题”候选，不要把最后一步动作误判为根因。

评价先走确定性规则，LLM 只解释复杂轨迹。一个候选规则必须链接到支持它的事件和反例，不能只保存一句自然语言结论。

#### 2.3 Distill：轨迹蒸馏到底做什么

蒸馏应在 run 结束或手动 dream 中离线运行，不能增加每个 action step 的延迟。具体分两种：

1. **失败教训蒸馏**：按 `app/package + goal family + failure code` 聚类，给 summarizer 同时看成功和失败的结构化轨迹，要求输出：
   - 可观察的触发条件；
   - 失败动作及结果；
   - 已被证实的恢复动作；
   - 适用 scope 和反例；
   - 若证据不足则输出 `insufficient_evidence`。
2. **工作流蒸馏**：从多条成功轨迹中删除实例值、旧 mark id、坐标和用户隐私，把步骤转换为：
   - `intent`；
   - 进入步骤前应看到什么；
   - 建议的 tool family；
   - 成功后应观察到什么；
   - 不满足时的恢复/退出分支。

这继承了 AWM 的“从具体上下文抽象共同子程序”和 ExpeL 的“成功/失败对照提炼 insight”，但保留手机环境的关键差异：workflow 只提供路线，不获得直接执行权。[S1][S3]

#### 2.4 Promote：建议的晋升管线

```text
raw event
  -> grouped episode
  -> candidate lesson/workflow
  -> shadow retrieval（只记录如果注入会选中什么）
  -> offline replay / fixture / emulator baseline-vs-trial
  -> Rule-of-3 + conflict check
  -> dry-run proposal
  -> human approve
  -> promoted + versioned materialized view
  -> monitored injection
  -> stale / revoke / archive
```

建议默认阈值：

- 普通教训：30 天内至少 **3 次独立支持、覆盖至少 2 个 task key、0 个未解决冲突**；
- 规避类教训还必须至少有 **1 条已验证恢复路径**，否则只能描述问题，不能指挥动作；
- workflow：至少 3 个可信终局，其中至少 1 个 A 级正例；同 app 大版本内验证；
- safety 经验：一次严重事件即可生成提案，但**永不自动放宽 gate**；
- 用户明确偏好不必 Rule-of-3，但必须标记为 `user_asserted`，且不能泛化到未声明 scope。

`promotion-review` 默认只输出 proposal，不写系统提示、规则或技能。历史轨迹可做离线 shadow/replay，但手机副作用通常无法从日志真实重放，因此“模型说新规则更好”不算验证；需要 fake fixture、模拟器或受控真机任务的 baseline/trial。dsh-auto-evolve 的 closed vocabulary、预算、cooldown 和 rollback 很值得借鉴，但它的自动 apply 不应成为首期默认。[S6]

DeepSeek Harness 官方仓库把 harness 组织为 “everything is a plugin”；更具体地，它用 profile 指定有序 bundle，再叠加用户 patch/命令行 patch，HMR 则通过卸载旧插件（释放 effects）再加载新插件完成局部替换。[S16][S17] 对本项目最有价值的不是复刻其 TypeScript 框架，而是把 `recall`、`distill`、`promotion` 视为可关闭、可版本化、可单独回滚的能力缝：它们可以影响 actor 的输入，但不能获得绕过 marks、safety、TaskDoc 或 finish verifier 的旁路。为保证单个 run 可重现，已选中的 memory/workflow generation 应固定到 run 结束；新版本只对下一个 run 生效，紧急 revoke 除外。

#### 2.5 Inject：只让“已晋升、当前相关”的内容进入 actor

推荐两种注入时机：

- **run-start recall**：根据 task、device、可能的 app family 选择最多 3 条教训 + 1 个 workflow，固定为本 run 的稳定记忆快照；
- **event-triggered recovery**：出现精确 error code 或 verifier reject 时，在下一轮追加一条命中的 recovery hint。它是新事件，不回写或替换旧历史。

默认总预算建议 600–800 tokens。排序先看结构化硬匹配（package、tool、error、app version、goal tag），再看验证次数、近期命中和历史帮助率。记忆不足数千条前，SQLite/JSON 索引和关键词/tag 检索足够；语义向量只应作为召回候选，不能绕过 scope 和状态过滤。

#### 2.6 Forget：遗忘是自进化的一部分

- 30 天未命中：`stale_candidate`，停止自动注入；
- 90 天未使用：归档，但保留证据链；
- 与当前设备/app version 冲突：立即停止注入并要求重验；
- 注入后失败率上升、出现反例或用户纠正：`revoked`，回滚到上一版本；
- 合并近重复项，避免“同一教训不同措辞”占满预算；
- 保留 promoted rule 的版本、来源、适用范围和撤销原因，确保可审计。

#### 2.7 主要失败模式与防护

| 失败模式 | 后果 | 防护 |
|---|---|---|
| 单次失败被过度概括 | 一条错误规则污染所有后续任务 | 成功/失败对照、Rule-of-3、scope、反证字段、人审 |
| 把相关性当因果 | 学到“点击 X 会成功”，其实是页面已变化 | 原子 observation、前后置条件、独立 task 支持 |
| UI/app 漂移 | 老 workflow 在新版页面误导 | app/version scope、precondition fail-closed、TTL、重验 |
| 屏幕文本 prompt injection 进入记忆 | 不可信内容升级为持久指令 | 数据/指令分层；屏幕文本只作为 quoted evidence，禁止直接成为 instruction |
| verifier 或 actor 自我确认形成反馈环 | 错误“成功”被反复强化 | 证据等级、独立 verifier 上下文、人工/环境信号、holdout 评测 |
| 只保存成功轨迹 | 学不到危险路径与恢复边界 | ExpeL 式成功/失败共同蒸馏 |
| 记忆携带隐私 | 跨 run 泄露联系人、订单、验证码 | 写入前 schema 白名单和 redaction；禁止原图/输入值；用户可清除 |
| 自动演化安全策略 | 一次 false positive 导致永久放宽 | safety memory 只供离线校准；放宽永远人审 |

### 3. 非交互式手机 Agent 的上下文窗口工程

#### 3.1 Claude Code 三层压缩，哪些适用

decodeclaude 对 Claude Code 的实现观察把压缩分为 microcompaction、接近窗口时的 auto-compaction、用户触发的 manual `/compact`，并强调 compact 后 rehydrate 工作状态。[S11] 这不是 Anthropic 官方契约，但可以作为工程参照。

| 层 | 对本项目的适用性 | 建议 |
|---|---|---|
| **Microcompaction** | 部分适用 | `images.py` 已在做旧图/marks 折叠。只有“大且不可重建”的工具输出值得落盘；屏幕可实时重观测，旧截图通常不值得为 actor 建立磁盘回填路径。 |
| **Auto-compaction** | 高度适用 | 当前 T1/T2 + TaskDoc + fresh-observation hint 方向正确；需要从固定比例升级为真实 headroom 与请求总成本核算。 |
| **Manual compaction** | 交互命令不适用，语义边界适用 | 非交互 agent 不应依赖用户 `/compact`；可在 app 切换、子任务闭合等明确边界触发，但只有上下文已经较大时才值得付一次 summarizer 和 cache reset。 |

**是否需要“落盘 + 可检索路径”？**

- screenshot 和 accessibility tree 属于易变世界状态；要继续行动，应重新 `observe()`，而不是从磁盘读旧屏。旧帧只需保留 hash、screen sequence、受控 evidence reference 供审计。
- 当前 phone tools 没有通用文件读取能力。仅返回一个路径并不真正“可检索”，为此新增通用文件工具会扩大执行面。
- 将来若加入大型 OCR、网页正文、长系统日志等不可重建输出，可采用 DeepSeek Harness 的做法：模型只看 head/tail preview + opaque locator，完整内容由 session-scoped 私有存储保存，读取受专用工具和预算约束；spill 失败不能把成功工具变成失败。[S9]

#### 3.2 当前 auto-compact 的优点与缺口

当前 `compact.py` 已经做对了四点：不切断 tool-call/tool-result 配对、保留 TaskDoc、结构化 handoff、压缩后要求 fresh observation。缺口是：

1. T2 目前基于 `state.messages` 的 token 估算；真实请求还包括工具 schema、provider wrapper 和输出空间。应改成：
   `effective_limit - reserved_output - reserved_compaction - fixed_schema - safety_margin`；
2. summarizer、verifier、safety reviewer 的 token/latency 没进入 `budget.py` 的统一账本；
3. compaction 是一次大范围 surface replace，官方缓存文档明确表明首个变化点后的 prefix 无法复用，因此 compact 后 cache read 暂时下降是预期行为；
4. 需要把被选中的 memory/workflow id、当前 app/device scope、未决安全确认和终局争议状态纳入 handoff contract，避免压缩后“记得路线但忘了为何要谨慎”。

#### 3.3 Prefix/KV cache 与当前原地改写的冲突

Anthropic 官方文档要求 cache breakpoint 之前的 prefix 完全相同，推荐静态 tools/system/context 在前、动态内容在后；任意前置 block 改变都会产生新 hash。[S10] OpenAI 官方文档同样要求完整 rendered prefix 匹配，并指出工具、格式、reasoning 配置和 context management 都会影响命中；compaction 替换早期内容后，首个请求的 cache reuse 可能下降。[S12]

当前实现有两个明确的 cache 破坏点：

1. `middleware/taskdoc.py` 每轮删除旧 `[TASK_DOC]`，生成新 UUID 并在尾部重发；即使 message id 不上行，内容和位置也在变。固定 UUID 不能解决内容变化。
2. `middleware/images.py` 会原地替换旧消息中的 image block 和 marks digest；当滚动窗口每轮淘汰一个旧观测时，变化发生在历史中部，变化点之后的 cache 都失效。

正确性优先于 cache；不能为了缓存保留 stale marks 或旧屏幕作为执行依据。但可以重新设计 provider-facing surface：

```text
A. 静态前缀：system policy + tool schemas                         <- 显式 cache breakpoint
B. run 稳定块：goal_base + device snapshot + recalled memory IDs  <- run 内不改
C. 追加式文本账本：intent/action/receipt/TaskDoc 更新事件          <- 尽量 append-only
D. 易变尾部：最新 TaskDoc 视图 + fresh marks + 当前 screenshot     <- 不期待跨轮全命中
```

实际落地有两档：

- **低风险档**：保持 P0 TaskDoc 语义不变，只确保 A/B 稳定并打显式 cache breakpoint；接受动态区不完全命中。先记录每次请求的 `prefix_fingerprint`、`first_diff_block`、`cached_tokens`。
- **高收益档（需单独批准 P0 变更）**：TaskDoc 只在内容改变时追加 versioned snapshot/delta，旧版本带“最高版本为准”的稳定契约；flow line 改为追加的 action receipt，不再每轮删除重建。旧图片不逐帧滚动改写，而是按 generation 批量折叠：若干轮保持 append-only，达到水位后一次 surface replace。这样 cache reset 变成低频、可解释事件。

DeepSeek Harness 的 event-sourced session 经验很契合：append-only log 是事实源，model-visible messages 由日志派生；请求头、工具 schema 和模型配置也被记录，从而能逐字节重建请求。prefix stability 是这种结构的结果，而不是靠猜缓存键。[S7][S8]

### 4. Agent 加速：什么真的适合一动作一观测

#### 4.1 减少模型调用

优先级从高到低：

1. **保留已经完成的首步优化**：`_initial_messages()` 已把第一次 atomic observation 的截图和 marks 放进初始用户消息，不需要先烧一次 `read_screen`；自进化层不应再引入一个启动规划调用。
2. **减少探索和纠错调用**：App-KB 消除 app 名猜测，workflow skeleton 提前提供路线，failure memory 提供已验证恢复路径；它们减少无效步，但不跳过 fresh observation。
3. **本地确定性工作**：TaskDoc flow line、inventory、marks 解析、finish doubts、结构匹配和 memory retrieval 都应保持无模型。
4. **有门槛的 workflow executor（later）**：只有当每个分支都可由本地结构信号判定、动作可逆、前后置条件可验证时，才允许一个 skill 内执行多步；每步仍重新观测并绑定当前 mark，任何歧义立即返回 actor。支付、凭据、自由文本判断和跨 app 不稳定流程永不进入宏执行。
5. **不要直接回放历史步骤**：旧 mark 带 epoch，坐标和页面布局都会漂移。AWM/Voyager 支持“复用子程序”，不等于复用旧环境引用。[S1][S4]

#### 4.2 降低每步延迟

- 保持 accessibility-first：它是低延迟结构化来源；MLX LocateAnything 只在 tree marks 无法命中时显式调用。当前 `PhoneSession` 已把 locate provider 做成 session singleton，应继续避免每步重新加载模型。
- LocateAnything 继续使用受 benchmark 支持的缩图和短 hint；预热应是 opt-in，并以真实 `p50/p95 load + inference` 决定是否值得占用内存。
- 对 actor 来说，输出通常只是一个 tool call，瓶颈更可能是长多模态 prefill/TTFT，而不是生成很多 token。因此稳定前缀、减少旧图、缩短 marks/TaskDoc 和缓存命中比“让模型少生成几句话”更重要。
- observation 的 foreground-before -> screenshot -> accessibility -> foreground-after 是一致性事务，不能为了并发缩短延迟而破坏 atomic observation。

#### 4.3 Token 与模型路由

RouteLLM 在通用文本基准上展示了强/弱模型路由的成本—质量收益，并明确要求在与实际查询分布相似的数据上校准 threshold。[S13] FrugalGPT 也通过 cheap-first cascade + reliability score 决定是否升级，但其 50%–98% 节省来自自然语言 QA/分类数据，不能直接外推到多模态手机控制。[S14]

对本项目建议：

- **现在就可路由**：compact summarizer、经验 distiller、普通 proposal reviewer 使用便宜文本模型；高风险 finish verifier 和 safety reviewer保留强模型。现有 `memory_model`、`verifier_model`、`safety_reviewer_model` 已提供角色分离入口。
- **主 actor 先 shadow**：用历史 episode 同时跑 weak/strong，比较 tool choice、mark binding、safety、终局和步数；不要用通用 benchmark threshold。
- **未来可弱模型执行的条件**：动作低风险且可逆、目标 mark 唯一、无需视觉 locate、无最近失败/安全预警、workflow 前置条件已满足、后置条件可本地检查。否则直接 strong，不要 cheap-call 失败后再升级造成双倍延迟。
- **升级条件**：ambiguous/stale/locate、TaskDoc 重规划、高风险动作、verifier reject、同一 recovery 再失败、模型置信校准不足。router 应是本地规则/小分类器，不额外调用一个 LLM。

#### 4.4 缓存、批处理与 speculative 的边界

- **Prompt caching**：最适合当前长 system/tools + 多轮历史，前提是修复/测量 prefix stability；记录 provider 的 cache read/write token，而不是仅凭延迟猜命中。[S10][S12]
- **Batching**：可以批量做离线轨迹蒸馏、embedding 或评测；不能批量/并行发多个设备动作，因为第一个动作就会使后续 mark epoch 失效。
- **Speculative decoding**：ICML 论文给出在不改变目标分布的 speculative decoding，并在 T5-XXL 报告 2–3 倍生成加速。[S15] 它只适用于自托管/provider 支持的 token decoding，不能跨越“动作后必须观察”的环境依赖；且 actor 输出很短，收益可能小于 prefill 优化，必须实测 TTFT 与 decode 占比。
- **并行 reviewer/verifier**：只有彼此不依赖且不阻塞当前动作时才有意义。安全 gate、finish verifier 都属于决策前置条件，不应后台“先执行后审核”。

---

## 二、给当前仓库的具体落地建议

### 1. 模块边界

| 目标 | 建议文件/模块 | 责任 | 与现有模块的关系 |
|---|---|---|---|
| 经验事实源 | `phone_agent/v2/experience.py` | `ExperienceEvent` schema、append-only JSONL、materialized episode index、redaction | 模式借鉴 `appkb.py`，但存储与 schema 完全分开 |
| 运行时采集 | `phone_agent/v2/middleware/experience.py` | 采集 tool/safety/finish/terminal/usage 信号；生成结构化事件 | 不能复用 `trace.py` 的 64 字文本作为蒸馏源；trace 只保留审计/指标 |
| 评价与蒸馏 | `phone_agent/v2/evolution.py` | episode grading、聚类、LLM distill、候选 schema、证据链接 | 通过 `evolution_model` 或 `memory_model` 调用；不在 action hot path 运行 |
| 晋升与遗忘 | `phone_agent/v2/memory_dream.py` | merge/dedupe/support/conflict/TTL、proposal、promote/revoke/archive | 当前 `dream.py` 继续只负责 App-KB 的确定性整理，避免“大一统 dream” |
| workflow 库 | `phone_agent/v2/workflow_memory.py` | workflow schema、scope、检索、统计、版本 | 输出路线提示或 TaskDoc 建议，不直接输出坐标/旧 mark |
| 选择性注入 | `phone_agent/v2/middleware/memory.py` | run-start recall、精确错误触发 recovery、token cap、provenance | 仅消费 `promoted`；候选在 shadow 模式只记命中，不发给 actor |
| 统一成本账本 | `phone_agent/v2/usage.py` | actor/compact/verifier/reviewer/distill 的 token、cache、latency | `budget.py` 改为读取总账本；避免只统计 actor `AIMessage` |
| 请求投影/缓存观测 | `phone_agent/v2/middleware/context_surface.py` | 稳定前缀、generation、request fingerprint、first diff、cache usage | 中期替代 `taskdoc.py`/`images.py` 的逐轮历史改写；第一阶段只观测 |

**不要扩展 `appkb.py` 去承载失败、用户画像或 workflow。** App-KB 的价值正是 device truth、resolver 和规则式 dream 都可确定性验证。通用经验引入 LLM 蒸馏和不确定性后，需要独立生命周期与权限边界。

### 2. 对现有文件的具体改动点（设计建议，本文未修改）

#### `phone_agent/v2/agent.py`

- 装配 `ExperienceMiddleware`、`MemoryRecallMiddleware` 和共享 `UsageLedger`；
- run 开始固定一次 recall snapshot，记录选中的 memory/workflow id；
- `_build_result()` 把 terminal reason 作为 episode final event；
- request 中稳定 system/tools 与动态 observation 分层，不把每轮易变内容拼回 base system prompt。

#### `phone_agent/v2/appkb.py` / `tools/actuation.py` / `adb/device.py`

- 保持 App-KB 专用 schema；
- 设计文档要求 launch 成功且用户说法不等于 canonical label 时写 `kind=learned`，用户纠正时写 `kind=user`。当前工具层和 ADB 层都把 `learning` 作为只读 lookup 传入 resolver，成功后只返回布尔值/receipt；全仓也没有这两类运行时写入调用。因此这是 App-KB 自积累链的实际缺口，应在实现阶段补齐“成功验证”和“显式纠正”两条写路径并测试，但不与通用经验 store 混合。

#### `phone_agent/v2/dream.py`

- 继续保持无模型、确定性的 App-KB merge/reconcile/prune；
- 通用经验另建 consolidator。顶层 CLI 可顺序调用两者，但不能让 LLM 重写 device truth。

#### `middleware/trace.py` 与 `middleware/diagnostic.py`

- trace 增加 `verdict_code`、`terminal_reason`、`usage_role`、`cache_read/write_tokens`、`request_fingerprint` 和 `experience_event_id`；
- 仍保持生产 trace 的脱敏/短文本；蒸馏只读结构化 experience projection；
- diagnostic full-fidelity 流仅用于明确开启的本地诊断，不能偷偷变成默认长期记忆。

#### `verify.py` / `review.py` / `middleware/safety.py`

- 将 verdict 作为 typed event 输出，保留 model/policy version；
- 区分“验证未触发”“APPROVE”“REJECT”“verifier error 后 fail-open”，不能把后三者折成一个 finished=true；
- safety warning、人工裁决和 takeover 原因进入评价，但 safety experience 只影响离线校准，不直接修改 gate。

#### `middleware/budget.py` / `middleware/compact.py`

- 共享 UsageLedger，所有旁路模型调用都计入 run/evolution budget；
- compact trigger 改为 headroom 公式，并计入 tool schema、最大输出和摘要器余量；
- summary contract 增加 `active_memory_ids`、`workflow_id/version`、app/device scope、未决 safety/finish dispute；
- compaction 仍 fail-open，但连续失败要有指标和终局可见性。

#### `middleware/taskdoc.py` / `middleware/images.py`

- 第一阶段只增加 request-diff/caching telemetry，不贸然改变 P0；
- 第二阶段评估 versioned TaskDoc + append-only flow receipts；
- 图片裁剪从“每轮淘汰一条并原地改写”改为 generation/batch roll-off，减少每轮 cache bust；
- 无论如何，最新截图与 marks 仍必须是易变尾部，执行只认当前 epoch。

#### `session.py` / grounding

- 设备画像记录 accessibility、screenshot、MLX locate 的 p50/p95、失败码和 app/version scope；
- 继续 accessibility-first、LocateAnything on-demand、session singleton；
- 不并行 atomic observation 的组成步骤，不复用跨 epoch marks。

### 3. 建议配置键

| 配置 | 初始默认 | 含义 |
|---|---:|---|
| `PHONE_AGENT_EXPERIENCE` | `off` | `off|observe`；先完成隐私审查再默认 observe |
| `PHONE_AGENT_EVOLUTION` | `off` | `off|propose|apply`；首期不开放自动 apply |
| `PHONE_AGENT_MEMORY_RECALL` | `shadow` | `off|shadow|on`；shadow 只记录会命中什么 |
| `PHONE_AGENT_MEMORY_MAX_ITEMS` | `3` | 每轮最多教训数；workflow 另限 1 个 |
| `PHONE_AGENT_MEMORY_MAX_TOKENS` | `800` | 总注入预算 |
| `PHONE_AGENT_MEMORY_MIN_SUPPORT` | `3` | 晋升最小独立支持次数 |
| `PHONE_AGENT_MEMORY_MAX_AGE_DAYS` | `90` | 未验证/未使用归档线 |
| `PHONE_AGENT_EVOLUTION_MODEL` | `PHONE_AGENT_MEMORY_MODEL` | 轨迹蒸馏/提案模型 |
| `PHONE_AGENT_EVOLUTION_TOKEN_BUDGET` | 独立小预算 | 防止 dream 比 actor 更贵 |
| `PHONE_AGENT_CONTEXT_OUTPUT_HEADROOM` | 按模型校准 | actor 最大输出保留 |
| `PHONE_AGENT_CONTEXT_COMPACT_HEADROOM` | 按摘要器校准 | 保证 summary 请求有空间完成 |
| `PHONE_AGENT_ROUTER` | `shadow` | `off|shadow|on`；主 actor 路由默认不生效 |
| `PHONE_AGENT_WEAK_MODEL` | unset | 低风险 actor/旁路候选模型 |

配置模式应继承现有 CLI > shell env > `.env` > defaults 的解析契约。

### 4. 最小可用验收指标

自进化不能只看“记了多少条”。至少要有以下离线和灰度指标：

- task success / verifier reject / human takeover；
- 每任务 model calls、无效动作数、重复错误数；
- actor + side-call 总 token、cache read ratio、TTFT、model/tool/locate p50/p95；
- recalled memory precision：被注入后是否被采用、是否改善、是否引发 conflict；
- workflow precondition failure、recovery success、app/version 分层成功率；
- candidate -> promoted -> revoked 比率和撤销时延；
- privacy audit：写入的原始文本、截图、敏感字段应为 0。

任何新 memory/route 的上线比较都应报告“无记忆 baseline、shadow、启用”三组；只看模型自评不算验收。

---

## 三、分期路线

### Now：先建立可信数据面，不改变 actor 行为

1. 定义 `ExperienceEvent` / `EpisodeOutcome` / `LessonCandidate` schema 和隐私白名单；
2. 新增 observe-only 经验事件流与 materialized episode index；默认不保存 raw screenshot、用户输入值或完整模型思维；
3. 统一 UsageLedger，补齐 summarizer/verifier/reviewer 调用，记录 cache token 和 request first-diff；
4. 修补 App-KB “launch 成功写 `kind=learned`、明确纠正写 `kind=user`”的设计—实现缺口；
5. 实现结构化 exact/tag 检索的 shadow recall，输出命中率与误命中，不注入 actor；
6. 建立一组历史 episode fixture，标注 A/B/negative outcome，作为后续 promotion 和 routing 的基线。

验收门：不改变现有 task success；experience 写入无敏感字段；总成本可完整对账；能解释每次 cache miss 的首个变化块。

### Next：受控地把经验变成候选，再注入已审核结果

1. 离线/手动 dream：成功—失败对照蒸馏，严格 JSON schema，证据不足即拒绝；
2. Rule-of-3、跨 task、冲突检查、TTL、dry-run proposal 和人工 approve/revoke；
3. 先开启 promoted failure lesson，再开启 workflow skeleton；注入上限 3+1、800 tokens；
4. 调整 compact handoff contract，加入 memory/workflow/safety 状态；
5. 做静态 cache breakpoint 与 generation 式图片折叠实验；是否改变 TaskDoc P0 另开设计评审；
6. 将便宜模型用于 compact/distill，一并进入预算和质量回归。

验收门：在 holdout/受控真机任务上减少无效步骤或 token，且 verifier reject、安全告警和 takeover 不恶化；可一键撤销某条 memory version。

### Later：经过实证后再开放执行级优化

1. workflow executor 只覆盖可逆、结构可判定、前后置条件完备的白名单流程；
2. weak/strong actor routing 从 shadow 转灰度，按本项目 episode 分布校准；
3. emulator/受控设备 baseline-vs-trial，支持自动回滚和 cooldown；
4. 当 memory 达到数千条且结构化召回出现经测量的 recall gap，再引入向量候选召回；
5. 自托管 serving 若 decode 占比足够高，再评估 speculative decoding；
6. 自动 apply 最多开放给低风险、可撤销的记忆/工作流资产，核心安全策略和代码仍走人审发布。

---

## 四、明确不做的事

1. **不把 App-KB 变成万能 memory。** 设备事实可确定性对账，经验教训是概率性结论；混在一起会污染 resolver 的事实权威。
2. **不先上 vector DB RAG。** 当前规模下，package/tool/error/version 等结构过滤更便宜、更可解释；向量近似不能决定规则是否有权生效。AWM/Voyager 使用 FAISS/Chroma 是其规模和任务的实现选择，不是本项目的起步要求。[S1][S4]
3. **不全量注入历史、日志或 memory。** 它会增加成本、稀释注意力并放大陈旧规则；最多 1–5 条相关内容，且有总 token cap。[S5]
4. **不回放旧坐标、旧 mark 或固定点击序列。** atomic observation 与 epoch 设计已明确：动作必须绑定当前世界状态。
5. **不把一次 Reflexion 直接升为永久规则。** 反思可以生成候选，但必须经过独立证据、跨任务验证、冲突检查和人审。[S2]
6. **不让模型在线改核心代码、system prompt 或 safety policy。** 自进化资产采用封闭 schema、版本、proposal 和 rollback；安全放宽永远需要人工评审。
7. **不把 verifier 当绝对真值。** 要保存“未触发/approve/reject/error-fail-open”的差别，并结合环境和人工证据。
8. **不持久化原始截图、验证码、联系人、订单、输入文本或完整思维链作为经验。** 学习数据必须是受控投影；诊断证据与长期记忆分域。
9. **不并行或 batch 执行设备动作。** 第一个动作后观察和 mark 已变化；批处理只用于离线蒸馏与评测。
10. **不因通用 benchmark 的 85%/98% 成本数字直接切弱 actor。** RouteLLM/FrugalGPT 的结果证明方向，不证明手机控制中的安全和正确性；必须用本项目分布校准。[S13][S14]
11. **不为每个旧截图建立 actor 可读的磁盘回填。** 当前屏幕应重观测；旧图只服务审计/评测，除非以后出现不可重建的大型工具产物。
12. **不为了 prefix cache 牺牲 TaskDoc、fresh observation 或 marks freshness。** 先保证静态前缀命中并测量收益，高收益 append-mostly 改造需单独修改 P0 契约。

---

## 五、结论

Open-AutoGLM 的正确演化方向不是更厚的 planner，而是让薄循环外围形成一个受治理的学习系统：**事件是事实源，评价决定证据强弱，LLM 只负责把多条轨迹压缩成候选，晋升决定候选何时获得影响未来行为的权限，注入严格有界，遗忘和回滚与学习同等重要。**

短期最有价值的闭环是“observe-only Experience Store + 统一 usage/cache 账本 + shadow recall”。它不会改变设备行为，却会回答三个现在无法可靠回答的问题：哪些失败会重复、哪些候选经验真正相关、哪部分延迟/成本来自 actor 之外。等这些数据成立后，再开启 promoted lesson 和 workflow skeleton，风险远低于直接做自动 skill 生成或向量 RAG。

---

## 本地代码依据

核验基线为 `feature/thin-loop-v2` 当前工作树（HEAD `7982f0979fc7`，2026-08-29）。工作树已有未提交改动，因此以下结论以核验时磁盘内容为准，而不是把 HEAD 当成唯一事实源。

| 结论 | 当前代码锚点 |
|---|---|
| thin-loop 与 P0 契约 | [AGENTS.md:5–30](/Users/bytedance/Open-AutoGLM/AGENTS.md:5) |
| 首轮已携带截图与 marks，无需额外 `read_screen` | [agent.py:55–86](/Users/bytedance/Open-AutoGLM/phone_agent/v2/agent.py:55) |
| middleware 装配、App-KB run-start 同步/有界注入、终局分类 | [agent.py:95–411](/Users/bytedance/Open-AutoGLM/phone_agent/v2/agent.py:95) |
| App-KB 事件日志/物化视图、设备同步、确定性 lookup | [appkb.py:156–447](/Users/bytedance/Open-AutoGLM/phone_agent/v2/appkb.py:156) |
| App-KB 写入规格（含成功 launch 学习） | [app-kb-memory-design.md:92–120](/Users/bytedance/Open-AutoGLM/docs/app-kb-memory-design.md:92) |
| launch 链路只把 `learning` 用于 lookup，成功后无 learned upsert | [actuation.py:372–420](/Users/bytedance/Open-AutoGLM/phone_agent/v2/tools/actuation.py:372); [device.py:436–508](/Users/bytedance/Open-AutoGLM/phone_agent/adb/device.py:436); [app_registry.py:352–408](/Users/bytedance/Open-AutoGLM/phone_agent/config/app_registry.py:352) |
| atomic observation 与 session 级 locate provider | [session.py:298–647](/Users/bytedance/Open-AutoGLM/phone_agent/v2/session.py:298) |
| TaskDoc 每轮删除旧块并以新 UUID 重发 | [taskdoc.py:189–239](/Users/bytedance/Open-AutoGLM/phone_agent/v2/middleware/taskdoc.py:189) |
| 旧 image/marks 原地替换 | [images.py:80–172](/Users/bytedance/Open-AutoGLM/phone_agent/v2/middleware/images.py:80) |
| T1/T2 阈值、summary、全量 message surface rebuild | [compact.py:225–322](/Users/bytedance/Open-AutoGLM/phone_agent/v2/middleware/compact.py:225) |
| token budget 当前仅累计 actor `AIMessage` | [budget.py:100–132](/Users/bytedance/Open-AutoGLM/phone_agent/v2/middleware/budget.py:100) |
| trace 64 字截断与 model/tool latency | [trace.py:31–197](/Users/bytedance/Open-AutoGLM/phone_agent/v2/middleware/trace.py:31) |
| finish 本地疑点、两段式确认和独立 verifier | [review.py:70–205](/Users/bytedance/Open-AutoGLM/phone_agent/v2/review.py:70); [control.py:135–179](/Users/bytedance/Open-AutoGLM/phone_agent/v2/tools/control.py:135); [verify.py:86–240](/Users/bytedance/Open-AutoGLM/phone_agent/v2/verify.py:86) |

---

## 来源

- **[S1]** Wang et al., *Agent Workflow Memory*, arXiv 2409.07429；官方实现与 offline/online induction：<https://github.com/zorazrw/agent-workflow-memory>
- **[S2]** Shinn et al., *Reflexion: Language Agents with Verbal Reinforcement Learning*, NeurIPS 2023：<https://openreview.net/forum?id=vAElhFcKW6>；官方代码：<https://github.com/noahshinn/reflexion>
- **[S3]** Zhao et al., *ExpeL: LLM Agents Are Experiential Learners*：<https://andrewzh112.github.io/expel/>；官方代码：<https://github.com/LeapLabTHU/ExpeL>
- **[S4]** Wang et al., *Voyager: An Open-Ended Embodied Agent with Large Language Models*；官方代码：<https://github.com/MineDojo/Voyager>
- **[S5]** `dsh-self-evolving-agent` 的四层记忆、Rule-of-3、dry-run 与降级设计：<https://github.com/fzs356113-oss/dsh-self-evolving-agent>
- **[S6]** `dsh-auto-evolve` 的 observe/propose/validate/apply/rollback、预算与 cooldown：<https://github.com/lispking/dsh-auto-evolve>
- **[S7]** DeepSeek Harness，event-sourced sessions：<https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-06-11-event-sourced-sessions.md>
- **[S8]** DeepSeek Harness，可重建请求与 append-only prefix：<https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-07-05-reconstructable-requests.md>
- **[S9]** DeepSeek Harness，tool output spill policy：<https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-07-08-tool-output-spill-files.md>
- **[S10]** Anthropic 官方 Prompt Caching 文档：<https://platform.claude.com/docs/en/build-with-claude/prompt-caching>
- **[S11]** Claude Code compaction 实现观察（二手逆向资料，非官方保证）：<https://decodeclaude.com/compaction-deep-dive/>；按版本抽取的 durable-memory、dream consolidation 与 reconciliation prompts：<https://github.com/Piebald-AI/claude-code-system-prompts>
- **[S12]** OpenAI 官方 Prompt Caching 文档：<https://platform.openai.com/docs/guides/prompt-caching>
- **[S13]** Ong et al., *RouteLLM: Learning to Route LLMs with Preference Data*；官方实现：<https://github.com/lm-sys/RouteLLM>
- **[S14]** Chen et al., *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance*：<https://ar5iv.labs.arxiv.org/html/2305.05176>
- **[S15]** Leviathan et al., *Fast Inference from Transformers via Speculative Decoding*, ICML 2023：<https://proceedings.mlr.press/v202/leviathan23a.html>
- **[S16]** DeepSeek Harness 官方仓库与 “everything is a plugin” 架构：<https://github.com/deepseek-ai/deepseek-harness>
- **[S17]** DeepSeek Harness 的 profile/bundle/patch 分层与 Cordis HMR 生命周期：<https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-08-05-profile-plugin-bundles.md>；<https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cordis-tutorial/06-composition-and-hmr.md>
