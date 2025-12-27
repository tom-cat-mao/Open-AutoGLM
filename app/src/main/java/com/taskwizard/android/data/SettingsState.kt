package com.taskwizard.android.data

import androidx.compose.runtime.Stable
import com.taskwizard.android.ui.theme.ThemeMode

/**
 * 设置页面专用状态
 *
 * 性能优化：
 * - 添加 @Stable 注解，确保 Compose 编译器将此类视为稳定类型
 * - 将设置相关的状态从 AppState 中分离出来，避免过度订阅
 * - 设置页面只会在相关字段变化时重组，不会受到主页面消息更新的影响
 *
 * 包含：
 * - API 配置（apiKey, baseUrl, model）
 * - 主题设置（themeMode, pureBlackEnabled）
 * - 高级设置（timeoutSeconds, retryCount, debugMode）
 * - 验证状态（isApiKeyValid, isBaseUrlValid, isSaveEnabled）
 */
@Stable
data class SettingsState(
    // ==================== API 配置 ====================
    val apiKey: String = "",
    val baseUrl: String = "",
    val model: String = "autoglm-phone",

    // ==================== 主题设置 ====================
    val themeMode: ThemeMode = ThemeMode.LIGHT,
    val pureBlackEnabled: Boolean = false,

    // ==================== 高级设置 ====================
    val timeoutSeconds: Int = 30,
    val retryCount: Int = 3,
    val debugMode: Boolean = false,

    // ==================== 验证状态 ====================
    val isApiKeyValid: Boolean = true,
    val isBaseUrlValid: Boolean = true,
    val isSaveEnabled: Boolean = false
)
