# TaskDoc 实施规格（thin-loop v2 增量一）

> 主文档：`docs/refactor-thin-loop-v2.md`。本文件是 TaskDoc（任务板）增量的唯一实施契约。
> 设计结论（讨论已定稿）：goal 与 plan 是**同一文档的两个段落**，不做两套。
> 形态 = state（session 持有）+ tool 写入（模型唯一写入者）+ before_model 渲染钩子（pinned、压缩免疫）。

## 1. 文档结构与语义

```
## 目标
  base: <用户任务原文>            # run 启动时 harness 播种；模型写入时改动 base 一律拒绝
  amendments: [append-only]       # 模型的理解细化/用户中途补充；只追加，不改不删
## 路线
  items: [{id, content, status, reason?}]
  status ∈ pending | in_progress | completed | blocked
  约束: 至多一个 in_progress；items ≤ 15；blocked 必须带 reason
## 关键事实
  facts: [str]                    # ≤ 10 条；每条 ≤ 120 字符；模型随手记（价格/已选值/坑）
```

- **里程碑粒度**：工具描述写明 3-7 项起步、边走边细化；不强制。
- **先观察后规划**：`session.screen_seq == 0`（尚无观测）时调用 update_task_doc → 接受写入但返回提示文本"建议先 read_screen 再规划"。

## 2. 模块契约

### 2.1 `phone_agent/v2/taskdoc.py`（W1）

```python
@dataclass
class TaskItem: id: str; content: str; status: str = "pending"; reason: str | None = None

@dataclass
class TaskDoc:
    goal_base: str = ""
    amendments: list[str] = field(default_factory=list)
    items: list[TaskItem] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)

    def validate(self) -> str | None: ...
        # 返回 None 或错误文本（多 in_progress / 超 15 items / blocked 无 reason / facts 超限）
    def has_open_items(self) -> bool: ...       # 存在 pending 或 in_progress
    def open_items_summary(self) -> str: ...    # finish 拒绝消息用
    def render(self, lang: str = "cn") -> str: ...
        # 渲染为 pinned block 文本；空文档返回 ""。facts/amendments 省略空段。
```

### 2.2 `phone_agent/v2/tools/taskdoc.py`（W1）

```python
def make_update_task_doc_tool(session, lang: str):
    @tool("update_task_doc")
    def update_task_doc(items: list[dict] | None = None,
                        add_amendments: list[str] | None = None,
                        facts: list[str] | None = None) -> str:
        """工具描述（写入 docstring）：维护任务板——目标/路线/关键事实。
        多步骤任务建议在第一次观察后创建；里程碑 3-7 项起步边走边细化；
        至多一个 in_progress；完成即时标记；blocked 带原因；item 不删除只转状态。"""
```

- 语义：`items` 给定则全量替换；`add_amendments` 追加；`facts` 给定则全量替换（guard 超限）。
- 不接受 goal_base 参数（base 只能 harness 播种）；validate 失败 → 返回错误文本，不写入。
- 成功返回确认 + 当前文档渲染；screen_seq==0 时附加"先观察"提示。
- `tools/__init__.py`（W1 负责）将其注册进 build_tools。

### 2.3 `phone_agent/v2/middleware/taskdoc.py`（W2）

```python
class TaskDocMiddleware(AgentMiddleware):
    """before_model: 注入 TaskDoc 渲染 block + 停滞轻推。"""
```

- 渲染：session.task_doc 非空 → 在 messages 尾部追加 system 消息 `"[TASK_DOC]\n" + doc.render(lang)`；空文档不注入。
- **停滞轻推**（带内提示，每 run 至多一次）：session 需新增轨迹跟踪（W2 在 session.py 追加 `seen_states: set[tuple[str,str]]` 并在 observe() 里记录 `(current_app, screen_hash)`；screen_hash 用截图字节 hash）。连续 `config.taskdoc_nudge_steps`（默认 5）次模型调用无新 state 且 task_doc.has_open_items() → 在渲染块后追加提示："最近 N 步无新状态。可选：update_task_doc 修正路线 / locate / ask_user / take_over / finish（若已完成）"。注入后置 `session.nudged=True`。
- 提示只陈述观测与可选空间，不给指令（设计原则：非指导性）。

### 2.4 finish 守卫（W2，改 `tools/control.py`）

- finish 现有 evidence 校验保留；新增：`session.task_doc.has_open_items()` → 拒绝并返回
  `"路线仍有未完成项：{open_items_summary}。请先完成、标记 blocked（带原因），或用 update_task_doc 修正路线后再 finish。"`

### 2.5 装配（W2，改 `agent.py` / `session.py` / `config.py`）

- `V2Config` 新增：`taskdoc_nudge_steps: int`（`PHONE_AGENT_TASKDOC_NUDGE_STEPS`，默认 5）、
  `taskdoc_enabled: bool`（`PHONE_AGENT_TASKDOC`，默认 true，off 时不注册工具不渲染）。
- `ThinPhoneAgent.__init__`：`session.task_doc = TaskDoc(goal_base=task)` 在 run() 开始播种；
  middleware 栈加入 TaskDocMiddleware（taskdoc_enabled 时）。
- session.py 追加 seen_states/nudged 字段与 observe() 记录（W2 直接改 W-core 文件，注意最小 diff）。

## 3. 测试（全 fake）

### W1 `tests/v2/test_taskdoc.py`
- validate：多 in_progress / 超 15 / blocked 无 reason / facts 超限 → 各返回错误不写入
- 工具：items 全量替换；amendments 追加不重复改写；facts 替换；screen_seq==0 提示；render 含三段
- finish 无关路径回归（build_tools 含 update_task_doc）

### W2 `tests/v2/test_taskdoc_integration.py`
- 渲染钩子：非空 doc → messages 尾部含 [TASK_DOC]；空 doc 不注入
- 停滞提示：构造 session seen_states 不变 5 次 + open items → 出现提示且只出现一次
- finish 守卫：open items → finish 被拒；全 completed → 放行（fake session）
- 播种：run() 启动后 session.task_doc.goal_base == task（用现有 fake agent loop 风格）

## 4. 文档（W2）

- `docs/refactor-thin-loop-v2-taskdoc.md` 已存在（本文件）无需改。
- `AGENTS.md` P0 表追加一条：TaskDoc base 段只有 harness 播种、模型唯一经 update_task_doc 写入、渲染 pinned 压缩免疫。
- `docs/future-roadmap.md` v2 章节标记 TaskDoc 已落地。

## 5. 验收（我逐项核验）

1. `tests/v2` + `tests/grounding` 全绿
2. 15 个工具（14 + update_task_doc）注册成功
3. finish 守卫 / 渲染 / 停滞提示 / 播种 行为符合本规格
4. 配置键 taskdoc_* 三级解析生效
