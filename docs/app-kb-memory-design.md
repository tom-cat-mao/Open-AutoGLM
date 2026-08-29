# App 知识库（App-KB）与 Agent Memory 骨架设计

> 状态：已与 owner 对齐方向（D1/D2/D4 通过，D3 配置化）。本文档是实施规格（修改文档），
> 供 worktree 并行施工与验收对照。硬约束见根 `AGENTS.md`（P0 表）。
>
> 调研依据：Claude Code 发布包提取的 memory prompts（Piebald-AI/claude-code-system-prompts，
> ccVersion 2.1.x）+ Voyager skill-library 范式。对应关系见附录 A。

---

## 1. 背景与问题

`phone_agent/config/apps.py::APP_PACKAGES` 是一张**手工写死的"友好名→包名"表**（约 70 条，
源自项目首个 draft，v1 遗产）。对通用 Agent 它是死路：

- **无人维护**：写进源码 = 永远不全、永远过时；
- **覆盖脆弱**：实测用户最自然的说法"哔哩哔哩"解析不到（表里是拼音 `bilibili` + 残缺别名）；
- **模型靠猜**：规划"打开某 App"时不知道该 App 装没装、规范名叫什么，只能瞎试到 `launch_app` 失败。

## 2. 设计原则：两极互补

| 极 | 来源 | 角色 |
|---|---|---|
| **极 A：设备事实** | 运行时从设备取（`pm get-application-label`） | **权威源**，对账时永远赢；回答"装了什么/真名叫啥" |
| **极 B：知识沉淀** | 本地持久化 App-KB，跨 run 累积 | 回答"口语别名怎么桥接"（b站→哔哩哔哩），跨 run/设备复用 |

**模型永远不接触包名**；包选择由 resolver 依据设备事实做出，歧义 fail-closed。

## 3. 总体架构

```
        ┌───────────── 设备（adb）─────────────┐
        │ pm get-application-label → {label↔包名}│   ← 极 A，每次 run 同步
        └───────────────┬─────────────────────┘
                        ▼
              memory/app_kb/  （JSONL 事件日志 + 物化视图）   ← 极 B，自累积
                        ▲        ▲
   launch 成功验证 / 用户纠正 ───┘        └──── dream 整理器（合并/对账/清理）
                        │
        resolver：设备 label 优先 → KB 别名兜底 → fail-closed
                        │
        system prompt 注入有界清单；launch_app 失败回执附候选
```

## 4. 存储设计（D1、D2 已锁定）

**目录**：`<memory_dir>/app_kb/`（`memory_dir` 默认仓库根 `memory/`，`PHONE_AGENT_MEMORY_DIR` 可配）。

- `events.jsonl` —— **追加式事件日志**（每次修改一条记录：`{op: upsert|mark_stale|delete, entry, ts}`）。
  这是"每次修改都记录"的落点，审计/回放用。
- `kb.json` —— **物化视图**（当前生效全量，由日志重放或增量维护）。
- 条目 schema：

```json
{
  "term": "哔哩哔哩",
  "label": "哔哩哔哩",
  "package": "tv.danmaku.bili",
  "kind": "device | alias | learned | user",
  "scope": "device:<serial> | global",
  "confidence": 1.0,
  "success_count": 3,
  "first_seen": "ISO-8601",
  "last_seen": "ISO-8601",
  "stale": false
}
```

**D2 两层作用域**：
- `device:<serial>` —— 设备事实（label↔包名），按设备隔离，换机不串味；
- `global` —— 纯别名（`b站→哔哩哔哩`），跨设备复用；**只存说法映射，不断言装机事实**。

**内容太长的对策**（照搬 CC 索引/内容分离）：进 context 的永远只有有界清单（索引级），
KB 明细只在 resolver 内部与失败回执里出现；日志无限增长由 dream 的 compact（重写物化视图 +
截断日志）处理。

## 5. 设备层（WP-A）

新增 `get_app_labels(device_id)`（`adb/device.py` + `device_factory.py` 转发）：

1. 列可启动包：优先 MAIN+LAUNCHER intent 查询（只回"能打开的 App"）；
   查询失败回退 `pm list packages -3`（第三方包）。
2. 逐包 `pm get-application-label <pkg>` 取用户可见名（AOSP 官方、免 root、本地化）。
   单条 `adb shell` 内循环完成，避免 N 次 adb 往返。
3. 返回 `list[AppLabelEntry(package, label)]`；任何一步失败返回空列表（fail-open 退化，
   不阻塞 run——KB/清单只是增强）。
4. **缓存**：以 `pm list packages` 输出指纹为键；装机集合不变不重查 label。

> ⚠️ 真机验证项（当前无设备）：命令确切输出格式、逐包耗时、MAIN+LAUNCHER 查询 flag。
> 施工时全部用 fake adb 输出单测；真机接上后先跑一遍冒烟再合入。

## 6. 写入路径（什么时候沉淀）

三个触发点（满足其一即写，且过门）：

1. **设备同步**：run 首取到 `{label↔包名}` → upsert `kind=device, scope=device:<serial>`；
2. **验证成功**：`launch_app` 启动成功且所用说法 ≠ 规范 label → upsert `kind=learned` 别名，
   `success_count += 1`；
3. **用户纠正**：消歧/改口发生 → upsert `kind=user`（最高置信）。

**写入门**（可适用 / 可持久 / 非敏感；拿不准不写）。一次性瞬态（如本次任务特有的临时说法）不写。
写入必须落 `events.jsonl` + 更新物化视图（每次修改有痕）。

## 7. 读取路径（什么时候调用 / 放多少 / 怎么放）

**resolver 解析顺序**（`LaunchTargetResolver.resolve` 新增一级，插在静态表之后）：

1. 静态注册表（存量行为）；
2. **KB lookup**：先按本机 device 层 label 匹配（精确 → 归一化 → 子串），
   再按 global 别名桥接到 label/包名；
3. candidates/inventory 兜底；歧义 fail-closed 回候选。

实现要点：现有 `learning` 参数是 duck-type 空槽（`.lookup(term)` / `.snapshot()`，无实体类）。
**App-KB 提供一个实现该接口的 `AppKnowledge` 对象**，插进现有槽位，resolver 主体不动。

**进 context 的量**：
- system prompt 尾部注入**有界清单**：本机可启动 App 规范名（label），上限 `PHONE_AGENT_APP_LIST_MAX`（默认 40）；
  超出按 device 事实的常用度截断并附 "…等 N 个，可用 launch_app 尝试其它名称"。
- `launch_app` 失败/歧义回执：附当前可用名候选（≤10 条），让模型下一步自愈。
- KB 明细（包名/计数/时间戳）**不进模型 context**。

## 8. dream 整理器（D3 配置化）

**规则式、无 LLM**（App-KB 是结构化数据，确定性整理比 LLM 可靠且零成本）：

- 合并：同包同 label 的重复条目合并（保留高置信/高计数）；
- 对账：与设备当前清单核对——已卸载/改名 → `stale=true`（device 层）；
- 清理：长期未命中（`last_seen` 超阈值）且低置信 → 删除；
- 日期绝对化、物化视图重写、日志截断；
- 输出整理摘要（合并/标 stale/删除各多少条）。

**触发（`PHONE_AGENT_DREAM=off|auto|manual`，默认 `manual`）**：
- `manual`：仅 `--dream` CLI 标志时跑；
- `auto`：每次 run 结束自动跑轻量版（只合并+对账，不删除）；
- `off`：不跑。

## 9. 配置键（新增，全部 `PHONE_AGENT_` 前缀）

| 键 | 默认 | 含义 |
|---|---|---|
| `PHONE_AGENT_MEMORY_DIR` | `memory/` | 记忆根目录 |
| `PHONE_AGENT_APP_KB` | `true` | App-KB 总开关（0/false/no/off 关） |
| `PHONE_AGENT_APP_LIST_MAX` | `40` | system prompt 清单上限 |
| `PHONE_AGENT_DREAM` | `manual` | off\|auto\|manual |

## 10. 隐私与安全

- KB 暴露"用户装了哪些 App"——**仅存本地、永不外传**（不进 trace/diagnostic  egress 的敏感位；
  trace 的 P0 #6 脱敏不变）；
- 提供清除机制：`--dream` 或手动删除 `memory/app_kb/` 即可；
- 不写任何凭据/账号类信息（写入门里的"非敏感"）。

## 11. 实施分解（work packages）

- **WP-A（worktree `wp/device-labels`）**：设备层 `get_app_labels` + 缓存 + fake 单测。
  不依赖主树未提交的 bug 修复。
- **WP-B（worktree `wp/app-kb-store`）**：`phone_agent/v2/appkb.py`（事件日志+物化视图+
  `AppKnowledge.lookup/snapshot`）+ `phone_agent/v2/dream.py`（规则式整理）+ 单测。
  全新文件，零依赖。
- **WP-C（主树，待 WP-A/B 合入后）**：集成——resolver 接 KB 槽位、run 首设备同步 +
  system prompt 清单注入、`launch_app` 失败回执附候选、配置键、`--dream` CLI、
  README/AGENTS 同步、全量测试。
- **WP-F（worktree `wp/appkb-write-closure`）**：闭合验证启动写路径——KB 命中成功后通过
  `upsert` 事件累计 `success_count`，静态解析或设备事实命中成功后将非敏感的新说法写为
  `kind=learned, scope=global`；所有写回失败均 fail-open，不改变启动回执。用户明确纠正写
  `kind=user` 仍留待具备纠正信号的后续入口。

## 12. 验收标准

1. `.venv/bin/pytest tests -q` 与 `.venv/bin/python -m pytest tests -q` 双全绿；
2. WP-A：fake 设备下 `get_app_labels` 正确解析 `pm` 输出、缓存命中不重复查询；
3. WP-B：事件日志每次写入有痕、物化视图一致、`lookup` 精确/归一化/子串/别名顺序正确、
   dream 合并/对账/清理行为正确；
4. WP-C：端到端 fake run 里——首条 system/上下文含 App 清单；"哔哩哔哩"经 KB 别名命中
   `tv.danmaku.bili`；launch 失败回执含候选；`PHONE_AGENT_APP_KB=0` 全关后行为退化为现状；
5. WP-F：fake launch 下新说法同时落入 `kb.json` / `events.jsonl`；既有 KB 命中只累计
   `success_count`，同名、敏感说法和关闭 App-KB 均不写，写回异常不影响成功回执；
6. 真机冒烟（设备接上后）：`get_app_labels` 真机输出格式核验通过。

## 附录 A：与 Claude Code memory 的机制对照

| Claude Code（源码实证） | 本设计 |
|---|---|
| CLAUDE.md（人维护权威源，对账赢） | 设备事实（极 A） |
| memory 主题文件（一文件一事实+frontmatter） | `app_kb` 条目（JSONL 事件 + 物化视图） |
| MEMORY.md 索引（≤200 行/25KB，超限报错倒逼精简） | system prompt 有界清单（`APP_LIST_MAX`） |
| durable-lesson 三观门（applicable/durable/legible） | 写入门（可适用/可持久/非敏感，拿不准不写） |
| 回复前即时写、不许拖 | launch 成功/用户纠正当下即写 |
| 召回子 agent（≤5、保守、按"问题关于什么"匹配） | resolver 内部查 KB，明细不进 context |
| 召回即存疑（对当前事实源核验） | 设备事实复核后才生效；冲突设备赢 |
| dream 四阶段（orient/gather/consolidate/prune）+ CLAUDE.md 对账 | dream 规则式整理（合并/对账/清理/重写视图） |
| `modified` 时间戳 + 相对日期绝对化 | 条目 first/last_seen + dream 绝对化 |
| 保留期豁免（memory 目录不被清扫） | `memory/` 仅本地、可手动清除 |
