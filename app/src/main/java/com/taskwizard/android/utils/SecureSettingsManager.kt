package com.taskwizard.android.utils

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.IOException
import java.security.GeneralSecurityException

/**
 * Secure settings manager using EncryptedSharedPreferences
 *
 * This provides encrypted storage for sensitive data like API keys.
 * Uses AndroidX Security library's EncryptedSharedPreferences which
 * encrypts keys and values with AES256_SIV and AES256_GCM respectively.
 *
 * Falls back to regular SharedPreferences if encryption is not available.
 */
object SecureSettingsManager {
    private const val TAG = "SecureSettingsManager"
    private const val PREF_NAME = "autoglm_secure_settings"
    private const val KEY_API_KEY = "secure_api_key"

    private var prefs: SharedPreferences? = null
    private var isEncrypted = false
    private var isInitialized = false

    /**
     * Initialize storage (tries encrypted, falls back to regular)
     *
     * @param context Application context
     */
    fun init(context: Context) {
        if (isInitialized) return

        try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()

            prefs = EncryptedSharedPreferences.create(
                context,
                PREF_NAME,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
            isEncrypted = true
            Log.i(TAG, "Using encrypted storage")
        } catch (e: GeneralSecurityException) {
            Log.w(TAG, "Encrypted storage not available, using fallback", e)
            // Fallback to regular SharedPreferences
            prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            isEncrypted = false
        } catch (e: IOException) {
            Log.w(TAG, "Encrypted storage IO error, using fallback", e)
            // Fallback to regular SharedPreferences
            prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            isEncrypted = false
        }
        isInitialized = true
    }

    /**
     * Check if encrypted storage is available
     */
    fun isSecureStorageAvailable(): Boolean = isEncrypted

    /**
     * Get the API key
     *
     * @return API key or empty string if not set
     */
    var apiKey: String
        get() {
            checkInitialized()
            return prefs?.getString(KEY_API_KEY, "") ?: ""
        }
        set(value) {
            checkInitialized()
            prefs?.edit()?.putString(KEY_API_KEY, value)?.apply()
        }

    /**
     * Check if an API key is stored
     *
     * @return true if API key exists and is not empty
     */
    fun hasApiKey(): Boolean {
        checkInitialized()
        return apiKey.isNotEmpty()
    }

    /**
     * Validate API key format (basic validation)
     *
     * @param key API key to validate
     * @return true if key appears valid (not too short)
     */
    fun isValidApiKey(key: String): Boolean {
        return key.length >= 10
    }

    /**
     * Clear the stored API key
     */
    fun clearApiKey() {
        checkInitialized()
        prefs?.edit()?.remove(KEY_API_KEY)?.apply()
    }

    private fun checkInitialized() {
        if (!isInitialized) {
            throw IllegalStateException("SecureSettingsManager not initialized. Call init() first.")
        }
    }
}
