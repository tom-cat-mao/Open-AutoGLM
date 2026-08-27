# 执行文档：locate 必选 scope（含区间形态）+ LA 单例复用 + 重编译防抖 + S4 校准

> 基线：1046 passed（commit 82884df）。来源：20260804-162648 运行取证
> （pi-9/pi-10/pi-11 三份独立分析）。全程测试绿，不 commit/push，CN/EN 同步，
> trace schema 只增。

## P1：scope 改为必选，两种形态

**实证**：三次 locate 未传 scope 全偏左列（LA 锚定跨月完整文本列）；容器/月标题
锚点在 marks 里一直存在（ax_5 ListView、ax_9"2026年10月"、ax_23"2026年11月"）。
决策：scope 从可选改为**必选**——工具契约="先指区域，再指东西"。

1. **形态 A（现有）**：`scope_mark_id`——单容器。
2. **形态 B（新增）**：`scope_start_mark_id` + `scope_end_mark_id`——区间裁剪：
   区域 = [start.top, end.top) × 容器/全屏宽（无 end 时到 start 所在容器底部）；
   padding/clamp/映射沿用现有 ScopeCrop 机制。
3. **validator**：三种形态必须满足其一（scope_mark_id 或 start/end 对）；
   引用的 mark 必须存在（现有校验沿用）；只给 start 不给 end 合法（到容器底）。
4. **失败反馈植入提示**（信息非指令）：0 框/多框失败的消息追加
   "可调整/扩大 scope 区域后重试"（CN/EN）。
5. **prompt 更新**（CN/EN，中性）：locate 的用法描述改为"指定搜索区域（一个包含
   目标的容器 mark，或两个锚点 mark 夹出的区间）+ 目标描述"；说明区域越紧
   准确度越高；删掉"可选"措辞。

## P2：LA 模型单例复用（RAM 修复）

**实证**：factory 每步新建 provider（plan.py:877、observation_capture.py:168、
locate.py:412），实例级懒加载永远落空，每次推理重载 ~2GB；无释放出口。

1. `agent.py _build_config`：构造一次 `LocateAnythingMLXProvider` 实例，经
   `configurable["locate_provider"]` 注入（通道已存在）；plan/observation/locate
   三路全部走注入实例（`build_mark_providers` 的 hybrid 分支也改用注入实例——
   注意 accessibility 子 provider 依赖每步 lambda，仍需新建，只复用 LA 实例）。
2. 懒加载保持现状（首次推理才 load）——**这就是"用到才 load，否则复用"**。
3. `LocateAnythingMLXProvider.unload()`：置空 _model/_processor +
   `mlx.core.clear_cache()`（延迟 import）+ gc。
4. `main.py` run 结束 finally 调 unload（含异常路径）。
5. 测试替身语义不变（cfg 注入优先于新建，测试不受影响）。

## P3：W2 重编译防抖

**实证**：8 步内 5 次 stall→重编译，每次契约换名 → 证据锁存清零 → 阶段打回。
1. `stage_stall_recompile` 判据收紧：**刚重编译后的前 K 个窗口为免疫期**（不累计
   stall）；stall 计数在重编译发生时清零重启。
2. （若实现简单）重编译时把旧契约 criterion 名→新名的映射尝试保留
   （按 description 相似度/顺序对齐），证据账本可迁移 latch；复杂则不做，报告说明。

## P4：S4 错框作废触发条件校准

**实证**：本轮三次 locate 错框，reflect 判 partial 但无 coordinate_or_tap_offset
（读数正确识别了"没选中"），作废未触发。
- 校准 `_newly_invalidated_locate_marks`：locate_* tap 后 verdict∈{failed, partial}
  且（错位 OR **目标状态未变化**——如反思明确表述未选中/未生效）→ 作废；
- 保持只针对 locate_* 来源。

## P5：e2e 环境隔离（skill 小改）

- `.agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py`：
  跑前可选 `--reset-app <package>`（默认携程任务时 `adb shell pm clear ctrip.android.view`），
  消除"最近搜索"残留污染（本轮 10-01 假象的根因）。`.trae` 旧副本同步。

## 明确不做

- 日历专用正则/网格合成（区间 scope 是通用解）
- verifier 浮层→列表 wrong_page 误判深挖（下轮）
- 不可满足标准的编译约束（下轮）

## 测试要求

- P1：必选校验（无 scope 拒绝）；区间裁剪几何（start.top→end.top、无 end 到底）；
  区间 mark 不存在/异屏 fail-closed；反馈文案含提示
- P2：多次推理共享同一 load（mock load 计 1 次）；unload 后复用会重载；
  main teardown 调用
- P3：免疫期不计 stall；重编译后计数重启
- P4：新触发形态作废；ax 不受影响
- 全量绿（1046 基线+新增）

## 交付

①改动文件+内容 ②P1 区间几何实现 ③P2 注入点与测试兼容说明 ④新测试清单
⑤最终 pytest 末尾 ⑥偏差说明。
