package com.taskwizard.android.service

import android.accessibilityservice.AccessibilityService
import android.app.UiAutomation
import android.content.Context
import android.os.Binder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.InputDevice
import android.view.MotionEvent
import com.taskwizard.android.IAutoGLMService
import com.taskwizard.android.hiddenapis.UiAutomationConnection
import com.taskwizard.android.hiddenapis.UiAutomationHidden
import dev.rikka.tools.refine.Refine
import java.io.File
import kotlin.system.exitProcess

/**
 * 运行在 ADB Shell 进程中的服务。
 * 拥有 UID 2000 权限，可直接执行 input/screencap 命令。
 *
 * Phase 2: UiAutomation API 集成
 * - 优先使用 UiAutomation API
 * - 失败时降级到 shell 命令
 */
class AutoGLMUserService(context: Context) : IAutoGLMService.Stub() {

    companion object {
        private const val TAG = "AutoGLMUserService"
        private const val SCREENSHOT_DIR = "/data/local/tmp"
    }

    // UiAutomation 实例
    private var uiAutomationHidden: UiAutomationHidden? = null
    private var uiAutomation: UiAutomation? = null
    private var isUiAutomationConnected = false

    override fun destroy() {
        // 断开 UiAutomation 连接
        try {
            uiAutomationHidden?.disconnect()
            Log.d(TAG, "UiAutomation disconnected")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to disconnect UiAutomation", e)
        }
        // Shizuku 推荐在销毁时退出进程
        exitProcess(0)
    }

    override fun executeShellCommand(command: String): String {
        return try {
            // 直接执行命令，无需 "adb shell" 前缀
            val process = Runtime.getRuntime().exec(command)

            // 使用超时机制防止命令阻塞
            val completed = process.waitFor(10, java.util.concurrent.TimeUnit.SECONDS)

            if (!completed) {
                process.destroyForcibly()
                Log.w(TAG, "Command timed out: $command")
                return "TIMEOUT"
            }

            process.inputStream.bufferedReader().use { it.readText() }
        } catch (e: Exception) {
            Log.e(TAG, "executeShellCommand failed: $command", e)
            "Error: ${e.message}"
        }
    }

    override fun takeScreenshotToFile(): String {
        return try {
            // 1. 确保目录存在
            val dir = File(SCREENSHOT_DIR)
            if (!dir.exists()) {
                val mkdirResult = executeShellCommand("mkdir -p $SCREENSHOT_DIR")
                Log.d(TAG, "mkdir result: $mkdirResult")
            }

            // 2. 生成唯一文件名（使用时间戳）
            val timestamp = System.currentTimeMillis()
            val filename = "screen_$timestamp.png"
            val fullPath = "$SCREENSHOT_DIR/$filename"

            // 3. 执行截图命令，直接输出到文件
            val process = Runtime.getRuntime().exec(arrayOf("screencap", "-p", fullPath))
            val exitCode = process.waitFor()

            Log.d(TAG, "screencap exit code: $exitCode, path: $fullPath")

            // 4. 验证文件是否成功创建
            val file = File(fullPath)
            if (file.exists() && file.length() > 1000) {
                // 文件存在且大小合理（大于1KB，避免空文件或错误信息）
                Log.d(TAG, "Screenshot saved successfully, size: ${file.length()} bytes")
                fullPath
            } else {
                val errorMsg = if (!file.exists()) {
                    "File not created"
                } else {
                    "File too small (${file.length()} bytes)"
                }
                Log.e(TAG, "Screenshot failed: $errorMsg")
                "ERROR: $errorMsg"
            }
        } catch (e: Exception) {
            Log.e(TAG, "Screenshot exception", e)
            "ERROR: ${e.message}"
        }
    }

    override fun injectInputBase64(base64Text: String) {
        Log.d(TAG, "injectInputBase64: ${base64Text.length} chars")

        try {
            // 使用显式包名确保广播到达 TaskWizardIME
            val cmd = "am broadcast -a ADB_INPUT_B64 --es msg $base64Text -p com.taskwizard.android"
            Log.d(TAG, "Executing broadcast command")

            val process = Runtime.getRuntime().exec(cmd)

            // 使用 Java 的 waitFor 超时，比 shell timeout 命令更可靠
            val completed = process.waitFor(5, java.util.concurrent.TimeUnit.SECONDS)

            if (!completed) {
                process.destroyForcibly()
                Log.w(TAG, "injectInputBase64: broadcast timed out after 5 seconds")
            } else {
                val result = process.inputStream.bufferedReader().use { it.readText() }
                Log.d(TAG, "injectInputBase64 result: $result")
            }
        } catch (e: Exception) {
            Log.e(TAG, "injectInputBase64 failed", e)
        }
    }

    override fun getCurrentPackage(): String {
        return try {
            // 使用 dumpsys window 获取当前焦点窗口
            // 对齐 Python 原版：不使用正则，直接搜索包名
            val output = executeShellCommand("dumpsys window")
            
            if (output.isEmpty()) {
                Log.w(TAG, "dumpsys window returned empty output")
                return ""
            }
            
            Log.d(TAG, "getCurrentPackage: searching in dumpsys output (${output.length} chars)")
            
            // 遍历每一行，查找包含 mCurrentFocus 或 mFocusedApp 的行
            for (line in output.split("\n")) {
                if ("mCurrentFocus" in line || "mFocusedApp" in line) {
                    Log.d(TAG, "Found focus line: $line")
                    
                    // 在这一行中搜索所有已知的包名
                    for ((appName, packageName) in com.taskwizard.android.config.AppMap.PACKAGES) {
                        if (packageName in line) {
                            Log.d(TAG, "Matched package: $packageName -> app: $appName")
                            return packageName
                        }
                    }
                }
            }
            
            Log.d(TAG, "No matching package found, returning empty")
            return ""
        } catch (e: Exception) {
            Log.e(TAG, "Failed to get current package", e)
            ""
        }
    }

    // ==================== Phase 3: IME Management ====================
    
    override fun getCurrentIME(): String {
        return try {
            // 使用 settings get 获取当前默认输入法
            val output = executeShellCommand("settings get secure default_input_method")
            val imeId = output.trim()
            
            Log.d(TAG, "getCurrentIME: $imeId")
            imeId
        } catch (e: Exception) {
            Log.e(TAG, "Failed to get current IME", e)
            ""
        }
    }
    
    override fun setIME(imeId: String): Boolean {
        return try {
            Log.d(TAG, "setIME: attempting to set IME to $imeId")
            
            // 1. 先启用输入法（如果未启用）
            val enableResult = executeShellCommand("ime enable $imeId")
            Log.d(TAG, "ime enable result: $enableResult")
            
            // 2. 设置为默认输入法
            val setResult = executeShellCommand("ime set $imeId")
            Log.d(TAG, "ime set result: $setResult")
            
            // 3. 验证是否设置成功
            val currentIME = getCurrentIME()
            val success = currentIME == imeId
            
            if (success) {
                Log.i(TAG, "Successfully set IME to $imeId")
            } else {
                Log.w(TAG, "Failed to set IME. Current: $currentIME, Expected: $imeId")
            }
            
            success
        } catch (e: Exception) {
            Log.e(TAG, "Failed to set IME", e)
            false
        }
    }
    
    override fun isADBKeyboardInstalled(): Boolean {
        return try {
            // 检查 ADB Keyboard 包是否已安装
            val output = executeShellCommand("pm list packages com.android.adbkeyboard")
            val installed = output.contains("com.android.adbkeyboard")

            Log.d(TAG, "isADBKeyboardInstalled: $installed")
            installed
        } catch (e: Exception) {
            Log.e(TAG, "Failed to check ADB Keyboard installation", e)
            false
        }
    }

    override fun isIMEEnabled(imeId: String): Boolean {
        return try {
            // 使用 ime list -s 获取已启用的输入法列表（更可靠）
            val output = executeShellCommand("ime list -s")
            Log.d(TAG, "Enabled IMEs list:\n$output")

            // 检查是否包含指定的 IME（支持多种格式匹配）
            val lines = output.trim().split("\n")
            val isEnabled = lines.any { line ->
                val trimmedLine = line.trim()
                // 精确匹配或包含匹配
                trimmedLine == imeId ||
                trimmedLine.contains(imeId) ||
                // 支持简写格式匹配（如 .ime.TaskWizardIME）
                (imeId.contains("/") && trimmedLine.endsWith(imeId.substringAfter("/")))
            }

            Log.d(TAG, "isIMEEnabled($imeId): $isEnabled")
            isEnabled
        } catch (e: Exception) {
            Log.e(TAG, "Failed to check if IME is enabled", e)
            false
        }
    }

    // ==================== UiAutomation API ====================

    override fun initUiAutomation(): Boolean {
        if (isUiAutomationConnected) {
            Log.d(TAG, "UiAutomation already connected")
            return true
        }

        return try {
            Log.d(TAG, "Initializing UiAutomation...")
            Binder.clearCallingIdentity()

            // 确保有 Looper
            if (Looper.myLooper() == null) {
                Looper.prepare()
            }

            // 创建 UiAutomation 连接
            val connection = UiAutomationConnection()
            uiAutomationHidden = UiAutomationHidden(Looper.myLooper()!!, connection)
            uiAutomationHidden?.connect(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)

            // 使用 Refine 转换为 UiAutomation
            uiAutomation = Refine.unsafeCast(uiAutomationHidden)

            isUiAutomationConnected = true
            Log.i(TAG, "UiAutomation initialized successfully")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize UiAutomation, will use shell fallback", e)
            isUiAutomationConnected = false
            // 返回 true 表示可以继续使用 shell 命令
            true
        }
    }

    override fun injectTap(x: Int, y: Int): Boolean {
        // 优先使用 UiAutomation
        if (isUiAutomationConnected && uiAutomation != null) {
            return try {
                val downTime = SystemClock.uptimeMillis()

                val downEvent = MotionEvent.obtain(
                    downTime, downTime,
                    MotionEvent.ACTION_DOWN,
                    x.toFloat(), y.toFloat(), 0
                ).apply { source = InputDevice.SOURCE_TOUCHSCREEN }

                val upEvent = MotionEvent.obtain(
                    downTime, downTime + 10,
                    MotionEvent.ACTION_UP,
                    x.toFloat(), y.toFloat(), 0
                ).apply { source = InputDevice.SOURCE_TOUCHSCREEN }

                val downOk = uiAutomation!!.injectInputEvent(downEvent, true)
                val upOk = uiAutomation!!.injectInputEvent(upEvent, true)

                downEvent.recycle()
                upEvent.recycle()

                Log.d(TAG, "injectTap($x, $y) via UiAutomation: down=$downOk, up=$upOk")
                downOk && upOk
            } catch (e: Exception) {
                Log.e(TAG, "UiAutomation tap failed, fallback to shell", e)
                fallbackShellTap(x, y)
            }
        }
        return fallbackShellTap(x, y)
    }

    private fun fallbackShellTap(x: Int, y: Int): Boolean {
        return try {
            executeShellCommand("input tap $x $y")
            Log.d(TAG, "injectTap($x, $y) via shell")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Shell tap failed", e)
            false
        }
    }

    override fun injectSwipe(x1: Int, y1: Int, x2: Int, y2: Int, duration: Long): Boolean {
        // 优先使用 UiAutomation
        if (isUiAutomationConnected && uiAutomation != null) {
            return try {
                val downTime = SystemClock.uptimeMillis()
                val steps = (duration / 10).toInt().coerceAtLeast(10)

                // DOWN
                val downEvent = MotionEvent.obtain(
                    downTime, downTime, MotionEvent.ACTION_DOWN,
                    x1.toFloat(), y1.toFloat(), 0
                ).apply { source = InputDevice.SOURCE_TOUCHSCREEN }
                uiAutomation!!.injectInputEvent(downEvent, true)
                downEvent.recycle()

                // MOVE
                for (i in 1..steps) {
                    val progress = i.toFloat() / steps
                    val x = x1 + ((x2 - x1) * progress)
                    val y = y1 + ((y2 - y1) * progress)
                    val eventTime = downTime + (duration * progress).toLong()

                    val moveEvent = MotionEvent.obtain(
                        downTime, eventTime, MotionEvent.ACTION_MOVE,
                        x, y, 0
                    ).apply { source = InputDevice.SOURCE_TOUCHSCREEN }
                    uiAutomation!!.injectInputEvent(moveEvent, true)
                    moveEvent.recycle()
                }

                // UP
                val upEvent = MotionEvent.obtain(
                    downTime, downTime + duration, MotionEvent.ACTION_UP,
                    x2.toFloat(), y2.toFloat(), 0
                ).apply { source = InputDevice.SOURCE_TOUCHSCREEN }
                val result = uiAutomation!!.injectInputEvent(upEvent, true)
                upEvent.recycle()

                Log.d(TAG, "injectSwipe via UiAutomation: $result")
                result
            } catch (e: Exception) {
                Log.e(TAG, "UiAutomation swipe failed, fallback to shell", e)
                fallbackShellSwipe(x1, y1, x2, y2, duration)
            }
        }
        return fallbackShellSwipe(x1, y1, x2, y2, duration)
    }

    private fun fallbackShellSwipe(x1: Int, y1: Int, x2: Int, y2: Int, duration: Long): Boolean {
        return try {
            executeShellCommand("input swipe $x1 $y1 $x2 $y2 $duration")
            Log.d(TAG, "injectSwipe via shell")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Shell swipe failed", e)
            false
        }
    }

    override fun performGlobalAction(action: Int): Boolean {
        // 优先使用 UiAutomation
        if (isUiAutomationConnected && uiAutomation != null) {
            return try {
                val result = uiAutomation!!.performGlobalAction(action)
                Log.d(TAG, "performGlobalAction($action) via UiAutomation: $result")
                result
            } catch (e: Exception) {
                Log.e(TAG, "UiAutomation global action failed, fallback to shell", e)
                fallbackShellGlobalAction(action)
            }
        }
        return fallbackShellGlobalAction(action)
    }

    private fun fallbackShellGlobalAction(action: Int): Boolean {
        return try {
            when (action) {
                AccessibilityService.GLOBAL_ACTION_BACK -> {
                    executeShellCommand("input keyevent KEYCODE_BACK")
                    true
                }
                AccessibilityService.GLOBAL_ACTION_HOME -> {
                    executeShellCommand("input keyevent KEYCODE_HOME")
                    true
                }
                AccessibilityService.GLOBAL_ACTION_RECENTS -> {
                    executeShellCommand("input keyevent KEYCODE_APP_SWITCH")
                    true
                }
                else -> {
                    Log.w(TAG, "Unsupported global action: $action")
                    false
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Shell global action failed", e)
            false
        }
    }

    override fun isUiAutomationAvailable(): Boolean {
        return isUiAutomationConnected && uiAutomation != null
    }
}
