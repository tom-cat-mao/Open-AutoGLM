package com.taskwizard.android.data

import com.taskwizard.android.ui.theme.ThemeMode
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf

/**
 * 应用全局状态
 * 使用单一数据源模式，所有UI状态集中管理
 *
 * 性能优化：使用 ImmutableList 确保 Compose 编译器将此类推断为稳定类型
 * 这样可以避免不必要的重组，显著提升性能
 */
data class AppState(
    // ==================== 配置相关 ====================
    val apiKey: String = "",
    val baseUrl: String = "",
    val model: String = "autoglm-phone",

    // ==================== 主题相关 ====================
    val themeMode: ThemeMode = ThemeMode.LIGHT,
    val pureBlackEnabled: Boolean = false,

    // ==================== 任务相关 ====================
    val isRunning: Boolean = false,
    val currentTask: String = "",
    val messages: ImmutableList<MessageItem> = persistentListOf(),  // ✅ 使用 ImmutableList

    // ==================== 继续对话相关 ====================
    val isContinuedConversation: Boolean = false,
    val originalTaskId: Long? = null,

    // ==================== 系统状态 ====================
    val hasShizukuPermission: Boolean = false,
    val isADBKeyboardInstalled: Boolean = false,
    val isADBKeyboardEnabled: Boolean = false,

    // ==================== UI状态 ====================
    val isSettingsOpen: Boolean = false
)
