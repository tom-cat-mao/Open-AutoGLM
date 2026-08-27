# 执行文档：LA 分辨率修复 + locate 守卫补洞 + bench 回归 + repeat 规避加固

> 基线：1112 passed（commit d2e965d）。来源：探针实锤（同帧同 query：960 缩略必错、
> 全分辨率 4/4 命中）+ pi-16/pi-17 护栏发现。全程测试绿，不 commit/push。

## R1：scoped locate 路径的分辨率修复（主修）

**事实**：`_build_scope_crop`（locate.py）以原生分辨率裁剪 scope 区，但
`_prepare_image`（locateanything.py:113 调用、281-288 定义）无差别
`thumbnail((960,960))`，把裁剪收益全部抹掉（1216×2066 → 565×960，日期数字
~38px→~17px，跌破 3B 可读性下限）。

**改法**：
1. `LocateAnythingMLXProvider` 的 `max_size` 支持按调用场景分档：
   - **locate 工具路径**（scoped crop 输入）：提到 **2048**（policy 常量
     `LOCATE_LA_MAX_SIZE=2048`，env 可配 `PHONE_AGENT_LOCATE_LA_MAX_SIZE`）；
     输入短边已 ≤960 时不缩（thumbnail 本身行为，确认即可）；
   - **observation fallback 路径**（全屏自动 LA）：维持 960 控成本。
   实现方式自选（如 provider 增加 per-call max_size 参数、或 locate 路径用
   注入的单例 provider 实例时配置不同 max_size——注意 P2 单例已上线，两条路径
   共享实例，需要支持按调用覆盖 max_size），在报告里说明选择。
2. 延迟实测：对 1216×2066 的 crop 在 2048 档测一次耗时，写进交付报告
   （本地 MLX，无 API 成本，但要看延迟）。

## R2：locate 重复守卫补洞

**事实**（pi-17）：成功的 locate 跳过 reflect 直接回 plan，execute 的 locate
成功分支不写 `update_gui_memory` → tried_actions 无 locate → repeat guard 的
prior 恒 0 → "同屏同述重复拒绝"对 locate 形同虚设（prompt 已承诺该行为）。
**改法**：execute 的 locate 成功/失败分支都记录 tried_actions（复用 H4 的
`_locate_repeat_key`：(Locate, surface, hint_digest)）；验证同屏同述第二次
仍允许（阈值=2）、第三次拒绝。

## R3：bench 回归样本

把 `outputs/live-diagnosis/20260805-001452-*/traces/screenshots/step_010_locate_frame.png`
拷入 `bench/grounding/data/`（或合适位置），配 manifest 条目：
query="2026年10月1日日期格"、scope 区=[0,200.38,1000,983.33]、
期望框≈[595,713,680,746]（容差±30）；断言命中正确格子（中心落在
[571,692,714,773] 内）。bench 运行机制允许的话加 "skip thumbnail" 配置项。

## R4：repeat guard 换 id 规避加固

**事实**（pi-16）：同一物理按钮第 3 次点 ax_41 被 repeated_target_loop 拒绝，
第 4 次换 ax_42（同 bbox [622,913]）通过。
**改法**：`repeated_action_key` 的 tap 类 key 在 mark_id 之外叠加**目标几何指纹**
（bbox 中心取整到容差桶，如 20 单位桶）——同坐标不同 mark_id 也算重复；
原有 mark_id 语义保留（不同位置同 id 仍算同 key 的另一维度）。阈值不变。

## 明确不做

- 网格句柄合成（后备）；prompt 位置语言（已证伪）；验收层三件套 a+b+d（下一轮）；
  verifier 浮层误判；续命抢占缝隙

## 测试要求

- R1：locate 路径 LA 收到的图 >960 档（或短边≤960 不缩）；fallback 路径仍 960；
  阈值 env 可配
- R2：locate 成功也计入 tried_actions；同屏同述第 3 次被拒、不同描述放行
- R3：bench 样本可运行（若 bench 需真模型则标注 manual）
- R4：同坐标不同 mark_id 计入重复；不同坐标不受影响
- 全量 .venv/bin/pytest tests/ -q 绿（1112 基线+新增）

## 交付

①改动文件+内容 ②R1 实现选择与延迟实测数据 ③新测试清单 ④最终 pytest 末尾
⑤偏差说明。
