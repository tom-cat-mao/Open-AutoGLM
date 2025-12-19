package com.example.autoglm.utils

import android.content.Context
import android.content.SharedPreferences

object SettingsManager {
    private const val PREF_NAME = "autoglm_settings"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_API_KEY = "api_key"
    private const val KEY_MODEL = "model"

    private lateinit var prefs: SharedPreferences

    fun init(context: Context) {
        prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
    }

    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, "https://open.bigmodel.cn/api/paas/v4/") ?: ""
        set(value) = prefs.edit().putString(KEY_BASE_URL, value).apply()

    var apiKey: String
        get() = prefs.getString(KEY_API_KEY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_API_KEY, value).apply()

    var model: String
        get() = prefs.getString(KEY_MODEL, "autoglm-phone") ?: "autoglm-phone"
        set(value) = prefs.edit().putString(KEY_MODEL, value).apply()
}
