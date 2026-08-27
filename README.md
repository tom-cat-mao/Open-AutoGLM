# Phone Agent

薄 loop（thin-loop v2）Android 手机智能助理：视觉语言模型每步一次调用，通过工具感知和操作真实设备。
harness（middleware）只提供工具、安全边界、context 卫生与可观测，不做工作流路由。

```
system(极简契约) + user(task + 首次观测含截图)
  → create_agent tool loop: model → [safety HITL] → tool(s) → model → …
  → 结束: session.finished | takeover_reason | 无 tool_call | ModelCallLimit
```

- **工具**（`phone_agent/v2/tools/`，15 个）：执行 `tap/long_press/type_text/scroll/swipe/back/home/launch_app/wait`、
  感知 `read_screen/locate`、控制 `finish/ask_user/take_over/update_task_doc`。
  `tap` 双寻址：`target_mark_id` | `target_description`（解析为唯一 mark，歧义/无匹配 fail-closed 不执行）。
- **TaskDoc 任务板**：目标 / 路线 / 关键事实一个文档，模型经 `update_task_doc` 维护，
  每轮 pinned 进 context（压缩免疫）；`finish` 在路线未完成时被拒。
- **Middleware**（`phone_agent/v2/middleware/`）：安全 HITL（敏感动作人工 approve/reject）、
  历史截图剪除、TaskDoc 渲染、JSONL trace（脱敏）、`ModelCallLimit`。
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

HITL 触发时按提示输入 `approve` / `reject` / 回答文本。退出码：成功 `0` / takeover `2` / 步数上限 `3` / 错误 `1`。

## 配置

全部走 `PHONE_AGENT_*` 环境变量（`.env` 只加载该前缀，shell 优先）+ CLI 覆盖。完整键表见
[`.env.example`](.env.example) 与 `phone_agent/v2/config.py`。常用：`PHONE_AGENT_BASE_URL` / `PHONE_AGENT_MODEL` /
`PHONE_AGENT_API_KEY` / `PHONE_AGENT_DEVICE_ID` / `PHONE_AGENT_GROUNDING_PROVIDER`（默认 `hybrid`）。

网关注意：自建网关若在 Cloudflare 后，需要浏览器 UA 头（`v2/model.py` 已处理）；部分模型有采样参数限制，
用 `PHONE_AGENT_TEMPERATURE` / `PHONE_AGENT_TOP_P` / `PHONE_AGENT_FREQUENCY_PENALTY` 覆盖。

## Grounding

marks-first：执行动作必须绑定 mark。marks 来自 `session.refresh_marks()`（Android accessibility tree，
随观测自动附带）；树上没有的目标用 `locate(描述)` 走本地 LocateAnything（MLX，Apple Silicon），
定位后注册为 mark 再点击。`PHONE_AGENT_GROUNDING_PROVIDER=hybrid`（默认）= accessibility 优先、
LocateAnything 兜底。Benchmark 见 `bench/grounding/`。

## 文档

- 架构状态与后续迭代：`docs/future-roadmap.md`
- Agent 工作约定（P0 约束）：`AGENTS.md`
- 后续迭代（含工具扩展理念）：`docs/future-roadmap.md`
