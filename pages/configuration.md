# 配置参考

所有运行时配置为 `PHONE_AGENT_*` 环境变量。优先级：**CLI 参数 > shell 环境变量 > `.env` > 默认值**。`.env.example` 为模板（含完整注释）。

## 模型

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_BASE_URL` | url | `http://localhost:8000/v1` | OpenAI-compatible 网关地址。**必填** |
| `PHONE_AGENT_MODEL` | str | `autoglm-phone-9b` | 主模型 id，需视觉多模态能力。**必填** |
| `PHONE_AGENT_API_KEY` | str | `EMPTY` | API key。**必填** |
| `PHONE_AGENT_MODEL_TIMEOUT` | int | `180` | 单次模型请求超时（秒） |
| `PHONE_AGENT_MODEL_MAX_RETRIES` | int | `2` | 模型请求重试次数 |
| `PHONE_AGENT_TEMPERATURE` | float | 不发送 | 采样参数；网关限制固定值时在此覆盖 |
| `PHONE_AGENT_TOP_P` | float | 不发送 | 同上 |
| `PHONE_AGENT_FREQUENCY_PENALTY` | float | 不发送 | 同上 |
| `PHONE_AGENT_HTTP_HEADERS` | str | 无 | 附加请求头，格式 `K1=V1;K2=V2` |
| `PHONE_AGENT_USER_AGENT` | str | 内置 UA | 覆盖默认 User-Agent |

## 运行控制

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_DEVICE_ID` | str | 自动识别 | ADB 设备序列号；多设备时必填 |
| `PHONE_AGENT_LANG` | `cn`/`en` | `cn` | 提示词语言 |
| `PHONE_AGENT_MAX_STEPS` | int | `100` | 单轮最大模型调用数；仅作失控保险丝，非成本手段 |
| `PHONE_AGENT_MAX_HITL_RESUMES` | int | `20` | 人工中断恢复次数上限 |

## 预算与上下文压缩

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_TOKEN_BUDGET` | int | `1000000` | 单轮 input+output token 总预算，耗尽终止运行 |
| `PHONE_AGENT_TOKEN_WARN_REMAINING` | int | `100000` | 剩余低于该值时向模型注入一次余量提醒 |
| `PHONE_AGENT_COMPACT` | bool | `true` | auto-compact 总开关 |
| `PHONE_AGENT_COMPACT_WARN_RATIO` | float | `0.75` | 上下文占窗口比例达到此值时提醒模型收敛 |
| `PHONE_AGENT_COMPACT_TRIGGER_RATIO` | float | `0.92` | 达到此值时生成 handoff 摘要并折叠历史 |
| `PHONE_AGENT_CONTEXT_WINDOW` | int | 按模型推断，兜底 `256000` | 手动覆盖上下文窗口大小 |
| `PHONE_AGENT_MEMORY_MODEL` | str | 主模型 | compact 摘要使用的模型 |
| `PHONE_AGENT_IMAGE_KEEP` | int | `2` | 历史中保留的含图消息数 |
| `PHONE_AGENT_OBS_MARKS_KEEP` | int | `2` | 历史中保留完整 marks 摘要的观测数 |

## 界面落地（Grounding）

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_GROUNDING_PROVIDER` | `hybrid`/`accessibility`/`locateanything` | `hybrid` | mark 来源；hybrid = 控件树优先，视觉兜底 |
| `PHONE_AGENT_ACCESSIBILITY_TIMEOUT` | float | `3.0` | 控件树抓取超时（秒） |
| `PHONE_AGENT_ACCESSIBILITY_MAX_MARKS` | int | `80` | 单次观测最多输出的 mark 数 |
| `PHONE_AGENT_MARKS_WINDOWED` | `auto`/`on`/`off` | `auto` | 窗口感知 marks（纯展示层）。`auto` 先试 `uiautomator dump --windows`，不支持则回退单根 dump；`on` 强制 `--windows`（不支持报错可见）；`off` 旧平铺渲染。仅影响分组/标注/渲染，寻址/执行/安全门/折叠/locate 不变，`op=blocked` 仅展示不拦截 |
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | path | 无 | 本地视觉定位模型路径；不配置则视觉定位不可用 |
| `PHONE_AGENT_LOCATE_MAX_SIZE` | int | `0` | locate 输入图最长边；`0` = 原图 |
| `PHONE_AGENT_SCOPE_PADDING_RATIO` | float | `0.05` | scope 区域裁剪的边缘扩展比例 |
| `PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` | int | `200` | locate 指令中单字段提示长度上限 |
| `PHONE_AGENT_PARALLEL_TOOL_CALLS` | bool | `false` | 并行工具调用；网关拒绝该参数时设 `true` |

## 观测

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_OBSERVE_SETTLE_MS` | int | `300` | 每次观测采样前的静置毫秒数；`0` 关闭。应对加载延迟的页面 |
| `PHONE_AGENT_BLACK_SCREEN_DETECT` | bool | `true` | 全黑截图判定为 FLAG_SECURE 保护屏，不下发黑图 |

## 安全与验收

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_SAFETY_MODE` | `off`/`wary`/`hard`/`reviewer` | `wary` | 执行类动作门控，详见[安全模式](safety.md) |
| `PHONE_AGENT_SAFETY_REVIEWER_MODEL` | str | verifier 模型 | `reviewer` 档的风险精排模型；未设置时该档不可用 |
| `PHONE_AGENT_FINISH_VERIFY` | `off`/`auto`/`always` | `auto` | finish 独立验收器触发策略 |
| `PHONE_AGENT_FINISH_VERIFY_K` | int | `1` | 验收器查看的尾部截图数 |
| `PHONE_AGENT_VERIFIER_MODEL` | str | 主模型 | 验收器模型 |

## 记忆

### App-KB

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_APP_KB` | bool | `true` | App-KB 总开关（同步/读取/写回/注入） |
| `PHONE_AGENT_MEMORY_DIR` | path | `memory` | 记忆根目录 |
| `PHONE_AGENT_APP_LIST_MAX` | int | `40` | 注入提示词的应用名数量上限 |
| `PHONE_AGENT_DREAM` | `off`/`auto`/`manual` | `manual` | 记忆整理时机；`manual` 仅 `--dream` |
| `PHONE_AGENT_IMPLICIT_ALIAS` | bool | `true` | 隐式纠正：叫法失败→候选包名成功时自动记别名 |
| `PHONE_AGENT_ALIAS_OVERWRITE` | bool | `true` | dream 是否从同 run 的“开错并秒退→随后成功”证据覆盖错误 learned 别名 |
| `PHONE_AGENT_ALIAS_OVERWRITE_NOTES` | comma-separated str | `开错,不对,不是,错了,wrong app` | 模型明确自述开错应用的匹配词表；事件只落命中的词，不落完整 note |

手工纠正可用 `main_v2.py --learn-alias "名称=包名"` 写入最高信任的全局 `user` 别名；即使包未安装也会在警告后保存。`main_v2.py --forget-alias "名称"` 只删除该名称的全局 `user` / `learned` 条目，不影响设备清单。两者都会把实际变更追加到 `memory/app_kb/events.jsonl`。

### App 名解析

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_RESOLVER_DECISION_MODE` | enum | `typed` | `typed` 使用证据类型化三态决策；`legacy` 回退旧 score 阈值决策 |
| `PHONE_AGENT_RESOLVER_MIN_SCORE` | float | `0.90` | legacy 模式 top1 成为 resolved 的最低综合分；typed 模式只作为弱候选展示阈值 |
| `PHONE_AGENT_RESOLVER_MARGIN` | float | `0.08` | legacy 模式 top1 相对 top2 的最小领先分差；不足则 ambiguous |
| `PHONE_AGENT_RESOLVER_TYPED_MARGIN` | float | `0.08` | typed 模式强证据 top1 相对 top2 的最小 `rank_score` 分差；不足则 ambiguous |
| `PHONE_AGENT_RESOLVER_TOP_K` | int | `10` | 结构化结果、trace 与失败回执最多保留的排序候选数 |
| `PHONE_AGENT_RESOLVER_LEXICAL` | bool | `true` | 启用归一化变体、字符 bigram/trigram 与 difflib 候选路 |
| `PHONE_AGENT_RESOLVER_PINYIN` | bool | `true` | 启用全拼与首字母候选路；pypinyin 不可用时 fail-open 跳过 |
| `PHONE_AGENT_RESOLVER_EMBED` | bool | `true` | 启用 `vec.db` App alias 向量候选路；索引/模型不可用时 fail-open 跳过 |
| `PHONE_AGENT_RESOLVER_PACKAGE_SEGMENT_MIN_LEN` | int | `4` | 包名分段强证据的最小段长 |
| `PHONE_AGENT_RESOLVER_PACKAGE_SEGMENT_STOPWORDS` | csv | `com,org,net,android,example,app,mobile,free,debug,release` | 包名按 `.`/`_`/`-`/camelCase 切段后过滤的无意义段 |
| `PHONE_AGENT_RESOLVER_AUTO_MATCH_TYPES` | csv | `exact_alias,exact_label,exact_package,exact_package_segment,registered_containment` | typed 模式允许自动 resolved 的强证据类型 |
| `PHONE_AGENT_RESOLVER_CLARIFY_MATCH_TYPES` | csv | `fuzzy,pinyin_full,pinyin_initials,embedding` | typed 模式只用于澄清/候选展示、不会单独自动 resolved 的弱证据类型 |
| `PHONE_AGENT_RESOLVER_W_SIM` | float | `0.8` | 综合分中的相似度权重 |
| `PHONE_AGENT_RESOLVER_W_PRIOR` | float | `0.2` | 综合分中的 App-KB 先验权重 |

`rank_score = W_SIM * sim + W_PRIOR * prior`。在 typed 模式下它只用于排序、margin、回执和 trace，不单独赋予执行 authority。`exact_package_segment` 只接受完整分段相等，例如 `Firefox` 匹配 `org.mozilla.firefox`，但 `fox` 不匹配；`PiliPlus` 匹配 `com.example.piliplus`，但 `plus` 不匹配。名称候选胜出后仍须通过设备安装事实和 launch policy；解析配置不能扩大启动权限。

### 经验与回想

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_EXPERIENCE` | bool | `true` | episode 档案记录开关（observe-only） |
| `PHONE_AGENT_EXPERIENCE_DIR` | path | `memory/experience` | 档案目录 |
| `PHONE_AGENT_EPISODE_KEEP` | int | `500` | 保留的完整档案数；更老的归档为聚合统计 |
| `PHONE_AGENT_EPISODE_ARCHIVE_DAYS` | int | `90` | 超过该天数的档案在 dream 时归档 |
| `PHONE_AGENT_MEMORY_RAG` | `off`/`shadow`/`on` | `shadow` | 语义回想档位；`shadow` 只观测不注入；`on` 注入人审通过的经验 |
| `PHONE_AGENT_EMBED_MODEL` | str | `Qwen/Qwen3-Embedding-0.6B` | 本地嵌入模型（MLX） |
| `PHONE_AGENT_EMBED_DIM` | int | `1024` | 嵌入向量维度 |
| `PHONE_AGENT_VEC_DB` | path | `memory/vec.db` | 向量索引文件；run 结束增量更新，dream 对账 |
| `PHONE_AGENT_INDEX_MIN_STEPS` | int | `2` | episode 索引质量闸门；更短的 run 只留档，alias 不受影响 |
| `PHONE_AGENT_RECALL_TOP_K` | int | `1` | episode 语义榜名额；app mention 独立返回、不占名额 |
| `PHONE_AGENT_RECALL_MIN_SCORE` | float | `0.50` | 基于噪音分布的语义起始门槛，可调；无词法精确命中时门槛更高 |
| `PHONE_AGENT_RECALL_DECAY_LAMBDA` | float | `0.02` | 时间衰减速率（按天），只用于语义同分决胜 |
| `PHONE_AGENT_EVOLUTION` | `off`/`manual` | `manual` | 经验提炼开关；`manual` 由 `--distill` 触发 |
| `PHONE_AGENT_LESSONS_DIR` | path | `memory/lessons` | 经验库存储目录 |
| `PHONE_AGENT_LESSON_INJECT_MAX` | int | `3` | 单次注入的经验条数上限 |
| `PHONE_AGENT_LESSON_INJECT_TOKENS` | int | `800` | 注入内容的 token 上限 |

## 任务板与记录

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PHONE_AGENT_TASKDOC` | bool | `true` | TaskDoc 任务板开关 |
| `PHONE_AGENT_TRACE` | bool | `true` | JSONL trace 开关（脱敏，不含截图） |
| `PHONE_AGENT_TRACE_DIR` | path | `.traces` | trace 目录 |
| `PHONE_AGENT_DIAG_EVIDENCE` | bool | `false` | 诊断证据流（live-diagnosis 用） |
| `PHONE_AGENT_DIAG_UNREDACTED` | bool | `false` | 本机诊断全保真模式 |
| `PHONE_AGENT_RUNS_DIR` | path | `memory/runs` | runner 子进程运行目录（事件/控制通道） |
