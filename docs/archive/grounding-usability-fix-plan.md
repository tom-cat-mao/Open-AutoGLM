# Grounding Usability 收紧计划（R1-R4）

> 来源：20260803 携程机票真机运行（日历点不中 10月1日）双方并行调查。
> 根因链：①控件树不暴露自定义绘制的日格子（只有整周行容器）②`_result_is_usable`
> 被垃圾分词短路 → LocateAnything 在日历步骤零调用 ③fail-closed 禁止裸坐标，
> 模型无法几何补偿。本计划修 ②（让视觉 provider 在需要时真正被用到）。
> 范围：**只做 R1-R4**。容器内相对坐标 tap 属增强项，单独立项，不在本次。

## R1 分词净化（`phone_agent/grounding/fallback.py::_tokenize_hint`）

现状：CJK/alnum 归并 + 全滑窗，产出 "20"/"02"/"6年"/"年1" 等 2 字符垃圾 token，
任何含日期/数字的屏幕必中 → usable 判定形同虚设。

规则：
- 纯数字 token：长度 <4 丢弃（"2026" 保留，"20"/"02" 杀掉）
- 单字符 token 一律丢弃（"1" 在任何屏幕都中）
- CJK 2 字滑窗保留（"携程"/"吉隆坡"是有效词），但仅作兜底——显著性判定走 R2 的长词
- 全字符串/长 CJK 短语（按非 alnum·非 CJK 切分）始终保留为候选显著词

## R2 匹配分级（`_result_is_usable` 重写）

- **显著词**定义：CJK 连续段 ≥4 字，或 casefold 后 alnum ≥4 字符（"吉隆坡"、"10月1日"、"chester117"、"2026"）
- usable 需要**显著词命中**：某显著词被某 mark 的 role/text_summary/source 包含
- 父级语境命中不算：月标题 "2026年10月" 不含显著词 "10月1日" → 不命中（R1 后垃圾 token 已无法兜底）
- 无显著词命中 → **not usable → fallback 链继续**（给 LocateAnything 机会）

## R3 容器排除

命中落在非目标 mark 上不算 usable：
- bbox 宽 ≥90% 屏宽（0-1000 归一化空间即 ≥900）且 role ∈ {ListView, RecyclerView, FrameLayout, View, ViewGroup, HorizontalScrollView, ScrollView} → 容器，排除
- 纯展示标签（role=TextView 且非可执行目标语义的命中）降级为弱命中：只有显著词命中且无任何其他命中时才可救回（实现时可先简单排除，留 TODO）
- 新增 `_is_target_like_mark(mark)` 辅助函数承载该判定

## R4 兜底不变

hint 完全无词（无显著词也无垃圾词）→ 保持现状：有 mark 即 usable。浏览类步骤零影响。

## 运行效果（日历场景）

```
accessibility 29 marks（行容器+月标题）
→ R2 显著词 "10月1日" 无命中 + R3 容器排除 → usable=False
→ 不短路 → LocateAnything 执行（~11s）→ 格子级 bbox marks 合并返回
→ 模型获得 "1日" 格子的真实 target_mark_id
LA 失败时：仍返回 accessibility marks（usable=false 标注进 fallback_chain），不破坏现有行为
```

## 成本护栏

LA 7-13s/次：只有"模型在找具体目标且树里无地址"时才触发（R2+R3 双失败才 fallback）；
树覆盖良好的场景（按钮/输入框精确文本）R2 直接通过，零开销。evals/诊断命令行现有
`--locateanything-*` 参数不变。

## 测试要求

- tokenize：垃圾数字 token 消失、"2026"/"吉隆坡" 保留、单字符丢弃
- usable 矩阵：日历场景（任务 hint+行容器/月标题）→ not usable；按钮含 "吉隆坡" → usable；
  宽容器含显著词 → not usable；无 hint → usable
- hybrid 集成：fake accessibility（弱）+ fake LA provider → LA 被调用且 marks 合并
- 全量 `.venv/bin/pytest tests/ -q` 绿（基线 844）
- 不 commit/push（验收后由主 Agent 处理）
