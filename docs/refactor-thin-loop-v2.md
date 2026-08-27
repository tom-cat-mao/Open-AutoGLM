# Thin-Loop v2 全量重构文档

> 状态：执行基准文档。三个并行工作流（core / tools / middleware）以此文档为唯一契约。
> 分支：`feature/thin-loop-v2`。本文档先于此分支上所有代码。

## 1. 背景与目标

v1（LangGraph goal→plan→execute→reflect→acceptance 节点图）在实践中暴露：流程过重（每步两次模型调用）、
mark 管线与观测解耦导致死锁（step-16 事故）、validation replan/guidance 机制复杂且低效。

v2 目标：**薄 loop + 工具化**。LLM 每步一次调用，通过工具感知和操作设备；harness 只负责
工具供给、安全边界、context 卫生、可观测，不做工作流路由。

已锁定设计约束（不在本轮讨论范围）：

1. **marks-first grounding**：执行类动作必须绑定 mark；原始坐标仅 fallback（swipe 例外）。
2. `tap` 支持**双寻址**：`target_mark_id`（直达）| `target_description`（自然语言，必须经
   grounding 解析为唯一 mark 才执行，歧义/无匹配 fail-closed 返回候选，不执行）。
3. 技术轨道：LangChain `create_agent` + 按需 middleware（不用 create_deep_agent 的
   filesystem/shell 电池）。
4. **可配置性完整保留**：所有 `PHONE_AGENT_*` env / `.env` / CLI。
5. 安全硬门只活在 middleware（危险动作 interrupt 等待人工 approve/reject）。
6. **本轮不做**（后续迭代）：handoff 压缩/writer、长期记忆文件、plan 工具、finish verifier 子代理、
   TodoList、loop-engineering 增强。

## 2. 删除与保留

### 2.1 删除（v1 架构，全部移除）

- `phone_agent/graph/`（全部：nodes/edges/state/context/goal*/marks/observation/trace/verifier/…）
- `phone_agent/agent.py`、`phone_agent/checkpoint/`
- `phone_agent/actions/`（adapter/validator/repair/grounding/capability/receipt 等 v1 ActionIR 管线）
- `phone_agent/config/prompts_zh.py`、`phone_agent/config/prompts_en.py`
- `main.py`（由 `main_v2.py` 取代）
- `evals/`（v1 耦合，后续重建）
- `tests/graph/`、`tests/actions/`、`tests/evals/`、`tests_meta/`（v1 耦合测试）
- `phone_agent/graph/tools/`（v1 工具层）

### 2.2 保留并复用（作为库，不属工作流）

- `phone_agent/adb/`：设备层（截图/tap/swipe/type/launch/back/home/dump_uiautomator_xml/foreground）
- `phone_agent/device_factory.py`：DeviceFactory
- `phone_agent/grounding/`：MarkProvider 体系（AccessibilityTreeProvider、LocateAnything、
  FallbackMarkProvider、factory、provider 契约 MarkCandidate/ScreenBinding/MarkProviderHint）
- `phone_agent/config/policy.py`：安全词汇与验证阈值（middleware 使用）
- `phone_agent/config/app_registry.py`：AppIdentity/installed inventory（launch_app 解析使用）
- `bench/`、`models/`、`scripts/`、`.agents/`、文档
- `tests/grounding/`、`tests/adb`（如存在）及不与 v1 图耦合的测试

### 2.3 已知连带破坏（记录在案，后续处理）

- `.agents/skills/phone-agent-live-diagnosis/` 依赖 v1 run 结构，v2 上线后需适配（本轮不动）。
- `README.md`/`AGENTS.md`/`CLAUDE.md`/`docs/future-roadmap.md` 描述的是 v1（本轮由集成任务更新指针）。

## 3. 目标结构

```
phone_agent/
  adb/                 # 保留
  grounding/           # 保留
  device_factory.py    # 保留
  config/
    policy.py          # 保留
    app_registry.py    # 保留
  v2/
    __init__.py
    config.py          # V2Config：env/.env/CLI 三级解析
    model.py           # build_chat_model()：ChatOpenAI 工厂
    session.py         # PhoneSession：设备状态 + 截图 + 当前 marks + locate
    resolver.py        # 目标解析：mark_id | description -> 唯一 mark（fail-closed）
    prompts.py         # 极简 system prompt（cn/en，lang 可配）
    agent.py           # ThinPhoneAgent：create_agent 装配 + run 循环 + HITL resume
    tools/
      __init__.py      # build_tools(session, config) 汇总
      actuation.py     # tap/long_press/type_text/scroll/swipe/back/home/launch_app/wait
      perception.py    # read_screen/locate
      control.py       # finish/ask_user/take_over
    middleware/
      __init__.py
      safety.py        # 危险动作 interrupt 谓词
      images.py        # 历史截图滚动剪除（留最新 2 张）
      trace.py         # JSONL trace（脱敏）
main_v2.py             # CLI 入口
tests/v2/              # 全部新测试
```

## 4. 配置层契约（`v2/config.py`）

```python
@dataclass
class V2Config:
    # model
    base_url: str            # PHONE_AGENT_BASE_URL
    model_name: str          # PHONE_AGENT_MODEL
    api_key: str             # PHONE_AGENT_API_KEY (default "EMPTY")
    model_timeout: float     # PHONE_AGENT_MODEL_TIMEOUT (default 180)
    model_max_retries: int   # PHONE_AGENT_MODEL_MAX_RETRIES (default 2)
    # device
    device_id: str | None    # PHONE_AGENT_DEVICE_ID
    # loop
    max_model_calls: int     # PHONE_AGENT_MAX_STEPS (default 20) -> ModelCallLimit
    # grounding
    grounding_provider: str  # PHONE_AGENT_GROUNDING_PROVIDER (default "hybrid")
    accessibility_timeout: float   # PHONE_AGENT_ACCESSIBILITY_TIMEOUT (default 3.0)
    accessibility_max_marks: int   # PHONE_AGENT_ACCESSIBILITY_MAX_MARKS (default 80)
    locateanything_model: str | None      # PHONE_AGENT_LOCATEANYTHING_MODEL
    locateanything_max_size: int          # PHONE_AGENT_LOCATEANYTHING_MAX_SIZE (default 960)
    # i18n / misc
    lang: str                # PHONE_AGENT_LANG (default "cn")
    # trace
    trace_dir: str           # PHONE_AGENT_TRACE_DIR (default ".traces")
    trace_enabled: bool      # PHONE_AGENT_TRACE (default true)
    # reserved（本轮只读取不实现）：PHONE_AGENT_MEMORY_MODEL / PHONE_AGENT_VERIFIER_MODEL
    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "V2Config": ...
```

- `.env` 加载：`load_project_env()`（从 run_diagnosis 移植进 `v2/config.py`）：只加载
  `PHONE_AGENT_` 前缀、不覆盖已存在 shell env、容忍 `export ` 前缀与引号。
- CLI 覆盖 > shell env > .env > 默认。
- 采样参数：`PHONE_AGENT_TEMPERATURE` / `PHONE_AGENT_TOP_P` / `PHONE_AGENT_FREQUENCY_PENALTY`
  （float，非法值 raise ValueError）。
- 请求头：`PHONE_AGENT_USER_AGENT`（默认浏览器 UA 常量）、`PHONE_AGENT_HTTP_HEADERS`
  （`k=v;k2=v2`）、`PHONE_AGENT_CF_ACCESS_CLIENT_ID`/`_SECRET`（必须成对，否则 ValueError）。
  实现放 `v2/model.py::build_default_headers()`（与 spike 脚本 `scripts/spike_langchain_compat.py` 一致）。

## 5. 模型层契约（`v2/model.py`）

```python
def build_chat_model(config: V2Config) -> BaseChatModel:
    # langchain_openai.ChatOpenAI(base_url, model, api_key, timeout, max_retries,
    #                             default_headers=build_default_headers(), **sampling)
```

已验证（spike）：网关 + tool_calls + image_url content block + 上述采样参数全部兼容。

## 6. Session 契约（`v2/session.py`）

```python
class PhoneSession:
    """一次 run 的设备侧状态。工具通过它访问设备与 marks。"""
    config: V2Config
    device_factory: DeviceFactory
    marks: dict[str, MarkCandidate]   # 当前屏 marks（mark_id -> candidate）
    screen_seq: int                    # 截图序号
    finished: bool
    finish_summary: str | None
    takeover_reason: str | None

    def screenshot(self) -> Screenshot          # adb 截图；is_valid=False 时 raise ScreenshotError
    def refresh_marks(self) -> list[MarkCandidate]
        # accessibility provider（dump_uiautomator_xml 回调）产出；失败返回空列表不 raise
    def observe(self) -> Observation
        # screenshot + refresh_marks + foreground_app；更新 self.marks/screen_seq
    def resolve_mark(self, mark_id: str) -> MarkCandidate   # 不在当前 marks -> raise StaleMarkError
    def mark_center_abs(self, mark: MarkCandidate) -> tuple[int, int]
        # mark.center 是 0-1000 相对坐标；convert_relative_to_absolute(从 graph/tools/coords.py 移植到 v2/coords.py)
    def locate(self, description: str) -> MarkCandidate
        # LocateAnything provider（经 grounding/factory build_locate_provider，lazy 单例）
        # 唯一置信候选 -> 注册进 self.marks 返回；零/多候选 -> raise LocateAmbiguousError(候选摘要)
```

`Observation`（dataclass）：`screenshot_b64, width, height, current_app, marks, screen_seq`。
marks 摘要素描文本：`format_marks_digest(marks, max_items=40)` -> 每行 `mark_id | role | text(≤32字) | center`。

## 7. 工具契约（`v2/tools/`）

所有工具返回 `str`（给模型读的结果文本）；失败返回错误说明字符串（不 raise 给模型，
fail-closed：错误留在 transcript）。执行类工具成功后返回
`"OK. <结果>" + 自动观测块`（见 7.4）。

### 7.1 执行族 `actuation.py`

```
tap(target_mark_id: str | None = None, target_description: str | None = None)
    二选一必填。mark_id -> session.resolve_mark（失效则返回 stale 提示）；
    description -> resolver.resolve_description()。得到唯一 mark -> 中心点 -> 绝对像素 -> device.tap。
long_press(target_mark_id=None, target_description=None)   # 同上
type_text(text: str, target_mark_id: str | None = None, target_description: str | None = None)
    给了 target 先 tap 聚焦；文本经 adb keyboard 输入（沿用 device_factory.type_text；
    需要时 detect_and_set_adb_keyboard / restore_keyboard）。
scroll(direction: Literal["up","down","left","right"])     # 屏幕中部 swipe 实现
swipe(start: list[int], end: list[int])                    # 0-1000 相对坐标（文档注明:坐标 fallback，优先用 scroll）
back() / home() / wait(seconds: float = 2.0)
launch_app(app_name: str)   # config/app_registry 解析为包名 -> device_factory.launch_app；
                            # 未知 App 返回候选/错误，不执行
```

### 7.2 感知族 `perception.py`

```
read_screen()   # 无副作用重观测：session.observe() -> 返回观测块文本（当前 App + marks 摘要）
locate(description: str)
    # 深度视觉定位（accessibility marks 覆盖不了目标时用）。
    # 成功: "已定位并注册为 mark <id>，可用 tap(target_mark_id=...) 点击"
    # 歧义/失败: 返回候选列表或失败原因（不注册不执行）
```

### 7.3 控制族 `control.py`

```
finish(summary: str, evidence: list[str])
    # evidence 必填非空（契约：枚举"完成了什么+屏幕上证据"）。
    # 记录 session.finished=True + summary，返回 "已记录完成声明"。
ask_user(question: str) -> str     # HITL：interrupt 等待用户文本回答（respond）
take_over(reason: str)             # HITL：声明需要人工接管；记录 takeover_reason
```

### 7.4 自动观测块（执行族工具统一附带）

```
[OBS] app=<current_app> screen#<seq>
marks (N): ax_1|TextView|WLAN|(500,300) · ax_2|... （<=40 行，超出省略）
```

## 8. Resolver 契约（`v2/resolver.py`）

```python
def resolve_description(session: PhoneSession, description: str) -> MarkCandidate
    # 1) 当前 marks 文本匹配：精确 > 子串 > 规范化模糊（去空白/大小写）
    # 2) 唯一命中 -> 返回
    # 3) 零命中 -> session.locate(description)（LocateAnything 兜底）
    # 4) 多命中 -> raise ResolveAmbiguousError(candidates[:5] 摘要)
    # 工具层捕获 ResolveAmbiguousError/LocateAmbiguousError ->
    #   返回 "ambiguous: ... 候选列表，请细化描述或使用 target_mark_id"（不执行）
```

## 9. Middleware 契约（`v2/middleware/`）

### 9.1 `safety.py`

- 谓词 `is_sensitive_tool_call(request) -> bool`：
  - `type_text`：text 命中支付/密码/验证码关键词（`config/policy.py` 词汇表，中文+英文）
  - `tap/long_press`：目标 mark 的 text_summary 命中敏感词（"支付/确认付款/密码/转账"等）
  - `launch_app`：目标 App 在敏感 App 表（银行/支付类，读 policy 配置）
- 命中则该工具调用进入 HITL interrupt（approve/reject）。实现用
  `HumanInTheLoopMiddleware(interrupt_on={"tap": {"when": pred, "allowed_decisions": ["approve","reject"]}, ...})`。
- `ask_user`：`{"allowed_decisions": ["respond"]}`；`take_over`：直接 interrupt（always）。

### 9.2 `images.py`

- `before_model` 钩子：messages 中除最新 1 个含图消息外，其余消息的 image content block
  替换为 `[screen#<n> 已剪除]` 文本占位。（本轮极简版；压缩合并机制后续迭代。）

### 9.3 `trace.py`

- 每次模型调用与工具调用写 JSONL：`{ts, event: model_call|tool_call|tool_result|run_end,
  step, tool, args_redacted, latency_ms, error}`。
- 脱敏：args 中文本值 >64 字符截断；密码/验证码关键词命中替换 `<redacted>`；
  不记录截图 base64（只记 screen_seq 与字节数）。

### 9.4 限额

- `ModelCallLimitMiddleware(thread_limit=config.max_model_calls, exit_behavior="end")`。

## 10. Agent 装配与 run 循环（`v2/agent.py`）

```python
class ThinPhoneAgent:
    def __init__(self, config: V2Config, checkpointer=None):
        # build_chat_model / PhoneSession / build_tools / middleware 栈 / create_agent
    def run(self, task: str, hitl_handler: Callable[[str], str] = input) -> RunResult:
        # 初始消息: [system(极简契约), user(task + 首次观测块(含截图 image block))]
        # invoke 循环；遇 __interrupt__ -> hitl_handler 收集决定 -> Command(resume={"decisions": [...]})
        # 结束条件: session.finished | session.takeover_reason | 模型无 tool_call | ModelCallLimit
@dataclass
class RunResult:
    success: bool            # session.finished 且非 takeover
    reason: str              # finish summary / takeover reason / "max_model_calls" / "model_stopped"
    steps: int
    trace_path: str | None
```

- checkpointer：`MemorySaver`（HITL resume 必需），thread_id = run uuid。
- system prompt（`v2/prompts.py`）：角色 + 工具契约（marks-first、描述解析、歧义时细化或换 mark_id）+
  安全规则（敏感动作会被人工确认）+ finish 契约（evidence 必填）。cn/en 按 lang。目标 ≤800 token，无 few-shot。

## 11. CLI（`main_v2.py`）

```
.venv/bin/python main_v2.py [task] --device-id X --max-steps 20 --model M --base-url U \
    --grounding-provider hybrid --lang cn --trace-dir .traces
```

- 先 `load_project_env()`，再 argparse（默认 None -> 不覆盖 env）。
- 打印每步工具调用与结果摘要；HITL 时 `input()` 收集 approve/reject/respond。
- 退出码：success 0 / takeover 2 / max_calls 3 / error 1。

## 12. 测试要求（`tests/v2/`，全部 fake，无真机无 MLX）

- `test_config.py`：env/.env/override 优先级、采样参数解析、CF 成对校验、headers 构造。
- `test_resolver.py`：精确/子串/零命中 locate 兜底/多候选 fail-closed 不执行。
- `test_tools.py`：tap mark_id 直达坐标换算正确（0-1000 -> 像素）；stale mark 返回提示；
  description 歧义返回候选文本；launch_app 未知 App 不执行；finish 空 evidence 拒绝。
- `test_middleware.py`：敏感词 interrupt 触发/非敏感不触发；images 剪除留最新 1 张；
  trace 脱敏（长文本截断、密码词 redacted）。
- `test_agent_loop.py`：FakeChatModel（langchain 测试工具）+ fake device factory +
  fake marks，端到端跑 3 步：read_screen -> tap -> finish，断言 RunResult 与工具调用序列。
- 现有 `tests/grounding/` 保持绿。

## 13. 文档更新（集成任务）

- `README.md`：入口改 `main_v2.py`，架构段落替换为薄 loop 描述（精简，不展开未实现特性）。
- `AGENTS.md`：P0 表替换为 v2 版（见下）；项目结构段更新。
- `docs/future-roadmap.md`：新增 v2 章节，标记 v1 退役、后续迭代项（压缩/记忆/plan/verifier）。
- v2 P0 表草案：① 坐标转换只在工具内（0-1000->像素）② 执行必绑定 mark（描述解析 fail-closed）
  ③ 历史截图滚动剪除 ④ 危险动作 interrupt 硬门 ⑤ 工具失败返回错误文本不执行（fail-closed）
  ⑥ trace/日志 egress 脱敏 ⑦ 设备操作只经 DeviceFactory ⑧ 配置只经 V2Config 三级解析
  ⑨ no force push ⑩ no auto-commit（除显式要求）。

## 14. 验收标准（我逐项核验）

1. `.venv/bin/pytest tests/v2 tests/grounding -q` 全绿。
2. `.venv/bin/python main_v2.py --help` 正常；`scripts/spike_langchain_compat.py` 仍 PASS。
3. 无 `phone_agent/graph`、`phone_agent/agent.py`、`phone_agent/actions`、`main.py` 残留引用
   （`rg` 全仓搜索 v1 符号在保留代码中零命中）。
4. 文档三件（README/AGENTS/roadmap）与代码一致。
5. 工具集、middleware、配置键与本文档逐项对齐。

## 15. 并行工作划分（worktree）

- **W-core**（`v2/config.py`、`v2/model.py`、`v2/session.py`、`v2/coords.py`、`v2/prompts.py`、
  `main_v2.py` 骨架 + `tests/v2/test_config.py`）
- **W-tools**（`v2/resolver.py`、`v2/tools/*` + `tests/v2/test_resolver.py`、`test_tools.py`）
  依赖契约：§6 session 接口、§7/§8 语义（按文档接口编程，core 未就绪时用文档中的签名 stub）。
- **W-middleware**（`v2/middleware/*` + `v2/agent.py` + `tests/v2/test_middleware.py`、
  `test_agent_loop.py` + 文档更新 README/AGENTS/roadmap）
  依赖契约：§9 middleware 语义、§10 装配、§13 文档清单。
- 冲突规避：三方只写各自文件；`v2/__init__.py` 由 W-core 写；合并顺序 core -> tools -> middleware。
