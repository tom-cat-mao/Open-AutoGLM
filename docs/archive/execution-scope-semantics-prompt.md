# 执行文档：locate scope 语义补全（context 层修复）

> 基线：1097 passed（commit de517e8）。来源：pi-13 取证——日历失败 2/3 是
> planner 传错 scope（选了标题行/错误周行），LA 在目标位于裁剪区内时零失误。
> 根因：prompt 只说了"包含目标"，没解释 scope 的**空间语义**（模型把 scope 当
> 语义相关元素选，把"2026年10月"标题当容器）。性质：**这是工具参数文档补全，
> 不是行为规则**——解释参数控制什么，选择权仍在模型。
> 全程测试绿，不 commit/push，CN/EN 严格同步。

## C1：Locate prompt 段落重写（prompts_zh.py / prompts_en.py）

现有 Locate 条目保留（能力/成本/预算/失败语义），scope 部分补全为完整语义说明：

1. **scope 是什么**：LA 只在指定区域内搜索（该区域会被从截图裁出单独检测）；
   目标不在区域内 = 必然失败。
2. **空间包含 ≠ 语义相关**：区域必须在画面上【空间地包含】目标本身；
   文字标签/标题不是容器（如"2026年10月"标题里没有任何日期格子）。
3. **松紧原则**：区域越紧越准；拿不准就选更大的容器（最大容器≈全屏，合法）。
4. **区间形态的教学**（关键新增）：当目标位于两个文字锚点之间时，用
   `scope_start_mark_id`/`scope_end_mark_id` 夹出区间——**例：日历中目标日期在
   "X月"标题与下一月标题之间，用两个月份标题做区间即可圈出整个月块，
   无需知道目标在第几行**。
5. 保持中性：不写"必须/应该何时用"的行为规定；语义说明 + 一个日历区间示例
   （示例是接口教学，保留）。

## C2：locate 失败反馈消息补语义（tools/locate.py `_scoped_failure_message` 等）

0 框/多框/scope_crop_failed 的反馈消息在现有"可调整/扩大 scope 区域后重试"基础上
补充关键一问（CN/EN，随 state.lang）：
- "确认 scope 是否【空间包含】目标本身——文字标签不是容器；若目标在某两个
  文字锚点之间，可用 start/end 区间锚定。"

## C3：grounding retry 反馈同步（如适用）

plan 侧 grounding retry 反馈（`_build_grounding_retry_messages`）若涉及 locate
相关失败，保持现有机制，不额外加内容；仅确保 C2 的消息能经 action_outcome_summary
到达下一轮 plan（已有通道，验证即可）。

## 明确不做

- 网格句柄合成、LA 分辨率调优、verifier 误判、编译可观察性约束
- 不改任何 scope 校验逻辑（必选/区间/存在性已上线，本轮纯语义层）

## 测试要求

- prompt 快照/关键词测试同步（CN/EN 各含：空间包含语义、标题≠容器、区间示例）
- 失败消息测试：0 框/多框消息含新语义提示（CN/EN 各一）
- CN/EN 内容对等检查（关键句逐一对应）
- 全量 .venv/bin/pytest tests/ -q 绿（1097 基线）

## 交付

①改动文件+diff 摘要 ②CN/EN 最终 prompt 文本（Locate 段全文）③失败消息全文
④测试清单 ⑤最终 pytest 末尾 ⑥偏差说明。
