# 执行文档 G：五路审查修复批次（11 项）

> 读者：reasonix 执行 agent。自包含任务书，**所有问题已由主 agent 逐条代码验证属实**，
> 每条附 file:line 证据与修法，直接执行即可。
> 工作区：/Users/bytedance/Open-AutoGLM-fixG（git worktree，分支 wt/review-fixes，基点 6c2ceac）。
> 测试：`PYTHONPATH=/Users/bytedance/Open-AutoGLM-fixG /Users/bytedance/Open-AutoGLM/.venv/bin/pytest tests/ -q`
> 先验证 `import phone_agent` 打印 worktree 路径；基线 **1278 全绿**。
> 禁止：git commit/push；**禁止 FakeModel 式 mock 判断测试**（只写确定性单测）；
> 禁止顺手改任何任务书外的东西；用 rg 不用 grep/find。

## 背景

5 个只读审查员 + 主 agent 验证发现的确认缺陷。全部修复须保持：P0#3 图片剥离、
P0#4 HITL interrupt 语义、P0#5 边守卫、P0#6 reducer 语义（plan 只追加/execute 全量
重建）、P0#10 脱敏、trace schema 只增不改、CN/EN 同步。

---

## Fix 1【高】reflect 屏幕变化信号读错层级（真实运行中恒失效）

**证据**：`phone_agent/graph/nodes/reflect.py:1519-1521` 读
`state["observation"]["screen_hash"]`；但 `Observation.to_dict()`
（`phone_agent/graph/observation.py:126-140`）顶层**没有** screen_hash——它嵌套在
`["snapshot"]["screen_hash"]`。而 plan 每步把顶层 `state["screen_hash"]` 写好了
（`plan.py:1177/1229/1304`，时序=动作前帧，正是 before）。
**修法**：reflect 改读 `state.get("screen_hash")`。
**修测试**：`tests/graph/test_effect_guards.py` 中构造顶层
`"observation": {"screen_hash": ...}` 的用例是自欺（真实形状不存在）——改为真实
形状（observation 顶层无 screen_hash，state 顶层有），断言行为不变。

## Fix 2【高】批量 eval 的 HITL interrupt 降级为 "Max steps reached"

**证据**：langgraph 1.2.2 实测（主 agent 已复现）：**无 checkpointer 时 invoke 也
返回 `{'x':0, '__interrupt__':[Interrupt(...)]}`，不抛 GraphInterrupt**。故
`agent.py:322` 的 `except GraphInterrupt` 永不触发；真实路径落
`_state_to_run_result` → success=False、finished=False、final_message 误导为
"Max steps reached"、hitl_count=0、无 run_interrupted trace。
**修法**：`run_structured` 在 `self._graph.invoke(...)` 返回后检查
`isinstance(result, dict) and result.get("__interrupt__")`，命中则用现有
`extract_interrupt()` 取 (message,type)，发 `run_interrupted` trace（与旧 catch 分支
同构），返回 `RunResult(success=False, finished=True, steps=int(result.get("step_count") or 0),
final_message=message, failure_cause=interrupt_type)`——对照旧 catch 分支的归因口径。
**修测试**：`tests/agent/test_hitl_resume.py` 里用 `_RaisingGraph` 假抛异常的两个
"semantics unchanged" 测试是自欺（保护的路径不存在）——改为**真实编译迷你图**
（已验证可行的最小形态）：
```python
from langgraph.graph import StateGraph
from langgraph.types import interrupt
from typing import TypedDict
class S(TypedDict, total=False): x: int
def node(s):
    v = interrupt({"message": "need human", "type": "takeover"})
    return {"x": v}
b = StateGraph(S); b.add_node("n", node); b.set_entry_point("n")
g = b.compile()  # 无 checkpointer，invoke 返回 {'x':0,'__interrupt__':[...]}
```
用它替换 PhoneAgent._graph（或构造等价路径）断言 marker 路径归因正确。

## Fix 3【高】run_live confirm 语义反转（回车=取消支付操作并终止）

**证据**：`agent.py:453` takeover/confirm 共用提示"完成后按回车继续（输入 n 终止）"；
但 confirm_node（`nodes/confirm.py:40`）把回车空串解析为 confirmed=False →
finished=True **终止**。confirm payload 里正确的 `"Confirm? (Y/N): "`（confirm.py:32）
被 `extract_interrupt`（agent.py:180-197）丢弃。
**修法**：
1. `extract_interrupt` 同时返回 payload 的 `prompt` 字段（有则用，无则回退现有拼装）。
2. run_live 按 interrupt_type 分支：
   - `takeover`：行为不变（回车=继续，n=终止）。
   - `confirmation`：提示语用 payload prompt（"敏感操作…Confirm? (Y/N): "），
     答案 `strip().lower() in ("y","yes")` → `Command(resume="Y")`；
     **其他任何输入（含空回车/n）→ `Command(resume="N")`**（fail-closed，节点走
     finished=True 干净终止，trace 完整）。不要直接 return，让图自己终结。
   - `goal_approval`（goal_node.py:201 的 interrupt，payload 有 prompt 无 message）：
     用 payload prompt 展示契约，y → resume True/继续，其他 → resume False/终止
     （按 goal_node 的 resume 解析实现来，先读代码确认它期待什么类型的 resume 值）。
3. `hitl_count` 口径统一：正常完成路径读 state、abort 路径读本地计数——确认
   `_state_to_run_result` 与 `_terminal` 两条路都正确累计（goal_node 分支若不写
   state hitl_count，以本地计数为准补齐）。
**修测试**：补空回车=n、y=Y 两个 confirmation 用例（迷你真图或函数级，禁假图抛异常）。

## Fix 4【高】Launch 无 repeat key，守卫零拦截

**证据**：`execute.py:331` candidate_repeat 只取 `text` 和 target_center；Launch 两者
皆无 → `repeated_action_key`（context.py）fall-through `return None` → 计数恒 0。
**修法**：仿 `_locate_repeat_key`：
1. execute.py candidate_repeat 增加 `"app": action_parsed.get("app")`（Launch 的字段名，
   先读 `actions/adapter.py`/`validator.py` 确认字段实际名称）。
2. context.py `repeated_action_key` 加 Launch 分支：
   `_launch_repeat_key(item, surface) = ("Launch", <app 的 sanitize+digest>, surface)`，
   digest 复用 locate hint 的 sanitize+digest 工具函数。
3. 测试：连续 launch 同一未知 app（无效果）达阈值→拒绝；launch 不同 app 不拦截。

## Fix 5【高】goal 重编译后 prefix 契约过期

**证据**：契约块只在 `step_count==0` append 进 messages（plan.py:982 区域）；
goal_node 重编译（`goal_node.py:35-65`，stage_stall_recompile 等触发）只更新
state 契约字段，messages 不动 → 模型永远看旧契约。
**修法**：goal_node 在**成功完成一次重编译**（首次编译除外：那时 messages 里还没有
契约块）后，返回**全量重建的 messages**（replace 语义，一次性）：找到内容以契约块
标记开头的 user 消息（先读 `build_goal_prompt_block` 确认块头标记文本），用新契约块
文本替换该消息内容；找不到则不动 messages（返回 {} 不碰）。
语义说明：重编译本就该让缓存失效一次，此后 prefix 重新稳定——这是正确行为。
**修测试**：`test_plan_context_cache.py` 里"contract appears exactly once"的绝对断言
改为：step0 后恰好一次 + **重编译后仍恰好一次但内容为新契约**（新增场景用例）。

## Fix 6【中】had_effect 的"新观察"信号只数条数不查新鲜性（近乎恒真，fail-open）

**证据**：`reflect.py:1525` 传 `len(criteria_observations or [])`；有契约时 reflect
每步都输出判据观察 → 信号恒 True。
**修法**：换成**判据 status 枚举变化**（形式层比较，不读内容）：在 goal_evidence 加
纯函数 `latest_status_by_criterion(ledger)`；reflect 计算本步观察中**status 与该判据
上一条账本记录不同**的条数，传给 `action_had_effect(new_observation_count=...)`。
函数签名不动，语义在 docstring 更新为"status-changed fresh observations"。
无契约/无上一条时行为：本步有观察即算 fresh（首见即新鲜）。
**测试**：同 status 重复观察 → 不 fresh；status 翻转 → fresh；首见 → fresh。

## Fix 7【中】学习缓存污染自证成功

**证据**：`tools/launch.py:95` 在 `am start` 命令级成功即 `learning.record`，无前台
验证；verifier `_package_for_app_name`（verifier.py:530）目标侧+前台侧都 learned-first
→ 学错时 launch_matched 自证成功，本跑内无法纠正。
**修法**：record 时机从"am start 成功"收紧为"前台包名验证命中"：
1. launch 工具不再 record；把 (app term, resolved package) 通过 action_result 元数据
   带出（result dict 增加键，add-only）。
2. reflect 在 verifier 确认前台包名 == 该步解析包名（launch_matched 或等价信号）
   后调用 `learning.record(term, package)`。
3. verifier 保持 learned-first（正确时的快路径），但因 record 已经前台验证背书，
   错误学习在本步就不会发生；历史脏数据无（缓存本跑私有）。
   先读 reflect 里 verifier 信号的现有结构再落点，保持 add-only。
**测试**：am start 成功但前台不符 → 不记账；前台命中 → 记账；记账后 resolve 走学习路径。

## Fix 8【中】每次 launch 双重 `pm list packages`

**证据**：`tools/launch.py:64` 取一次 inventory；`adb/device.py:306` `launch_app`
在 inventory=None 时再取一次，而 launch.py 没透传。
**修法**：launch.py 把已取的 inventory 透传给 `device_factory.launch_app(..., inventory=inventory)`
（先读 DeviceFactory 与 device.launch_app 签名确认参数名）。一次 launch 只查一次 pm。
**测试**：monkeypatch inventory 函数计数，launch 全流程只调 1 次。

## Fix 9【中】package_candidates 子串匹配无最小长度

**证据**：`config/app_registry.py:494-510 _match_candidates_to_inventory` 只跳过空串；
"a"/"com" 是合法 needle（稀疏设备上可唯一命中系统包）。
**修法**：normalized needle `len < 4` 直接跳过（normalize 后计长；合法包名片段如
"tongcheng"/"12306" 均 ≥4，不误伤）。**不动**"已装=可启动"语义，不加系统包黑名单
（该边界变更待用户拍板，交接节注明）。
**测试**："a"/"com" 不再产生匹配；"tongcheng" 正常命中。

## Fix 10【中】assistant 历史无上限增长（think 块）

**证据**：execute 重建只把 user 胖 tail 换瘦行，assistant 消息
（`<think>...</think><answer>...</answer>`，execute.py:113-121 区域）全量累积；
100 步 thinking 约 30-80k 字。
**修法**：在 execute 全量重建路径（skinny 替换同一处）对**历史 assistant 消息**
（除最新一条外）剥掉 `<think>...</think>` 段只留 `<answer>`——form 级文本处理，
P0#3 剥图同款思路。正则/字符串切分均可，要求：无 think 块时原样保留；
answer 部分一个字不动。thinking 含敏感内容时也顺带从后续请求消失（隐私加成，
docstring 提及）。
**测试**：20 步合成会话，assistant 历史字符数有界（断言阈值）；最新 assistant 保留 think；
answer 文本逐字节不变。

## Fix 11【低】--live --dry-run 冲突检查在真机 pm clear 之后

**证据**：`run_diagnosis.py` main() 先 `collect_preflight`+`reset_app_on_device`
（真的 `adb shell pm clear`），后查 `args.live and args.dry_run`（375-382 区域）。
**修法**：把冲突检查挪到任何设备操作之前（argparse 之后立即检查）。

---

## 完成标准

1. 11 项全部落地，全量测试绿（基线 1278，新增用例另计）。
2. 每项修复至少一个确定性单测；**Fix 1/2 的自欺测试必须改写**（不允许换个姿势继续自欺）。
3. 文档末尾写"## 交接"：每项的实际改动文件:行、测试数变化、Fix 9 的黑名单遗留说明、
   任何执行中发现的与任务书不符的事实。
4. 未 commit/push。
