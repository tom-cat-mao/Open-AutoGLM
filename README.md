# Phone Agent

薄 loop（thin-loop v2）Android 手机智能助理：视觉语言模型每步一次调用，通过工具感知和操作真实设备。
harness（middleware）只提供工具、安全边界、context 卫生与可观测，不做工作流路由。

```
system(极简契约) + user(task + 首次观测含截图)
  → create_agent tool loop: model → [safety HITL] → tool(s) → model → …
  → 结束: session.finished | takeover_reason | 无 tool_call | token 预算耗尽 | 死循环保险丝
```

- **工具**（`phone_agent/v2/tools/`，15 个）：执行 `tap/long_press/type_text/scroll/swipe/back/home/launch_app/wait`、
  感知 `read_screen/locate`、控制 `finish/ask_user/take_over/update_task_doc`。
  `tap` 双寻址：`target_mark_id` | `target_description`（解析为唯一 mark，歧义/无匹配 fail-closed 不执行）。
- **TaskDoc 任务板**：目标 / 路线 / 关键事实一个文档，模型经 `update_task_doc` 维护，
  每轮 pinned 进 context（压缩免疫）；`finish` 在路线未完成时被拒。状态迁移有纪律：
  不能把 `pending` 直接标 `completed`（须先 `in_progress`），也不能一次批量补标多项 `completed`。
- **finish 两段式 + 验收器**：`finish` 先给世界镜像复核包（不落定），`finish(confirm=true)` 才定稿；
  高风险目标/硬矛盾坚持 confirm 时过独立 context 验收器（只看目标+证据路线+尾帧截图，不看 actor 自辩），
  REJECT 带内回传、2 次转人工；验收器故障 fail-open（`PHONE_AGENT_FINISH_VERIFY=off|auto|always`，默认 auto）。
- **Middleware**（`phone_agent/v2/middleware/`）：安全 HITL（分层 `classify_tool_call`：宽召回→精排→硬门；
  硬门=不可逆动词/密码框/凭据/自我申报，软候选默认不弹窗，`PHONE_AGENT_SAFETY_MODE=off|hard|reviewer`）、
  两级 auto-compact（接近上下文窗口时折叠远古段）、历史截图剪除 + marks 折叠、TaskDoc 渲染、
  token 预算（L0 余量镜子 + 硬成本上限）、JSONL trace（脱敏）、`ModelCallLimit`（死循环保险丝）。
- **预算与 context 卫生**：成本以 **token** 计（累计 `usage_metadata` 的 input+output，缺失回退估算）。
  `PHONE_AGENT_TOKEN_BUDGET`（默认 1M）为总预算，耗尽即停（终局 `token_budget_exhausted`）；
  `PHONE_AGENT_TOKEN_WARN_REMAINING`（默认 100k）为绝对余量预警。`PHONE_AGENT_MAX_STEPS`（默认 100）
  降为防跑飞的死循环保险丝（终局 `loop_fuse`）。两级 auto-compact：`PHONE_AGENT_COMPACT_WARN_RATIO`
  （默认 0.75）注入"写任务板/收尾探索"提示，`PHONE_AGENT_COMPACT_TRIGGER_RATIO`（默认 0.92）
  调纯文本 LLM 生成手机版 handoff 摘要替换远古段（切点保 tool_use/tool_result 配对，不切 TaskDoc/pinned）。
- **保留库**：`phone_agent/adb/`（设备层）、`phone_agent/grounding/`（accessibility tree + LocateAnything）、
  `phone_agent/config/`（policy / app_registry / redact）。

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入 base_url / model / api_key
```

设备：Android 7.0+，开 USB 调试，`adb devices` 可见；文本输入需装
[ADBKeyboard](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)。

```bash
.venv/bin/python main_v2.py "打开设置进入WLAN" --device-id <serial>
.venv/bin/python main_v2.py "在飞猪查10月2日上海飞桃仙的最低价机票" --max-steps 40
.venv/bin/pytest tests -q            # 全 fake，无真机无 MLX
```

HITL 触发时按提示输入 `approve` / `reject` / 回答文本。退出码：成功 `0` / takeover `2` / 预算或保险丝耗尽 `3` / 错误 `1`。

## 配置

全部走 `PHONE_AGENT_*` 环境变量（`.env` 只加载该前缀，shell 优先）+ CLI 覆盖。完整键表见
[`.env.example`](.env.example) 与 `phone_agent/v2/config.py`。常用：`PHONE_AGENT_BASE_URL` / `PHONE_AGENT_MODEL` /
`PHONE_AGENT_API_KEY` / `PHONE_AGENT_DEVICE_ID` / `PHONE_AGENT_GROUNDING_PROVIDER`（默认 `hybrid`）。

网关注意：自建网关若在 Cloudflare 后，需要浏览器 UA 头（`v2/model.py` 已处理）；部分模型有采样参数限制，
用 `PHONE_AGENT_TEMPERATURE` / `PHONE_AGENT_TOP_P` / `PHONE_AGENT_FREQUENCY_PENALTY` 覆盖。

## Grounding

marks-first：执行动作必须绑定 mark。观测是**唯一原子生产者** `session.observe()`——一个采样窗口内
foreground → 截图 → accessibility dump（复用同一张截图）→ foreground 取齐，不稳重试一次、再不稳观测失败。
每次成功观测把批次计数 `epoch` +1，对外 mark ID 带**批次工牌** `ax_1@e<epoch>`；`tap` 引用非当前批次的
工牌会在 `resolve_mark` 处 fail-closed（`StaleMarkError`），观测失败则整批 marks 作废、不留旧寻址权限。
树上没有的目标用 `locate(描述)` 走本地 LocateAnything（MLX，Apple Silicon），命中后铸入**当前批次**并原帧返回、
再点击。`PHONE_AGENT_GROUNDING_PROVIDER=hybrid`（默认）= accessibility 优先、LocateAnything 兜底。
薄环一次观测一次动作，缺省关闭并行 tool calls（`PHONE_AGENT_PARALLEL_TOOL_CALLS=false`）。Benchmark 见 `bench/grounding/`。

## 文档

- 架构状态与后续迭代：`docs/future-roadmap.md`
- Agent 工作约定（P0 约束）：`AGENTS.md`
- 后续迭代（含工具扩展理念）：`docs/future-roadmap.md`
