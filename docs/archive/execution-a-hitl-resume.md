# 执行文档 A：HITL 恢复（interrupt → 人工操作 → resume 续跑）

> 读者：pi 执行 agent。自包含任务书。
> 工作区：你所在目录是一个 git worktree（分支 wt/hitl-resume），基点 commit 17a7e25。
> 测试：`PYTHONPATH=<你的worktree绝对路径> /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 先验证导入：`PYTHONPATH=<worktree> /Users/bytedance/Open-AutoGLM/.venv/bin/python -c "import phone_agent; print(phone_agent.__file__)"` 必须打印 worktree 内路径。
> 禁止：git commit/push；禁止 FakeModel 式 mock 判断测试（只允许确定性单测与基础设施 stub）。

## 1. 问题

takeover/confirm 用 LangGraph `interrupt()` 暂停，但图没挂 checkpointer
（`phone_agent/graph/builder.py:95` 裸 `graph.compile()`），interrupt 只能以
GraphInterrupt 异常逃出；`phone_agent/agent.py:286-309` 捕获后直接打包终局
RunResult（注释自认 "no resume path"）。结果：人工接管=任务死亡。登录墙、
滑块额度等场景本该"人操作完→agent 从原地续跑"。

## 2. 代码事实（已核实，可直接依赖）

- `phone_agent/graph/nodes/takeover.py`、`confirm.py` **已是 resume-ready**：
  interrupt() 重入时返回 resume 值；confirm 已解析 Y/N→bool；无需改节点逻辑。
- `phone_agent/graph/edges.py:161 after_interrupt` 恢复路由已在（confirm 接受
  且 pending_execute→execute；否则→reflect 重新观察）。P0#5：先查 finished/error。
- state 按设计可序列化：runtime_goal_context 在 config 不在 state
  （`RuntimeGoalContext.__getstate__` 主动 raise TypeError）；goal_contract 在
  state 中只是 runtime reference；mark_registry 为 dict。
- `phone_agent/checkpoint/serde.py` RedactingSerializer：dumps 时脱敏，
  异常则传播（宁死不写明文）——包到 saver 的 serde 上即满足 P0#10。
- `phone_agent/agent.py:286` `self._graph.invoke(initial_state, config)`；
  `interrupt_payload()` 已能从 GraphInterrupt 提取 (message, type)。
- 批量评测语义：`evals/run_eval.py:161` 捕获 GraphInterrupt→终局 RunResult
  （failure_cause="takeover"）——**保持不变**（无人值守场景）。

## 3. 设计

1. **图挂 checkpointer**：`AgentConfig` 新增 `enable_hitl_resume: bool = False`；
   builder 在 flag 为真时 `graph.compile(checkpointer=MemorySaver())`
   （进程内 live 场景足够；不引入 SqliteSaver 依赖）。
2. **thread_id**：`_build_graph_config` 在 flag 为真时把 `configurable.thread_id`
   设为 trace_id（每次 run 唯一）。
3. **live 运行路径**（`agent.py` 新方法，如 `run_live(task, resume_input=input)`）：
   ```
   result = graph.invoke(state, config)
   loop:
     except GraphInterrupt as gi / 或检查 result 中的 __interrupt__:
       提取 (message, type)；trace run_interrupted；
       answer = resume_input(f"{message}\n完成后按回车继续（输入 n 终止）: ")
       若 'n' → 按现有语义返回终局 RunResult(failure_cause=interrupt_type)
       否则 → trace run_resumed；result = graph.invoke(Command(resume=answer), config)
   ```
   resume 语义：takeover→resume 值透传（节点忽略内容，清状态继续）；
   confirm→把 answer 字符串传给节点解析 Y/N。hitl_count 累计。
   先 rg .venv 内 langgraph 版本核实 interrupt/Command(resume) API 形态再写码。
4. **接线**：`.agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py`
   走 live 路径（交互式）；`evals/run_eval.py` 与 `agent.run_structured` 语义不动。
5. **trace**：新增 `run_interrupted`（已有）、`run_resumed` 事件（add-only）。

## 4. 步骤

1. 核实 .venv langgraph 版本与 interrupt/Command(resume) 行为（读包源码或写
   最小脚本验证：挂 MemorySaver 的图 interrupt 后 Command(resume=...) 可续）。
2. builder + AgentConfig + config thread_id。
3. run_live 循环 + run_diagnosis 接线。
4. RedactingSerializer 包到 MemorySaver 的 serde（若 MemorySaver 支持 serde 参数；
   不支持则文档注明原因并仅在 saver 选型处留接口）。
5. 测试（全部确定性单测/集成，禁止 mock 模型判断）：
   - 最小真实图（一个会 interrupt 的测试节点，无模型）：compile+MemorySaver →
     invoke 抛 GraphInterrupt → Command(resume) 后续跑完成，且 state 完整保留
   - run_live 循环：stub 一个**图对象**（基础设施 stub，非模型 mock）首次抛
     GraphInterrupt、resume 后返回终态 → 断言 RunResult 字段/hitl_count/trace 事件
   - run_structured 批量语义回归（interrupt→终局，不变）
   - state 经 RedactingSerializer dumps 后敏感键为 stub（若 serde 接入）
6. 全量测试绿后收尾，在本文档末尾写交接。

## 5. 硬性约束

- P0#4：两节点继续用 interrupt()；P0#5 边守卫；P0#10 checkpoint 落盘脱敏；
  P0#6 messages reducer 语义不动；trace add-only。
- 禁止 FakeModel/预设模型输出的判断测试；只允许确定性单测与基础设施 stub。
- 不 commit、不 push。用 rg 不用 grep/find。
- 完成标准：全量测试绿；run_live 交互续跑可用；批量语义不变；交接节写清
  变更文件、测试数变化、langgraph API 版本核实结论、遗留风险。

## 交接

### 完成情况

HITL 恢复闭环已实现：图挂上 checkpointer（`enable_hitl_resume=True` 时编译
`InMemorySaver`），confirm/takeover 的 `interrupt()` 不再"任务死亡"，`run_live`
循环可交互续跑；`run_diagnosis.py --live` 接线；批量 eval 语义不变。全量测试绿
（1204 passed）。

### 变更文件

| 文件 | 变更 |
|---|---|
| `phone_agent/graph/builder.py` | `create_agent_graph(checkpointer=None)` 透传到 `graph.compile()` |
| `phone_agent/agent.py` | `AgentConfig.enable_hitl_resume=False`；`__init__` 挂 `build_hitl_checkpointer()`；`_build_graph_config` 开启时写 `configurable.thread_id=trace_id`；新增 `run_live(task, resume_input=...)`（interrupt 循环 + `Command(resume=...)`）；新增 `extract_interrupt()`（读 `__interrupt__` marker）；trace 新增 `run_resumed` 事件（add-only） |
| `phone_agent/checkpoint/__init__.py` | 新增 `build_hitl_checkpointer()` saver 选型接口（纯 `InMemorySaver` + 未接 RedactingSerializer 的原因文档） |
| `.agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py` | 新增 `--live` 分支 + `run_live_agent()`（进程内 `run_live`，产出与 eval 同构的 result.json/summary.json，dry-run 冲突报错） |
| `.agents/skills/phone-agent-live-diagnosis/SKILL.md` | 增加 `--live` 用法示例 |
| `tests/agent/test_hitl_resume.py`（新增） | 18 个确定性测试（无模型 mock；仅基础设施 stub） |

环境说明：worktree 缺 gitignore 的 `models/` 目录导致既有用例
`test_observation_fallback_path_keeps_960_tier`（基线即挂）失败；已通过软链
`models -> /Users/bytedance/Open-AutoGLM/models` 修复，并在共享
`.git/info/exclude` 登记避免进入 git status。未改动任何既有测试。

### 测试数变化

起始 1186（1185 passed + 1 环境失败）→ 完成 1204 passed（+18，全为新增确定性
测试）。新增覆盖：最小真实图 interrupt→`Command(resume)` 续跑且 state 完整保留；
`run_live` marker 循环/resume 值透传/hitl_count 累计/`n` 终止/异常兜底/run_error；
`enable_hitl_resume` 默认值与 thread_id 配置；`run_structured` 批量语义回归
（flag 开关均不变）；serde 不兼容性根因固定；`run_diagnosis --live` 接线。

### langgraph interrupt/Command API 版本核实结论

- 版本：`langgraph 1.2.2`（`.venv`，`langgraph.checkpoint.memory.InMemorySaver`）。
- `interrupt()` 在**有 checkpointer** 的图上不再抛 `GraphInterrupt`：`invoke`
  返回 `{"__interrupt__": [Interrupt(value=..., id=...), ...]}` marker；
  续跑用 `graph.invoke(Command(resume=<value>), config)`（同一 `thread_id`），
  节点从 `interrupt()` 处重入并收到 resume 值。已用最小脚本 + 真实图双重验证。
- 无 checkpointer 时同样返回 marker（不抛异常）——`run_structured`/`run_eval.py`
  里捕获 `GraphInterrupt` 的路径在 1.2.2 下是防御性死代码（既有测试通过 stub
  graph 直接抛异常来测该路径，语义不受影响）。
- `InMemorySaver(serde=...)` 支持自定义 serde；但 **RedactingSerializer 不能作为
  其 serde**（见遗留风险 1），故未接入。
- 中断值可含 `id`（resume 可映射到指定 interrupt）；当前单中断场景
  `Command(resume=单值)` 即满足。

### 遗留风险

1. **RedactingSerializer 未接入 MemorySaver（有意）**：步骤 4 的 serde 接入经
   实测不可行——(a) checkpoint 信封的 `channel_versions` 字符串在 egress 被
   stub 成 `{redacted,length}`，恢复时 `InMemorySaver._load_blobs` 用其做
   blob key 报 `TypeError: unhashable type: 'dict'`（已用最小脚本复现）；(b)
   即使修好信封，stub 策略会把 `task`/`messages`/`action_raw` 等非白名单 state
   通道整体替换为 stub，resume 后任务与对话历史尽失，"从原地续跑"破功。P0#10
   不受影响：MemorySaver 进程内、永不落盘，state 本就只含脱敏 observation，
   `screenshot_b64` 恒为 None；持久化 sqlite 场景需在 `build_hitl_checkpointer`
   处重新引入 resume-safe 的 egress 策略。
2. **批量语义保持现状**：任务书要求 `run_structured`/`run_eval` 语义不动，故未
   给 marker 分支加终局转换。1.2.2 下真实图上 interrupt 在批量路径返回的
   `__interrupt__` marker 会走 `_state_to_run_result`（success=False、
   finished=False），与旧版"抛异常→failure_cause=takeover"的归因不同——这是
   基线分支在 1.2.2 下的既有差异，本任务不改（测试仍走 stub 异常路径，语义
   断言不变）。
3. **goal_contract 运行时引用**：不接 serde 时 state 的
   `goal_contract_state_metadata_v1`（runtime_reference）经默认 JsonPlusSerializer
   往返完好，resume 后 `ensure_goal_contract` 能从 config 内存活的
   `RuntimeGoalContext` 重新解析（已验证）。若未来接上 egress 塌缩策略，该引用
   会退化为 summary，需按 `goal_resume` 的 trusted projection 机制处理。
4. **checkpoint 落盘失败面**：运行中途 state 若出现 msgpack 不可序列化对象，
   `put` 会抛错并经 run_live 的 run_error 路径 fail-closed；当前所有 state 通道
   均为 JSON 安全类型，未见触发路径。
5. **run_diagnosis --live**：需要真实设备/模型，`--dry-run` 不兼容（返回码 2）。
   `reset_app_on_device` 仍会执行 `adb shell pm clear`（与既有路径一致）。
