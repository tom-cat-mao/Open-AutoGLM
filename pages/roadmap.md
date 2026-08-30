# 路线图

## 已完成

| 模块 | 内容 |
|---|---|
| thin-loop v2 | 工具环替代图编排；v1 LangGraph 架构已删除 |
| 原子观测 | 单生产者 + epoch 批次徽章 + mark 新鲜度闸门 |
| 安全 | 预警制（wary 默认）、finish 两段式、独立验收器 |
| 成本 | token 预算硬上限、两级 auto-compact |
| App-KB | 设备事实 + learned/user 别名 + dream 整理 + 隐式纠正 |
| locate | 原图输入、hint-first、可选 scope 区域裁剪 |
| 经验档案 | episode 记录（隐私白名单）+ UsageLedger 持久化 + dream 归档 |
| 语义回想 | sqlite-vec + 本地 MLX 嵌入 + shadow 模式自验证 |
| 能力注册表 | 能力独立开关、依赖可见、run 快照可审计 |
| 观测加固 | FLAG_SECURE 黑屏检测、观测静置（全局 + 按动作可选 settle_ms） |
| 控制台 | 步骤时间线、钉帧回看、任务板/应用库/记忆页、软停止、每轮配置覆盖 |

## 进行中

- 影子回想的数据积累与命中率观测（控制台「记忆」页）

## 下一步

| 方向 | 前置条件 |
|---|---|
| 经验提炼与晋升（dream v2：LLM 蒸馏 → Rule-of-3 → 人审 → 有界注入） | shadow 命中率达标、episode 积累足够样本 |
| prefix-cache 动静块排序 | 需 P0 评审（涉及 TaskDoc 注入语义） |
| compact 摘要携带记忆状态 | 提炼机制上线后 |
| workflow 程序性记忆 | 白名单流程定义完成后 |
| 强弱模型路由 | 按任务复杂度分流，降成本 |

演进原则：先记录、再影子验证、最后才改变模型行为；一切可配置；记忆不旁路安全与验收；注入前必须人工确认。
