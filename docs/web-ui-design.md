# Web 前端设计（WP-D 设计基准）

> 本文件是 Web 前端的**设计基准**（设计者：主 agent；traex 施工须与此对照，出入处以本文为准）。
> 目标读者：实现者与验收者。涉及代码：`phone_agent/web/`（新增）、`phone_agent/v2/agent.py`（仅加挂点）。

## 1. 目标 / 非目标

**目标**：本地起一个网页，让人**看着**手机 agent 跑一个任务，并能在需要时介入（HITL）。一句话：把 CLI 的"黑盒跑"变成"可观测、可接管"的 run。

**非目标（v1 不做）**：
- 多用户/多设备并发（一台设备一个 run）；
- 历史 run 回放/检索（诊断 skill 已覆盖，不重复造）；
- 鉴权、远程访问（只绑 127.0.0.1，本机自用）；
- 在网页里直接点手机屏幕操控设备（那是远控，不是 agent 观测台）。

## 2. 用户场景与交互流

```
打开网页(127.0.0.1:8080)
  → 顶部输入任务 → [开始]
  → 左栏实时显示手机画面（每步更新）+ 当前 App + screen_seq
  → 右栏步骤时间线逐条增长（#N intent → 工具<目标> → 结果）
  → 任务板区显示 pinned TaskDoc 文本
  → 需要人工时（hard 档拦截 / ask_user / take_over）：弹出介入面板
      approve / reject / 文本回答 → 提交 → run 继续
  → 终局：状态栏显示 成功/接管/预算耗尽/保险丝 + steps + 耗时
  → [停止]：软停止旗标，当前步结束后收尾（v1.1；v1 可只读不可停）
```

## 3. 页面布局（信息架构）

单页三区块：

```
┌──────────────────────────────────────────────────┐
│ 顶栏: [任务输入框............] [开始] [停止] 状态丸 │
├──────────────────────┬───────────────────────────┤
│ 左栏「手机画面」      │ 右栏 tabs:                │
│  ┌────────────┐     │  [步骤] [任务板]           │
│  │  screenshot │     │  步骤: 时间线卡片流         │
│  │  (等比缩放)  │     │   #3 把出发地改成上海       │
│  └────────────┘     │   → tap「上海」(ax_3) → ok  │
│  当前 App / screen#N │  任务板: TaskDoc 渲染文本    │
├──────────────────────┴───────────────────────────┤
│ 介入面板(仅 pending 时显示): 提示 + [approve][reject][输入] │
└──────────────────────────────────────────────────┘
```

- 步骤卡片配色：ok=绿、error=红、safety 预警=黄、控制类(finish/ask_user/take_over)=蓝。
- 截图用 `data:` URL 直接渲染（不落盘），保持等比。

## 4. 架构与线程模型

**核心约束**：`ThinPhoneAgent.run()` 是同步阻塞循环；NiceGUI 跑在 asyncio 主循环。两者用**生产者-消费者队列**解耦，UI 只轮询、绝不跨线程直接改 agent。

```
agent 线程: ThinPhoneAgent.run(task, hitl_handler=bridge.hitl)
              └─ WebEventMiddleware（挂在 extra_middleware）
                   ├─ wrap_model_call → event: model_call
                   ├─ wrap_tool_call  → event: tool_call / tool_result(+最新截图)
                   └─ after_agent     → event: run_end
                        │  queue.Queue（线程安全）
UI 线程:   ui.timer(0.4s) 从队列 drain → 更新状态 → 刷新组件
HITL:      hitl_handler 在 agent 线程阻塞等 threading.Event
           UI 提交答案 → event.set()（答案经共享槽传递）
```

- **挂点**：`ThinPhoneAgent.__init__` 新增 `extra_middleware: list | None`，追加进 middleware 列表（WebEventMiddleware 由此注入）。这是 agent 核心唯一允许的改动。
- **事件形状**（dict，全部 JSON 可序列化；截图只存 data-url 引用）：
  ```python
  {"type": "model_call", "step": int, "latency_ms": int, "error": str|None}
  {"type": "tool_call", "step": int, "name": str, "intent": str, "args": dict}   # args 脱敏
  {"type": "tool_result", "step": int, "name": str, "text": str, "ok": bool, "is_warning": bool}
  {"type": "screen", "seq": int, "app": str, "image": "data:image/png;base64,..."}
  {"type": "taskdoc", "text": str}
  {"type": "run_end", "success": bool, "reason": str, "steps": int}
  ```
- **截图来源**：wrap_tool_call 里从 ToolMessage content 的 image 块提取最新 data-url（与 diagnostic 中间件同源思路，但走内存不落盘）。
- **脱敏**：args/text 过 `config/redact.py` 的 `redact_context_text`（复用现有，不新造）。

## 5. HITL 桥（关键路径）

- `hitl_handler(prompt: str) -> str`：把 prompt 放上共享槽 → `threading.Event.wait()`（无超时，跟 CLI 的 `input()` 语义一致）→ UI 提交后返回答案字符串。
- UI 介入面板三态：approve / reject / 文本（ask_user 用）。提交即 set。
- run 结束或失败时若有悬挂的 HITL，UI 面板关闭、状态标终局（防御性）。

## 6. 状态机（UI 侧）

`idle → running → (hitl_pending ⇄ running) → done(success|takeover|budget|fuse|error)`

- 任何时刻只允许一个 run；running 中[开始]禁用。
- 停止（v1.1 可选）：共享旗标，WebEventMiddleware 在 before_model 检查 → 置 `session.takeover_reason="用户从 Web 停止"`（复用既有终局通道，不新造机制）。

## 7. 文件划分

```
phone_agent/web/
├── __init__.py
├── __main__.py        # python -m phone_agent.web [--device-id …] [--port 8080]
├── bridge.py          # WebRunBridge + WebEventMiddleware + HITL 桥
└── app.py             # NiceGUI 页面（布局/轮询/交互）
tests/web/
└── test_bridge.py     # 事件产生、HITL 阻塞/唤醒、run_end 落地（全 fake）
```

## 8. 边界与失败处理

- 设备未连接/截图失败：左栏显示占位文本"无画面（原因）"，run 继续（agent 自身已 fail-open）。
- Web 服务挂了不影响 agent：middleware 所有钩子 try/except 包裹，写队列失败静默（跟 trace/diagnostic 同一纪律）。
- 端口占用：`--port` 参数 + 启动失败明确报错退出码 1。
- 配置：复用 `V2Config.from_env`（.env 全生效）；端口/host 走 `__main__` CLI 参数，不进 V2Config（属 UI 层细节）。

## 9. 验收标准

1. `tests` 全绿（含新增 web 测试）；`.venv/bin/python -m phone_agent.web --help` 可用；
2. fake 环境下 bridge 产出全类型事件、HITL 阻塞-唤醒正确；
3. agent 核心改动仅 `extra_middleware` 挂点，无其它行为变化；
4. UI 文本中文；README/配置文档新增启动说明一行（由后续 docs 合并时统一处理亦可）。
