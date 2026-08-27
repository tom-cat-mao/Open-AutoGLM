# 执行文档：屏幕身份解耦 + 毒标记治理（D1/D2/D3 + A-lite）

> 基线：956 passed（commits e65da64 + a8ff471）。来源：pi-24 架构审查（trace×代码双证）。
> 核心洞察：**screen_id 的拓扑指纹包含了模型生成物（la_*/provider marks），provider
> 每轮重跑的抖动让"同一物理页面"被判成换屏**——日历页 ax 树两次观察逐一同（digest
> d26bb9486e538b53 不变），翻转全部来自 la 集合变化。这污染所有 screen_id 消费方
> （grounding 校验、verifier page_changed、证据新鲜度、visited 去重…）。
> 全程测试绿，不 commit/push，CN/EN 同步，trace schema 只增不改。

## D1：screen_id 拓扑指纹与 mark 集合解耦（核心）

**事实**：`build_observation` 最终 screen_id（observation.py:673）用 `marks=all_marks`
（base ax + provider marks）算 topology_digest；provisional（:607）只用 base marks。
la_* 每轮 churn → topology 雪崩 → screen_id 翻转。

**改法**：最终 screen_id 的拓扑组件**只取 base（ax）marks**——provider/locate 标记
不进入屏幕身份。实现选择（任选，说明理由）：
- 最终 screen_id 直接复用 provisional 的拓扑 digest；或
- `build_screen_id` 调用处传 base marks 而非 all_marks。
注意 marks.py:213-215 三组件（semantic|p-hash|topology）精确拼接的公式不变，
只换 topology 的输入。**实证依据**：s9/s10 ax digest 恒等、真换屏（s12 重进、tap 后）
digest 必翻转，区分度完美。

**连带检查**（不改语义，只确认）：verifier.py:335-345 的 before（带 marks 拓扑）vs
after（无 marks）天然不等的旧不一致，D1 后是否自动消解，在报告里说明。

## D2：locate 继承门控放宽（只放宽合并，不放宽执行）

**现状**：`_inherit_locate_marks` → `with_extra_marks` 的异屏门控（marks.py:160）
要求 screen_id 精确相等。D1 后大多数情况已相等，但 ax 树真抖动（52→53 节点）时
仍会被误丢。

**改法**：locate 继承专用门控——允许合并当且仅当：
`semantic_screen_id 相同 ∧ (ax 结构 digest 相同 ∨ perceptual_hash 汉明距离 ≤ 阈值)`。
- 只用于 `_inherit_locate_marks` 路径（可给 with_extra_marks 加参数或加专用方法）；
- 继承成功的 mark **重绑到新 registry 的 screen_id**；
- **grounding.py 执行校验（451-494 三重校验、458 短路）一律不动**——执行侧保持
  fail-closed；真换屏（semantic 不同或结构 digest 不同且 p-hash 不近似）仍丢弃；
- p-hash 阈值放 policy.py（如 `LOCATE_INHERIT_PHASH_MAX_DISTANCE = 8`），注意本仓
  p-hash 是 8×8 均值哈希、对浅色大板块页面有退化前科（00ffff...），阈值要保守，
  并在报告里评估退化风险。

## D3：la_* 毒标记治理

**事实**（pi-24 实证）：la_1_1 的 text_summary="2026年10月2日日期" 是模型上一步
locate 查询词的**逐字回显**（plan.py:820-825 hints 取 action_parsed.target_text_hint
→ locateanything.py:106 `text_summary=description`）。模型看到自己的话被当成屏幕
内容回显 → 盲目信任 → 误点。

**改法**：locateanything provider 产 mark 时 text_summary **不再等于查询词**——
改为 None 或固定的中性来源标注（如 "visual-match"），让 la_* 标记在 marks_block 里
不再冒充"已确认目标"。prompt 渲染、evidence_summary 等下游同步适配。

## A-lite：locate 成功后下一轮不跑自动 LA provider

**改法**：plan 构建 provider hints 时，若上一步 action 是成功的 Locate
（state 可查），本轮 observation **跳过 LocateAnything provider**（churn 源头+
毒标记竞争同时消失）。其余 observation 流程不变（仍截新图、仍解析 ax）。
下一轮恢复正常。实现落点：plan.py 调 build_observation 前的 provider 选择处。

## 明确不做

- B 方案（整体放宽 screen_id / grounding 458 短路松动）——安全回退，禁止
- 跳过整轮 observation 的完整 A 方案（D1+D2 已覆盖，留着以后需要再说）
- verifier `_observation_page_changed` 改绑结构 digest（评估后可附带，非必须）
- 日历网格合成 mark（F5，下一轮）、auto-LA 全面收缩（D3 是第一步）

## 测试要求

- D1：同物理页不同 la 集合 → screen_id 相同；真换屏 → 不同；topology 输入只含 base
- D2：ax 抖动（节点+1）下 locate 继承存活；semantic 不同必丢；结构不同+p-hash 不近似
  必丢；继承 mark 重绑新 screen_id；grounding 执行校验行为零变化（用现有测试锁定）
- D3：la_* text_summary 不再含查询词原文；marks_block 渲染不炸
- A-lite：locate 成功后的 observation 无 la_* 新标记；其余轮次正常
- 全量 .venv/bin/pytest tests/ -q 绿（956 基线+新增）

## 交付

①改动文件+内容 ②D1 实现选择与 verifier 一致性评估 ③D2 门控确切条件+阈值依据
④新测试清单 ⑤最终 pytest 末尾 ⑥偏差说明。
