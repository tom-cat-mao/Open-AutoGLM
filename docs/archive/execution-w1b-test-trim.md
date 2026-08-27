# 执行文档 W1-B：测试套件精修

> 基线：1013 passed（commit 3d39681）。来源：双独立测试审计（kimi×deepseek 高度收敛）。
> 原则：**精修不砍保障**——P0 守卫测试一个不动；删的是元测试与重复覆盖；
> 补的是真缺口。全程 .venv/bin/pytest tests/ -q 绿，不 commit/push。
> 注意：W1-A 并行在改 acceptance 相关测试，本伦**不动** tests/graph/test_acceptance.py
> 与 finish-gate 相关文件。

## B1：元测试移出默认套件（~19 个）

以下不测 phone_agent 生产行为，移到 `tests_meta/`（不被 `pytest tests/` 收集）：
- `tests/trae/`（6，断言 .codex 文档）
- `tests/test_packaging_docs.py`（4，断言 README/roadmap 文案）
- `tests/test_live_diagnosis_rules.py`（6，断言技能规则）
- `tests/characterization/`（3，phase0 遗留）
在 pytest 配置或 CI 说明里注明 tests_meta 的按需运行方式。

## B2：重复覆盖收敛

- **text_dsl 防复活 8→2**：保留 model 层 1 个 + plan 端到端 1 个，删 test_client.py
  多余变体与 test_plan_reflect.py:585 附近重复（P0#2 由 adapter/validator 常规
  测试兜底）；
- **reducer 语义 9→4**：保留 test_state.py 直测 3 个 + test_plan_reflect.py 端到端 1 个；
  删 test_p4_continuity.py 的 think-wrapper 变体（含 :249 逐字节断言——临时迁移物）；
- **CN/EN 双生合并**：failure_recovery_map / directive filter / goal prompt block 等
  ~10 对改 `@pytest.mark.parametrize("lang", ["zh","en"])`；
- **fallback 双文件重叠**：test_grounding_provider.py 中与 test_fallback_usability.py
  重复的 fallback 分支用例压缩（保留行为面更全的那份）。

## B3：脆弱测试加固（少量）

- test_grounding_provider.py:1319/1403：mlx 内部 kwargs 精确断言 → 放宽为行为断言
  （输出格式/关键字段存在性）；
- 全局 patch `pathlib.Path.exists` 的 6 处改为窄目标 patch（打在被测模块的引用上）。

## B4：补真缺口（P0 薄点，双审计一致发现）

- **坐标转换边界**：`coords.py convert_relative_to_absolute` 一个参数化测试
  （0 / 1000 / 越界 clamp / 取整 / 非方形宽高比），放 tests/graph/test_tools.py 或
  就近合适文件；
- **图片剥离负例**：多历史图、图在中间位置时被剥离、当前图保留（
  `remove_images_from_message` 路径，放 test_p4_continuity.py 或 test_plan_reflect.py）。

## 明确不做

- 任何 P0 守卫域删减（edges/ActionIR/脱敏/fail-closed/finish-gate 全保留）
- test_plan_reflect.py 的参数化重构（属巨函数拆分债，后续轮）
- 生产代码零改动（本轮纯 tests/ 与 tests_meta/）

## 验收标准

- `.venv/bin/pytest tests/ -q` 绿；用例数降至约 870-920 区间（裁 ~100-140，含 B4 新增）；
- `pytest tests_meta/ -q` 可独立运行（若选择保留可运行）。

## 交付

①改动文件清单 ②各类删除/合并数量与理由 ③B4 新测试内容 ④最终 pytest 末尾
（tests/ 与 tests_meta/ 各一）⑤偏差说明。
