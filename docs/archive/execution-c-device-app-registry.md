# 执行文档 C：App 注册表设备化（设备为事实源，静态表降级为别名种子）

> 读者：pi 执行 agent。自包含任务书。
> 工作区：git worktree（分支 wt/device-app-registry），基点 commit 17a7e25。
> 测试：`PYTHONPATH=<worktree绝对路径> /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 先验证导入路径打印的是 worktree 内 phone_agent。
> 禁止：git commit/push；禁止 FakeModel 式 mock 判断测试（只写确定性单测）。

## 1. 问题（真机实证）

换 app 跑任务（携程→同程），agent step 0 就死：`apps.py` 静态表没有"同程"。
**一张静态表戴了两顶帽子**：
- `config/app_registry.py:147 resolve_term` 只查别名索引（源自 APP_PACKAGES +
  APP_ALIASES），**连直接给包名 "com.tongcheng.android" 都返回 unknown**
- `config/apps.py:396` `LaunchPolicy(allowed=frozenset(APP_PACKAGES.values()))`
  ——启动白名单就是同一张表的值
结果：不在表=不可启动、不可核验、编译提示词也看不见它。通用 agent 不该有
准入门槛。

## 2. 代码事实（已核实）

- `adb/device.py:66 get_installed_app_inventory`：`pm list packages` 拿设备实装
  包名集合（无标签）；`device_factory.py:149` 透传。目前只当过滤器用。
- `adb/device.py:~280 launch_app` → `DEFAULT_LAUNCH_TARGET_RESOLVER.resolve`
  → unknown/denied 即返回 False。
- `graph/verifier.py:520 _package_for_app_name`：前台核对也吃静态表。
- `graph/goal_compiler.py` 的 `get_app_registry_summary` 把静态表喂编译提示词。
- `LaunchTargetResolver.resolve` 状态机：resolved/unknown/ambiguous/denied/
  not_installed（ambiguous 处理已存在，可复用）。

## 3. 设计（代码提供设备事实，模型做名称映射判断）

1. **解析链改三段**（`LaunchTargetResolver.resolve` 扩展，保持原状态机）：
   a. 静态别名命中 → resolved（现状保留，静态表降级为种子）；
   b. **runtime 学习缓存命中** → resolved（见 2）；
   c. **设备 inventory 路径**：调用方（Launch 动作执行处）把
      `action.package_candidates`（模型给出的候选包名/关键词列表，新增可选字段）
      与 inventory 做大小写不敏感子串匹配：唯一命中→resolved；多命中→ambiguous
      （候选列表回给模型）；零命中→unknown。
      模型知道"同程≈tongcheng"，代码验证设备事实——这是放权划分。
   d. unknown 时 `launch_app` 返回 False 且 message 含"未在设备找到该应用，
      可从桌面图标启动"（信息性，非行为规则）。模型可随后走 UI 路径
      （回桌面→看 launcher 图标→点），**这条不需要新代码**，agent 本来就会。
2. **runtime 学习缓存**（每跑一份，不进 checkpoint/state，挂 runtime_goal_context
   同级的 runtime 容器或 configurable）：
   - Launch 成功（任何解析路径）后，把 `action.app` 正规化 → 实际包名 写入缓存；
   - resolve_term 第 b 段查它；verifier 前台核对**先查缓存再查静态表**。
3. **LaunchPolicy 新语义（用户已拍板放宽）**：
   `allowed = inventory.contains(package)`（设备装了即可启动）∪ 静态白名单；
   `observation_only` 身份仍 denied。每次启动决策落 trace（add-only 字段）。
4. **编译提示词 app 摘要**：`get_app_registry_summary` 改输出"静态种子 ∪
   本跑已学习映射 ∪（若可廉价获得）设备实装包名 Top-N（按字母序截断，约 30 条，
   附说明"设备实装应用"）"。契约只引用真实存在的应用。
5. Launch 动作 schema：`package_candidates` 可选字段（auto/json_schema 输出
   契约同步，CN/EN 提示词各一句格式说明；adapter/validator 容忍缺失）。

## 4. 步骤

1. runtime 学习缓存容器 + 写入点（Launch 成功处）+ resolve_term 第 b 段。
2. `package_candidates` 字段：adapter/validator/动作 schema/提示词（CN/EN）。
3. 解析链 c 段（inventory 子串匹配，fake inventory 可测）。
4. LaunchPolicy 新语义 + trace。
5. verifier 缓存优先 + 编译摘要改造。
6. 测试（全部确定性单测，设备边界用 fake inventory/fake 包名集合）：
   - 三段解析链：静态命中/缓存命中/candidates 唯一命中/多命中 ambiguous/
     零命中 unknown
   - 学习缓存：launch 成功后同 term 解析命中；verifier 缓存优先
   - policy：已安装=allowed、未安装=denied、observation_only=denied
   - 编译摘要含学习映射；`package_candidates` 缺失时行为不变（回归）
7. 全量测试绿，写交接节。

## 5. 硬性约束

- P0#12：设备操作仍全走 DeviceFactory→adb 层，不新增直接 ADB 调用。
- Launch 失败 fail-closed 语义不变；unknown 不执行任何启动。
- trace schema add-only；CN/EN 提示词同步。
- 禁止 mock 模型判断测试；inventory/设备边界用合成数据 fake。
- 不 commit、不 push。用 rg。
- 完成标准：全量测试绿；静态表外 app 可经 candidates/inventory 启动并学会；
  交接节含变更文件、测试数变化、遗留风险（UI 图标路径实测留待 e2e）。

## 交接

### 完成情况
- 全部步骤 1-7 完成，全量测试绿：`1216 passed`（起始 1186，净增 30 个确定性单测）。
- 解析链三段（静态别名 → 本跑学习缓存 → 设备 inventory 子串匹配）落地于 `LaunchTargetResolver.resolve`，状态机（resolved/unknown/ambiguous/denied/not_installed）不变。
- LaunchPolicy 新语义：`allows_package`/`is_allowed` 改为「静态白名单 ∪ 设备已安装」，`observation_only` 身份仍 denied；每次启动决策经 execute 节点 `launch_decision` trace 事件落盘（add-only）。
- `package_candidates` 为可选字段（adapter/validator/动作 schema/CN-EN 提示词/JSON 契约示例），缺失时行为完全不变（含回归测试）；未知 app 必须带候选包名才放行校验，零命中 fail-closed，不执行任何启动。
- 说明：worktree 缺 `models/LocateAnything-3B-4bit` 权重，`test_locate_resolution_tier.py::test_observation_fallback_path_keeps_960_tier` 在基点 commit 即因 `model_not_found` 失败（环境问题，非本任务引入）。已用未跟踪软链 `models -> /Users/bytedance/Open-AutoGLM/models` 修复，测试全绿。

### 变更文件
- 新增 `phone_agent/graph/runtime_app_learning.py`：每跑一份、不可序列化的学习缓存容器（term→package）。
- `phone_agent/config/app_registry.py`：LaunchPolicy 新语义、`LaunchTargetResolver.resolve` 三段链、candidates 子串匹配、observation_only 包级拒绝。
- `phone_agent/adb/device.py` / `phone_agent/device_factory.py`：`launch_app` 扩展 `package_candidates/learning/inventory` 参数（设备事实源，P0#12 仍全走 factory→adb）。
- `phone_agent/graph/tools/launch.py`：解析 + 失败信息（"未在设备找到该应用，可从桌面图标启动"）+ 成功后写学习缓存 + `launch_decision` trace。
- `phone_agent/graph/tools/runtime.py`：新增 app_learning / trace_emitter contextvar 注入。
- `phone_agent/graph/nodes/execute.py`：dispatch 时注入学习缓存与 trace emitter。
- `phone_agent/graph/verifier.py`：`_package_for_app_name` 缓存优先；`verify_action_outcome` 增加 `learning` 参数。
- `phone_agent/graph/nodes/reflect.py` / `acceptance.py`：verifier 调用传学习缓存。
- `phone_agent/config/apps.py`：`get_app_registry_summary` 支持学习映射 + 设备实装 Top-N（约 30 条）。
- `phone_agent/graph/goal_compiler.py` / `nodes/plan.py`：编译/plan 提示词注入学习映射 +（可廉价获得时）设备实装包名。
- `phone_agent/actions/adapter.py` / `validator.py` / `ir.py`：`package_candidates` 可选字段。
- `phone_agent/agent.py`：configurable 注入 `RuntimeAppLearningContext()`。
- `phone_agent/config/prompts_zh.py` / `prompts_en.py`：Launch 格式说明 + JSON 契约示例同步。
- 测试：新增 `tests/test_app_learning.py`（14 例）、`tests/test_app_registry.py` 扩展（10 例）、`tests/actions/test_adapter.py` 扩展（6 例）；`tests/conftest.py`/`tests/graph/test_tools.py` 合成 fake 适配。

### 测试数变化
- 起始 1186 → 完成 1216（+30：解析链 6、policy 3、学习缓存与 verifier 4、摘要 3、launch 工具 4、trace 1、adapter/validator 6、learning 容器 3）。

### 遗留风险
- UI 图标路径（unknown 时回桌面点 launcher 图标）未做真机实测，留待 e2e；代码只保证信息性 message 与不阻断。
- 编译/plan 提示词在 step 0 会多一次 `pm list packages`（约 10s 超时上限）调用，仅当 device_factory 可用时发生，异常已吞掉降级为纯静态摘要。
- 学习缓存仅在单次 run 内存活（每跑一份），跨 run 不持久化（设计如此）；同一新 app 多跑仍需重复 candidate 命中。
- 双解析（tool 层 + adb 层各一次 resolve）输入一致、结果确定，存在理论上的重复 inventory 获取，未优化。
- `models` 软链为环境修复，未跟踪，克隆新环境需自行提供权重。
