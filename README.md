# Phone Agent

基于 LangChain `create_agent` 的**薄 loop（thin-loop v2）** 手机端智能助理框架。通过视觉语言模型理解屏幕内容，每步一次模型调用，通过工具感知和操作真实 Android 设备。

> **v2 架构已上线，v1（LangGraph goal→plan→execute→reflect→acceptance 节点图）已退役。** 迁移背景与后续迭代项见 [`docs/refactor-thin-loop-v2.md`](docs/refactor-thin-loop-v2.md) 与 [`docs/future-roadmap.md`](docs/future-roadmap.md)。

## 架构

v2 是**薄 loop + 工具化**：LLM 每步一次调用，通过工具感知/操作设备；harness（middleware）只负责工具供给、安全边界、context 卫生和可观测，不做工作流路由。

```
system(极简契约) + user(task + 首次观测块含截图)
      → create_agent tool-calling loop:
          model → [safety HITL] → tool(s) → model → …
      → 结束条件: session.finished | session.takeover_reason | 模型无 tool_call | ModelCallLimit
```

- **工具（`phone_agent/v2/tools/`）** — 执行族 `tap/long_press/type_text/scroll/swipe/back/home/launch_app/wait`、感知族 `read_screen/locate`、控制族 `finish/ask_user/take_over`。执行类工具成功后自动附带 `[OBS]` 观测块。
- **Session（`phone_agent/v2/session.py`）** — 一次 run 的设备侧状态：截图、当前屏 marks、`resolve_mark`、`locate`、`finished/takeover_reason`。
- **marks-first grounding** — 执行类动作必须绑定 mark；`tap` 支持双寻址 `target_mark_id`（直达）| `target_description`（自然语言，须经 grounding 解析为唯一 mark，歧义/无匹配 fail-closed 不执行）。原始坐标仅 swipe fallback。
- **Middleware（`phone_agent/v2/middleware/`）** — 安全 HITL、历史截图滚动剪除、JSONL trace 脱敏、`ModelCallLimit`（见下）。

### Middleware 栈

| Middleware | 作用 |
|------------|------|
| `safety.py` | 危险动作（支付/密码/验证码/敏感 App）经 `HumanInTheLoopMiddleware` interrupt 等待人工 `approve`/`reject`；`ask_user`→`respond`；`take_over` 始终 interrupt。安全硬门只活在这里 |
| `images.py` | `before_model` 钩子：除最新 1 个含图消息外，其余 image content block 替换为 `[screen#n 已剪除]` 文本占位，滚动剪除历史截图 |
| `trace.py` | 每次 model/tool 调用写 JSONL（`model_call`/`tool_call`/`tool_result`/`run_end`），脱敏（>64 字截断、敏感词 redacted），不记录截图 base64（只记 screen_seq 与字节数） |
| `ModelCallLimitMiddleware` | `thread_limit=max_model_calls`，`exit_behavior="end"`，到界优雅停止 |

### 项目结构

```
phone_agent/
├── adb/                 # 设备层（截图/tap/swipe/type/launch/back/home/dump_uiautomator_xml/foreground）【保留】
├── device_factory.py    # DeviceFactory【保留】
├── grounding/           # MarkProvider 体系（accessibility tree / LocateAnything / fallback / factory）【保留】
├── config/
│   ├── policy.py        # versioned SafetyPolicyRegistry / VerificationPolicy（middleware 使用）【保留】
│   ├── app_registry.py  # AppIdentity / inventory / LaunchPolicy（launch_app 解析使用）【保留】
│   ├── apps.py / i18n.py / timing.py
│   └── redact.py        # 隐私脱敏原语（trace/grounding 共用）
└── v2/
    ├── config.py        # V2Config：env/.env/CLI 三级解析
    ├── model.py         # build_chat_model()：ChatOpenAI 工厂
    ├── session.py       # PhoneSession：设备状态 + 截图 + marks + locate
    ├── coords.py        # 0-1000 相对坐标 → 绝对像素
    ├── resolver.py      # 目标解析：mark_id | description → 唯一 mark（fail-closed）
    ├── prompts.py       # 极简 system prompt（cn/en）
    ├── agent.py         # ThinPhoneAgent：create_agent 装配 + run 循环 + HITL resume
    ├── tools/           # actuation / perception / control 工具族
    └── middleware/      # safety / images / trace
main_v2.py               # CLI 入口
tests/v2/                # v2 测试（全部 fake，无真机无 MLX）
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
pip install -e ".[dev]"
```

### ADB Keyboard 安装（Android 文本输入必需）

下载 [ADBKeyboard.apk](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk) 安装到设备，并在设置中启用。

### 运行

```bash
# 指定任务（task 是位置参数）
.venv/bin/python main_v2.py "打开美团搜索附近的火锅店" \
    --device-id <serial> --max-steps 20 \
    --model autoglm-phone-9b --base-url http://localhost:8000/v1 \
    --grounding-provider hybrid --lang cn --trace-dir .traces

# 英文模式
.venv/bin/python main_v2.py "Open Chrome browser" --lang en --base-url http://localhost:8000/v1
```

CLI 先 `load_project_env()` 加载 `.env`，再 argparse（默认 None 不覆盖 env）。退出码：success `0` / takeover `2` / max_calls `3` / error `1`。HITL 时用 `input()` 收集 approve/reject/respond。

### Python API

```python
from phone_agent.v2.config import V2Config
from phone_agent.v2.agent import ThinPhoneAgent

config = V2Config.from_env()
agent = ThinPhoneAgent(config)
result = agent.run("打开淘宝搜索无线耳机")   # hitl_handler 默认 input
print(result.success, result.reason, result.steps, result.trace_path)
```

`ThinPhoneAgent.run(task, hitl_handler=input)` 返回 `RunResult(success, reason, steps, trace_path)`：
- `success` — `session.finished` 且非 takeover
- `reason` — finish summary / takeover reason / `"max_model_calls"` / `"model_stopped"`

默认启用本地 JSONL trace，文件写入 `<trace_dir>/<run_id>.jsonl`；截图 base64、敏感文本默认不以原文写入（见 Middleware 栈 · `trace.py`）。



> 完整架构文档见 [Grounding Architecture](docs/grounding-architecture.html)

Open-AutoGLM 支持把主 VLM 的语义/意图与本地视觉定位拆开：Observation 阶段先由静态 marks、设备 marks、LocateAnything/Fake 等 `MarkProvider` 生成当前屏幕的 `MarkRegistry`；Android UiAutomator provider 同时生成 `ScreenStructure` 与 `ObjectRegistry` sidecars，用于表达 observation-local 的 list/card/input/button 等对象；主 VLM 在 JSON/tool_calls 模式可通过 `target_mark_id` 引用屏幕目标，也可在 Screen Objects block 存在唯一对象时输出 `target_object_id`、`object_role` + `ordinal` 或 strict `object_filter`。这些 selector 只是 IntentIR metadata，本地 harness 必须先在 `phone_agent/actions/grounding.py` 中把它们编译为唯一 atomic mark，再进入 canonical `ActionIR -> Validator -> Repair -> Validator -> Safety/HITL -> Executor`。

```bash
# 默认测试不需要 MLX；真实 LocateAnything 需要 Apple Silicon + Metal + mlx-vlm
# 当前仓库没有 pyproject optional extra，mlx-vlm 不在默认 requirements 中。
# 如需运行真实 provider/benchmark，请在本机 MLX 环境中安装兼容版本的 mlx-vlm。

# 可选：启用本地 LocateAnything mark provider
export PHONE_AGENT_GROUNDING_PROVIDER=locateanything
export PHONE_AGENT_LOCATEANYTHING_MODEL=models/LocateAnything-3B-4bit
# 可选：覆盖 LocateAnything 输入图最长边；默认 960，1280 可回滚到更高质量/更慢路径
export PHONE_AGENT_LOCATEANYTHING_MAX_SIZE=960

# 可选：优先使用 Android UiAutomator accessibility tree，失败时再调用 LocateAnything
export PHONE_AGENT_GROUNDING_PROVIDER=hybrid
export PHONE_AGENT_ACCESSIBILITY_MAX_MARKS=80
# 可选：开启 LocateAnything 视觉结构 sidecar；默认 off，不改变稳定 fallback 行为
export PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE=target  # off | target | screen
# 可选：直接把 accessibility tree marks 注入 MarkRegistry，不启用小模型 provider
export PHONE_AGENT_ACCESSIBILITY_MARKS=true
```

LocateAnything provider 会先读取当前完整截图，再按最长边 `max_size` 等比例缩小后送入模型；默认 `max_size=960`，这是基于本地 benchmark 在速度和 bbox 一致性之间的折中。运行时可通过 config `locateanything_max_size`（优先）或 `grounding_max_size` 覆盖，也可通过环境变量 `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE`（优先）或 `PHONE_AGENT_GROUNDING_MAX_SIZE` 灰度/回滚；非法或非正整数会回落默认值。

Android accessibility tree 通过 `adb exec-out uiautomator dump /dev/tty` 获取当前 UI XML；`dump_uiautomator_xml()` 只从 stdout 提取闭合 `<hierarchy>`，不会接受 stderr 中的 XML 片段。parser 解析可交互节点的 `bounds/text/content-desc/resource-id/class/clickable/focusable/focused/checkable/checked/scrollable/enabled/visible-to-user` 等 dump-supported 字段，并统一转换为 0-1000 MarkRegistry marks；signed/negative bounds 与带空白的 bounds 会归一化到 0-1000 或被拒绝。XML 控制字符只做清洗，未转义 `&`、截断 hierarchy、缺失闭合标签或结构错乱会以 `accessibility_xml_parse_error` fail-closed，不做语义修复。provider 还会输出 trace-safe `ScreenStructure`，`plan_node` 再从当前 `ScreenStructure + MarkRegistry` 构建 `ObjectRegistry`，把 Screen Objects block 放在 Screen Marks block 之前。Screen Objects prompt 只展示 object type/role/list/ordinal、primary mark 和 hash/lineage evidence，不展示 raw title/text；默认上限为 lists 5、objects 30、总 block 4000 chars。object_id/list_id/ordinal 只在当前 observation 内有效，reobserve 后必须重新选择或重新验证。

`PHONE_AGENT_GROUNDING_PROVIDER=hybrid` 是推荐路径：默认由 `build_mark_providers()` 构建 `AccessibilityTreeProvider -> LocateAnythingMLXProvider` 的 `FallbackMarkProvider`。如果 tree marks 与 provider hint 没有弱匹配、tree 为空、不可用或没有候选 marks，fallback 会继续调用 LocateAnything；缺少 dump callback 或显式 `skip_accessibility_provider=true` 时仍可降级到 LocateAnything，但 `metadata.hybrid_factory` 与 synthetic `fallback_chain` skip row 会记录 `accessibility_dump_callback_missing` / `skip_accessibility_provider`。`PHONE_AGENT_ACCESSIBILITY_MARKS=true` 会把 accessibility marks 作为设备 base marks 注入；如果它与 `hybrid` 同时开启，direct/base accessibility mark 注入会被跳过，accessibility marks 由 hybrid provider 链统一生成和 gating，避免绕过 hint-aware fallback。

当前诊断字段会进入脱敏 observation/trace summary，便于回答“tree-first 为什么没有跑”或“为什么没有点击到候选”。`AccessibilityTreeProvider` 自身的 `metadata.parse_summary` 只包含 `xml_status`、XML node/mark/structure 计数、bounds 解析失败计数、零面积过滤计数和可交互候选计数；默认 hybrid wrapper 当前主要透出 `metadata.fallback_chain[]` 与 `metadata.hybrid_factory`，还没有把 child provider 的 `parse_summary` 汇总到顶层 hybrid result，这是下一步需要补齐的诊断闭环。`fallback_chain[]` 只应包含 provider、success、failure_code、mark/candidate/structure count、usable、skip_reason、latency；`hybrid_factory` 只记录 hybrid 是否启用、accessibility child 是否构建、skip reason 与 provider_order。审查发现当前 sanitizer 仍依赖 regex-redaction + 截断，尚未对 provider/failure_code/xml_status/provider_order 等 code 字段实施严格枚举或 safe-identifier allowlist；在接入 custom provider 前需要继续收紧。

LocateAnything 结构化视觉 mark 默认关闭，可通过 `locateanything_structure_mode` 或 `PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE=target|screen` 灰度开启。`target` 只在 LocateAnything 被调用时把当前 hint 的 bbox 生成 `structure_kind=visual` sidecar；`screen` 使用 bounded category prompts 生成屏幕级视觉候选，并受 `locateanything_max_visual_candidates`、`locateanything_visual_category_budget`、`locateanything_max_structure_calls` 限制。视觉 sidecar 不伪装成 Android accessibility tree：它只保存 bbox、role/type guess、visual order、confidence tier、source/provider 和 bounded `sensitivity_tags`，不会生成 clickable/focusable/resource id 等平台真值。Observation 会按 accessibility first、visual second 构建 composite `screen_structures`，trace/checkpoint 只保留 kind/source/tier/count/digest summary。Screen Objects prompt 会标出 `source=visual tier=weak eligible=true|false`；resolver 对 visual object 的 `executable_selector` 做硬校验，低置信、多候选、重叠或未授权 selector 均 fail-closed，不回退为 raw bbox tap。

LocateAnything prompt 必须保持官方/库侧 chat template：代码通过 `mlx_vlm.prompt_utils.apply_chat_template(..., num_images=1)` 生成最终 prompt，fallback 仅为旧版 `mlx-vlm` 保留 `<image-0>` 前缀。默认 instruction 保持短句：`Locate the region that matches the following description: ...`。可选 `PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` / config `locateanything_context_max_chars` 只允许追加一行 bounded `Context:`，默认 `0` 表示不注入额外 context，避免小模型被长 prompt 干扰。

安全边界：屏幕目标点击类动作的唯一可执行边界仍是 atomic `target_mark_id`。object selector 只允许作为编译前 IntentIR metadata；adapter 只做字段白名单、类型归一化和 strict `object_filter` 校验，不读取 ObjectRegistry、不解析 ordinal、不生成 compiled mark。`object_filter` 只允许 flat JSON object，key 限于 `object_type`、`role`、`source`、`list_id`、`title_hash_prefix`、`text_hash_prefix`、`resource_id_hash_prefix`、`lineage_hash_prefix`，值必须是 1-64 字符 string，hash prefix 为 6-16 位 hex；禁止 raw title/text、regex、array、nested object、provider/backend/device 字段。resolver 的 object failure codes 为 `object_registry_missing`、`object_stale`、`unknown_object`、`ordinal_out_of_range`、`object_ambiguous`、`object_without_mark`、`mark_stale`、`mark_low_confidence`、`visual_object_ambiguous`、`visual_object_not_executable`、`visual_structure_missing`，均 fail-closed/reobserve/replan/takeover，不回退为主 VLM 直接坐标 Tap。敏感 object evidence 不是 grounding failure：payment/privacy/delete/permission 会返回带 confirmation message 的 canonical Tap，login/password/OTP 会返回 canonical `Take_over`，继续由 Safety Gate 路由到 HITL。

`target_text_hint` / 目标描述只能作为受控、bounded 的本地 MarkProvider hint，不能直接生成 ActionIR；本地 LocateAnything/Fake 可在内存中使用 raw hint 做 query-conditioned grounding，但 raw hint 不写入 trace、checkpoint、prompt marks block、eval JSON 或报告。LocateAnything/Fake/OCR/UIAutomator/SoM 只能生成 marks；未知 mark、缺失 registry、provider 缺失、stale/hash mismatch、低置信、bad bbox、多候选歧义等会 fail-closed 为 `error_layer=grounding`，不会回退为主 VLM 直接坐标 Tap。trace/eval 只记录 provider、mark id、bbox/center、screen/hash、latency、failure code、candidate_count、provider 诊断 summary（当前包括 accessibility provider 的 `parse_summary`、hybrid wrapper 的 `fallback_chain` / `hybrid_factory`）、`screen_structure_summary`、`object_registry_summary`、compiled mark 与脱敏 hint/object summary，不记录原始截图、raw XML、raw hint、raw target text 或 raw private title。若未来接入远程 grounding provider，raw hint 必须显式 opt-in，否则默认使用脱敏 hint。

### Grounding Benchmark

正式 benchmark 代码位于 `bench/grounding/`，用于评估 `screenshot + target description -> bbox` 的 provider 能力。benchmark 仍使用 description-to-bbox 任务形态；runtime 不把 benchmark target description 当执行协议，而是把 provider 输出转成 mark candidates 后再由 VLM 选择 `target_mark_id`。

核心文件：

| 文件 | 作用 |
|------|------|
| `bench/grounding/datasets.py` | post-training JSONL 读取、0-1 bbox 转 0-1000 bbox、clean/trusted 过滤、balanced sampling |
| `bench/grounding/scoring.py` | CI 可运行的纯 Python scoring primitives |
| `bench/grounding/reporting.py` | prediction enrichment、summary、按 target type / area bucket 分组 |
| `bench/grounding/run_locateanything.py` | LocateAnything 正式 benchmark runner |
| `bench/grounding/score_predictions.py` | 固定 manifest 后离线复评 predictions |

推荐先固定 manifest，再用同一 manifest 比较不同模型。当前 LocateAnything 主 suite：

```bash
.venv/bin/python -m bench.grounding.run_locateanything \
  --post-training-data /Users/bytedance/post-training/data/grounding_os_atlas_aw_mobile/raw.jsonl \
  --model /Users/bytedance/Open-AutoGLM/models/LocateAnything-3B-4bit \
  --limit 1000 \
  --seed 46 \
  --sampling balanced \
  --per-type-cap 120 \
  --per-area-cap 400 \
  --clean \
  --exclude-weak-types \
  --trusted-types-only \
  --min-area-ratio 0.0005 \
  --max-size 960 \
  --manifest-output bench_output/grounding/aw_mobile_clean_trusted_1000_manifest.json \
  --output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_predictions.jsonl \
  --summary-output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_summary.json
```

MLX/Metal 需要可访问 GPU 的 macOS arm64 环境。若在沙箱、headless 或虚拟化环境中出现 `No Metal device available`，需要在非沙箱 shell 中运行同一命令。`bench_output/` 是本地结果目录，默认不作为源码提交对象。

离线复评：

```bash
.venv/bin/python -m bench.grounding.score_predictions \
  --manifest bench_output/grounding/aw_mobile_clean_trusted_1000_manifest.json \
  --predictions bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_predictions.jsonl \
  --output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_scored.jsonl \
  --summary-output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_rescore_summary.json
```

关键指标包括 `parse_success_rate`、`success_rate`、`center_hit_rate`、`acc_iou_0_3`、`acc_iou_0_5`、`mean_iou`、`required_recall`、`precision`、`latency_ms_p50/p95`，summary 会同时输出 `by_target_type`、`by_area_bucket` 与 `parse_errors`。

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

运行 `.venv/bin/python main_v2.py --help` 查看可用参数。运行时使用统一 AppRegistry 解析别名；前台 package/activity observation、设备安装状态和启动授权是三个独立事实，未知前台 package 不会被猜测成系统桌面。

## 远程调试

```bash
# WiFi 连接
adb connect 192.168.1.100:5555

# 指定设备运行
.venv/bin/python main_v2.py "打开抖音刷视频" --device-id 192.168.1.100:5555 --base-url http://localhost:8000/v1
```

## 环境变量

配置只经 `V2Config` 三级解析：CLI 覆盖 > shell env > `.env`（`PHONE_AGENT_` 前缀，不覆盖已存在 shell env）> 默认。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PHONE_AGENT_BASE_URL` | 模型 API 地址 | `http://localhost:8000/v1` |
| `PHONE_AGENT_MODEL` | 模型名称 | `autoglm-phone-9b` |
| `PHONE_AGENT_API_KEY` | API Key | `EMPTY` |
| `PHONE_AGENT_MODEL_TIMEOUT` | 模型请求超时（秒） | `180` |
| `PHONE_AGENT_MODEL_MAX_RETRIES` | 模型请求重试次数 | `2` |
| `PHONE_AGENT_MAX_STEPS` | 最大模型调用数（→ `ModelCallLimit`） | `20` |
| `PHONE_AGENT_DEVICE_ID` | 设备 ID | 自动检测 |
| `PHONE_AGENT_LANG` | 语言（`cn` / `en`） | `cn` |
| `PHONE_AGENT_TRACE_DIR` | JSONL trace 输出目录 | `.traces` |
| `PHONE_AGENT_TRACE` | 是否启用 trace | `true` |
| `PHONE_AGENT_TEMPERATURE` / `PHONE_AGENT_TOP_P` / `PHONE_AGENT_FREQUENCY_PENALTY` | 采样参数（float，非法值 raise） | 未设置 |
| `PHONE_AGENT_USER_AGENT` / `PHONE_AGENT_HTTP_HEADERS`（`k=v;k2=v2`） | 自定义请求头 | 默认 UA |
| `PHONE_AGENT_CF_ACCESS_CLIENT_ID` / `_SECRET` | Cloudflare Access（必须成对，否则 raise） | 未设置 |
| `PHONE_AGENT_GROUNDING_PROVIDER` | grounding provider：`hybrid` / `locateanything` / `accessibility` / `fake` / `off`（别名：`accessibility_tree`/`uiautomator`→accessibility，`locateanything_mlx`/`mlx`→locateanything，`accessibility_locateanything`/`uiautomator_locateanything`→hybrid） | `hybrid` |
| `PHONE_AGENT_ACCESSIBILITY_TIMEOUT` | `uiautomator dump` 超时时间（秒） | `3.0` |
| `PHONE_AGENT_ACCESSIBILITY_MAX_MARKS` | 每屏最多保留的 accessibility marks 数量 | `80` |
| `PHONE_AGENT_LOCATEANYTHING_MODEL` | LocateAnything-3B-4bit 模型路径 | `models/LocateAnything-3B-4bit` |
| `PHONE_AGENT_LOCATEANYTHING_MAX_SIZE` | LocateAnything 输入图最长边 | `960` |
| `PHONE_AGENT_MEMORY_MODEL` / `PHONE_AGENT_VERIFIER_MODEL` | 预留（本轮只读取不实现） | 未设置 |

## 开发

```bash
# 安装开发依赖
.venv/bin/pip install -e ".[dev]"

# 运行 v2 + grounding 测试（全部 fake，无真机无 MLX）
.venv/bin/pytest tests/v2 tests/grounding -q

# LangChain 网关兼容 spike（tool_calls + image content block + 采样参数）
.venv/bin/python scripts/spike_langchain_compat.py
```

### Agent 开发工作流配置

给 AI coding agent 的项目约定集中在仓库根 `AGENTS.md`（P0 硬性约束 + 工作约定）与 `CLAUDE.md`（Claude Code 引导）；OMX 规划/执行 workflow skills 在 `.codex/skills/`（如 `ralplan`、`autopilot`）。架构与阶段状态以本 README、`docs/refactor-thin-loop-v2.md`、`docs/future-roadmap.md` 为准。项目命令必须优先使用 `.venv/bin/python`、`.venv/bin/pytest`、`.venv/bin/pip`。

## 常见问题

**设备未找到**：`adb kill-server && adb start-server && adb devices`，检查 USB 调试和数据线。

**能打开应用但无法点击**：开启「USB 调试（安全设置）」。

**文本输入不工作**：确认 ADB Keyboard 已安装并启用。

**截图无效或黑屏**：敏感页面（支付/银行）可能触发 Android secure screenshot block。运行时会 fail-closed，返回 `error_layer="grounding"`、`error_code="secure_screenshot_blocked"` 或 `screenshot_unavailable`，不会把黑图继续发给模型。
