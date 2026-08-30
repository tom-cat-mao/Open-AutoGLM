# 安全模式

`PHONE_AGENT_SAFETY_MODE` 控制执行类动作（tap / long_press / type_text / launch_app）的门控。

## 四档

| 档位 | 行为 | 适用场景 |
|---|---|---|
| `wary`（默认） | 风险调用不执行、不中断：工具返回预警（世界事实 + 选项），模型带 `confirm_irreversible=true` 重发才执行 | 有人看管的日常使用 |
| `hard` | 风险调用中断，等待人工 approve / reject | 无人值守运行 |
| `reviewer` | wary + 第二模型对软候选做可逆性精排；精排故障时按预警处理 | 需要压低误报的场景 |
| `off` | 执行动作不门控 | 受控环境跑批 |

任何档位下，`ask_user` 与 `take_over` 都会中断并等待人工输入。

## 风险判定

满足以下任一条件即触发门控：承诺动词 + 不可逆对象（如"确认支付"）；密码/凭据/验证码输入；模型自声明 `sensitive=true`。可逆动作（如打开应用）不触发。

## wary 流程

```mermaid
flowchart TD
    CALL["模型发起执行调用"] --> GATE{"安全门判定"}
    GATE -- "无风险" --> EXEC["执行并回传新观测"]
    GATE -- "风险" --> WARN["短路：返回预警回执<br/>（世界事实 + 选项空间）"]
    WARN --> CHOICE{"模型选择"}
    CHOICE -- "confirm_irreversible=true 重发" --> EXEC
    CHOICE -- "放弃该动作" --> NEXT["继续任务"]
    CHOICE -- "ask_user / take_over" --> HUMAN["中断，等待人工"]
```

## finish 验收

与安全门控独立的两道完成检查：

1. **两段式 finish**：首次 `finish` 返回复核包（目标、路线完成度、疑点）；模型 `confirm=true` 再次调用才定稿；
2. **独立验收器**（`PHONE_AGENT_FINISH_VERIFY`）：`auto` 档在高风险目标或硬矛盾时触发——验收器只能看到目标、证据路线与尾部截图，看不到 actor 的完整对话；验收器故障时放行（fail-open）。
