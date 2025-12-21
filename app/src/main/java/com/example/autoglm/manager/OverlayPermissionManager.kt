package com.example.autoglm.manager

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings

/**
 * 悬浮窗权限管理器
 *
 * 负责检查和请求SYSTEM_ALERT_WINDOW权限
 * 遵循Android最佳实践，确保权限管理的安全性和用户体验
 */
object OverlayPermissionManager {

    /**
     * 检查是否有悬浮窗权限
     *
     * @param context 上下文
     * @return true表示有权限，false表示无权限
     */
    fun checkPermission(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                Settings.canDrawOverlays(context)
            } catch (e: Exception) {
                false
            }
        } else {
            // Android 6.0以下默认有权限
            true
        }
    }

    /**
     * 请求悬浮窗权限
     *
     * 会跳转到系统设置页面让用户授权
     * 注意：Intent不会返回结果，需要在onResume中再次检查权限
     *
     * @param activity Activity实例
     */
    fun requestPermission(activity: Activity) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                val intent = Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:${activity.packageName}")
                )
                activity.startActivity(intent)
            } catch (e: Exception) {
                // 如果无法打开设置页面，尝试打开通用设置
                try {
                    val intent = Intent(Settings.ACTION_SETTINGS)
                    activity.startActivity(intent)
                } catch (e2: Exception) {
                    // 忽略，无法打开设置
                }
            }
        }
    }

    /**
     * 判断是否应该显示权限说明
     *
     * 对于SYSTEM_ALERT_WINDOW权限，Android没有提供shouldShowRequestPermissionRationale
     * 我们通过检查权限状态来判断
     *
     * @param context 上下文
     * @return true表示应该显示说明，false表示不需要
     */
    fun shouldShowRationale(context: Context): Boolean {
        // 如果没有权限，就应该显示说明
        return !checkPermission(context)
    }

    /**
     * 获取权限请求的Intent
     *
     * 用于ActivityResultLauncher
     *
     * @param context 上下文
     * @return Intent
     */
    fun getPermissionIntent(context: Context): Intent {
        return Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:${context.packageName}")
        )
    }
}
