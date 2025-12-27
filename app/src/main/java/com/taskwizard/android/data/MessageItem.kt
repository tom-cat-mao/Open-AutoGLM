package com.taskwizard.android.data

import androidx.compose.runtime.Stable
import com.taskwizard.android.data.Action
import java.util.UUID

/**
 * 消息项基类
 * 用于在UI中展示不同类型的消息
 *
 * 性能优化：
 * - 添加 @Stable 注解，确保 Compose 编译器将此类视为稳定类型
 * - 添加唯一 ID，确保 LazyColumn 可以正确追踪列表项
 * - 避免使用 hashCode() 作为 key，因为 hashCode 可能重复
 */
@Stable
sealed class MessageItem {
    abstract val id: String  // ✅ 唯一标识符

    /**
     * AI思考消息
     * 显示AI的推理过程（来自<think>标签）
     */
    data class ThinkMessage(
        override val id: String = UUID.randomUUID().toString(),
        val content: String,
        val timestamp: Long = System.currentTimeMillis()
    ) : MessageItem()

    /**
     * 操作消息
     * 显示AI执行的具体操作
     */
    data class ActionMessage(
        override val id: String = UUID.randomUUID().toString(),
        val action: Action,
        val timestamp: Long = System.currentTimeMillis()
    ) : MessageItem()

    /**
     * 系统消息
     * 显示系统状态、错误、成功等信息
     */
    data class SystemMessage(
        override val id: String = UUID.randomUUID().toString(),
        val content: String,
        val type: SystemMessageType,
        val timestamp: Long = System.currentTimeMillis()
    ) : MessageItem()
}

/**
 * 系统消息类型
 */
enum class SystemMessageType {
    INFO,       // 信息提示（蓝色）
    SUCCESS,    // 成功提示（绿色）
    WARNING,    // 警告提示（橙色）
    ERROR       // 错误提示（红色）
}
