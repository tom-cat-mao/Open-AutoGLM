package com.example.autoglm.utils

import android.content.Context
import android.content.SharedPreferences

/**
 * 设置管理器
 * 使用 SharedPreferences 持久化应用配置
 *
 * 性能优化：添加高级设置的持久化支持
 */
object SettingsManager {
    private const val PREF_NAME = "autoglm_settings"

    // 基础配置 Keys
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_API_KEY = "api_key"
    private const val KEY_MODEL = "model"

    // 高级设置 Keys（性能优化新增）
    private const val KEY_TIMEOUT_SECONDS = "timeout_seconds"
    private const val KEY_RETRY_COUNT = "retry_count"
    private const val KEY_DEBUG_MODE = "debug_mode"

    private lateinit var prefs: SharedPreferences

    fun init(context: Context) {
        prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
    }

    // ==================== 基础配置 ====================

    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, "https://open.bigmodel.cn/api/paas/v4/") ?: ""
        set(value) = prefs.edit().putString(KEY_BASE_URL, value).apply()

    var apiKey: String
        get() = prefs.getString(KEY_API_KEY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_API_KEY, value).apply()

    var model: String
        get() = prefs.getString(KEY_MODEL, "autoglm-phone") ?: "autoglm-phone"
        set(value) = prefs.edit().putString(KEY_MODEL, value).apply()

    // ==================== 高级设置（性能优化新增）====================

    /**
     * 请求超时时间（秒）
     * 默认值：30秒
     */
    var timeoutSeconds: Int
        get() = prefs.getInt(KEY_TIMEOUT_SECONDS, 30)
        set(value) = prefs.edit().putInt(KEY_TIMEOUT_SECONDS, value).apply()

    /**
     * 失败重试次数
     * 默认值：3次
     */
    var retryCount: Int
        get() = prefs.getInt(KEY_RETRY_COUNT, 3)
        set(value) = prefs.edit().putInt(KEY_RETRY_COUNT, value).apply()

    /**
     * 调试模式
     * 默认值：false
     */
    var debugMode: Boolean
        get() = prefs.getBoolean(KEY_DEBUG_MODE, false)
        set(value) = prefs.edit().putBoolean(KEY_DEBUG_MODE, value).apply()
}
