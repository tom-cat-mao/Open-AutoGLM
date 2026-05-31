# Future Roadmap

> 本文档记录 LangGraph roadmap 的阶段进展与未来方向。详细执行约束以 `.trae/rules/graph.mdc` 为准。

---

## 当前状态

- **Phase 1-8**: ✅ 全部完成
- **测试**: 已恢复可执行 graph/actions/evals 回归测试与安装门禁；当前本地门禁为 `.venv/bin/pytest tests -q` 全绿
- **架构**: LangGraph Plan-Execute-Reflect StateGraph
- **图拓扑**: `plan → execute → [confirm|takeover|reflect|replan|end]`
- **结构化 API**: 已提供 `PhoneAgent.run_structured()` / `RunResult`，`run()` 继续保持字符串返回兼容
- **可观测性**: 已提供默认本地 JSONL trace，`RunResult` / eval JSON 可通过 `trace_id` 与 `trace_path` 关联 trace 文件；默认脱敏敏感截图、prompt/API key 与隐私文本
- **评测地基**: 已提供 `evals/run_eval.py --dry-run` smoke harness；当前统计结构化结果、HITL interrupt routing 与 trace 文件关联，不承诺跨进程 resume

---

## 已完成 MVP: 可观测性（本地 Trace 优先，LangFuse 可选）

**目标**: 让 Agent 的每一步行为可追踪、可回放。

**当前状态**: Phase 8 已落地默认本地 JSONL trace。`PhoneAgent.run_structured()` 返回 `trace_id` / `trace_path`，`evals/run_eval.py --dry-run` 输出的每条结果也包含可解析 trace 文件路径。

**为什么需要**: 长任务失败时需要定位具体 graph step；真实手机 GUI Agent 涉及隐私、支付、账号操作，必须能解释每一步为什么发生，并且默认不上传敏感数据。

**已落地方案**:

```
.traces/{trace_id}.jsonl
```

每行 JSON 包含：
- `run_id` / `trace_id`
- `step_id`
- `node`
- `event`
- `timestamp`
- `payload`（默认脱敏）

覆盖事件：
- `agent`: `run_start` / `run_end` / `run_error`
- `plan`: `plan_start` / `plan_result` / `plan_error`
- `execute`: `execute_result` / `execute_finish` / `confirm_interrupt` / `takeover_interrupt` / `execute_error`
- `reflect`: `reflect_start` / `reflect_result` / `reflect_error`
- `confirm` / `takeover`: interrupt 与 resume 结果事件

**可选增强方向**:

```python
# 可选：LangGraph / LangFuse callback 集成
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler(
    secret_key="...",
    public_key="...",
    host="https://cloud.langfuse.com"
)

result = graph.invoke(initial_state, config={
    "callbacks": [langfuse_handler],
    "configurable": {...}
})
```

**产出**:
- 默认本地 JSONL trace，关联 `RunResult.trace_id` / `RunResult.trace_path`
- Eval JSON 每条结果包含 `trace_id` / `trace_path`
- 每一步的动作、结果、reflection、HITL interrupt 可追踪；截图、prompt、API key、任务文本与隐私文本默认脱敏
- LangFuse Dashboard 作为可选增强，不作为本地运行和测试硬依赖

**验证命令**:

```bash
.venv/bin/pytest tests/graph tests/evals -q
.venv/bin/python evals/run_eval.py --dry-run --trace-dir .traces/smoke
```

---

## 已完成 MVP: 评估基准 (Evaluation Harness)

**目标**: 量化 Agent 的任务完成能力。

**当前状态**: Phase 7 已落地 MVP，且已在 Phase 8 接入 trace 关联：`RunResult` / `run_structured()` 提供结构化结果与 `trace_id` / `trace_path`，`evals/run_eval.py --dry-run` 可在无模型、无设备情况下输出稳定 JSON 指标与本地 JSONL trace 路径。

**为什么需要**: 当前没有任何量化指标。面试时无法回答"成功率多少？平均几步？"。

**已落地方案**:

```
evals/
├── tasks.json          # 标准任务集
└── run_eval.py         # 自动化评估脚本，支持 --dry-run
```

**tasks.json 示例**:

```json
[
  {
    "id": "wechat_send_msg",
    "task": "打开微信，给文件传输助手发一条消息：测试",
    "category": "social",
    "expected_apps": ["微信"],
    "max_steps": 15
  },
  {
    "id": "taobao_search",
    "task": "打开淘宝，搜索无线耳机",
    "category": "shopping",
    "expected_apps": ["淘宝"],
    "max_steps": 10
  }
]
```

**当前评估指标**:
- 成功率 (task completed / total)
- 平均步数
- 平均耗时
- 错误信息
- HITL interrupt routing 计数 (`hitl_count`)
- `trace_id` / `trace_path`，用于关联本地 JSONL trace

**暂未承诺**:
- Token 消耗统计
- 真实 AndroidWorld 规模 benchmark
- 跨进程 checkpoint/resume 成功率（Phase 10 后补）

**后续扩展复杂度**: 🟡 中等
- 接入真实设备状态检查器
- 保存历史结果与对比趋势
- 与 Phase 10 resume 指标打通

---

## P1: 策略级反思 (Strategic Reflection)

**目标**: 将当前二元反思（生效/不生效）升级为因果分析 + 策略切换。

**为什么需要**: 当前 reflect 只判断 continue/retry，Agent 失败后只会盲目重试。策略级反思让 Agent 分析失败原因并切换策略。

**当前状态**:

```python
# reflect_node 当前逻辑
action_succeeded = raw_action.startswith("continue")  # 二元判断
```

**目标状态**:

```python
# 升级后的 reflect prompt 要求模型输出
{
    "verdict": "succeeded" | "failed" | "partial",
    "cause": "element_not_found" | "app_not_responding" | "wrong_page" | "network_error" | ...,
    "strategy": "retry" | "retry_with_offset" | "go_back" | "swipe_to_find" | "restart_app" | "skip",
    "reasoning": "..."
}
```

**图拓扑变更**: 无需变更。只改 `reflect_node` 的 prompt 和解析逻辑。

**State 新增字段**:

```python
class AgentState(TypedDict):
    # ... 现有字段 ...
    failure_cause: Optional[str]      # 失败原因分类
    suggested_strategy: Optional[str] # 建议的恢复策略
```

**复杂度**: 🟡 中等
- 搭建: 0 天（不需要新基础设施）
- 实现: 2-3 天
- 调试: 1-2 天

---

## P2: 跨会话记忆 (Cross-Session Memory)

**目标**: Agent 能记住上次 run 做了什么、用户偏好、已知事实。

**为什么需要**: 当前每次 `agent.run()` 都是全新开始，Agent 没有任何记忆。跨会话记忆是 Agent 从"工具"变成"助手"的关键一步。

### 记忆分层架构

```
┌─────────────────────────────────────────────┐
│ Layer 1: 会话记忆（Session Memory）          │
│ 一次 run 内的 messages 历史                  │
│ 已实现：AgentState.messages                  │
│ 状态：✅ 已有                                │
├─────────────────────────────────────────────┤
│ Layer 2: 跨会话记忆（Cross-Session Memory）  │
│ 上次 run 做了什么、用户偏好、已知事实         │
│ 需要：LangGraph Checkpointer + Store         │
│ 状态：❌ 待实现                              │
├─────────────────────────────────────────────┤
│ Layer 3: 技能记忆（Skill/Procedural Memory） │
│ "打开微信发消息"的完整操作序列                │
│ 需要：轨迹存储 + 模式提取 + 检索             │
│ 状态：❌ 待实现（需足够重复任务数据）         │
└─────────────────────────────────────────────┘
```

### Layer 2 实现方案

**依赖**: LangGraph 原生 `Checkpointer` + `Store`

```python
# 1. SQLite Checkpointer — 持久化 State（支持暂停/恢复）
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("checkpoints.db")
graph = create_agent_graph().compile(checkpointer=checkpointer)

# 2. LangGraph Store — 持久化跨会话记忆
from langgraph.store.sqlite import SqliteStore

store = SqliteStore.from_conn_string("memory.db")
```

**记忆写入**（run 结束后）:

```python
# agent.py: PhoneAgent.run()
def run(self, task: str) -> str:
    config = {
        "configurable": {
            "model_client": self.model_client,
            "device_factory": device_factory,
            "system_prompt": self.agent_config.system_prompt,
            "verbose": self.agent_config.verbose,
            "user_id": "default",           # 新增
            "thread_id": str(uuid.uuid4()), # 新增：每次 run 唯一
        },
        "store": self._store,               # 新增：注入 Store
    }
    result = self._graph.invoke(initial_state, config)

    # 存储关键信息到长期记忆
    self._store.put(
        ("memories", "default", f"run_{thread_id}"),
        {
            "task": task,
            "result": result.get("action_result", {}).get("message", ""),
            "steps": result["step_count"],
            "timestamp": datetime.now().isoformat(),
        }
    )
    return ...
```

**记忆检索**（plan_node 中）:

```python
# graph/nodes/plan.py: plan_node()
def plan_node(state, config):
    store = config.get("store")
    if store:
        user_id = config["configurable"].get("user_id", "default")
        # 语义搜索相关记忆
        memories = store.search(
            ("memories", user_id),
            query=state["task"],
            limit=5,
        )
        if memories:
            memory_text = "\n".join([
                f"- {m.value['task']} ({m.value['steps']} steps)"
                for m in memories
            ])
            # 注入到 user message 中
            memory_context = f"\n\n** Previous related tasks **\n{memory_text}"
    # ... 正常推理 ...
```

**State 新增字段**:

```python
class AgentState(TypedDict):
    # ... 现有字段 ...
    retrieved_memories: Optional[list[dict]]  # 检索到的相关记忆
    memory_context: Optional[str]             # 格式化后的记忆文本
```

**文件变更**:

| 文件 | 变更 |
|------|------|
| `phone_agent/agent.py` | 新增 `_store` 属性，`run()` 中注入 Store + 写入记忆 |
| `phone_agent/graph/state.py` | 新增 `retrieved_memories`, `memory_context` 字段 |
| `phone_agent/graph/nodes/plan.py` | 检索记忆并注入 prompt |
| `phone_agent/graph/builder.py` | 编译时传入 checkpointer |
| `setup.py` | 在现有安装入口补充 checkpoint 相关依赖（如后续 Phase 需要） |

**复杂度**: 🟡 中等
- 搭建: 0.5 天
- 实现: 2-3 天
- 调试: 1-2 天

### Layer 3 实现方案（远期）

**前提**: 需要有足够的重复任务数据积累。如果每次任务都不同，技能记忆不会产生价值。

**技能存储结构**:

```sql
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT,                    -- "打开微信发消息"
    trigger_pattern TEXT,         -- "发微信|微信消息|wechat"
    embedding BLOB,               -- 用于语义匹配
    steps JSON,                   -- [{"action": "Launch", "app": "微信"}, ...]
    success_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    avg_steps REAL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**工作流程**:

```
任务输入 → embedding 匹配 → 命中 skill？
  ├─ 是 → 直接回放 steps（跳过推理）
  └─ 否 → 正常 Plan-Execute-Reflect → 成功后存入 skills
```

**复杂度**: 🔴 高
- 搭建: 1 天
- 实现: 3-4 天
- 调试: 2-3 天
- 前提: 需要足够的重复任务数据

---

## 不做: Dreaming（离线经验巩固）

**理由**: Dreaming 的价值正比于会话量。Anthropic 的客户跑了成千上万次会话才有显著提升。个人项目跑几十次，提取不出有统计意义的模式。且实现复杂度高（10+ 天），ROI 低。

---

## 不做: 多 Agent 架构（Supervisor + Specialists）

**理由**: 当前场景是单一的手机自动化，所有操作共享同一套工具（Tap/Swipe/Type/Launch）。拆成"微信专家""淘宝专家"是过度设计——它们用的工具完全一样。多 Agent 的价值在于异构任务分工，当前场景不需要。

---

## 不做: Command 动态路由

**理由**: 当前条件边（`after_execute` / `after_interrupt` / `should_continue`）已覆盖所有路由场景。路由逻辑是纯函数式的（检查 State 字段），不需要 Node 内部动态决定路由。Command 适用于模型自主决定路由的场景，当前是规则路由。

---

## 不做: Prompt 外部化

**理由**: Prompt 与模型输出格式（`do(action=...)`）强耦合，外部化（YAML/JSON）增加复杂度但无实际收益。当前没有多租户、A/B 测试需求。唯一值得做的是合并 `prompts_zh.py` 和 `prompts_en.py` 减少重复，但这是代码整洁而非架构升级。

---

## 优先级总览

| 优先级 | 功能 | 时间 | 简历价值 | 理由 |
|--------|------|------|----------|------|
| P0 | 本地 Trace + 可选 LangFuse | 2 天 | ⭐⭐⭐ | 先本地可追踪，再可选接 Dashboard |
| ✅ | 评估基准 MVP | 已完成 | ⭐⭐⭐⭐ | 已有结构化 API 与 dry-run smoke 指标，后续扩展真实 benchmark |
| P1 | 策略级反思 | 3.5 天 | ⭐⭐⭐⭐ | 不改架构，Agent 智能深度明显提升 |
| P2 | 跨会话记忆 | 4 天 | ⭐⭐⭐⭐ | LangGraph 原生能力，展示记忆设计 |
| P3 | 技能记忆 | 7 天 | ⭐⭐⭐⭐⭐ | 需要重复任务数据积累 |
| ❌ | Dreaming | 10+ 天 | — | 无足够会话数据 |
| ❌ | 多 Agent | 15+ 天 | — | 场景不需要 |
