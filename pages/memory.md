# 记忆与自进化

TaskWizard 的记忆分三层，全部本地存储（`memory/`），不依赖外部服务。

## 总览

```mermaid
flowchart TD
    RUN["每次运行"] -->|结束| EP["episode 档案<br/>memory/experience/"]
    RUN -->|应用启动事实| KB["App-KB<br/>memory/app_kb/"]
    EP --> IDX["向量索引<br/>memory/vec.db"]
    KB --> IDX
    IDX -->|shadow：只记录| STATS["命中率统计<br/>recall_stats.json"]
    DREAM["dream 整理<br/>（run 之间）"] --> KB
    DREAM --> EP
```

## 第一层：App-KB（应用事实库）

记录本机可启动应用及其别名，按设备序列号隔离。

写入路径：

| 来源 | 条件 | 优先级 |
|---|---|---|
| `device` | run 启动时同步本机应用清单 | 最低（可被覆盖） |
| `learned` | 启动成功且叫法与官方名不同；或"中文叫法失败→候选包名成功"的隐式纠正 | 中 |
| `user` | 用户明确纠正（`--learn-alias 名称=包名`） | 最高 |

管理入口：`--learn-alias` 写入 user 别名、`--forget-alias` 删除 user/learned 别名（device 条目不动）。

错误别名纠正：dream 在整理时识别"启动 A → 1-2 步内模型自述开错并退出 → 启动 B 成功"的签名，直接覆盖错误 learned 映射（仅保存命中的自述词，不落完整模型 note）；user 别名阻止自动覆盖。

App 名解析（启动时的名字→包名）：归一化 → 多路候选生成（精确/词汇/拼音/嵌入向量）→ 先验排序 → 证据分型三态决策。设备事实类强证据（精确别名、产品名等于包名片段且唯一）才自动执行；拼音/模糊/嵌入弱证据只给排序候选由模型选择。

存储为 JSONL 事件日志 + 可重建的 `kb.json` 视图。`dream` 在 run 之间整理：淘汰已卸载应用、衰减长期未用条目（`--dream` 或 `PHONE_AGENT_DREAM=auto`）。

## 第二层：episode 经验档案

每次 run 结束写一条结构化档案（`memory/experience/`）：目标（脱敏）、成败与终局原因、步数、分角色 token、警告次数、验收判决、涉及应用、时段/星期、能力快照。

- 隐私白名单：只存上述骨架字段；输入内容、mark 文本、截图永不落盘；
- observe-only：记录不改变模型行为；
- 超量（默认 500 条）或超龄（默认 90 天）的档案由 dream 折叠为聚合统计。

## 第三层：语义回想（RAG，默认 shadow）

```mermaid
flowchart LR
    Q["新任务"] --> M{"任务文本提到 app？"}
    M -- "mention 精确命中" --> AA["app 候选（确定性）"]
    M -- 否 --> F["硬过滤<br/>本机设备 · 未撤销"]
    F --> S["语义召回<br/>episode 独立榜 top-1"]
    AA --> R["候选"]
    S --> R
    R -->|shadow 档| LOG["只写 trace/统计<br/>不进模型上下文"]
    LOG --> EVAL["run 结束自动对答案<br/>（召回的 app vs 实际启动的 app）"]
```

- 嵌入模型：本地 MLX 运行 Qwen3-Embedding-0.6B（`PHONE_AGENT_EMBED_MODEL` 可换）；
- 索引：run 结束自动增量更新（质量闸门：空转档案不进索引）；`--rebuild-vec` 可全量重建；
- 别名嵌入文本含中文名（learned/user 别名 → 静态 registry → 包名）；纯包名条目只供精确匹配；
- 统计口径：Hit@1、命中率、污染率（contaminated run rate）、包级 P/R，控制台「记忆」页展示；达标前不开启注入。

## 提炼、晋升与回注（已实现）

- **提炼**：`--distill` 离线蒸馏——LLM 对成功/失败档案做对照分析，产出候选经验（严格 schema，证据不足即拒绝，正文不落用户输入原文）；
- **晋升**：Rule-of-3（≥3 次独立出现、跨 ≥2 任务、0 冲突）+ 人工审批（`--review-lessons` / `--approve-lesson` / `--revoke-lesson`），版本链可撤销；
- **回注**（`PHONE_AGENT_MEMORY_RAG=on`）：已批准的经验在 run 开局以"参考提示"身份注入（上限 3 条 / 800 token，设备 scope 过滤，run 内钉死该代）；注入的 lesson id 写入 trace 与 episode 档案，用于事后度量"注入是否有帮助"；
- **约束**：只有人审通过的经验可被注入；proposed/revoked 永不注入；shadow/off 档完全不注入。

原则：先记录、再影子验证、晋升靠人审、注入有上限可撤销；每一步可回退。
