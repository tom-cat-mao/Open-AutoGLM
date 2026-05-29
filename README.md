# Phone Agent

基于 LangGraph 的手机端智能助理框架。通过视觉语言模型理解屏幕内容，自动规划并执行操作流程。

## 架构

核心采用 **Plan-Execute-Reflect** 三节点拓扑，由 LangGraph StateGraph 驱动：

```
START → plan → execute → [confirm|takeover|reflect|replan|end]
                         ├─ confirm → after_interrupt → [reflect|end]
                         ├─ takeover → after_interrupt → [reflect|end]
                         ├─ reflect → should_continue → [replan|end]
                         ├─ replan → plan
                         └─ end → END
```

- **plan** — 截图 + 模型推理 + 解析 action
- **execute** — `dispatch_tool()` 路由到 `@tool` 函数执行动作
- **reflect** — 再截图 + 模型判断动作是否生效
- **confirm / takeover** — LangGraph `interrupt()` 实现可恢复的 Human-in-the-Loop

### 项目结构

```
phone_agent/
├── agent.py                     # PhoneAgent 入口，使用 StateGraph
├── device_factory.py            # 设备工厂
├── model/
│   └── client.py                # ModelClient (OpenAI 兼容)
├── actions/
│   └── handler.py               # parse_action(), ActionResult, do(), finish()
├── adb/                         # Android 设备控制
├── config/
│   ├── apps.py                  # 应用包名映射
│   ├── prompts.py / prompts_zh.py / prompts_en.py
│   └── timing.py
└── graph/                       # LangGraph 核心
    ├── state.py                 # AgentState TypedDict
    ├── builder.py               # create_agent_graph()
    ├── edges.py                 # 条件边路由
    ├── nodes/
    │   ├── plan.py
    │   ├── execute.py
    │   ├── reflect.py
    │   ├── confirm.py           # interrupt() 确认
    │   └── takeover.py          # interrupt() 接管
    └── tools/                   # @tool 函数
        ├── __init__.py          # dispatch_tool, get_tool_map
        ├── coords.py            # 坐标转换
        ├── tap.py / type_text.py / swipe.py / launch.py
        ├── navigation.py        # back / home
        ├── press.py             # double_tap / long_press
        ├── wait.py
        └── misc.py              # note / call_api / interact
```

## 快速开始

### 环境要求

- Python 3.10+
- Android 设备（7.0+），开启 USB 调试
- ADB 已安装并可用
- AutoGLM 模型服务（本地部署或 API）

### 安装

```bash
git clone git@github.com:tom-cat-mao/Open-AutoGLM.git
cd Open-AutoGLM
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### ADB Keyboard 安装（Android 文本输入必需）

下载 [ADBKeyboard.apk](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk) 安装到设备，并在设置中启用。

### 运行

```bash
# 交互模式
python main.py --base-url http://localhost:8000/v1 --model "autoglm-phone-9b"

# 指定任务
python main.py --base-url http://localhost:8000/v1 "打开美团搜索附近的火锅店"

# 使用智谱 API
python main.py --base-url https://open.bigmodel.cn/api/paas/v4 --model "autoglm-phone" --apikey "your-key"

# 英文模式
python main.py --lang en --base-url http://localhost:8000/v1 "Open Chrome browser"
```

### Python API

```python
from phone_agent import PhoneAgent
from phone_agent.model import ModelConfig

agent = PhoneAgent(model_config=ModelConfig(
    base_url="http://localhost:8000/v1",
    model_name="autoglm-phone-9b",
))

result = agent.run("打开淘宝搜索无线耳机")
print(result)
```

## 模型部署

### 选项 A：第三方 API

| 服务商 | base-url | model |
|--------|----------|-------|
| 智谱 BigModel | `https://open.bigmodel.cn/api/paas/v4` | `autoglm-phone` |
| ModelScope | `https://api-inference.modelscope.cn/v1` | `ZhipuAI/AutoGLM-Phone-9B` |

### 选项 B：本地部署

需要 NVIDIA GPU（建议 24GB+ 显存）：

```bash
# vLLM
python3 -m vllm.entrypoints.openai.api_server \
  --served-model-name autoglm-phone-9b \
  --allowed-local-media-path / \
  --mm-encoder-tp-mode data \
  --mm_processor_cache_type shm \
  --mm_processor_kwargs '{"max_pixels":5000000}' \
  --max-model-len 25480 \
  --chat-template-content-format string \
  --limit-mm-per-prompt '{"image":10}' \
  --model zai-org/AutoGLM-Phone-9B \
  --port 8000

# SGLang
python3 -m sglang.launch_server \
  --model-path zai-org/AutoGLM-Phone-9B \
  --served-model-name autoglm-phone-9b \
  --context-length 25480 \
  --mm-enable-dp-encoder \
  --mm-process-config '{"image":{"max_pixels":5000000}}' \
  --port 8000
```

部署后用检查脚本验证：

```bash
python scripts/check_deployment_cn.py --base-url http://localhost:8000/v1 --model autoglm-phone-9b
```

## 支持的操作

| 操作 | 说明 |
|------|------|
| `Launch` | 启动应用 |
| `Tap` | 点击坐标 |
| `Type` / `Type_Name` | 输入文本 |
| `Swipe` | 滑动屏幕 |
| `Back` | 返回 |
| `Home` | 回到桌面 |
| `Double Tap` | 双击 |
| `Long Press` | 长按 |
| `Wait` | 等待加载 |
| `Take_over` | 人工接管（interrupt） |

运行 `python main.py --list-apps` 查看支持的应用列表。

## 远程调试

```bash
# WiFi 连接
adb connect 192.168.1.100:5555

# 指定设备运行
python main.py --device-id 192.168.1.100:5555 --base-url http://localhost:8000/v1 "打开抖音刷视频"
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PHONE_AGENT_BASE_URL` | 模型 API 地址 | `http://localhost:8000/v1` |
| `PHONE_AGENT_MODEL` | 模型名称 | `autoglm-phone-9b` |
| `PHONE_AGENT_API_KEY` | API Key | `EMPTY` |
| `PHONE_AGENT_MAX_STEPS` | 最大步数 | `100` |
| `PHONE_AGENT_DEVICE_ID` | 设备 ID | 自动检测 |
| `PHONE_AGENT_LANG` | 语言 | `cn` |

## 开发

```bash
# 安装开发依赖
.venv/bin/pip install -e ".[dev]"

# 运行测试
.venv/bin/pytest tests/ -v

# 运行 graph 测试
.venv/bin/pytest tests/graph -v
```

## 常见问题

**设备未找到**：`adb kill-server && adb start-server && adb devices`，检查 USB 调试和数据线。

**能打开应用但无法点击**：开启「USB 调试（安全设置）」。

**文本输入不工作**：确认 ADB Keyboard 已安装并启用。

**截图黑屏**：敏感页面（支付/银行）的正常现象，系统会自动处理。
