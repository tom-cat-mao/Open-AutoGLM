# Grounding 小 VLM Benchmark 设计

## 目标

- 评估 `phone_agent/grounding/` 中 description → bbox 的小 VLM grounding 能力。
- 覆盖三类正确性：
  - **元素是否都被框到**：required UI 元素召回率。
  - **坐标是否正确**：IoU、中心点是否落入 GT、点击可用性。
  - **文字理解是否正确**：UI 文本/OCR/目标描述理解的字段级 F1。
- 保持坐标协议与项目一致：所有 bbox 均为 `[x1, y1, x2, y2]`，归一化到 `0-1000`。

## 现状

- `scoring.py`：纯 Python 评分核心，CI 可运行，不依赖 MLX/ADB。
- `datasets.py`：post-training raw JSONL → Open-AutoGLM manifest，统一转换到 0-1000 bbox。
- `run_locateanything.py`：正式 LocateAnything benchmark runner，输出 prediction JSONL 和 summary JSON。
- `score_predictions.py`：离线复评已有 predictions，便于固定 manifest 后复现实验。
- `bench_grounding.py`：保留为本地 smoke/preprocess 实验，不作为正式报告入口。

## 推荐数据集

- **首选 GUI Grounding 数据集**
  - ScreenSpot / ScreenSpot-Pro：移动端、网页、桌面 UI 元素定位，常用 Acc@IoU / center-hit。
  - Android in the Wild / AndroidControl / AndroidWorld 轨迹：适合从真实手机任务中抽 screenshot + target element。
- **补充通用视觉 grounding**
  - RefCOCO / RefCOCO+ / RefCOCOg：指代表达定位，适合测 natural language grounding 基础能力。
  - Visual7W / Flickr30k Entities / PhraseCut：短语 grounding、区域召回。
- **补充 OCR/文字理解**
  - OCR-VQA、TextCaps、DocVQA、UI 截图人工标注：用于检测“找包含某文字的按钮/输入框”。
- **本项目自建集**
  - 从真实 App 截图开始小规模人工标注：设置、微信、浏览器、支付/登录等高频页面。
  - 每个 case 标注 `required` / `optional` 元素，避免只评一个 bbox。

## Case Schema

```json
{
  "id": "settings_wifi_row",
  "image": "images/settings.png",
  "prompt": "Locate the Wi-Fi settings row",
  "elements": [
    {"id": "wifi_row", "bbox": [42, 118, 958, 190], "required": true, "text": "Wi-Fi"},
    {"id": "wifi_icon", "bbox": [58, 132, 96, 172], "required": false}
  ],
  "expected_text": {"target_label": "Wi-Fi"},
  "tags": ["android", "settings", "text_target"]
}
```

## Prediction Schema

```json
{
  "case_id": "settings_wifi_row",
  "boxes": [[40, 116, 960, 192]],
  "text": {"target_label": "Wi-Fi"},
  "latency_ms": 830
}
```

## LocateAnything Benchmark

当前正式入口：

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

MLX/Metal 在 macOS arm64 上运行，沙箱环境可能没有 Metal device；如果出现 `No Metal device available`，需要在非沙箱执行同一命令。

固定 manifest 后可离线复评：

```bash
.venv/bin/python -m bench.grounding.score_predictions \
  --manifest bench_output/grounding/aw_mobile_clean_trusted_1000_manifest.json \
  --predictions bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_predictions.jsonl \
  --output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_scored.jsonl \
  --summary-output bench_output/grounding/locateanything_aw_mobile_clean_trusted_1000_rescore_summary.json
```

### post-training 数据适配

`/Users/bytedance/post-training/data/grounding_os_atlas_aw_mobile/raw.jsonl` 字段：

- `image_path`：截图路径。
- `instruction`：目标描述，映射为 benchmark `prompt`。
- `target_type`：控件类型，进入 `metadata.target_type` 和分组指标。
- `bbox`：0-1 normalized xyxy，导入时转换为 0-1000 xyxy。

clean/trusted suite 默认过滤：

- bbox 缺失、退化、面积过小。
- instruction 为空或超过 300 字符。
- 弱类型：`android.webkit.WebView`、`android.view.ViewGroup`、`android.view.View`、`android.widget.RelativeLayout`。
- trusted 类型外的样本，避免把噪声标注当作模型错误。

### 推荐 suite

- **Smoke**：`--limit 30 --sampling random`，验证模型加载、解析、输出路径。
- **Random 300**：`--limit 300 --sampling random`，估计全量噪声分布下表现。
- **Clean Balanced 300**：`--limit 300 --sampling balanced --clean --exclude-weak-types`，看清洗后各类型表现。
- **Clean Trusted Balanced 1000**：上面的正式命令，作为当前 LocateAnything 主 benchmark。

summary JSON 包含：

- `overall`：解析成功率、provider success、center hit、Acc@IoU0.3/0.5、mean IoU、required recall、precision、延迟 P50/P95。
- `by_target_type`：按 Android 控件类型切分。
- `by_area_bucket`：按 `tiny/small/medium/large` 切分。
- `parse_errors`：fail-closed 原因分布。

## 核心指标

- **Required Recall**：required GT 被匹配数量 / required GT 总数。
  - 这是“需要的元素是否都被框选到”的主指标。
  - required recall < 1 直接标记 `missing_required_element`。
- **Precision / False Positive**：预测框中被 GT 接受的比例；多框误检会扣分。
- **Mean IoU**：匹配框平均 IoU，默认阈值 `IoU >= 0.5`。
- **Center Hit / Click Accuracy**：预测框中心点是否落入 GT。
  - 小 UI 控件很窄，IoU 偏低但中心可点时仍应有部分 credit。
  - 当前 fallback：`center_hit && IoU >= 0.1` 也算匹配。
- **Text Score**：`expected_text` 字段级 token F1。
  - exact match = 1。
  - 大小写、标点、连续空白不敏感。
- **Latency**：不进主评分，单独统计 P50/P95/均值。

## 默认总分

- `score = 0.55 * localization + 0.25 * coverage + 0.20 * text`
- `localization = 0.7 * mean_iou + 0.3 * click_accuracy`
- `coverage = 0.8 * required_recall + 0.2 * optional_recall`
- 推荐发布指标时同时报：
  - `Score`
  - `Required Recall`
  - `Acc@IoU0.5`
  - `Center Hit Rate`
  - `Text F1`
  - `P50/P95 Latency`

## Benchmark 分层

- **Level 0：解析与安全**
  - bbox 格式合法、范围 0-1000、顺序正确、单/多候选 fail-closed 行为。
- **Level 1：单元素定位**
  - 一个 prompt 对一个 required 元素；看 IoU/center-hit。
- **Level 2：组合元素覆盖**
  - 一个目标由 row/icon/text 等多个 required/optional 元素组成；看 required recall。
- **Level 3：文字理解**
  - “包含 XX 文案的按钮/输入框/列表项”；看 text F1 + bbox。
- **Level 4：真实任务回放**
  - 使用 trace 中的 screenshot + target_text_hint，测 grounding 是否能支撑最终 action。

## 落地建议

- 先建 `bench/grounding/data/manifest.sample.json` 格式，后续真实数据不要提交隐私截图。
- runner 输出统一 prediction JSON，再调用 `score_grounding_case()` 批量汇总。
- `bench_grounding.py` 可保留为本地模型/预处理实验；正式报告使用本目录评分口径。
