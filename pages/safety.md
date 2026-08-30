# 安全模式

`PHONE_AGENT_SAFETY_MODE` 控制执行类动作（tap / long_press / type_text / launch_app）的门控策略。

## 四档

| 档位 | 行为 | 适用 |
|---|---|---|
| **wary（默认）** | 检出风险的执行调用**不执行也不叫人**：工具返回一段预警（世界事实 + 选项空间）；模型带 `confirm_irreversible=true` 重发才执行 | 日常使用——你在旁边看着，模型自己确认 |
| **hard** | 旧式 HITL 硬拦：风险调用中断，等人工 approve / reject | 无人值守挂机 |
| **reviewer** | wary + 软候选过第二模型精排（判可逆性）；精排模型故障时 fail-closed 预警 | 对误报敏感的场景 |
| **off** | 执行动作全不门控 | 长期低摩擦跑批（谨慎） |

无论哪一档，`ask_user` 与 `take_over` 都会正常中断等人。

## 什么算"风险"

检测语义（四档共用）：不可逆承诺 = **承诺动词 + 不可逆对象**（如"确认支付"）、密码框 `type_text`、凭据/验证码输入、模型自声明敏感（`sensitive=true` 永远预警）。软候选（如 launch_app 这类可逆动作）不会触发门控。

## wary 预警长什么样

模型调用被短路，收到一条工具回执，包含：

- **世界事实**：这一步将做什么、目标是什么；
- **选项空间**：带 `confirm_irreversible=true` 重发 / 放弃 / `ask_user` 问人 / `take_over` 交给人。

同时 stdout 与 trace 各留一条非阻塞记录。

## finish 验收

独立于安全门控的另一道保险：`PHONE_AGENT_FINISH_VERIFY`（`off`/`auto`/`always`）。

- finish 本身**两段式**：先返回复核包（世界事实镜像 + 路线 + 疑点），模型 `confirm=true` 才定稿；
- `auto`：仅高风险目标（支付/删除/凭据词表）或硬矛盾下坚持 confirm 时，触发**独立上下文验收器**——它只看目标 + 证据路线 + 尾部截图，永远看不到 actor 的完整对话；
- 验收器故障 **fail-open**（放行）——两段式 L1 已经拦过一轮。
