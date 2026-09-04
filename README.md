# TaskWizard

LLM 驱动的安卓手机操作 Agent：看一眼屏幕、想一步、动一下，带安全预警与自积累记忆。

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776AB?logo=python&logoColor=white)](setup.py)
[![CI](https://img.shields.io/badge/CI-GitHub_Actions-lightgrey?logo=githubactions)](https://github.com/tom-cat-mao/TaskWizard/actions)

TaskWizard 采用 thin-loop v2：模型每轮观察真实设备、决定一个工具调用并执行一步；harness 只负责工具、安全边界、上下文卫生和可观测性，不替模型编排工作流。

## Demo

![TaskWizard 控制台：真实运行中的步骤时间线与接管终态](pages/assets/console-run.png)

## Features

- **Marks-first grounding**：执行动作绑定当前屏幕元素，过期、歧义或未命中的目标会 fail-closed。
- **高精度视觉定位**：`locate` 默认把原分辨率截图交给视觉定位器，优先利用“外观 + 可见文字 + 相对位置”提示；歧义时可用当前批次的容器或上下锚点 mark 做原图 scope 裁剪，结果仿射回映射为全屏 mark。
- **观测加固**：默认在每次原子观测前静置 300ms 并识别 `FLAG_SECURE` 均匀黑屏；执行工具可用 `settle_ms` 替代全局静置（搜索/提交/开页建议 1500–2500ms）。
- **安全预警制**：风险动作先返回警告与选项，模型明确确认后才执行（confirm-to-execute）。
- **可信完成**：TaskDoc 任务板与流程线持续记录进度；finish 两段式确认，并可交给独立上下文验收器复核。
- **HTML 产出物**：攻略、计划、比价报告等成果可由 `write_document` / `update_document` 写成本 run 的自包含单页 HTML；路径由 run id 派生，内容限 256 KiB，模型不能指定任意文件。
- **App-KB 自积累记忆**：同步本机应用名称；验证启动成功后沉淀非敏感别名，并累计成功反馈。同一 run 中未知中文名失败、回执列出的包名随后启动成功时，自动把该中文名写为 `learned` 别名（隐式纠正，证据闭环）。dream 还能从最小化工具事件识别“启动 A→1–2 步内明确自述开错并退出→成功启动 B”，删除错误 learned 映射并写入 B；用户可通过 CLI 写入最高信任的 `user` 别名或忘记 user/learned 别名。
- **类型化 App 名解析**：统一做 NFKC/大小写/空白归一化，再从 exact、lexical、pinyin、embedding 四路生成候选；候选携带 `match_type` / `authority`，默认按证据类型与三态代价决策，`rank_score` 只参与排序和分差。歧义时只返回排序后的 top-K，装机事实与 launch policy 仍独立 fail-closed。
- **经验数据面**：每次 run 结束以固定 schema 落盘 episode outcome 与工具结果分类（字符串原文照存，工具事件另含模型逐步自述的 intent/note；schema 之外的内容直接丢弃），持久化分角色 token 账本；数据采集全程 observe-only，并审计本轮实际注入的 lesson id。
- **经验提炼与晋升**：离线 `--distill` 从证据充足的 episode 组生成 proposed lesson；Rule-of-3 通过后仍须人工 approve。仅 `PHONE_AGENT_MEMORY_RAG=on` 时，approved lesson 才在 run 开局以“参考、非规则”的 L0 Mirror 受控注入。
- **RAG shadow 召回**：sqlite-vec + FTS5 混合检索历史 episode 与 App 别名；默认只写 trace 并按实际启动应用统计命中率，绝不注入 actor 上下文。
- **能力装配层**：十个内建能力通过稳定 `cap_id` 和 `apply/release` 生命周期挂入中间件、工具、提示块、run hooks 或 CLI 命令；注册表按 id/档位 diff 做 reconcile，依赖缺失保持 pending，卸载后不残留能力产物。每次 run 的能力快照仍写入 trace 与 episode。
- **长任务可控**：token 预算限制成本，两级 auto-compact 在接近上下文窗口时保留关键状态。

## 快速开始

需要 Python 3.10+；Android 7.0+ 设备需开启 USB 调试、能被 `adb devices` 识别，并安装 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # 填写模型网关、模型名与 API Key
```

`PHONE_AGENT_LOCATE_MAX_SIZE=0` 保持 `locate` 原图输入；低配机器可设为正整数限制最长边。`PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS` 限制 intent/可见文字提示长度，`PHONE_AGENT_SCOPE_PADDING_RATIO` 控制可选 scope 裁剪的边缘扩展比例。
`PHONE_AGENT_MARKS_WINDOWED=auto|on|off`（默认 `auto`）控制窗口感知 marks（纯展示层）：`auto` 先试 `uiautomator dump --windows`，设备不支持则回退旧的单根 dump；`on` 强制 `--windows`（不支持则报错可见）；`off` 保持旧平铺渲染。模型看到的 marks 会按窗口分组、标注可操作性四档（`confirmed`/`likely`/`blocked`/`unknown`，启发式窗口不会给 `blocked`）和稀疏语义容器路径（`path=`，最多 3 层）；分组渲染带 `windowed/v1 source=<src>` 徽标、窗口头补 `active`/`focus` 裸标记，头部在解析器给出 `total_candidates` 时显示 `marks (K/total)`。这是纯展示升级——mark id 徽章、`resolve_mark`、工具执行、安全门、折叠与 `locate` 一寸不动，`op=blocked` 本包只展示不拦截。accessibility dump 若为瞬时失败（timeout/解析错/provider 错，以及 `on` 模式设备不支持）会作为观测不稳定重试一次；截图有效但 marks dump 仍失败时提交一帧带标注的零 mark 观测（OBS 头 `marks (0) [accessibility:<code>]`），dump 失败不再被当成“无控件”。`locate` 命中开新批次（epoch+1、旧 marks 失效、只把命中 mark 铸入新批），铸造 id 掺单调序号避免同批碰撞。
`PHONE_AGENT_OBSERVE_SETTLE_MS=300` 控制观测前静置（`0` 关闭），`PHONE_AGENT_BLACK_SCREEN_DETECT=on|off` 控制保护页黑屏检测；动作参数 `settle_ms` 会 clamp 到 0–5000ms，并替代而非叠加全局值。
`PHONE_AGENT_IMPLICIT_ALIAS=on|off` 控制 App-KB 的证据闭环隐式纠正（默认 `on`）；无失败回执候选证据时不会猜测或写入。
App 名解析默认 `PHONE_AGENT_RESOLVER_DECISION_MODE=typed`：lexical / pinyin / embedding 仍由 `PHONE_AGENT_RESOLVER_LEXICAL`、`PHONE_AGENT_RESOLVER_PINYIN`、`PHONE_AGENT_RESOLVER_EMBED` 控制，但候选会先标注 `match_type` 与 `authority`，再做三态决策。`exact_alias` / `exact_label` / `exact_package` / `exact_package_segment` / `registered_containment` 可自动 resolved；`fuzzy` / `pinyin_full` / `pinyin_initials` / `embedding` 只用于澄清，不会单独自动启动。`rank_score = RESOLVER_W_SIM*sim + RESOLVER_W_PRIOR*prior` 仅作排序、margin、回执和 trace 信号；`PHONE_AGENT_RESOLVER_TYPED_MARGIN=0.08` 控制强证据 top2 分差。包名分段由 `PHONE_AGENT_RESOLVER_PACKAGE_SEGMENT_MIN_LEN` 与 `PHONE_AGENT_RESOLVER_PACKAGE_SEGMENT_STOPWORDS` 控制，按 `.`/`_`/`-`/camelCase 切完整段，不做任意 substring。需要旧阈值行为时可设 `PHONE_AGENT_RESOLVER_DECISION_MODE=legacy`，此时 `PHONE_AGENT_RESOLVER_MIN_SCORE` / `PHONE_AGENT_RESOLVER_MARGIN` 恢复旧语义。授权边界不变，resolved 后仍须过装机事实和 launch policy。
`PHONE_AGENT_ALIAS_OVERWRITE=on|off` 控制 dream 的错误别名覆盖（默认 `on`）；`PHONE_AGENT_ALIAS_OVERWRITE_NOTES` 是逗号分隔的明确自述词表，默认 `开错,不对,不是,错了,wrong app`。事件只保存命中的词，不保存完整模型 note。
`PHONE_AGENT_DELIVERABLE=on|off` 控制 `deliverable` 能力（默认 `on`）；开启后模型可把文档成果写入 `PHONE_AGENT_DELIVERABLE_DIR/<run_id>.html`（默认 `outputs/deliverables`），只能创建或全量更新本 run 的 UTF-8 单页 HTML，大小上限 256 KiB。成功路径会进入 episode 的可选 `deliverable_path` 字段，生产 trace 只记录 HTML 字节数，不记录正文。

```bash
.venv/bin/python main_v2.py "打开设置进入 WLAN" --device-id <serial>
.venv/bin/python main_v2.py "在飞猪查询 10 月 2 日上海飞桃仙的最低价机票" --max-steps 40
.venv/bin/python -m phone_agent.web --device-id <serial> --port 8080   # Web 控制台
.venv/bin/python main_v2.py --dream    # 手动整理本地 App-KB 与经验库
.venv/bin/python main_v2.py --learn-alias "小红书=com.xingin.xhs"  # 写入最高信任 user 别名
.venv/bin/python main_v2.py --forget-alias "小红书"                # 删除该名称的 user/learned 别名
.venv/bin/python main_v2.py --rebuild-vec  # 从 episode/App-KB 全量重建语义索引
.venv/bin/python main_v2.py --distill     # 离线蒸馏，只写 proposed lesson
.venv/bin/python main_v2.py --review-lessons
.venv/bin/python main_v2.py --approve-lesson <lesson-id>
.venv/bin/python main_v2.py --revoke-lesson <lesson-id> "原因"
.venv/bin/python main_v2.py --supersede-lesson <lesson-id> "修订后的规则"
.venv/bin/pytest tests -q
```

RAG 默认 `PHONE_AGENT_MEMORY_RAG=shadow`。每次 run 结束会把通过
`PHONE_AGENT_INDEX_MIN_STEPS=2` 质量闸门的 episode 与本 run 变更的 App-KB alias
增量写入 sqlite-vec；dream 负责从 JSON 权威源补漏并清除失效项。App alias 通过静态 registry
与 learned/user 名称做确定性 mention 匹配，episode 独立走默认 top-1 语义榜（门槛 0.50）。向量模型
`Qwen/Qwen3-Embedding-0.6B` 仅在索引或非空 episode 召回第一次真正 embed 时懒加载；
`PHONE_AGENT_MEMORY_RAG=on` 会在 run 开局一次性注入人审 approved lesson，默认上限为
`PHONE_AGENT_LESSON_INJECT_MAX=3` 条、`PHONE_AGENT_LESSON_INJECT_TOKENS=800` 估算 token；
设备 scope 必须匹配，开局未知 app 时不会选择 app 级 lesson。提示明确标为历史参考而非规则，
lesson 视图缺失或损坏时 fail-open。`shadow` 仍只做 trace 召回与命中统计，`off` 不召回也不注入。
召回统计以 evaluations 为 run 级统一分母，输出 `hit_at_1`、`contaminated_run_rate`、
`conditional_hit_rate`、`precision_at_k` / `recall_at_k` 与 package 级 precision/recall；
旧 `false_hits` / `false_hit_rate` 字段暂时保留给现有 Web 控制台读取。
runner 的 `control.jsonl` 接受 `revoke_lesson` 紧急撤销消息：它会立即把 lesson store 标为 revoked，
并让本 run 的后续注入点排除该 id。已经发送给模型的历史消息不可撤回，不会伪装成已从上下文删除。

Web 控制台默认只监听 `127.0.0.1:8080`：输入任务后可实时查看手机画面、步骤时间线、任务板与终局状态，
并处理 `ask_user` / `take_over` / hard 档安全确认。任务由独立 runner 子进程执行，控制台重启后会从
`PHONE_AGENT_RUNS_DIR`（默认 `memory/runs`）回放事件并重连仍存活的任务；无界面用法仍保持进程内直跑。

经验数据默认写入 `memory/experience/{events.jsonl,episodes.json}`；前者是追加式事实日志，后者是按
`run_id` 索引、可重建的物化视图。`PHONE_AGENT_EXPERIENCE=off` 可完全关闭写入；`--dream` 按
`PHONE_AGENT_EPISODE_KEEP` / `PHONE_AGENT_EPISODE_ARCHIVE_DAYS` 将旧全文折叠为无原文的类别成功率统计。

`PHONE_AGENT_EVOLUTION=manual` 仅开放显式离线命令；候选写入
`memory/lessons/{events.jsonl,lessons.json}`。蒸馏以水位线批次处理新 episode（上限 40 条），看到完整任务过程卡片（目标原文 + 逐步 intent/note 账本 + 结局），输出先经严格
schema、证据与 scope 校验，再以 proposed 状态落盘；Rule-of-3 也只产生“可供人工晋升”结论。
dream 会对账：证据被折叠后不再够格的 approved 经验自动降回草案（lesson_demoted），并按注入组/未注入组成功率给出建议撤销清单（仅提醒）。
离线管线不参与 actor prompt；proposed/revoked 永不注入。默认 `shadow` 继续只观测，只有显式
`PHONE_AGENT_MEMORY_RAG=on` 才按上述边界把 approved lesson 注入一次，并在 trace 与 episode 记录 id。

## 文档

📖 **完整文档站：<https://tom-cat-mao.github.io/TaskWizard/>**

| 主题 | 入口 |
|---|---|
| 快速开始 | [文档站](https://tom-cat-mao.github.io/TaskWizard/quickstart/) · [`.env` 模板](.env.example) |
| 配置参考（全量） | [文档站配置页](https://tom-cat-mao.github.io/TaskWizard/configuration/) |
| 架构 / 安全 / 记忆 / 路线图 | [文档站](https://tom-cat-mao.github.io/TaskWizard/) |
| Agent 开发约定 | [AGENTS.md](AGENTS.md) |

## Contributing

欢迎提交 Issue 和 Pull Request；开始编码前请先阅读 [AGENTS.md](AGENTS.md) 的开发契约与 P0 约束。

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。
