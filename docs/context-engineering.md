# Context Engineering 架构详解

> 本文档全面描述 Open-AutoGLM 的 Context Engineering 系统——从核心循环中的数据流、三种上下文模式、Consumer-Aware 分层脱敏、Context Block 构建流水线，到请求压缩、Prompt 组装、外部边界脱敏和关键不变式。
>
> 配套可视化架构图：[context-engineering-architecture.html](./context-engineering-architecture.html)

---

## 目录

1. [系统总览](#1-系统总览)
2. [核心循环中的 Context 生命周期](#2-核心循环中的-context-生命周期)
3. [三种 Context 模式](#3-三种-context-模式)
4. [Consumer-Aware 分层脱敏架构](#4-consumer-aware-分层脱敏架构)
5. [Context Block 构建流水线](#5-context-block-构建流水线)
6. [请求压缩机制](#6-请求压缩机制)
7. [Prompt 组装与注入](#7-prompt-组装与注入)
8. [外部边界脱敏](#8-外部边界脱敏)
9. [AgentState Context 字段详解](#9-agentstate-context-字段详解)
10. [模块依赖关系](#10-模块依赖关系)
11. [关键不变式](#11-关键不变式)
12. [端到端数据流](#12-端到端数据流)
13. [配置与默认值](#13-配置与默认值)

---

## 1. 系统总览

Context Engineering 系统是一个多层管道，负责在 LLM 规划循环中收集、脱敏、预算裁剪和注入短期上下文。它横跨三个核心关注点：

- **上下文收集**（reflect 节点写入 state）
- **上下文选择与脱敏**（context.py 选择/脱敏 section）
- **上下文注入与压缩**（plan 节点注入 block，压缩消息）

数据流总览：

```
reflect_node → 更新 state (screen_belief, failure_memory, gui_memory, ...)
    ↓
plan_node → 读取 state → select_plan_context → build_plan_context_block → compact_messages_for_request → LLM request
    ↓
execute_node → 更新 action_outcome_summary + trace 脱敏
    ↓
reflect_node → 循环
```

核心代码入口：`phone_agent/graph/context.py`（916 行），包含所有 context 相关的函数、常量、策略和消费者模型。

---

## 2. 核心循环中的 Context 生命周期

### 2.1 Plan Node（上下文消费者）

**文件**：`phone_agent/graph/nodes/plan.py`

Plan 节点是 context 系统的主要消费者，负责：

1. **读取上下文模式**：`get_context_mode(state, config)` — 从 graph config → state → 默认值 依次读取
2. **验证 Prompt 版本**：`get_prompt_version(configurable.get("prompt_version"))` — 当前仅支持 `context_harness_v1`
3. **选择上下文 section**：`select_plan_context(state, mode=context_mode, lang=lang, prompt_version=prompt_version)` — 根据模式选择有效 section，inject 模式下构建 context block
4. **提取 context block**：`context_block = context_selection.context_block`
5. **注入到 user message**：将 context_block 追加到 marks_block 之后
6. **请求压缩**：`compact_messages_for_request(full_messages, context_selection)` — 压缩传给 LLM 的消息副本
7. **记录 metrics**：`context_metrics = context_selection.metrics()`

### 2.2 Execute Node（上下文辅助写入）

**文件**：`phone_agent/graph/nodes/execute.py`

Execute 节点在上下文系统中的角色：

- 读取 `get_context_mode(state, config)` 判断上下文是否启用
- 当 `context_enabled(mode)` 为 True 时，通过 `_context_update()` 构建 `action_outcome_summary`
- 使用 `sanitize_context_payload(consumer="trace_payload")` 脱敏手势 trace
- 使用 `MessageBuilder.remove_images_from_message()` 剥离历史图片
- 重建完整 messages 列表（replace mode，通过 `messages_reducer`）

### 2.3 Reflect Node（上下文核心写入者）

**文件**：`phone_agent/graph/nodes/reflect.py`

Reflect 节点是 context 状态的核心写入者。当 `context_enabled(context_mode)` 为 True 时：

1. **构建屏幕信念**：`build_screen_belief()` — 生成 regex 脱敏的 screen belief
2. **构建动作结果摘要**：`build_action_outcome_summary()` — 从 state 提取动作执行结果
3. **检测重复失败**：`detect_repeated_failure()` — 按 action/cause/app 三元组检测
4. **更新失败记忆**：`update_failure_memory()` — 追加失败记录，保持有界窗口
5. **更新压缩历史**：`update_summarized_history()` — 追加一行历史，enforce budget
6. **写入短期记忆**：`short_term_memory` 字典 — 包含 screen_belief、last_action_outcome、latest_failures、grounding_observation
7. **更新动作账本**：`action_ledger` — 有界到最近 10 条
8. **更新 GUI 记忆**：`update_gui_memory()` — 维护 visited_screens、tried_actions、scroll_memory、task_progress
9. **脱敏反思 prompt**：`sanitize_context_payload(consumer="reflect_prompt", task_context=task)` — 对 action_parsed 和 action_result 进行 regex 脱敏

---

## 3. 三种 Context 模式

系统支持三种上下文模式，通过 `context_mode` 字段控制：

| 模式 | 标签 | 收集 | 构建 Block | 注入 Block | 策略名 |
|------|------|------|-----------|-----------|--------|
| **Off** | `context_mode="off"` | 否 | 否 | 否 | `"off"` |
| **Observe** | `context_mode="observe"` | 是 | 否 | 否 | `"observe_only"` |
| **Inject**（默认） | `context_mode="inject"` | 是 | 是 | 是 | `"inject_redacted_block"` |

### 3.1 Off 模式

- 不收集任何上下文
- `select_plan_context()` 返回空 section 列表
- 不构建 context_block
- reflect 节点不更新 context state
- 适用于纯零样本场景

### 3.2 Observe 模式（默认）

- 收集并记录 section IDs（用于 trace/eval 可观测性）
- 更新 screen_belief / failure_memory / history 等状态字段
- **不**构建 context_block
- **不**注入到 LLM prompt
- 策略标记为 `observe_only`
- 用途：在不影响 LLM 推理的前提下，收集上下文指标用于评估和调试

### 3.3 Inject 模式

- 完整管道：收集 + 构建 + 注入
- `build_plan_context_block()` 生成有界 JSON block
- 注入到 plan node 的 user message
- 策略标记为 `inject_redacted_block`
- 所有字符串经 regex 脱敏
- context block 标题明确标注"仅为信念，不代表授权"

### 3.4 模式解析

模式通过 `get_context_mode(state, config)` 解析，优先级：

1. `config["configurable"]["context_mode"]`（graph config）
2. `state["context_mode"]`（state 字段）
3. `DEFAULT_CONTEXT_MODE`（默认 `"inject"`）

`normalize_context_mode()` 负责归一化：空值回退默认，非法值回退默认。

---

## 4. Consumer-Aware 分层脱敏架构

### 4.1 核心原则

**State 写入路径只做 regex 替换，不做 stub**；stub 策略仅在 checkpoint egress 由 `RedactingSerializer` 触发。

这意味着：
- 运行时内存中的 state 保持可读文本（regex 脱敏后）
- 只有写入 checkpoint 时才对敏感键做 stub 替换
- 不同消费者看到不同粒度的脱敏结果
- `expected_outcome` 是 verifier 运行态合同：state 中只保留 hash/哨兵结构，不保留 provider 自由文本；verifier 对当轮 UI 文本做现场 hash/片段 hash 匹配。`action_raw`、trace、report、checkpoint 同样使用 stub/hash summary，避免原文进入外发或持久化路径。

### 4.2 Consumer 策略表

| Consumer | 策略 | 调用位置 | 脱敏行为 | task_context 支持 |
|----------|------|---------|---------|------------------|
| `"inject"` | **regex** | `build_plan_context_block()`, `observation.py` | 所有字符串 regex 替换敏感模式为 `<redacted>`；不做 key-level stub | 是 — 匹配 task 敏感值替换为 `<matches_task_value>` |
| `"reflect_prompt"` | **regex** | `reflect_node` 构建 action_str / result_str | 同 inject；regex 替换 | 是 — 同上 |
| `"trace_payload"` | **regex** | `execute_node` 手势 trace 脱敏 | regex 替换；不做 stub | 否 |
| `"default"` | **regex** | 未指定 consumer 时的默认 | regex 替换 | 否 |
| `"checkpoint"` | **stub** | `RedactingSerializer` 在 checkpoint egress | `PRIVATE_CONTEXT_TEXT_KEYS` 替换为 `{redacted, length, sha256}` stub；其余 regex | 否 |

### 4.3 sanitize_context_payload() — 核心脱敏调度器

**签名**：

```python
def sanitize_context_payload(
    payload: Any,
    key: str | None = None,
    *,
    inject: bool | None = None,      # 已废弃的向后兼容参数
    consumer: str | None = None,      # 推荐的 consumer 参数
    task_context: str | None = None,  # 任务文本，用于 task-aware 脱敏
) -> Any
```

**工作流程**：

1. 通过 `_resolve_consumer()` 将 `(consumer, inject)` 映射为规范 consumer tag
2. 从 `CONSUMER_POLICY` 查找策略（`"regex"` 或 `"stub"`）
3. 如果 consumer 是 `"inject"` 或 `"reflect_prompt"` 且提供了 `task_context`，提取任务敏感值
4. 调用 `_sanitize_payload_impl()` 递归处理

**向后兼容**：`inject: bool` 是已废弃的别名：
- `inject=True` ≡ `consumer="inject"`
- `inject=False` ≡ `consumer="checkpoint"`
- 两者同时指定时 `consumer` 优先

### 4.4 _sanitize_payload_impl() — 递归脱敏实现

```python
def _sanitize_payload_impl(
    payload: Any,
    key: str | None,
    *,
    policy: str,                          # "regex" 或 "stub"
    task_values: tuple[str, ...] = (),    # 从 task 提取的敏感值
) -> Any
```

处理逻辑：

- **字符串**：
  - `policy == "stub"` 且 key 在 `PRIVATE_CONTEXT_TEXT_KEYS` → 返回 `{redacted, length, sha256}` stub
  - `policy == "stub"` 且 key 不在 `SAFE_CONTEXT_TEXT_KEYS` → 同样返回 stub（未知键一律 stub）
  - 否则：先 `_mark_task_matches()`（如有 task_values），再 `redact_context_text()` regex 替换
- **字典**：递归处理每个 value，key 传递给下一层
- **列表/元组**：递归处理每个元素

### 4.5 Regex 内联替换 — redact_context_text()

使用 `SENSITIVE_PATTERN` 正则匹配并替换为 `<redacted>`，保留周围文本结构：

| 模式 | 正则 | 示例 |
|------|------|------|
| 手机号 | `1[3-9]\d{9}` | 13812345678 → `<redacted>` |
| 邮箱 | `[\w.+-]+@[\w-]+(?:\.[\w-]+)+` | user@example.com → `<redacted>` |
| 订单号 | `(?:订单\|order)[\s:#：-]*[A-Za-z0-9-]{4,}` | 订单号: ABC-1234 → `<redacted>` |
| 验证码 | `(?:验证码\|code)[\s:#：-]*\d{4,8}` | 验证码: 123456 → `<redacted>` |
| API Key | `(?:api[_-]?key\|token\|secret)[\s:=：]+[A-Za-z0-9._-]+` | api_key=sk-abc → `<redacted>` |
| OpenAI Key | `sk-[A-Za-z0-9._-]+` | sk-xxx → `<redacted>` |
| Bearer Token | `Bearer\s+[A-Za-z0-9._-]+` | Bearer xxx → `<redacted>` |
| JWT | `eyJ[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+` | eyJhbG... → `<redacted>` |
| Base64 | `[A-Za-z0-9+/]{120,}={0,2}` | 长 base64 字符串 → `<redacted>` |

### 4.6 Key-Level Stub — 仅 Checkpoint Consumer

当 `consumer="checkpoint"` 时，`PRIVATE_CONTEXT_TEXT_KEYS` 中的键值被替换为摘要 stub：

```json
{ "redacted": true, "length": 42, "sha256": "a1b2c3d4e5f6" }
```

**PRIVATE_CONTEXT_TEXT_KEYS**（21 个键）：

```
visible_text, observed_text, raw_text, chat_content, text, label, value,
title, subtitle, address, captcha, verification_code, account, payment_info,
message, text_hint, target_text_hint, result_message_summary, final_message,
error, reflection
```

**SAFE_CONTEXT_TEXT_KEYS**（17 个键，在 stub 策略下保留为 regex 脱敏）：

```
summary, current_app, confidence, action, action_metadata, reflection_verdict,
failure_cause, suggested_strategy, summarized_history, screen_id, mark_id,
provider, provider_input_hash, raw_screenshot_hash, failure_code, last_verdict, sha256
```

### 4.7 Task-Aware 脱敏 — `<matches_task_value>`

当 `consumer="inject"` 或 `consumer="reflect_prompt"` 且提供 `task_context` 时：

1. `_task_sensitive_values(task_context)` 从任务文本中提取所有匹配 `SENSITIVE_PATTERN` 的值
2. `_mark_task_matches(text, task_values)` 在 payload 中将匹配值替换为 `<matches_task_value>`
3. 然后再做 regex 脱敏

这样 VLM 知道某个值来自任务本身（如用户要求搜索的手机号），而非屏幕泄露。

### 4.8 向后兼容别名

- `sanitize_context_text_regex` = `redact_context_text`（函数别名）
- `inject: bool` 参数映射到 consumer（`True` → `"inject"`，`False` → `"checkpoint"`）
- `SAFE_CONTEXT_TEXT_KEYS` 保留用于回答"此 key 在 inject=False 时是否存活"的问题

---

## 5. Context Block 构建流水线

### 5.1 Step 1: Section 选择 — select_plan_context()

**签名**：

```python
def select_plan_context(
    state: dict[str, Any],
    *,
    mode: str,
    lang: str = "cn",
    prompt_version: str | None = None,
) -> ContextSelectionResult
```

遍历 `CONTEXT_SECTION_IDS`（11 个 section），调用 `_section_has_value()` 判断每个 section 是否有信息量：

**11 个 Section ID**：

| Section ID | 判断有值的逻辑 |
|-----------|--------------|
| `screen_belief` | summary 非 "unknown" 或 confidence 非 "unknown" 或 loading/unsafe 标记 |
| `last_action_outcome` | action/success/result_message/verdict/cause/strategy 任一非空 |
| `failure_memory` | 列表非空 |
| `summarized_history` | 字符串非空 |
| `short_term_memory` | 字典非空 |
| `action_ledger` | 列表非空 |
| `gui_memory.visited_screens` | 列表非空 |
| `gui_memory.tried_actions` | 列表非空 |
| `gui_memory.scroll_memory` | 字典非空 |
| `gui_memory.task_progress` | 字典非空 |
| `grounding_observation` | 字典非空 |

**模式行为**：

- `off`：返回空 section 列表，strategy="off"
- `observe`：返回 section IDs（用于 trace metrics），不构建 block，strategy="observe_only"
- `inject`：返回 section IDs + 调用 `build_plan_context_block()` 构建 block，strategy="inject_redacted_block"

**关键约束**：`select_plan_context()` 不修改 state，只产出 section IDs、context block 与计数指标。

### 5.2 Step 2: Block 构建 — build_plan_context_block()

**签名**：

```python
def build_plan_context_block(
    state: dict[str, Any],
    lang: str = "cn",
    *,
    consumer: ContextConsumer = "inject",
) -> tuple[str, dict[str, Any]]
```

从 raw state 字段直接读取，组装 6 个组件：

#### 组件 1: screen_belief

```python
{
    "current_app": sanitize_context_payload(current_app, "current_app", consumer=consumer, task_context=task_context),
    "summary": sanitize_context_payload(summary_text, "summary", consumer=consumer, task_context=task_context),
    "loading_or_blocked": bool(screen_belief.get("loading_or_blocked")),
    "unsafe_or_sensitive": bool(screen_belief.get("unsafe_or_sensitive")),
    "confidence": str(screen_belief.get("confidence") or "unknown"),
}
```

- summary 来源：优先取 `reflection`，其次取 `screen_belief.summary`，最后 "unknown"
- summary 先按 `screen_belief_summary_chars`（默认 300）裁剪，再 regex 脱敏

#### 组件 2: last_action_outcome

```python
{
    "step_count": int,
    "action": sanitize_context_payload(raw_action, "action", ...),
    "execution_success": bool | None,
    "result_message": sanitize_context_payload(raw_message, "message", ...),
    "reflection_verdict": str | None,
    "failure_cause": str | None,
    "suggested_strategy": str | None,
}
```

#### 组件 3: latest_failure_memory

仅取 `failure_memory` 的**最后 1 条**（注意：failure_memory 本身保持最近 3 条，但 block 只取最新 1 条以节省 token）：

```python
[{
    "step_count": int,
    "action": str,
    "current_app": sanitize_context_payload(...),
    "failure_cause": str,
    "suggested_strategy": str,
}]
```

#### 组件 4: summarized_history

- 从 state 读取 `summarized_history` 字符串
- 先检查是否超过 `summarized_history_chars`（默认 800）预算
- 经 `sanitize_context_payload()` regex 脱敏
- 脱敏后再次检查长度，超限则 `trim_text()` 裁剪

#### 组件 5: gui_memory

通过 `_sanitize_gui_memory_for_block()` 处理，包含四个子字段：

- **visited_screens**：最近 10 条，每条含 screen_id、current_app（regex 脱敏）、step_count
- **tried_actions**：最近 10 条，每条含 step_count、screen_id、action、mark_id、result_success、failure_cause（regex 脱敏）
- **scroll_memory**：每个 screen_id 记录 last_direction 和 count
- **task_progress**：last_verdict 和 suggested_strategy（regex 脱敏）

#### 组件 6: grounding_observation

直接经 `sanitize_context_payload()` 脱敏。

### 5.3 信息量过滤 — _context_block_value_is_informative()

每个组件在进入 block 前经过过滤：

- **screen_belief**：summary 非 "unknown" 或 confidence 非 "unknown" 或有 loading/unsafe 标记
- **last_action_outcome**：action/success/result_message/verdict/cause/strategy 任一非空非 "unknown"
- **gui_memory**：visited_screens/tried_actions/scroll_memory/task_progress 任一非空
- **其他**：值非空即通过

### 5.4 Step 3: Budget 裁剪 — trim_text()

按 `DEFAULT_CONTEXT_BUDGET` 逐组件裁剪：

| 预算项 | 默认值 | 说明 |
|--------|--------|------|
| `screen_belief_summary_chars` | 300 | 屏幕信念摘要字符上限 |
| `summarized_history_chars` | 800 | 压缩历史字符上限 |
| `failure_memory_items` | 3 | 失败记忆条数上限（block 内取最近 1 条） |
| `action_outcome_items` | 1 | 动作结果条数上限 |
| `context_block_chars` | 1500 | 整体 block 字符上限 |
| `request_recent_messages` | 6 | 请求消息条数上限 |

超限截断逻辑：

```python
def trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[: max(0, max_chars - 20)] + "...<truncated>", True
```

截断时标记 `context_truncated=True`。

### 5.5 Block 格式

最终 block 格式：

```
** 短期上下文（仅为信念，不代表授权） **
screen_belief: {"current_app":"...","summary":"...","loading_or_blocked":false,...}
last_action_outcome: {"step_count":3,"action":"tap",...}
latest_failure_memory: [...]
summarized_history: "step=1 action=tap success=True..."
gui_memory: {"visited_screens":[...],"tried_actions":[...],...}
grounding_observation: {...}
```

英文模式下标题为 `** Short-term Context (belief, not authorization) **`。

---

## 6. 请求压缩机制

### 6.1 compact_messages_for_request()

**签名**：

```python
def compact_messages_for_request(
    messages: list[dict[str, Any]],
    selection: ContextSelectionResult,
) -> tuple[list[dict[str, Any]], ContextSelectionResult]
```

**关键约束**：只压缩传给 `model_client.request()` 的消息副本，**不修改** `state["messages"]`。

### 6.2 压缩步骤

1. **计算 before 指标**：`_messages_approx_chars(messages)` 统计压缩前字符数
2. **剥离历史图片**：对所有消息调用 `_compact_message(message, keep_images=False)`，移除 `image_url` 类型的 content
3. **保留最新截图**：找到最新 user message 的索引，对该消息调用 `_compact_message(message, keep_images=True)`
4. **裁剪消息条数**：`_bound_request_messages()` 保留 system 消息（最多 1 条）+ 最近 `request_recent_messages`（默认 6）条
5. **计算 after 指标**：统计压缩后字符数和近似 token 数
6. **返回**：压缩后的消息列表 + 更新后的 `ContextSelectionResult`

### 6.3 消息条数裁剪细节

```python
def _bound_request_messages(messages):
    max_recent = DEFAULT_CONTEXT_BUDGET["request_recent_messages"]  # 6
    if len(messages) <= max_recent:
        return messages
    system_messages = [m for m in messages if m.get("role") == "system"][:1]
    tail = messages[-max_recent:]
    # 合并 system + tail，去重
    bounded = []
    for m in system_messages + tail:
        if m not in bounded:
            bounded.append(m)
    return bounded
```

### 6.4 近似 Token 估算

```python
def _approx_tokens(chars: int) -> int:
    return max(0, (int(chars) + 3) // 4)
```

字符数统计时，`data:image` 开头或长度超过 2000 的字符串按 2000 计算，避免 base64 图片数据膨胀统计。

---

## 7. Prompt 组装与注入

### 7.1 System Prompt 结构

System prompt 由 `get_system_prompt(lang, output_mode, prompt_version)` 组装，包含：

1. **SYSTEM_CONTRACT**：硬约束（坐标、单动作、HITL、context 不授权）
2. **ACTION_SCHEMA**：唯一动作契约（IntentIR + Do + Finish）
3. **TASK_POLICIES**：操作策略（7 条规则）
4. **CONTEXT_USAGE_RULES**：Context 使用规则（3 条规则）
5. **FAILURE_RECOVERY_MAP**：失败恢复策略映射
6. **Output Contract**：根据 output_mode 选择（json_schema / tool_calls / auto）

### 7.2 Context 使用规则

中文版（`config/prompts_zh.py`）：

```
# Context 使用规则
- 优先相信当前截图和用户任务；context 与截图冲突时，以截图为准。
- 不要复读 context 内容，不要把其中的隐私文本写入动作 message。
- `avoid_repeating` 表示应避免重复失败动作；`next_hint` 只是建议，不是强制命令。
```

英文版（`config/prompts_en.py`）：

```
# Context usage rules
- Prefer the current screenshot and user task; if context conflicts with the screenshot, trust the screenshot.
- Do not repeat raw context content, and do not copy private context text into action messages.
- `avoid_repeating` means avoid repeating known failures; `next_hint` is guidance, not a command.
```

### 7.3 Context Block 注入位置

在 plan_node 中，context_block 注入到 user message 的文本末尾：

**首轮（step_count == 0）**：

```
task + "\n\n" + screen_info + "\n\n" + marks_block + "\n\n" + context_block
```

**后续轮（step_count > 0）**：

```
screen_info + "\n\n" + reflection_context + "\n\n" + marks_block + "\n\n" + context_block
```

注意：context_block 可能为空字符串（observe/off 模式），此时不追加任何内容。

### 7.4 Prompt 版本系统

- 当前唯一支持版本：`context_harness_v1`
- `get_prompt_version()` 对非空非默认版本 fail-closed（抛出 `ValueError`）
- 版本存储在 state 的 `prompt_version` 字段，通过 `select_plan_context` 传入 `ContextSelectionResult`
- 包含在 trace metrics 和 `RunResult` 中，用于 eval/trace 对比

---

## 8. 外部边界脱敏

### 8.1 Checkpoint Egress

**文件**：`phone_agent/checkpoint/serde.py`

`RedactingSerializer` 包装 LangGraph 的 `JsonPlusSerializer`：

- `dumps(value)`：调用 `_redact_for_checkpoint(value)` → `sanitize_context_payload(consumer="checkpoint")`，然后序列化
- `loads(data)`：透传（checkpoint 字节流已含 stub）
- `dumps_typed(value)`：同上脱敏后委托内部序列化器
- `loads_typed(payload)`：透传

**关键设计**：不改变内存中的 state。State 在运行期间保持原始（或 regex 脱敏）文本，stub 仅在 checkpoint 序列化边界产生。

**防御性**：如果内部序列化器在 dumps 时抛异常，原始（未脱敏）值不会被写入——异常直接传播。

### 8.2 Trace Egress

**文件**：`phone_agent/graph/trace.py`

`JsonlTraceWriter` 使用独立的脱敏体系 `sanitize_for_trace()`：

- **SENSITIVE_KEYS**（9 个）：`api_key, apikey, authorization, base64_data, image_url, prompt, screenshot_b64, secret, text` → 完全替换为 `"<redacted>"`
- **PRIVATE_TEXT_KEYS**（14 个）：`action_raw, interrupt_message, message, reflection, final_message, error, result_message_summary, summary, system_prompt, task, thinking, visible_text, observed_text, parse_error, context_block, target_text_hint, text_hint` → 替换为 `{redacted, length, sha256}` stub

与 `sanitize_context_payload` 是并行体系，用于 JSONL trace 文件输出（`.traces/{trace_id}.jsonl`）。

### 8.3 Mark Registry Prompt Block

**文件**：`phone_agent/graph/marks.py`

- `Mark.to_trace_dict()` 使用 `sanitize_context_payload(consumer="checkpoint")` 对 `text_summary` 做 stub 替换
- `MarkRegistry.prompt_block()` 只输出 mark_id / role / source / confidence / stub
- 防止原始屏幕文本泄露到 VLM prompt 或 trace

### 8.4 Grounding Provider Hints

**文件**：`phone_agent/graph/observation.py`

- **本地 provider**：可接收 raw hints（`allow_raw_hints=True`），raw hint 仅在内存中使用
- **远程 provider**：必须经 `_redact_provider_hints()` 脱敏
- 所有 hint 文本经 `sanitize_context_payload(consumer="inject")` 脱敏
- Provider result 的 text_summary 同样经 inject consumer 脱敏
- `build_mark_provider_hints()` 最多生成 3 条 hint（`max_hints=3`），每条文本截断至 240 字符
- Hint 来源优先级：config 提供 → task 文本 → reflection 文本

**关键约束**：raw hint 不得进入 trace/checkpoint/prompt/report；远程 provider raw hint 必须显式 opt-in。

---

## 9. AgentState Context 字段详解

**文件**：`phone_agent/graph/state.py`

| 字段 | 类型 | 说明 | 初始值 | 更新节点 |
|------|------|------|--------|---------|
| `context_mode` | str | off / observe / inject | AgentConfig.context_mode | Plan (读取 config) |
| `context_strategy` | str | off / observe_only / inject_redacted_block | 根据 context_mode 计算 | Plan (select_plan_context) |
| `prompt_version` | str | prompt renderer 版本 | AgentConfig.prompt_version | Plan (get_prompt_version) |
| `selected_sections` | list[str] | 有值的 section ID 列表 | `[]` | Plan (select_plan_context) |
| `screen_belief` | dict | 短期屏幕信念 | `default_screen_belief()` | Reflect (build_screen_belief) |
| `action_outcome_summary` | dict \| None | 最近动作结果摘要 | `None` | Reflect + Execute |
| `failure_memory` | list[dict] | 有界失败记忆（最近 3 条） | `[]` | Reflect (update_failure_memory) |
| `summarized_history` | str | 压缩历史（budget 裁剪） | `""` | Reflect (update_summarized_history) |
| `short_term_memory` | dict | 请求级短期记忆 | `{}` | Reflect |
| `action_ledger` | list[dict] | 有界动作账本（最近 10 条） | `[]` | Reflect |
| `gui_memory` | dict | GUI 短期记忆 | `default_gui_memory()` | Reflect (update_gui_memory) |
| `context_budget` | dict | 裁剪预算配置 | `default_context_budget()` | 初始化 |
| `context_truncated` | bool | 是否发生了裁剪 | `False` | Plan + Reflect |
| `context_block_chars` | int | 注入 block 字符数 | `0` | Plan (build_plan_context_block) |
| `messages_before` | int | 压缩前消息数 | `0` | Plan (compact_messages_for_request) |
| `messages_after` | int | 压缩后消息数 | `0` | Plan (compact_messages_for_request) |
| `message_chars_before` | int | 压缩前近似字符数 | `0` | Plan (compact_messages_for_request) |
| `message_chars_after` | int | 压缩后近似字符数 | `0` | Plan (compact_messages_for_request) |
| `approx_tokens_before` | int | 压缩前近似 token 数 | `0` | Plan (compact_messages_for_request) |
| `approx_tokens_after` | int | 压缩后近似 token 数 | `0` | Plan (compact_messages_for_request) |
| `failure_memory_hit_count` | int | 失败记忆命中次数 | `0` | Reflect |
| `repeated_failure_count` | int | 重复失败计数 | `0` | Reflect |

### 9.1 default_screen_belief()

```python
{
    "current_app": "",
    "summary": "unknown",
    "loading_or_blocked": False,
    "unsafe_or_sensitive": False,
    "confidence": "unknown",
    "updated_step": 0,
}
```

### 9.2 default_gui_memory()

```python
{
    "visited_screens": [],
    "tried_actions": [],
    "scroll_memory": {},
    "task_progress": {},
}
```

### 9.3 short_term_memory 结构

由 reflect 节点构建：

```python
{
    "screen_belief": belief,                    # 当前屏幕信念
    "last_action_outcome": outcome,             # 最近动作结果
    "latest_failures": failure_memory[-3:],     # 最近 3 条失败记录
    "grounding_observation": state.get("grounding_observation"),  # grounding 上下文
}
```

---

## 10. 模块依赖关系

### 10.1 配置层

| 模块 | 职责 |
|------|------|
| `config/__init__.py` | `PROMPT_VERSION`, `get_prompt_version()`, `get_system_prompt()`, output contract 路由 |
| `config/prompts_zh.py` | 中文版 prompt 各 section + output contracts |
| `config/prompts_en.py` | 英文版 prompt 各 section + output contracts |
| `config/apps.py` | `APP_PACKAGES`, `get_app_registry_summary()` |

### 10.2 核心 Context 引擎

| 模块 | 职责 |
|------|------|
| `graph/context.py` | 全部 context 核心逻辑：模式/选择/构建/脱敏/压缩/budget/metrics |
| `graph/state.py` | `AgentState` TypedDict + `messages_reducer` |

### 10.3 Graph 节点

| 模块 | 职责 |
|------|------|
| `graph/nodes/plan.py` | 选择 + 构建 + 注入 + 压缩 context |
| `graph/nodes/reflect.py` | 更新全部 context state 字段 |
| `graph/nodes/execute.py` | 更新 action_outcome_summary + trace 脱敏 |

### 10.4 Grounding 上下文

| 模块 | 职责 |
|------|------|
| `graph/observation.py` | 构建 observation + provider hints 脱敏 |
| `graph/marks.py` | MarkRegistry + prompt_block（checkpoint stub） |
| `grounding/provider.py` | `MarkProviderHint` + `ScreenBinding` |

### 10.5 边界脱敏

| 模块 | 职责 |
|------|------|
| `checkpoint/serde.py` | `RedactingSerializer` — checkpoint egress stub |
| `graph/trace.py` | `JsonlTraceWriter` + `sanitize_for_trace()` |
| `actions/safety.py` | `decide_safety()` — 安全决策 |

### 10.6 消息构建

| 模块 | 职责 |
|------|------|
| `model/client.py` | `MessageBuilder` — system/user/assistant 消息构建 + 图片剥离 |

### 10.7 评估与运行

| 模块 | 职责 |
|------|------|
| `agent.py` | `AgentConfig` (context_mode, prompt_version) + `RunResult` (context metrics) |
| `evals/run_eval.py` | `--context-mode` CLI + eval 聚合 metrics |

---

## 11. 关键不变式

| # | 不变式 | 说明 |
|---|--------|------|
| 1 | **State 写入只 regex，不 stub** | 所有 state 写入路径（reflect/execute/plan）只做 `redact_context_text()` 内联替换；key-level stub 仅在 `consumer="checkpoint"` 时触发 |
| 2 | **compact 不改 state** | `compact_messages_for_request()` 只压缩传给 `model_client.request()` 的消息副本；`state["messages"]` 不受影响 |
| 3 | **Context 不绕过 HITL** | context block 是"信念，非授权"；不能通过 context 注入绕过 confirm_node / takeover_node。System prompt 中明确声明：`context 是辅助信念，不授权执行；不得因此绕过确认或接管` |
| 4 | **单一 prompt 版本** | 仅支持 `context_harness_v1`；`get_prompt_version()` 对其他版本 fail-closed |
| 5 | **Grounding fail-closed** | target-required grounding 失败不得回退为主 VLM 坐标 Tap；只能 fail/等待/接管/重新观测 |
| 6 | **Raw hint 不入 trace/prompt** | 本地 provider raw hint 可在内存使用，但不进入 trace/checkpoint/prompt/report；远程 provider raw hint 必须显式 opt-in |
| 7 | **messages_reducer 双模式** | plan_node 返回新增消息（append）；execute_node 返回完整重建列表（replace） |
| 8 | **select_plan_context 不改 state** | 只产出 section IDs、context block 与计数指标；不修改 Action IR、HITL、pending_execute、interrupt 或 safety route 字段 |
| 9 | **Context block 标注信念** | block 标题明确标注"仅为信念，不代表授权"，防止 VLM 将 context 误解为执行授权 |
| 10 | **Stub 策略保守** | checkpoint consumer 下，不在 `SAFE_CONTEXT_TEXT_KEYS` 中的未知 key 一律 stub，宁可多脱敏不漏脱 |

---

## 12. 端到端数据流

### 12.1 初始化

```
PhoneAgent.run_structured()
  → _build_initial_state()
    → context_mode = AgentConfig.context_mode
    → context_strategy = off / observe_only / inject_redacted_block
    → prompt_version = AgentConfig.prompt_version
    → screen_belief = default_screen_belief()
    → failure_memory = []
    → summarized_history = ""
    → gui_memory = default_gui_memory()
    → context_budget = default_context_budget()
    → 所有 metrics 字段初始化为 0/False
  → _build_graph_config()
    → configurable["context_mode"] = context_mode
    → configurable["prompt_version"] = prompt_version
```

### 12.2 Plan Node 执行

```
plan_node(state, config)
  │
  ├─ 1. build_observation(screenshot, current_app, marks, providers, hints)
  │     └─ sanitize_context_payload(consumer="inject") 脱敏 provider hints
  │
  ├─ 2. get_context_mode(state, config) → context_mode
  │
  ├─ 3. select_plan_context(state, mode, lang, prompt_version)
  │     ├─ off → ContextSelectionResult(strategy="off", sections=[])
  │     ├─ observe → ContextSelectionResult(strategy="observe_only", sections=[...])
  │     └─ inject → build_plan_context_block(state, lang, consumer="inject")
  │                → ContextSelectionResult(block="...", strategy="inject_redacted_block")
  │
  ├─ 4. 组装 user message
  │     ├─ 首轮: task + screen_info + marks_block + context_block
  │     └─ 后续: screen_info + reflection_context + marks_block + context_block
  │
  ├─ 5. compact_messages_for_request(full_messages, selection)
  │     ├─ 剥离历史图片（保留最新截图）
  │     ├─ 裁剪至 request_recent_messages=6
  │     └─ 计算 before/after metrics
  │
  ├─ 6. model_client.request(compacted_messages) → response
  │
  └─ 7. 返回 context_metrics + new_messages → state 更新
```

### 12.3 Execute Node 执行

```
execute_node(state, config)
  │
  ├─ get_context_mode(state, config) → context_mode
  ├─ context_enabled(mode) → bool
  │
  ├─ if context_enabled:
  │     └─ _context_update() → action_outcome_summary
  │
  ├─ sanitize_context_payload(gesture_trace, consumer="trace_payload")
  ├─ MessageBuilder.remove_images_from_message()
  └─ 重建 messages 列表（replace mode via messages_reducer）
```

### 12.4 Reflect Node 执行

```
reflect_node(state, config)
  │
  ├─ get_context_mode(state, config) → context_mode
  │
  ├─ if context_enabled(context_mode):
  │     ├─ build_screen_belief() → screen_belief (regex 脱敏 summary)
  │     ├─ build_action_outcome_summary() → action_outcome_summary
  │     ├─ detect_repeated_failure() → repeated: bool
  │     ├─ update_failure_memory() → failure_memory (bounded to 3)
  │     ├─ update_summarized_history() → summarized_history (budget trimmed)
  │     ├─ short_term_memory = {screen_belief, last_action_outcome, latest_failures, grounding_observation}
  │     ├─ action_ledger = (existing + [outcome])[-10:]
  │     ├─ update_gui_memory() → gui_memory
  │     └─ sanitize_context_payload(action, consumer="reflect_prompt", task_context=task)
  │
  └─ 返回 context_updates → state 写回
```

### 12.5 外部边界

```
Checkpoint Egress:
  RedactingSerializer.dumps(value)
    → _redact_for_checkpoint(value)
      → sanitize_context_payload(value, consumer="checkpoint")
        → PRIVATE_CONTEXT_TEXT_KEYS → {redacted, length, sha256} stub
        → 其余 → regex 替换
    → inner.dumps(redacted)

Trace Egress:
  JsonlTraceWriter.emit(node, event, payload)
    → sanitize_for_trace(payload)
      → SENSITIVE_KEYS → "<redacted>"
      → PRIVATE_TEXT_KEYS → {redacted, length, sha256} stub
    → 写入 .traces/{trace_id}.jsonl

Prompt Block:
  MarkRegistry.prompt_block(lang)
    → Mark.to_trace_dict(consumer="checkpoint")
      → text_summary → stub
    → 只输出 mark_id / role / source / confidence / stub

Provider Hints:
  build_mark_provider_hints(task, reflection, provider_hints)
    → sanitize_context_payload(hint.text, "message", consumer="inject")
    → 最多 3 条 hint，每条截断至 240 字符
  _redact_provider_hints(hints)
    → sanitize_context_payload(consumer="inject") 脱敏
```

---

## 13. 配置与默认值

### 13.1 DEFAULT_CONTEXT_BUDGET

```python
DEFAULT_CONTEXT_BUDGET = {
    "screen_belief_summary_chars": 300,   # 屏幕信念摘要字符上限
    "summarized_history_chars": 800,      # 压缩历史字符上限
    "failure_memory_items": 3,            # 失败记忆条数上限
    "action_outcome_items": 1,            # 动作结果条数上限
    "context_block_chars": 1500,          # 整体 context block 字符上限
    "request_recent_messages": 6,         # 请求消息条数上限
}
```

### 13.2 FAILURE_TAXONOMY

12 个规范失败原因标签：

```
none, element_not_found, wrong_page, app_not_responding,
network_or_loading, permission_or_login_or_captcha, unsafe_or_sensitive,
coordinate_or_tap_offset, context_lost, repeated_action,
model_parse_failed, unknown
```

别名映射（`FAILURE_CAUSE_ALIASES`）：

| 旧名称 | 映射到 |
|--------|--------|
| `app_not_responding_or_loading` | `app_not_responding` |
| `permission_login_captcha` | `permission_or_login_or_captcha` |
| `unsafe_or_sensitive_hitl` | `unsafe_or_sensitive` |
| `coordinate_or_click_offset` | `coordinate_or_tap_offset` |
| `network` | `network_or_loading` |

### 13.3 AgentConfig 中的 Context 配置

```python
class AgentConfig:
    context_mode: str = DEFAULT_CONTEXT_MODE    # "inject"
    prompt_version: str = PROMPT_VERSION         # "context_harness_v1"
```

### 13.4 RunResult 中的 Context 指标

`RunResult` 包含所有 context metrics 字段，通过 `build_context_metrics(state)` 从最终 state 提取：

```python
{
    "context_mode": str,
    "context_strategy": str,
    "prompt_version": str,
    "selected_sections": list[str],
    "context_block_chars": int,
    "context_truncated": bool,
    "messages_before": int,
    "messages_after": int,
    "message_chars_before": int,
    "message_chars_after": int,
    "approx_tokens_before": int,
    "approx_tokens_after": int,
    "failure_memory_hit_count": int,
    "repeated_failure_count": int,
}
```

### 13.5 CLI 参数

`evals/run_eval.py` 支持 `--context-mode` 参数，允许在评估时指定上下文模式。

---

## 附录：ContextSelectionResult 数据类

```python
@dataclass(frozen=True)
class ContextSelectionResult:
    """Trace-safe context selector output for one model request."""

    context_mode: str                           # off / observe / inject
    context_strategy: str                       # off / observe_only / inject_redacted_block
    prompt_version: str = DEFAULT_PROMPT_VERSION
    selected_sections: list[str] | None = None  # 有值的 section ID 列表
    context_block: str = ""                     # 注入的 context block 文本
    context_block_chars: int = 0                # block 字符数
    context_truncated: bool = False             # 是否被裁剪
    messages_before: int = 0                    # 压缩前消息数
    messages_after: int = 0                     # 压缩后消息数
    message_chars_before: int = 0               # 压缩前字符数
    message_chars_after: int = 0                # 压缩后字符数
    approx_tokens_before: int = 0               # 压缩前近似 token 数
    approx_tokens_after: int = 0                # 压缩后近似 token 数

    def metrics(self, include_block: bool = False) -> dict[str, Any]:
        """返回 JSON 友好、隐私安全的 metrics 字典。"""
        ...
```

该数据类是 context 选择流程的核心输出，贯穿 `select_plan_context()` → `compact_messages_for_request()` → `plan_node` → `RunResult` 的完整链路。它被设计为 frozen（不可变），确保在传递过程中不被意外修改。
