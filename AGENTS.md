# Open-AutoGLM Agent Guide

## Core Loop (MUST READ)

```
Screenshot -> VLM inference (thinking + action) -> Parse action -> Execute on device -> Repeat
```

Every component exists to serve this loop.

## Global Constraints (MUST follow before ANY code change)

1. **Coordinate system**: Model outputs 0-1000 relative coordinates. Action handlers MUST convert to absolute pixels via `_convert_relative_to_absolute()`. Never pass raw model coordinates to device commands.
2. **Action parsing safety**: MUST use `ast.parse` + `ast.literal_eval`. NEVER use `eval()`. See `phone_agent/actions/handler.py:parse_action()`.
3. **Image context management**: After each step, images MUST be stripped from conversation history via `MessageBuilder.remove_images_from_message()`. This prevents token overflow.
4. **Security callbacks**: Sensitive operations (payment, privacy) MUST go through `confirmation_callback`. Login/captcha MUST go through `takeover_callback`. Both have console defaults but are overridable.
5. **Device abstraction**: All device operations go through `DeviceFactory` -> `phone_agent/adb/` module. Single platform, single code path.

## Architecture at a Glance

```
main.py                          # CLI entry: arg parsing, system checks, agent creation
phone_agent/
├── agent.py                     # PhoneAgent (Android), use_graph switch
├── device_factory.py            # DeviceFactory: loads adb module
├── model/
│   └── client.py                # ModelClient (OpenAI streaming), ModelConfig, MessageBuilder
├── actions/
│   └── handler.py               # ActionHandler + parse_action()
├── adb/                         # Android device control
├── config/
│   ├── apps.py
│   ├── prompts.py / prompts_zh.py / prompts_en.py
│   ├── i18n.py
│   └── timing.py
└── graph/                       # LangGraph Plan-Execute-Reflect (NEW)
    ├── state.py                 # AgentState TypedDict
    ├── builder.py               # create_agent_graph()
    ├── edges.py                 # Conditional edges
    ├── nodes/
    │   ├── plan.py              # plan_node
    │   ├── execute.py           # execute_node (uses dispatch_tool)
    │   ├── reflect.py           # reflect_node
    │   ├── confirm.py           # confirm_node (interrupt)
    │   └── takeover.py          # takeover_node (interrupt)
    └── tools/                   # @tool functions (Phase 3)
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
- **Actions**: `phone_agent/actions/handler.py`
- **Model**: `phone_agent/model/client.py:ModelClient`
- **Device**: `phone_agent/device_factory.py:DeviceFactory`
- **Prompts**: `phone_agent/config/prompts.py` (CN), `phone_agent/config/prompts_en.py` (EN)

## LangGraph Refactoring

**Goal**: Replace PhoneAgent while-loop with LangGraph StateGraph (Plan-Execute-Reflect)

**Current Phase**: Phase 3 — Tool Abstraction ✅ Completed (80/80 tests passing)

### Phase 1 Status: Completed
- AgentState, plan_node, execute_node, reflect_node implemented
- Conditional edges (after_execute, should_continue) working
- PhoneAgent.use_graph switch added
- Mock tests verify graph loop, skip-reflect, finish-routing (16 tests)

### Phase 2 Status: Completed
- `confirm_node` + `takeover_node` using `interrupt()` for resumable Human-in-the-Loop
- `execute_node` detects sensitive actions (`Tap` with `message`) and `Take_over`, routes to HITL nodes
- `after_execute` edge expanded with `confirm` / `takeover` routes
- `after_interrupt` edge routes to `reflect` or `END` based on user response
- Full graph topology: `plan → execute → [confirm|takeover|reflect|replan|end]`
- End-to-end tests: max_steps termination, plan error, skip_reflect, multi-loop, confirm/takeover flows (25 tests)

**Key New Files** (Phase 2): `phone_agent/graph/nodes/{confirm.py, takeover.py}`

**Backward Compat**: `PhoneAgent.run()` API unchanged; graph runs in parallel until full migration.

**Design Doc**: `.trae/rules/graph.mdc` — detailed State/Node/Edge/HITL spec, read before touching `phone_agent/graph/`.

### Phase 3 Status: Completed
- Each Action 封装为 `@tool` 装饰器函数（tap, type_text, swipe, back, home, launch, double_tap, long_press, wait, note, call_api, interact）
- `dispatch_tool()` 统一调度，替代 `ActionHandler.execute()`
- `convert_relative_to_absolute()` 提取为 `tools/coords.py` 独立 utility
- `execute_node` 通过 `use_tools` config 切换新旧路径，默认 `use_tools=True`
- `ActionHandler.execute()` 标记 deprecated，Phase 4 移除
- 坐标转换、dispatch 路由、全图集成、向后兼容测试（39 新增 tests）

**Key New Files** (Phase 3): `phone_agent/graph/tools/{__init__.py, coords.py, tap.py, type_text.py, swipe.py, navigation.py, launch.py, press.py, wait.py, misc.py}`

### Phase 4 (Planned): Cleanup
- 移除旧 `_execute_step` while 循环
- 移除旧 `ActionHandler` 类
- 更新 AGENTS.md 和 `.trae/rules`

## Version Management

- **Phase 完成即提交**：每个 Phase 完成后必须运行 `git commit`，message 格式：`feat(graph): <phase 目标>`
- **禁止 force push**：`main` 和 `feature/langgraph-refactor` 分支禁止 `git push --force`
- **Hooks 辅助**：`.trae/traecli.yaml` 已配置 `post_tool_use` hook，当 TaskUpdate 标记 completed 时追加 commit 提醒

## Compact Instructions

压缩时始终保留：
- 当前正在执行的任务描述和进度
- Global Constraints 中的 5 条不变量
- Architecture at a Glance 中的目录结构
- LangGraph Refactoring 段（当前阶段 + 图拓扑）
- Version Management 段（phase 完成状态）
