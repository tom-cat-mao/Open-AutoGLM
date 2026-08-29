# Open-AutoGLM

LLM 驱动的安卓手机操作 Agent：看一眼屏幕、想一步、动一下，带安全预警与自积累记忆。

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776AB?logo=python&logoColor=white)](setup.py)
[![CI](https://img.shields.io/badge/CI-GitHub_Actions-lightgrey?logo=githubactions)](https://github.com/tom-cat-mao/Open-AutoGLM/actions)

Open-AutoGLM 采用 thin-loop v2：模型每轮观察真实设备、决定一个工具调用并执行一步；harness 只负责工具、安全边界、上下文卫生和可观测性，不替模型编排工作流。

## Demo

<!-- TODO(owner): 将下方截图替换为真实任务的 GIF 录屏。 -->
![Open-AutoGLM Android demo](resources/screenshot-20251209-181423.png)

## Features

- **Marks-first grounding**：执行动作绑定当前屏幕元素，过期、歧义或未命中的目标会 fail-closed。
- **安全预警制**：风险动作先返回警告与选项，模型明确确认后才执行（confirm-to-execute）。
- **可信完成**：TaskDoc 任务板与流程线持续记录进度；finish 两段式确认，并可交给独立上下文验收器复核。
- **App-KB 自积累记忆**：同步本机应用名称与别名，在多次运行间持续完善应用知识。
- **长任务可控**：token 预算限制成本，两级 auto-compact 在接近上下文窗口时保留关键状态。

## 快速开始

需要 Python 3.10+；Android 7.0+ 设备需开启 USB 调试、能被 `adb devices` 识别，并安装 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # 填写模型网关、模型名与 API Key
```

```bash
.venv/bin/python main_v2.py "打开设置进入 WLAN" --device-id <serial>
.venv/bin/python main_v2.py "在飞猪查询 10 月 2 日上海飞桃仙的最低价机票" --max-steps 40
.venv/bin/python -m phone_agent.web --device-id <serial> --port 8080   # Web 控制台
.venv/bin/python main_v2.py --dream    # 手动整理本地 App-KB
.venv/bin/pytest tests -q
```

Web 控制台默认只监听 `127.0.0.1:8080`：输入任务后可实时查看手机画面、步骤时间线、任务板与终局状态，
并处理 `ask_user` / `take_over` / hard 档安全确认。Web 是可选观察层，无界面用法不变。

## 文档

| 主题 | 入口 |
|---|---|
| 配置 | [完整配置说明](docs/configuration.md) · [`.env` 模板](.env.example) |
| App-KB 设计 | [docs/app-kb-memory-design.md](docs/app-kb-memory-design.md) |
| 架构状态与路线图 | [docs/future-roadmap.md](docs/future-roadmap.md) |
| Agent 开发约定 | [AGENTS.md](AGENTS.md) |

## Contributing

欢迎提交 Issue 和 Pull Request；开始编码前请先阅读 [AGENTS.md](AGENTS.md) 的开发契约与 P0 约束。

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。
