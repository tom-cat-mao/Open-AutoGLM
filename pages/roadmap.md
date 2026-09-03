# 路线图

## 已完成

| 模块 | 内容 |
|---|---|
| thin-loop v2 | 工具环替代图编排；v1 LangGraph 架构已删除 |
| 原子观测 | 单生产者 + epoch 批次徽章 + mark 新鲜度闸门；dump 失败触发重试并在观测文本标注 |
| 安全 | 预警制（wary 默认）、finish 两段式、独立验收器 |
| 成本 | token 预算上限、两级 auto-compact（摘要携带记忆/能力状态）、cached_tokens 与 first-diff 计量 |
| App 名解析 | 四层解析（归一化→多路候选→先验排序→证据分型三态决策）；`exact_package_segment` 设备事实证据；拼音/嵌入/模糊永不单独自动执行；typed 默认 + legacy 可回退 |
| App-KB | 设备事实 + learned 别名（验证启动写回 + 隐式纠正）+ dream 整理与错误别名覆盖（秒退+自述签名）+ 用户纠正入口（`--learn-alias`/`--forget-alias`，最高信任级） |
| 窗口化 marks | `uiautomator dump --windows` 双模式采集（auto 回退）；窗口分组渲染 + 可操作性四档（confirmed/likely/blocked/unknown）；名额按窗口配额，顶层弹窗保底 |
| locate | 原图输入、hint-first、可选 scope 区域裁剪；连击 id 不碰撞；命中开启新观测批次 |
| 产出物 | `write_document`/`update_document` 产出单页 HTML（攻略/计划/报告）；路径由 run id 派生，控制台「产出」页预览/删除 |
| 经验档案 | episode 记录（隐私白名单）+ UsageLedger 持久化 + dream 归档 |
| 语义回想 | sqlite-vec + 本地 MLX 嵌入（全量精度）；run 结束自动增量索引；别名/档案分榜召回；Hit@1 等新口径统计 |
| 经验提炼与晋升 | `--distill` 离线蒸馏（严格 schema、证据校验）、Rule-of-3、人审 CLI、版本链撤销 |
| 经验回注 | `MEMORY_RAG=on` 时注入人审通过的经验（参考提示身份、上限、scope 过滤、可审计） |
| 能力体系 | 注册表 + apply/release 挂载层（装配 reconcile、依赖可见、run 快照审计、紧急撤销通道） |
| 运行隔离 | runner 子进程执行、控制台重启不中断任务、断线重连回放 |
| 观测加固 | FLAG_SECURE 黑屏检测、观测静置（全局 + 按动作可选 settle_ms） |
| 控制台 | 步骤时间线、钉帧回看、任务板/应用库/记忆/产出页、软停止、每轮配置覆盖 |
| 实机诊断 | 诊断 skill：证据流 + 截图落盘 + 逐步回放 HTML 报告 + 源码归因 + 录屏；解析路/召回对照/别名生命周期维度 |

## 进行中

- 经验数据积累与回想命中率观测（控制台「记忆」页，新口径 Hit@1 / 污染率）

## 下一步

| 方向 | 状态 |
|---|---|
| prefix-cache 优化（任务板版本化 + 图片批量折叠） | 评审文档已备，待批准后开工 |
| marks 可操作性进执行（op=blocked 执行前 fail-closed） | 待窗口分组真机验证后开工 |
| 产出物进语义索引（攻略可复用召回） | 待产出物积累 |
| 工作流程序性记忆 | 待定义白名单流程形态 |
| 强弱模型路由 | 按任务复杂度分流，降成本 |

演进原则：先记录、再影子验证、晋升靠人审；注入有上限、可撤销；记忆不旁路安全与验收。
