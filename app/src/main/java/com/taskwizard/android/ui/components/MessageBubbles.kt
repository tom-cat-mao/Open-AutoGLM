package com.taskwizard.android.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.SystemMessageType
import java.text.SimpleDateFormat
import java.util.*

/**
 * AI思考消息气泡
 * 左对齐，显示AI的推理过程
 */
@Composable
fun ThinkMessageBubble(
    message: MessageItem.ThinkMessage,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Start
    ) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceContainerLow
            ),
            elevation = CardDefaults.cardElevation(
                defaultElevation = 1.dp
            ),
            shape = MaterialTheme.shapes.medium,
            modifier = Modifier.fillMaxWidth(0.85f)
        ) {
            Column(
                modifier = Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // 标题行
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        imageVector = Icons.Rounded.Psychology,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                    Text(
                        text = "AI思考",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                }

                // 内容 - 添加额外的安全检查
                val displayText = when {
                    message.content.isBlank() -> "正在思考中..."
                    message.content.length < 3 -> "正在分析..."
                    else -> message.content
                }

                Text(
                    text = displayText,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSecondaryContainer
                )

                // 时间戳
                Text(
                    text = formatTimestamp(message.timestamp),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSecondaryContainer.copy(alpha = 0.7f)
                )
            }
        }
    }
}

/**
 * 操作消息气泡
 * 右对齐，显示AI执行的具体操作
 */
@Composable
fun ActionMessageBubble(
    message: MessageItem.ActionMessage,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.End
    ) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.primaryContainer
            ),
            elevation = CardDefaults.cardElevation(
                defaultElevation = 2.dp
            ),
            shape = MaterialTheme.shapes.medium,
            modifier = Modifier.fillMaxWidth(0.85f)
        ) {
            Column(
                modifier = Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // 标题行
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        imageVector = getActionIcon(message.action),
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                    Text(
                        text = "执行操作",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                }

                // 操作内容
                Text(
                    text = formatAction(message.action),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onPrimaryContainer
                )

                // 时间戳
                Text(
                    text = formatTimestamp(message.timestamp),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f)
                )
            }
        }
    }
}

/**
 * 系统消息气泡
 * 居中显示，用于系统提示
 */
@Composable
fun SystemMessageBubble(
    message: MessageItem.SystemMessage,
    onSaveAsTask: (() -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Center
    ) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = when (message.type) {
                    SystemMessageType.INFO -> MaterialTheme.colorScheme.surfaceContainer
                    SystemMessageType.SUCCESS -> MaterialTheme.colorScheme.primaryContainer
                    SystemMessageType.WARNING -> MaterialTheme.colorScheme.tertiaryContainer
                    SystemMessageType.ERROR -> MaterialTheme.colorScheme.errorContainer
                    SystemMessageType.SAVE_TASK_PROMPT -> MaterialTheme.colorScheme.secondaryContainer
                }
            ),
            elevation = CardDefaults.cardElevation(
                defaultElevation = 1.dp
            ),
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.fillMaxWidth(0.9f)
        ) {
            Row(
                modifier = Modifier.padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = when (message.type) {
                        SystemMessageType.INFO -> Icons.Rounded.Info
                        SystemMessageType.SUCCESS -> Icons.Rounded.CheckCircle
                        SystemMessageType.WARNING -> Icons.Rounded.Warning
                        SystemMessageType.ERROR -> Icons.Rounded.Error
                        SystemMessageType.SAVE_TASK_PROMPT -> Icons.Rounded.Save
                    },
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = when (message.type) {
                        SystemMessageType.INFO -> MaterialTheme.colorScheme.onSurface
                        SystemMessageType.SUCCESS -> MaterialTheme.colorScheme.onPrimaryContainer
                        SystemMessageType.WARNING -> MaterialTheme.colorScheme.onTertiaryContainer
                        SystemMessageType.ERROR -> MaterialTheme.colorScheme.onErrorContainer
                        SystemMessageType.SAVE_TASK_PROMPT -> MaterialTheme.colorScheme.onSecondaryContainer
                    }
                )

                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    Text(
                        text = message.content,
                        style = MaterialTheme.typography.bodySmall,
                        color = when (message.type) {
                            SystemMessageType.INFO -> MaterialTheme.colorScheme.onSurface
                            SystemMessageType.SUCCESS -> MaterialTheme.colorScheme.onPrimaryContainer
                            SystemMessageType.WARNING -> MaterialTheme.colorScheme.onTertiaryContainer
                            SystemMessageType.ERROR -> MaterialTheme.colorScheme.onErrorContainer
                            SystemMessageType.SAVE_TASK_PROMPT -> MaterialTheme.colorScheme.onSecondaryContainer
                        }
                    )

                    Text(
                        text = formatTimestamp(message.timestamp),
                        style = MaterialTheme.typography.labelSmall,
                        color = when (message.type) {
                            SystemMessageType.INFO -> MaterialTheme.colorScheme.onSurface
                            SystemMessageType.SUCCESS -> MaterialTheme.colorScheme.onPrimaryContainer
                            SystemMessageType.WARNING -> MaterialTheme.colorScheme.onTertiaryContainer
                            SystemMessageType.ERROR -> MaterialTheme.colorScheme.onErrorContainer
                            SystemMessageType.SAVE_TASK_PROMPT -> MaterialTheme.colorScheme.onSecondaryContainer
                        }.copy(alpha = 0.7f)
                    )

                    // 保存为Task按钮（仅在 SAVE_TASK_PROMPT 类型时显示）
                    if (message.type == SystemMessageType.SAVE_TASK_PROMPT) {
                        Spacer(modifier = Modifier.height(8.dp))
                        OutlinedButton(
                            onClick = { onSaveAsTask?.invoke() },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.outlinedButtonColors(
                                contentColor = MaterialTheme.colorScheme.primary
                            )
                        ) {
                            Icon(
                                imageVector = Icons.Rounded.Save,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text("保存为Task (${message.stepCount}步)")
                        }
                    }
                }
            }
        }
    }
}

/**
 * 操作类型到图标的映射表（性能优化：避免每次调用时创建新字符串）
 */
private val ACTION_ICON_MAP = mapOf(
    "tap" to Icons.Rounded.TouchApp,
    "double tap" to Icons.Rounded.TouchApp,
    "swipe" to Icons.Rounded.SwipeRight,
    "type" to Icons.Rounded.Keyboard,
    "type_name" to Icons.Rounded.Keyboard,
    "launch" to Icons.Rounded.RocketLaunch,
    "back" to Icons.Rounded.ArrowBack,
    "home" to Icons.Rounded.Home,
    "long press" to Icons.Rounded.TouchApp,
    "finish" to Icons.Rounded.CheckCircle
)

/**
 * 根据操作类型获取对应的图标
 */
private fun getActionIcon(action: Action): ImageVector {
    return ACTION_ICON_MAP[action.action?.lowercase()] ?: Icons.Rounded.PlayArrow
}

/**
 * 格式化操作信息
 */
private fun formatAction(action: Action): String {
    val actionType = action.action ?: "Unknown"
    val parts = mutableListOf<String>()

    parts.add("动作: $actionType")

    action.location?.let { coords ->
        if (coords.size >= 2) {
            parts.add("坐标: [${coords[0]}, ${coords[1]}]")
        }
    }

    action.content?.let {
        parts.add("内容: ${it.take(50)}${if (it.length > 50) "..." else ""}")
    }

    action.message?.let {
        parts.add("消息: $it")
    }

    return parts.joinToString("\n")
}

/**
 * 格式化时间戳
 * 性能优化：使用缓存对象避免每次创建 SimpleDateFormat 实例
 */
private fun formatTimestamp(timestamp: Long): String {
    return TimestampCache.format(timestamp)
}

/**
 * 时间戳缓存对象
 * 使用单例模式缓存 SimpleDateFormat 实例以提升性能
 */
private object TimestampCache {
    private val dateFormat = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    fun format(timestamp: Long): String {
        return dateFormat.format(Date(timestamp))
    }
}
