package com.taskwizard.android.core

import android.content.Context
import android.util.Log
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.taskwizard.android.IAutoGLMService
import com.taskwizard.android.config.AppMap
import com.taskwizard.android.config.TimingConfig
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.template.TaskTemplateEntity
import com.taskwizard.android.security.ShellCommandBuilder
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlin.coroutines.coroutineContext

/**
 * 模板执行器 - 无 AI 参与的 Task 回放
 */
class TemplateExecutor(
    private val context: Context,
    private val service: IAutoGLMService,
    private val screenWidth: Int,
    private val screenHeight: Int
) {
    companion object {
        private const val TAG = "TemplateExecutor"
        private const val TASKWIZARD_IME = "com.taskwizard.android/.ime.TaskWizardIME"
        private const val ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
    }

    private val gson = Gson()

    // IME 状态管理
    private var originalIME: String? = null
    private var imeHasBeenSwitched = false

    // 用于 JSON 解析的内部数据类（使用普通 List 而非 ImmutableList）
    private data class ActionJson(
        val action: String?,
        val location: List<Int>? = null,
        val content: String? = null,
        val message: String? = null,
        val duration: Int? = null,
        val instruction: String? = null
    )

    sealed class ExecutionResult {
        data class Success(val stepCount: Int, val durationMs: Long) : ExecutionResult()
        data class Failure(val step: Int, val error: String) : ExecutionResult()
        object Cancelled : ExecutionResult()
    }

    /**
     * 执行模板
     */
    suspend fun execute(
        template: TaskTemplateEntity,
        onProgress: (step: Int, total: Int, action: Action) -> Unit
    ): ExecutionResult {
        val startTime = System.currentTimeMillis()

        try {
            // 初始化 UiAutomation（关键：否则动作会降级到 shell 命令）
            service.initUiAutomation()
            Log.d(TAG, "UiAutomation initialized for template execution")

            // 解析动作列表
            val actions = parseActions(template.actionsJson)
            if (actions.isEmpty()) {
                return ExecutionResult.Failure(0, "No actions in template")
            }

            Log.i(TAG, "Executing template: ${template.name} (${actions.size} steps)")

            // 调试日志：打印模板和屏幕信息
            Log.d(TAG, "=== Template execution debug ===")
            Log.d(TAG, "Template screen: ${template.screenWidth}x${template.screenHeight}")
            Log.d(TAG, "Current screen: ${screenWidth}x${screenHeight}")
            Log.d(TAG, "Actions to execute:")
            actions.forEachIndexed { i, action ->
                Log.d(TAG, "  [$i] ${action.action} location=${action.location} content=${action.content?.take(20)}")
            }

            // 计算坐标缩放比例
            val scaleX = screenWidth.toFloat() / template.screenWidth
            val scaleY = screenHeight.toFloat() / template.screenHeight

            // 执行每个动作
            for ((index, action) in actions.withIndex()) {
                // 检查取消
                if (!coroutineContext.isActive) {
                    Log.i(TAG, "Execution cancelled at step ${index + 1}")
                    return ExecutionResult.Cancelled
                }

                // 回调进度
                onProgress(index + 1, actions.size, action)

                // 缩放坐标
                val scaledAction = scaleAction(action, scaleX, scaleY)

                // 执行动作
                val success = executeAction(scaledAction)
                if (!success) {
                    return ExecutionResult.Failure(
                        index + 1,
                        "Failed to execute: ${action.action}"
                    )
                }
            }

            val duration = System.currentTimeMillis() - startTime
            Log.i(TAG, "Template completed: ${actions.size} steps in ${duration}ms")

            return ExecutionResult.Success(actions.size, duration)

        } catch (e: CancellationException) {
            Log.i(TAG, "Execution cancelled")
            return ExecutionResult.Cancelled
        } catch (e: Exception) {
            Log.e(TAG, "Execution error", e)
            return ExecutionResult.Failure(0, e.message ?: "Unknown error")
        } finally {
            // 确保恢复原始输入法
            restoreIME()
        }
    }

    private fun parseActions(json: String): List<Action> {
        return try {
            val type = object : TypeToken<List<ActionJson>>() {}.type
            val jsonActions: List<ActionJson> = gson.fromJson(json, type) ?: emptyList()

            // 转换为 Action（使用 ImmutableList）
            jsonActions.map { jsonAction ->
                Action(
                    action = jsonAction.action,
                    location = jsonAction.location?.let {
                        kotlinx.collections.immutable.persistentListOf<Int>().addAll(it)
                    },
                    content = jsonAction.content,
                    message = jsonAction.message,
                    duration = jsonAction.duration,
                    instruction = jsonAction.instruction
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to parse actions", e)
            emptyList()
        }
    }

    /**
     * 将归一化坐标(0-1000)转换为像素坐标
     * AI任务使用归一化坐标，模板保存的也是归一化坐标
     */
    private fun scaleAction(action: Action, scaleX: Float, scaleY: Float): Action {
        val location = action.location
        if (location == null || location.isEmpty()) return action

        // 归一化坐标(0-1000) -> 像素坐标
        val scaled = when (location.size) {
            2 -> listOf(
                (location[0] / 1000.0 * screenWidth).toInt(),
                (location[1] / 1000.0 * screenHeight).toInt()
            )
            4 -> listOf(
                (location[0] / 1000.0 * screenWidth).toInt(),
                (location[1] / 1000.0 * screenHeight).toInt(),
                (location[2] / 1000.0 * screenWidth).toInt(),
                (location[3] / 1000.0 * screenHeight).toInt()
            )
            else -> location.toList()
        }

        Log.d(TAG, "Coordinate conversion: [${location.joinToString()}] -> [${scaled.joinToString()}]")

        return action.copy(
            location = kotlinx.collections.immutable.persistentListOf<Int>()
                .addAll(scaled)
        )
    }

    private suspend fun executeAction(action: Action): Boolean {
        val type = action.action ?: return true

        return try {
            when (type.lowercase()) {
                "tap" -> {
                    val coords = action.location ?: return true
                    if (coords.size >= 2) {
                        service.injectTap(coords[0], coords[1])
                        delay(TimingConfig.device.defaultTapDelay)
                    }
                    true
                }
                "swipe" -> {
                    val coords = action.location ?: return true
                    if (coords.size >= 4) {
                        service.injectSwipe(
                            coords[0], coords[1],
                            coords[2], coords[3],
                            300
                        )
                        delay(TimingConfig.device.defaultSwipeDelay)
                    }
                    true
                }
                "home" -> {
                    service.performGlobalAction(
                        android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME
                    )
                    delay(TimingConfig.device.defaultHomeDelay)
                    true
                }
                "back" -> {
                    service.performGlobalAction(
                        android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK
                    )
                    delay(TimingConfig.device.defaultBackDelay)
                    true
                }
                "wait" -> {
                    val duration = action.duration ?: 2000
                    delay(duration.toLong())
                    true
                }
                "type" -> {
                    val text = action.content ?: return true
                    Log.d(TAG, "Type action: text='${text.take(20)}...' (${text.length} chars)")

                    // 首次 type 动作时切换到兼容输入法
                    switchToCompatibleIME()
                    Log.d(TAG, "IME switched, encoding text...")

                    val base64 = android.util.Base64.encodeToString(
                        text.toByteArray(),
                        android.util.Base64.NO_WRAP
                    )
                    Log.d(TAG, "Calling injectInputBase64 (base64 len=${base64.length})")
                    service.injectInputBase64(base64)
                    Log.d(TAG, "injectInputBase64 returned, waiting ${TimingConfig.action.textInputDelay}ms")
                    delay(TimingConfig.action.textInputDelay)
                    Log.d(TAG, "Type action completed")
                    true
                }
                "launch" -> {
                    val appName = action.content ?: return true
                    val packageName = AppMap.getPackageName(appName)

                    if (packageName != null) {
                        val cmd = ShellCommandBuilder.buildMonkeyCommand(packageName)
                        service.executeShellCommand(cmd)
                        delay(TimingConfig.device.defaultLaunchDelay)
                        Log.d(TAG, "Launched app: $appName -> $packageName")
                        true
                    } else {
                        Log.e(TAG, "App not found: $appName")
                        false
                    }
                }
                "finish" -> {
                    Log.d(TAG, "Task finished: ${action.message ?: action.content ?: "completed"}")
                    true
                }
                else -> {
                    Log.w(TAG, "Unsupported action: $type")
                    true
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Action failed: $type", e)
            false
        }
    }

    /**
     * 切换到兼容的输入法
     */
    private suspend fun switchToCompatibleIME() {
        if (imeHasBeenSwitched) return

        originalIME = service.getCurrentIME()
        Log.d(TAG, "Original IME: $originalIME")

        val targetIME = when {
            originalIME == TASKWIZARD_IME || originalIME == ADB_KEYBOARD_IME -> {
                // 关键修复: 已使用兼容IME时，设置标志
                Log.d(TAG, "Already using compatible IME: $originalIME")
                imeHasBeenSwitched = true
                null
            }
            service.isIMEEnabled(TASKWIZARD_IME) -> TASKWIZARD_IME
            service.isIMEEnabled(ADB_KEYBOARD_IME) -> ADB_KEYBOARD_IME
            else -> {
                Log.w(TAG, "No compatible IME available")
                null
            }
        }

        if (targetIME != null) {
            Log.d(TAG, "Switching IME to: $targetIME")
            val success = service.setIME(targetIME)
            if (success) {
                imeHasBeenSwitched = true  // 成功后立即设置
                Log.i(TAG, "Successfully switched to $targetIME")
                delay(TimingConfig.action.keyboardSwitchDelay)
            } else {
                Log.e(TAG, "Failed to switch IME to $targetIME")
            }
        }
    }

    /**
     * 恢复原始输入法
     */
    private fun restoreIME() {
        if (!imeHasBeenSwitched || originalIME == null) return

        try {
            Log.d(TAG, "Restoring IME to: $originalIME")
            service.setIME(originalIME!!)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to restore IME", e)
        } finally {
            imeHasBeenSwitched = false
            originalIME = null
        }
    }
}
