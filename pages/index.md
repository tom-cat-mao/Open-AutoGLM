# TaskWizard

**LLM 驱动的安卓手机操作 Agent**：看一眼屏幕、想一步、动一下——带安全预警与自积累记忆。

![TaskWizard Web 控制台](assets/console.png)

## 它是什么

TaskWizard 采用 **thin-loop v2** 架构：模型每轮观察真实设备、决定一个工具调用并执行一步；harness（运行壳）只负责工具、安全边界、上下文卫生和可观测性，**不替模型编排工作流**。

一句话：把"怎么做事"的判断完全交给模型，把"做事的规矩"全部钉死在壳里。

## 特性

- **Marks-first grounding** — 执行动作绑定当前屏幕元素；过期、歧义或未命中的目标一律 fail-closed（宁可报错，不乱点）
- **高精度视觉定位** — `locate` 默认原分辨率截图输入；歧义时可用容器/锚点 mark 圈定区域做原图裁剪定位
- **安全预警制** — 风险动作先返回警告与选项，模型明确确认后才执行（confirm-to-execute）
- **可信完成** — TaskDoc 任务板 + 流程线持续记录进度；finish 两段式确认 + 独立验收器复核
- **App-KB 自积累记忆** — 同步本机应用、沉淀别名、按设备隔离，越用越准
- **长任务可控** — token 预算限制成本；两级 auto-compact 在接近上下文窗口时保留关键状态
- **实时控制台** — NiceGUI Web 界面：手机画面、步骤时间线、任务板、应用库、软停止

## 30 秒预览

```bash
.venv/bin/python main_v2.py "打开设置进入 WLAN" --device-id <serial>
.venv/bin/python -m phone_agent.web --port 8080   # 打开 http://127.0.0.1:8080
```

[立即开始 :octicons-arrow-right-24:](quickstart.md){ .md-button .md-button--primary }
[看架构 :octicons-arrow-right-24:](architecture.md){ .md-button }

## License

Apache License 2.0
