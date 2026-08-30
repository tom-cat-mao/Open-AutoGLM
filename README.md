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
- **安全预警制**：风险动作先返回警告与选项，模型明确确认后才执行（confirm-to-execute）。
- **可信完成**：TaskDoc 任务板与流程线持续记录进度；finish 两段式确认，并可交给独立上下文验收器复核。
- **App-KB 自积累记忆**：同步本机应用名称；验证启动成功后沉淀非敏感别名，并累计成功反馈。同一 run 中未知中文名失败、回执列出的包名随后启动成功时，自动把该中文名写为 `learned` 别名（隐式纠正，证据闭环）。
- **经验数据面**：每次 run 结束以严格隐私白名单落盘 episode outcome 与工具结果分类，持久化分角色 token 账本；全程 observe-only，不向 actor 回注。
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
`PHONE_AGENT_IMPLICIT_ALIAS=on|off` 控制 App-KB 的证据闭环隐式纠正（默认 `on`）；无失败回执候选证据时不会猜测或写入。

```bash
.venv/bin/python main_v2.py "打开设置进入 WLAN" --device-id <serial>
.venv/bin/python main_v2.py "在飞猪查询 10 月 2 日上海飞桃仙的最低价机票" --max-steps 40
.venv/bin/python -m phone_agent.web --device-id <serial> --port 8080   # Web 控制台
.venv/bin/python main_v2.py --dream    # 手动整理本地 App-KB 与经验库
.venv/bin/python main_v2.py --dream    # 手动整理本地 App-KB
.venv/bin/python main_v2.py --rebuild-vec  # 从 episode/App-KB 全量重建语义索引
.venv/bin/pytest tests -q
```

RAG 默认 `PHONE_AGENT_MEMORY_RAG=shadow`。向量模型
`mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ` 仅在索引或非空召回第一次真正 embed 时懒加载；
`PHONE_AGENT_MEMORY_RAG=on` 当前只是保留配置档位，不启用上下文注入。

Web 控制台默认只监听 `127.0.0.1:8080`：输入任务后可实时查看手机画面、步骤时间线、任务板与终局状态，
并处理 `ask_user` / `take_over` / hard 档安全确认。Web 是可选观察层，无界面用法不变。

经验数据默认写入 `memory/experience/{events.jsonl,episodes.json}`；前者是追加式事实日志，后者是按
`run_id` 索引、可重建的物化视图。`PHONE_AGENT_EXPERIENCE=off` 可完全关闭写入；`--dream` 按
`PHONE_AGENT_EPISODE_KEEP` / `PHONE_AGENT_EPISODE_ARCHIVE_DAYS` 将旧全文折叠为无原文的类别成功率统计。

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
