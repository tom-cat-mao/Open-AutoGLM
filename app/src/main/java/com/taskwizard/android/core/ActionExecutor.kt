package com.taskwizard.android.core

import android.content.Context
import android.util.Log
import com.taskwizard.android.IAutoGLMService
import com.taskwizard.android.data.Action
import com.taskwizard.android.config.AppMap
import com.taskwizard.android.config.TimingConfig
import kotlinx.coroutines.delay

/**
 * ActionExecutor - 执行 Agent 的各种操作
 * Phase 2: 完整支持所有动作类型
 * Phase 3: IME 自动化管理
 * Phase 4: Safety & Timing - 敏感操作确认和执行时序
 * 
 * 坐标转换修复：使用截图实际尺寸而非物理屏幕尺寸
 * 严格对齐 Open-AutoGLM 原版 phone_agent/actions/handler.py
 */
class ActionExecutor(
    private val context: Context,
    private val service: IAutoGLMService,
    private var screenWidth: Int,   // 改为 var，支持动态更新
    private var screenHeight: Int,  // 改为 var，支持动态更新
    private val onTakeOver: (suspend (String) -> Unit)? = null,          // Take_over 回调（改为 suspend）
    private val onInteract: ((String) -> String?)? = null,               // Interact 回调
    private val onNote: ((String) -> Unit)? = null,                      // Note 回调
    private val onConfirmation: (suspend (String) -> Boolean)? = null    // Phase 4: 敏感操作确认回调
) {
    
    companion object {
        private const val TAG = "ActionExecutor"
        private const val ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
    }
    
    // Phase 3: IME Management - 记录原始输入法
    private var originalIME: String? = null
    private var imeHasBeenSwitched = false

    /**
     * 更新屏幕尺寸（基于截图实际尺寸）
     * 必须在每次截图后调用，确保坐标转换使用正确的基准
     */
    fun updateScreenSize(width: Int, height: Int) {
        screenWidth = width
        screenHeight = height
        Log.d(TAG, "Screen size updated: ${width}x${height}")
    }

    /**
     * 执行结果
     */
    data class ExecuteResult(
        val success: Boolean,
        val shouldContinue: Boolean = true,  // false 表示用户取消，应停止任务
        val errorMessage: String? = null
    )

    /**
     * Phase 4: 改为 suspend 函数以支持敏感操作确认
     * 严格对齐原版 handler.py 的 execute 方法
     *
     * @return ExecuteResult - 包含执行状态和错误信息
     */
    suspend fun execute(action: Action): ExecuteResult {
        val type = action.action ?: return ExecuteResult(success = true)

        Log.d(TAG, "Executing: $type with params: ${action.location} / ${action.content}")
        Log.d(TAG, "Current screen size: ${screenWidth}x${screenHeight}")

        // Normalize action type (case insensitive)
        when (type.lowercase()) {
            "launch" -> {
                val appName = action.content ?: return ExecuteResult(success = true)
                val packageName = findPackageName(appName)

                if (packageName != null) {
                    Log.d(TAG, "Resolved app '$appName' to package '$packageName'")
                    runShell("monkey -p $packageName -c android.intent.category.LAUNCHER 1")

                    // Phase 4: 添加延迟（对齐原版 timing.py）
                    delay(TimingConfig.device.defaultLaunchDelay)

                    return ExecuteResult(success = true)
                } else {
                    val errorMsg = "应用 '$appName' 未在系统中找到。请检查应用名称是否正确，或确认应用已安装。"
                    Log.e(TAG, "Launch failed: $errorMsg")
                    Log.e(TAG, "Available apps in AppMap: bilibili, 微信, QQ, 淘宝, 京东, 抖音, 快手...")
                    return ExecuteResult(
                        success = false,
                        errorMessage = errorMsg
                    )
                }
            }
            
            "tap" -> {
                val coords = action.location ?: return ExecuteResult(success = true)
                if (coords.size >= 2) {
                    val x = (coords[0] / 1000.0 * screenWidth).toInt()
                    val y = (coords[1] / 1000.0 * screenHeight).toInt()

                    Log.d(TAG, "Tap: model coords=[${coords[0]}, ${coords[1]}] -> screen coords=($x, $y)")

                    // Phase 4: 敏感操作确认（严格对齐原版 handler.py）
                    if (action.message != null) {
                        Log.w(TAG, "Sensitive operation detected: ${action.message}")

                        val confirmed = onConfirmation?.invoke(action.message) ?: true

                        if (!confirmed) {
                            Log.i(TAG, "User cancelled sensitive operation")
                            return ExecuteResult(success = true, shouldContinue = false)
                        }

                        Log.i(TAG, "User confirmed sensitive operation")
                    }

                    runShell("input tap $x $y")

                    // Phase 4: 添加延迟（对齐原版 timing.py）
                    delay(TimingConfig.device.defaultTapDelay)
                }
            }
            
            // Phase 2: Double Tap 实现
            "double tap" -> {
                val coords = action.location ?: return ExecuteResult(success = true)
                if (coords.size >= 2) {
                    val x = (coords[0] / 1000.0 * screenWidth).toInt()
                    val y = (coords[1] / 1000.0 * screenHeight).toInt()
                    
                    Log.d(TAG, "Double Tap: model coords=[${coords[0]}, ${coords[1]}] -> screen coords=($x, $y)")
                    
                    // 第一次点击
                    runShell("input tap $x $y")
                    
                    // Phase 4: 使用 TimingConfig 的 doubleTapInterval（对齐原版）
                    delay(TimingConfig.device.doubleTapInterval)
                    
                    // 第二次点击
                    runShell("input tap $x $y")
                    
                    // Phase 4: 添加延迟
                    delay(TimingConfig.device.defaultDoubleTapDelay)
                    
                    Log.d(TAG, "Double tap executed at ($x, $y)")
                }
            }
            
            // Phase 2: Long Press 支持动态 duration
            "long press" -> {
                val coords = action.location ?: return ExecuteResult(success = true)
                if (coords.size >= 2) {
                    val x = (coords[0] / 1000.0 * screenWidth).toInt()
                    val y = (coords[1] / 1000.0 * screenHeight).toInt()
                    
                    Log.d(TAG, "Long Press: model coords=[${coords[0]}, ${coords[1]}] -> screen coords=($x, $y)")
                    
                    // 使用 action.duration 或默认 1000ms
                    val duration = action.duration ?: 1000
                    
                    runShell("input swipe $x $y $x $y $duration")
                    
                    // Phase 4: 添加延迟
                    delay(TimingConfig.device.defaultLongPressDelay)
                    
                    Log.d(TAG, "Long press executed at ($x, $y) for ${duration}ms")
                }
            }
            
            "type", "type_name" -> {
                val text = action.content ?: return ExecuteResult(success = true)
                
                // Phase 3: 自动切换到 ADB Keyboard
                if (!imeHasBeenSwitched) {
                    try {
                        // 记录当前输入法
                        originalIME = service.getCurrentIME()
                        Log.i(TAG, "Original IME: $originalIME")
                        
                        // 切换到 ADB Keyboard
                        if (originalIME != ADB_KEYBOARD_IME) {
                            val success = service.setIME(ADB_KEYBOARD_IME)
                            if (success) {
                                imeHasBeenSwitched = true
                                Log.i(TAG, "Successfully switched to ADB Keyboard")
                                
                                // Phase 4: 添加 IME 切换延迟（对齐原版 timing.py）
                                delay(TimingConfig.action.keyboardSwitchDelay)
                            } else {
                                Log.w(TAG, "Failed to switch to ADB Keyboard, will try to input anyway")
                            }
                        } else {
                            Log.d(TAG, "Already using ADB Keyboard")
                            imeHasBeenSwitched = true
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Failed to switch IME", e)
                    }
                }
                
                // 执行输入
                val base64 = android.util.Base64.encodeToString(text.toByteArray(), android.util.Base64.NO_WRAP)
                try {
                    service.injectInputBase64(base64)
                    Log.d(TAG, "Text input executed: ${text.take(20)}...")
                    
                    // Phase 4: 添加文本输入延迟（对齐原版 timing.py）
                    delay(TimingConfig.action.textInputDelay)
                } catch (e: Exception) {
                    Log.e(TAG, "Input failed", e)
                }
            }
            
            "swipe" -> {
                 val coords = action.location ?: return ExecuteResult(success = true)
                 if (coords.size >= 4) {
                     val x1 = (coords[0] / 1000.0 * screenWidth).toInt()
                     val y1 = (coords[1] / 1000.0 * screenHeight).toInt()
                     val x2 = (coords[2] / 1000.0 * screenWidth).toInt()
                     val y2 = (coords[3] / 1000.0 * screenHeight).toInt()
                     
                     Log.d(TAG, "Swipe: model coords=[${coords[0]}, ${coords[1]}] -> [${coords[2]}, ${coords[3]}]")
                     Log.d(TAG, "Swipe: screen coords=($x1, $y1) -> ($x2, $y2)")
                     
                     runShell("input swipe $x1 $y1 $x2 $y2 300")
                     
                     // Phase 4: 添加延迟
                     delay(TimingConfig.device.defaultSwipeDelay)
                 }
            }
            
            "home" -> {
                runShell("input keyevent KEYCODE_HOME")
                // Phase 4: 添加延迟
                delay(TimingConfig.device.defaultHomeDelay)
            }
            
            "back" -> {
                runShell("input keyevent KEYCODE_BACK")
                // Phase 4: 添加延迟
                delay(TimingConfig.device.defaultBackDelay)
            }
            
            "enter" -> {
                runShell("input keyevent KEYCODE_ENTER")
            }
            
            // Phase 2: Take_over 实现（suspend 调用，会等待用户操作完成）
            "take_over" -> {
                val message = action.message ?: action.content ?: "需要人工介入"
                Log.i(TAG, "Take_over requested: $message")
                onTakeOver?.invoke(message)  // 现在会正确等待用户操作
            }
            
            // Phase 2: Interact 实现
            "interact" -> {
                val message = action.message ?: action.content ?: "请选择"
                Log.i(TAG, "Interact requested: $message")
                val result = onInteract?.invoke(message)
                Log.d(TAG, "User selected: $result")
                // TODO: 将用户选择结果传回 Agent
            }
            
            // Phase 2: Note 实现
            "note" -> {
                val message = action.message ?: action.content ?: "True"
                Log.i(TAG, "Note: $message")
                onNote?.invoke(message)
            }
            
            // Call_API 在 AgentCore 中处理，这里只记录
            "call_api" -> {
                val instruction = action.instruction ?: action.content ?: ""
                Log.i(TAG, "Call_API: $instruction")
                // 实际处理在 AgentCore.kt 中
            }
            
            "wait" -> { 
                val duration = action.duration ?: 2000
                delay(duration.toLong())
            }
            
            "finish" -> { 
                val message = action.message ?: action.content ?: "任务完成"
                Log.i(TAG, "Task Finished: $message")
            }
            
            else -> {
                Log.w(TAG, "Unknown action: $type")
            }
        }

        return ExecuteResult(success = true)  // 默认返回成功
    }

    private fun findPackageName(appName: String): String? {
        // Static Map Lookup ONLY
        AppMap.PACKAGES[appName]?.let { return it }
        
        // Case-insensitive match
        AppMap.PACKAGES.entries.find { it.key.equals(appName, ignoreCase = true) }?.let { return it.value }

        Log.w(TAG, "App not found in static map: $appName")
        return null
    }

    private fun runShell(cmd: String) {
        try {
            Log.d(TAG, "Running: $cmd")
            service.executeShellCommand(cmd)
        } catch (e: Exception) {
            Log.e(TAG, "Shell failed", e)
        }
    }
    
    // ==================== Phase 3: IME Management ====================
    
    /**
     * 还原原始输入法
     * 应在任务完成或发生错误时调用
     */
    fun restoreIME() {
        if (!imeHasBeenSwitched || originalIME == null) {
            Log.d(TAG, "No need to restore IME (not switched or no original IME)")
            return
        }
        
        try {
            Log.i(TAG, "Restoring IME to: $originalIME")
            val success = service.setIME(originalIME!!)
            
            if (success) {
                Log.i(TAG, "Successfully restored IME")
            } else {
                Log.w(TAG, "Failed to restore IME")
            }
            
            // 重置状态
            imeHasBeenSwitched = false
            originalIME = null
        } catch (e: Exception) {
            Log.e(TAG, "Exception while restoring IME", e)
        }
    }
    
    /**
     * 检查是否已切换 IME
     */
    fun hasIMEBeenSwitched(): Boolean = imeHasBeenSwitched
}
