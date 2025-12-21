package com.example.autoglm.service

import android.app.*
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import android.view.Display
import android.view.Gravity
import android.view.WindowManager
import androidx.compose.ui.platform.ComposeView
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import com.example.autoglm.R
import com.example.autoglm.ui.overlay.OverlayContent
import com.example.autoglm.ui.theme.AutoGLMTheme
import com.example.autoglm.ui.viewmodel.OverlayViewModel

/**
 * OverlayService - 悬浮窗服务
 *
 * 负责显示和管理悬浮窗
 * 使用LifecycleService作为基类，自动管理生命周期
 *
 * 关键特性：
 * - 前台服务：Android 8.0+必须显示通知
 * - 生命周期管理：使用ServiceLifecycleOwner
 * - Compose集成：使用ComposeView显示UI
 * - 权限检查：启动时验证SYSTEM_ALERT_WINDOW权限
 * - 异常处理：捕获所有可能的异常，防止崩溃
 */
class OverlayService : LifecycleService() {

    companion object {
        private const val TAG = "OverlayService"
        private const val CHANNEL_ID = "overlay_service_channel"
        private const val NOTIFICATION_ID = 1001

        // 单例引用，用于外部更新状态
        var instance: OverlayService? = null
    }

    // ==================== 组件 ====================

    private lateinit var windowManager: WindowManager
    private var overlayView: ComposeView? = null
    private lateinit var lifecycleOwner: ServiceLifecycleOwner
    private lateinit var overlayViewModel: OverlayViewModel

    // ==================== Service生命周期 ====================

    override fun onCreate() {
        super.onCreate()
        Log.d(TAG, "onCreate")

        // 设置单例引用
        instance = this

        // 1. 创建通知Channel（Android 8.0+）
        createNotificationChannel()

        // 2. 启动前台服务
        startForeground(NOTIFICATION_ID, createNotification())

        // 3. 初始化生命周期管理
        lifecycleOwner = ServiceLifecycleOwner()

        // 4. 初始化OverlayViewModel
        overlayViewModel = ViewModelProvider.AndroidViewModelFactory
            .getInstance(application)
            .create(OverlayViewModel::class.java)

        // 5. 获取WindowManager
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager

        // 6. 创建悬浮窗
        createOverlayWindow()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.d(TAG, "onStartCommand")

        // 检查权限
        if (!Settings.canDrawOverlays(this)) {
            Log.e(TAG, "Overlay permission revoked")
            // 发送广播通知 MainActivity 权限被撤销
            val errorIntent = Intent("com.example.autoglm.OVERLAY_PERMISSION_REVOKED")
            sendBroadcast(errorIntent)
            stopSelf()
            return START_NOT_STICKY
        }

        return super.onStartCommand(intent, flags, startId)
    }

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    override fun onDestroy() {
        Log.d(TAG, "onDestroy")

        // 清理单例引用
        instance = null

        // 移除悬浮窗
        overlayView?.let {
            try {
                windowManager.removeView(it)
                Log.d(TAG, "Overlay view removed")
            } catch (e: Exception) {
                Log.e(TAG, "Error removing overlay view", e)
            }
        }
        overlayView = null

        // 销毁生命周期
        lifecycleOwner.onDestroy()

        super.onDestroy()
    }

    // ==================== 悬浮窗创建 ====================

    /**
     * 创建悬浮窗
     */
    private fun createOverlayWindow() {
        try {
            // 创建正确的Context（Android 11+）
            val overlayContext = createOverlayContext()

            // 创建ComposeView
            overlayView = ComposeView(overlayContext).apply {
                // 绑定生命周期（关键！）
                setViewTreeLifecycleOwner(lifecycleOwner)
                setViewTreeViewModelStoreOwner(lifecycleOwner)
                setViewTreeSavedStateRegistryOwner(lifecycleOwner)

                // 设置Compose内容
                setContent {
                    AutoGLMTheme {
                        OverlayContent(
                            viewModel = overlayViewModel,
                            onExit = { stopSelf() },
                            onReturnToApp = { returnToMainActivity() }
                        )
                    }
                }
            }

            // 配置窗口参数
            val params = createWindowLayoutParams()

            // 添加到WindowManager
            windowManager.addView(overlayView, params)
            Log.d(TAG, "Overlay window created successfully")

        } catch (e: SecurityException) {
            Log.e(TAG, "Permission denied", e)
            stopSelf()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to create overlay window", e)
            stopSelf()
        }
    }

    /**
     * 创建Overlay Context
     * Android 11+需要使用createWindowContext
     */
    private fun createOverlayContext(): Context {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                val displayManager = getSystemService(DisplayManager::class.java)
                val defaultDisplay = displayManager.getDisplay(Display.DEFAULT_DISPLAY)
                createDisplayContext(defaultDisplay)
                    .createWindowContext(
                        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                        null
                    )
            } catch (e: Exception) {
                Log.w(TAG, "Failed to create window context, using service context", e)
                this
            }
        } else {
            this
        }
    }

    /**
     * 创建窗口布局参数
     */
    private fun createWindowLayoutParams(): WindowManager.LayoutParams {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            // 关键标志位
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or      // 不抢焦点
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or    // 触摸外部不拦截
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,     // 允许超出屏幕
            PixelFormat.TRANSLUCENT  // 支持透明
        ).apply {
            gravity = Gravity.TOP or Gravity.END
            x = 16
            y = 100
        }
    }

    // ==================== 通知管理 ====================

    /**
     * 创建通知Channel
     * Android 8.0+必需
     */
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "任务执行状态",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "显示AutoGLM任务执行状态"
                setShowBadge(false)
                enableLights(false)
                enableVibration(false)
            }

            val notificationManager = getSystemService(NotificationManager::class.java)
            notificationManager.createNotificationChannel(channel)
            Log.d(TAG, "Notification channel created")
        }
    }

    /**
     * 创建前台服务通知
     */
    private fun createNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            packageManager.getLaunchIntentForPackage(packageName),
            PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AutoGLM 运行中")
            .setContentText("点击返回应用")
            .setSmallIcon(R.drawable.ic_launcher) // TODO: 创建专用图标
            .setContentIntent(pendingIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setShowWhen(false)
            .build()
    }

    // ==================== 状态更新方法（供外部调用）====================

    /**
     * 更新thinking状态
     */
    fun updateThinking(isThinking: Boolean) {
        Log.d(TAG, "updateThinking: $isThinking")
        overlayViewModel.updateThinkingState(isThinking)
    }

    /**
     * 更新action
     */
    fun updateAction(action: String?) {
        Log.d(TAG, "updateAction: $action")
        action?.let { overlayViewModel.updateAction(it) }
    }

    /**
     * 标记任务完成
     */
    fun markCompleted() {
        Log.d(TAG, "markCompleted")
        overlayViewModel.markTaskCompleted()
    }

    /**
     * 标记任务开始
     */
    fun markTaskStarted() {
        Log.d(TAG, "markTaskStarted")
        overlayViewModel.markTaskStarted()
    }

    // ==================== 辅助方法 ====================

    /**
     * 返回主Activity
     */
    private fun returnToMainActivity() {
        try {
            val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
            launchIntent?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(launchIntent)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to return to main activity", e)
        }
    }
}
