# 快速开始

## 前置条件

| 条件 | 验证方式 |
|---|---|
| Python ≥ 3.10 | `python3 --version` |
| ADB 可用 | `adb version` |
| 安卓设备开启 USB 调试并被识别 | `adb devices` 列出设备 |
| 设备安装 ADBKeyboard | [APK 下载](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk) |
| OpenAI-compatible 视觉模型网关 | `curl $BASE_URL/models` |

## 安装

```bash
git clone https://github.com/tom-cat-mao/TaskWizard.git
cd TaskWizard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，填入三项必填：

```bash
PHONE_AGENT_BASE_URL="https://你的网关/v1"
PHONE_AGENT_MODEL="你的模型 id"
PHONE_AGENT_API_KEY="你的 key"
```

## 运行

```bash
# 命令行
.venv/bin/python main_v2.py "打开设置进入 WLAN"

# Web 控制台
.venv/bin/python -m phone_agent.web --port 8080
```

成功标志：命令行逐步打印工具回执，终局输出 `finished`；控制台左侧显示手机实时画面，步骤时间线逐步增长。

## 常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| `adb devices` 无设备 | USB 调试未开 / 授权未点 | 重新插线，手机上点"允许调试" |
| 网关 403 | 网关有额外访问控制 | 检查 `PHONE_AGENT_BASE_URL` 与 key；需要自定义请求头时用 `PHONE_AGENT_HTTP_HEADERS` |
| 截图失败 | 当前页面被 FLAG_SECURE 保护（登录/支付页） | 属预期行为；agents 会收到保护提示并可能请求人工接管 |
| 步数耗尽 `loop_fuse` | 任务超出保险丝 | 调大 `--max-steps`，或缩小任务范围 |
| `unknown app` | 应用未安装或名称未收录 | 先看控制台「应用库」页确认本机应用名 |

## 下一步

- [配置参考](configuration.md)：调整预算、安全模式、记忆开关
- [Web 控制台](console.md)：界面各区说明
- [安全模式](safety.md)：四档门控的选择
