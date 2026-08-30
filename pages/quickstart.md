# 快速开始

## 前置条件

- Python 3.10+
- Android 7.0+ 设备：开启 USB 调试、能被 `adb devices` 识别
- 设备上安装 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)（文本输入通道）
- 一个 OpenAI-compatible 模型网关（视觉多模态模型）

## 安装

```bash
git clone https://github.com/tom-cat-mao/TaskWizard.git
cd TaskWizard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填入你的网关地址、模型名、API Key
```

`.env` 最少配置三项：

```bash
PHONE_AGENT_BASE_URL="https://你的网关/v1"
PHONE_AGENT_MODEL="你的模型id"
PHONE_AGENT_API_KEY="你的key"
```

!!! tip "网关在 Cloudflare 后面？"
    保留默认的浏览器风格 User-Agent 即可（代码已内置）；若网关有 Cloudflare Access，需成对配置 `PHONE_AGENT_CF_ACCESS_CLIENT_ID` / `PHONE_AGENT_CF_ACCESS_CLIENT_SECRET`。

## 跑第一个任务

```bash
# 命令行
.venv/bin/python main_v2.py "打开设置进入 WLAN" --device-id <serial>

# 或 Web 控制台（推荐，可视化）
.venv/bin/python -m phone_agent.web --device-id <serial> --port 8080
# 打开 http://127.0.0.1:8080
```

更多例子：

```bash
.venv/bin/python main_v2.py "在飞猪查询 10 月 2 日上海飞桃仙的最低价机票" --max-steps 40
.venv/bin/python main_v2.py --dream        # 手动整理本地 App-KB 记忆
.venv/bin/pytest tests -q                  # 运行测试套件
```

## 可选：本地视觉定位模型

`locate` 工具用本地 LocateAnything 模型（MLX）做深度视觉定位：

```bash
PHONE_AGENT_LOCATEANYTHING_MODEL="models/LocateAnything-3B-4bit"
```

不配也能跑——grounding 默认 `hybrid` 模式优先走 accessibility tree，视觉定位是兜底。

## 下一步

- [配置参考](configuration.md)：全部 `PHONE_AGENT_*` 键
- [Web 控制台](console.md)：实时监看与操控
- [安全模式](safety.md)：wary / hard / reviewer / off 怎么选
