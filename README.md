# TaskWizard

LLM 驱动的安卓手机操作 Agent：看一眼屏幕、想一步、动一下，带安全预警与自积累记忆。

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776AB?logo=python&logoColor=white)](setup.py)
[![CI](https://img.shields.io/badge/CI-GitHub_Actions-lightgrey?logo=githubactions)](https://github.com/tom-cat-mao/TaskWizard/actions)

TaskWizard 采用 thin-loop v2：模型每轮观察真实设备、决定一个工具调用并执行一步；harness 只负责工具、安全边界、上下文卫生和可观测性，不替模型编排工作流。

## Demo

![TaskWizard 控制台：真实运行中的步骤时间线与接管终态](pages/assets/console-run.png)

## Features

- **Marks-first grounding**：执行动作绑定当前屏幕元素，过期、歧义或未命中的目标会 fail-closed。
- **高精度视觉定位**：`locate` 默认把原分辨率截图交给视觉定位器，优先利用“外观 + 可见文字 + 相对位置”提示；歧义时可用当前批次的容器或上下锚点 mark 做原图 scope 裁剪，结果仿射回映射为全屏 mark。
- **观测加固**：默认在每次原子观测前静置 300ms 并识别 `FLAG_SECURE` 均匀黑屏；执行工具可用 `settle_ms` 替代全局静置（搜索/提交/开页建议 1500–2500ms）。
- **安全预警制**：风险动作先返回警告与选项，模型明确确认后才执行（confirm-to-execute）。
- **可信完成**：TaskDoc 任务板与流程线持续记录进度；finish 两段式确认，并可交给独立上下文验收器复核。
- **App-KB 自积累记忆**：同步本机应用名称；验证启动成功后沉淀非敏感别名，并累计成功反馈。同一 run 中未知中文名失败、回执列出的包名随后启动成功时，自动把该中文名写为 `learned` 别名（隐式纠正，证据闭环）。
- **经验数据面**：每次 run 结束以严格隐私白名单落盘 episode outcome 与工具结果分类，持久化分角色 token 账本；数据采集全程 observe-only，并审计本轮实际注入的 lesson id。
- **经验提炼与晋升**：离线 `--distill` 从证据充足的 episode 组生成 proposed lesson；Rule-of-3 通过后仍须人工 approve。仅 `PHONE_AGENT_MEMORY_RAG=on` 时，approved lesson 才在 run 开局以“参考、非规则”的 L0 Mirror 受控注入。
- **RAG shadow 召回**：sqlite-vec + FTS5 混合检索历史 episode 与 App 别名；默认只写 trace 并按实际启动应用统计命中率，绝不注入 actor 上下文。
- **能力注册表**：每个能力（App-KB/dream/经验/回想/安全/压缩/验收…）有稳定 id、档位与依赖声明；依赖缺失可见待岗；每次 run 的能力快照写入 trace 与 episode，可审计可复盘。
- **长任务可控**：token 预算限制成本，两级 auto-compact 在接近上下文窗口时保留关键状态。

## 快速开始

需要 Python 3.10+；Android 7.0+ 设备需开启 USB 调试、能被 `adb devices` 识别，并安装 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # 填写模型网关、模型名与 API Key
```

`PHONE_AGENT_LOCATE_MAX_SIZE=0` 保持 `locate` 原图输入；低配机器可设为正整数限制最长边。`PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` 限制 intent/可见文字提示长度，`PHONE_AGENT_SCOPE_PADDING_RATIO` 控制可选 scope 裁剪的边缘扩展比例。
`PHONE_AGENT_OBSERVE_SETTLE_MS=300` 控制观测前静置（`0` 关闭），`PHONE_AGENT_BLACK_SCREEN_DETECT=on|off` 控制保护页黑屏检测；动作参数 `settle_ms` 会 clamp 到 0–5000ms，并替代而非叠加全局值。
`PHONE_AGENT_IMPLICIT_ALIAS=on|off` 控制 App-KB 的证据闭环隐式纠正（默认 `on`）；无失败回执候选证据时不会猜测或写入。

```bash
.venv/bin/python main_v2.py "打开设置进入 WLAN" --device-id <serial>
.venv/bin/python main_v2.py "在飞猪查询 10 月 2 日上海飞桃仙的最低价机票" --max-steps 40
.venv/bin/python -m phone_agent.web --device-id <serial> --port 8080   # Web 控制台
.venv/bin/python main_v2.py --dream    # 手动整理本地 App-KB 与经验库
.venv/bin/python main_v2.py --rebuild-vec  # 从 episode/App-KB 全量重建语义索引
.venv/bin/python main_v2.py --distill     # 离线蒸馏，只写 proposed lesson
.venv/bin/python main_v2.py --review-lessons
.venv/bin/python main_v2.py --approve-lesson <lesson-id>
.venv/bin/python main_v2.py --revoke-lesson <lesson-id> "原因"
.venv/bin/python main_v2.py --supersede-lesson <lesson-id> "修订后的规则"
.venv/bin/pytest tests -q
```

RAG 默认 `PHONE_AGENT_MEMORY_RAG=shadow`。向量模型
`Qwen/Qwen3-Embedding-0.6B` 仅在索引或非空召回第一次真正 embed 时懒加载；
`PHONE_AGENT_MEMORY_RAG=on` 会在 run 开局一次性注入人审 approved lesson，默认上限为
`PHONE_AGENT_LESSON_INJECT_MAX=3` 条、`PHONE_AGENT_LESSON_INJECT_TOKENS=800` 估算 token；
设备 scope 必须匹配，开局未知 app 时不会选择 app 级 lesson。提示明确标为历史参考而非规则，
lesson 视图缺失或损坏时 fail-open。`shadow` 仍只做 trace 召回与命中统计，`off` 不召回也不注入。

Web 控制台默认只监听 `127.0.0.1:8080`：输入任务后可实时查看手机画面、步骤时间线、任务板与终局状态，
并处理 `ask_user` / `take_over` / hard 档安全确认。任务由独立 runner 子进程执行，控制台重启后会从
`PHONE_AGENT_RUNS_DIR`（默认 `memory/runs`）回放事件并重连仍存活的任务；无界面用法仍保持进程内直跑。

经验数据默认写入 `memory/experience/{events.jsonl,episodes.json}`；前者是追加式事实日志，后者是按
`run_id` 索引、可重建的物化视图。`PHONE_AGENT_EXPERIENCE=off` 可完全关闭写入；`--dream` 按
`PHONE_AGENT_EPISODE_KEEP` / `PHONE_AGENT_EPISODE_ARCHIVE_DAYS` 将旧全文折叠为无原文的类别成功率统计。

`PHONE_AGENT_EVOLUTION=manual` 仅开放显式离线命令；候选写入
`memory/lessons/{events.jsonl,lessons.json}`。蒸馏只看到隐私最小化的 episode 摘要，输出先经严格
schema、证据、scope 与原文泄漏检查，再以 proposed 状态落盘；Rule-of-3 也只产生“可供人工晋升”结论。
离线管线不参与 actor prompt；proposed/revoked 永不注入。默认 `shadow` 继续只观测，只有显式
`PHONE_AGENT_MEMORY_RAG=on` 才按上述边界把 approved lesson 注入一次，并在 trace 与 episode 记录 id。

## 文档

📖 **完整文档站：<https://tom-cat-mao.github.io/TaskWizard/>**

| 主题 | 入口 |
|---|---|
| 快速开始 | [文档站](https://tom-cat-mao.github.io/TaskWizard/quickstart/) · [`.env` 模板](.env.example) |
| 配置参考（全量） | [文档站配置页](https://tom-cat-mao.github.io/TaskWizard/configuration/) |
| 架构 / 安全 / 记忆 / 路线图 | [文档站](https://tom-cat-mao.github.io/TaskWizard/) |
| Agent 开发约定 | [AGENTS.md](AGENTS.md) |

## Contributing

欢迎提交 Issue 和 Pull Request；开始编码前请先阅读 [AGENTS.md](AGENTS.md) 的开发契约与 P0 约束。

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。
