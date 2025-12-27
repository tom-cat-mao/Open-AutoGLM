package com.taskwizard.android.ui.utils

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log

/**
 * Utility class for launching external apps and settings
 */
object AppLauncher {
    private const val TAG = "AppLauncher"

    // Shizuku app package name
    private const val SHIZUKU_PACKAGE = "moe.shizuku.privileged"

    // ADB Keyboard package name
    private const val ADB_KEYBOARD_PACKAGE = "com.android.adbkeyboard"

    // ADB Keyboard Google Play URL
    private const val ADB_KEYBOARD_PLAY_URL = "market://details?id=com.android.adbkeyboard"
    private const val ADB_KEYBOARD_WEB_URL = "https://play.google.com/store/apps/details?id=com.android.adbkeyboard"

    /**
     * Open Shizuku app
     */
    fun openShizukuApp(context: Context) {
        try {
            val intent = context.packageManager.getLaunchIntentForPackage(SHIZUKU_PACKAGE)
            if (intent != null) {
                context.startActivity(intent)
                Log.d(TAG, "Opened Shizuku app")
            } else {
                // Shizuku not installed, open web page
                openShizukuDownload(context)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open Shizuku app", e)
            openShizukuDownload(context)
        }
    }

    /**
     * Open Shizuku download page (GitHub)
     */
    fun openShizukuDownload(context: Context) {
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/RikkaApps/Shizuku/releases"))
            context.startActivity(intent)
            Log.d(TAG, "Opened Shizuku download page")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open Shizuku download page", e)
        }
    }

    /**
     * Open IME (input method) settings
     */
    fun openIMESettings(context: Context) {
        try {
            val intent = Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)
            context.startActivity(intent)
            Log.d(TAG, "Opened IME settings")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open IME settings", e)
        }
    }

    /**
     * Open overlay permission settings
     */
    fun openOverlayPermissionSettings(context: Context) {
        try {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:${context.packageName}")
            )
            context.startActivity(intent)
            Log.d(TAG, "Opened overlay permission settings")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open overlay permission settings", e)
            // Fallback to general settings
            try {
                val intent = Intent(Settings.ACTION_SETTINGS)
                context.startActivity(intent)
            } catch (e2: Exception) {
                Log.e(TAG, "Failed to open settings", e2)
            }
        }
    }

    /**
     * Open ADB Keyboard download page
     * First tries Google Play Store, then falls back to web browser
     */
    fun openADBKeyboardDownload(context: Context) {
        try {
            // Try to open Google Play Store first
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(ADB_KEYBOARD_PLAY_URL))
            // Verify that there's an app to handle this intent
            if (intent.resolveActivity(context.packageManager) != null) {
                context.startActivity(intent)
                Log.d(TAG, "Opened ADB Keyboard on Google Play")
            } else {
                // Fallback to web browser
                val webIntent = Intent(Intent.ACTION_VIEW, Uri.parse(ADB_KEYBOARD_WEB_URL))
                context.startActivity(webIntent)
                Log.d(TAG, "Opened ADB Keyboard web page")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open ADB Keyboard download", e)
            // Last resort: try web browser
            try {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(ADB_KEYBOARD_WEB_URL))
                context.startActivity(intent)
            } catch (e2: Exception) {
                Log.e(TAG, "Failed to open web browser", e2)
            }
        }
    }

    /**
     * Check if Shizuku app is installed
     */
    fun isShizukuInstalled(context: Context): Boolean {
        return try {
            context.packageManager.getPackageInfo(SHIZUKU_PACKAGE, 0)
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Check if ADB Keyboard is installed
     */
    fun isADBKeyboardInstalled(context: Context): Boolean {
        return try {
            context.packageManager.getPackageInfo(ADB_KEYBOARD_PACKAGE, 0)
            true
        } catch (e: Exception) {
            false
        }
    }
}
