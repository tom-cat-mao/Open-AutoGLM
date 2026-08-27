# 执行文档：scoped locate（控件树引导的区域聚焦定位）

> 基线：977 passed（commit 5c9674d）。来源：pi-1 根因定案——LA（3B）对 442×960
> 压缩截图上的密集日历网格确定性退化为框住星期表头；坐标链无 bug；单框输出绕过
> fail-closed。修法共识：**裁剪由模型用已有容器 mark 指定，代码只提供变焦能力**——
> 分辨率（原图裁剪，数字 12px→30px+）与歧义消除（12 个"2"→块内 1 个）双管齐下。
> 哲学：模型决定去哪看，代码提供机制；不写"何时必须用"的行为规则。
> 全程测试绿，不 commit/push，CN/EN 同步，trace schema 只增。

## S1：action schema 增加可选 `scope_mark_id`

- `{"type":"intent","action":"locate","target_text_hint":"10月2日","scope_mark_id":"ax_5"}`
- adapter/validator：`scope_mark_id` 可选；若提供必须是**当前 registry 中存在的 mark**
  （P0#8 不变：只引用存在的 mark）；不存在/非法 → 校验失败走既有错误路径。
- 不提供时行为与现状完全一致（全屏 locate）。

## S2：locate 工具的裁剪与坐标映射

落点 `graph/tools/locate.py`（原子化流程不变）：
1. 当场截图 F（现状保留）；
2. 若有 scope：取该 mark 的 bbox（0-1000 空间）→ 转设备像素 → **外扩 padding**
   （policy 常量 `LOCATE_SCOPE_PADDING_RATIO = 0.05`，双边 5%）→  clamp 到图内 →
   从 F 裁剪出区域 R（**原图分辨率，不缩放**）；
3. LA 查询作用于 R（LA 内部仍会做它自己的 prepare/thumbnail——但输入已是高分辨率
   局部图，等效清晰度大幅提升）；记录 `scope` 信息进 outcome/trace；
4. **坐标映射回全屏 0-1000**：LA 对 R 输出的是 R 内的 0-1000 归一化框，必须仿射回
   全屏：`full = R.origin_1000 + box * R.size_1000 / 1000`（逐边计算），再注册 mark；
5. fail-closed 语义全部沿用（0 框/多框失败；mark 绑 F 的 hash）；预算/重复守卫不变；
6. scope 的截图hash/绑定：mark 仍绑 F（全帧），不是 R——保证 P0#9 语义不变。

## S3：prompt 中性描述补充（CN/EN 同步）

Locate 条目追加（中性、能力+成本风格，不写使用规定）：
- 能力：可用 `scope_mark_id`（一个包含目标的已有 Screen mark）把搜索范围缩小到该
  mark 区域内——**目标小或密集时能提高定位准确度**；
- 约束：scope 必须引用存在的 mark；裁剪只影响检测范围，返回的 mark 仍是全屏坐标。

## S4：防御闭环（轻量版）

反思/验证已能检出"tap 后未选中"。本项只做一件小事：
locate 注册、被 tap、且下一步 reflection_verdict 为 failed/partial 且明确未生效时，
该 locate mark **作废**（不再出现在后续 marks_block，grounding 拒绝并提示已作废），
防止"同一错框反复点烧预算"（本轮实证 3 次）。实现：execute/reflect 侧记录
`invalidated_mark_ids`（state，trace-only 不含隐私），registry 渲染/grounding 时过滤。
**边界**：只作废 locate_* 来源（LA 框可能错）；ax 来源不作废（结构可信）。
若实现中发现与继承/版本语义纠缠过深，可降级为：仅 trace 记录+模型可见提示，
不做硬拒绝——在报告中说明选择。

## 明确不做

- 代码自动锚匹配（query 前缀匹配 ax 文本决定裁剪区）——规则化，违反哲学
- 两遍 LA（LA 找月块再裁）
- 网格句柄合成（grid_r1c5）——scoped locate 不够用时再议
- LA 输入全局分辨率提升（PHONE_AGENT_LOCATEANYTHING_MAX_SIZE 调优留作后续实测）

## 测试要求

- S1：schema 校验（scope 存在/不存在/非法 mark id）
- S2：坐标映射回算精度（构造已知 crop+box 验证全屏坐标）；padding clamp；
  scope 裁剪后 LA 收到的图尺寸正确；mark 绑 F 不绑 R；0/多框 fail-closed 不变
- S4：作废 mark 不再渲染/不可 grounding；ax mark 不受影响
- 集成：locate(scope) → 注册 → 下一步 tap 命中映射后坐标
- 全量 .venv/bin/pytest tests/ -q 绿（977 基线+新增）

## 交付

①改动文件+内容 ②坐标映射实现与精度论证 ③S4 的实现选择（硬作废 vs 软提示）及理由
④新测试清单 ⑤最终 pytest 末尾 ⑥偏差说明。
