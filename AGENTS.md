# Open-AutoGLM Agent Guide

## Core Loop (MUST READ)

```
Screenshot -> VLM inference (thinking + action) -> Parse action -> Execute on device -> Reflect -> Repeat
```

Every component exists to serve this loop. The loop is implemented as a LangGraph StateGraph.

## Global Constraints (MUST follow before ANY code change)

1. **Coordinate system**: Model outputs 0-1000 relative coordinates. Tools MUST convert to absolute pixels via `convert_relative_to_absolute()` in `graph/tools/coords.py`. Never pass raw model coordinates to device commands.
2. **Action parsing safety**: MUST use `ast.parse` + `ast.literal_eval`. NEVER use `eval()`. See `phone_agent/actions/handler.py:parse_action()`.
3. **Image context management**: After each step, images MUST be stripped from conversation history via `MessageBuilder.remove_images_from_message()`. This prevents token overflow.
4. **Human-in-the-Loop**: Sensitive operations (payment, privacy) MUST go through `confirm_node` (interrupt). Login/captcha MUST go through `takeover_node` (interrupt). Both use LangGraph `interrupt()` for resumable pauses.
5. **Device abstraction**: All device operations go through `DeviceFactory` -> `phone_agent/adb/` module. Single platform, single code path.
6. **messages_reducer semantics**: `plan_node` returns only new messages (append mode); `execute_node` returns full rebuilt list (replace mode). Violating this causes message duplication and token explosion.
7. **Confirm-then-execute**: After confirm accepts a sensitive Tap, `after_interrupt` routes to `execute` (not `reflect`). The `pending_execute` branch in `execute_node` MUST NOT call `_strip_and_append` again and MUST set `action_confirmed=True`.

## Architecture at a Glance

```
main.py                          # CLI entry: arg parsing, system checks, agent creation
phone_agent/
├── agent.py                     # PhoneAgent — uses LangGraph StateGraph
├── device_factory.py            # DeviceFactory: loads adb module
├── model/
│   └── client.py                # ModelClient (OpenAI streaming), ModelConfig, MessageBuilder
├── actions/
│   └── handler.py               # ActionResult, parse_action(), do(), finish()
├── adb/                         # Android device control
├── config/
│   ├── apps.py
│   ├── prompts.py / prompts_zh.py / prompts_en.py
│   ├── i18n.py
│   └── timing.py
└── graph/                       # LangGraph Plan-Execute-Reflect
    ├── state.py                 # AgentState TypedDict
    ├── builder.py               # create_agent_graph()
    ├── edges.py                 # Conditional edges
    ├── nodes/
    │   ├── plan.py              # plan_node
    │   ├── execute.py           # execute_node (uses dispatch_tool)
    │   ├── reflect.py           # reflect_node
    │   ├── confirm.py           # confirm_node (interrupt)
    │   └── takeover.py          # takeover_node (interrupt)
    └── tools/                   # @tool functions
        ├── __init__.py          # dispatch_tool, get_tool_map, get_all_tools
        ├── coords.py            # convert_relative_to_absolute
        ├── tap.py
        ├── type_text.py
        ├── swipe.py
        ├── navigation.py        # back / home
        ├── launch.py
        ├── press.py             # double_tap / long_press
        ├── wait.py
        └── misc.py              # note / call_api / interact
```

## Quick Reference

- **Entry**: `main.py`
- **Agent**: `phone_agent/agent.py:PhoneAgent`
- **Graph**: `phone_agent/graph/builder.py:create_agent_graph()`
- **Tools**: `phone_agent/graph/tools/` — `dispatch_tool()` for action execution
- **Actions**: `phone_agent/actions/handler.py` — `parse_action()`, `ActionResult`, `do()`, `finish()`
- **Model**: `phone_agent/model/client.py:ModelClient`
- **Device**: `phone_agent/device_factory.py:DeviceFactory`
- **Prompts**: `phone_agent/config/prompts.py` (CN), `phone_agent/config/prompts_en.py` (EN)

## LangGraph Refactoring

**Goal**: Replace PhoneAgent while-loop with LangGraph StateGraph (Plan-Execute-Reflect) ✅ COMPLETED

**Current Phase**: Phase 5 — Bugfix ✅ Completed (89/89 tests passing, mypy zero errors)

### Graph Topology

```
START → plan → execute → [confirm|takeover|reflect|replan|end]
                         ├─ confirm → after_interrupt → [execute|reflect|end]
                         ├─ takeover → after_interrupt → [reflect|end]
                         ├─ reflect → should_continue → [replan|end]
                         ├─ replan → plan (skip reflect for Wait/Note/Call_API/Interact)
                         └─ end → END
```

### Phase 1 Status: Completed
- AgentState, plan_node, execute_node, reflect_node implemented
- Conditional edges (after_execute, should_continue) working
- Mock tests verify graph loop, skip-reflect, finish-routing (16 tests)

### Phase 2 Status: Completed
- `confirm_node` + `takeover_node` using `interrupt()` for resumable Human-in-the-Loop
- `execute_node` detects sensitive actions (`Tap` with `message`) and `Take_over`, routes to HITL nodes
- `after_execute` edge expanded with `confirm` / `takeover` routes
- `after_interrupt` edge routes to `reflect` or `END` based on user response
- End-to-end tests: max_steps termination, plan error, skip_reflect, multi-loop, confirm/takeover flows (25 tests)

### Phase 3 Status: Completed
- Each Action 封装为 `@tool` 装饰器函数（tap, type_text, swipe, back, home, launch, double_tap, long_press, wait, note, call_api, interact）
- `dispatch_tool()` 统一调度
- `convert_relative_to_absolute()` 提取为 `tools/coords.py` 独立 utility
- 坐标转换、dispatch 路由、全图集成测试（39 新增 tests）

### Phase 4 Status: Completed
- 移除旧 `_execute_step` while 循环和 `_run_loop` 方法
- 移除旧 `ActionHandler` 类（保留 `ActionResult`, `parse_action`, `do`, `finish`）
- 移除 `use_graph` 开关，`PhoneAgent` 默认使用 StateGraph
- 移除 `use_tools` 开关，`execute_node` 默认使用 `dispatch_tool`
- 移除 `confirmation_callback` / `takeover_callback` 参数，改用 `interrupt()` HITL 节点
- 更新文档和测试（79 tests passing）

## Version Management

- **Phase 完成即提交**：每个 Phase 完成后必须运行 `git commit`，message 格式：`feat(graph): <phase 目标>`
- **禁止 force push**：`main` 和 `feature/langgraph-refactor` 分支禁止 `git push --force`

## Compact Instructions

压缩时始终保留：
- 当前正在执行的任务描述和进度
- Global Constraints 中的 7 条不变量
- Architecture at a Glance 中的目录结构
- LangGraph Refactoring 段（当前阶段 + 图拓扑）
- Version Management 段（phase 完成状态）
