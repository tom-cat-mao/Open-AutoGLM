package com.taskwizard.android

import android.util.Log
import com.taskwizard.android.core.AgentCore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * TaskScope - Application 级别的协程作用域
 *
 * 用于执行长时间运行的任务，不受 Activity 生命周期影响
 * 使用 SupervisorJob 确保子协程失败不会影响其他协程
 *
 * 关键特性：
 * - 独立于 Activity 生命周期
 * - 使用 SupervisorJob 实现错误隔离
 * - 使用 Dispatchers.Default 作为默认调度器
 * - 支持全局取消（应用退出时）
 * - 提供任务管理方法（启动、停止）
 * - 使用 invokeOnCompletion 保证清理代码执行
 * - 支持任务停止回调，用于状态同步
 */
object TaskScope {
    private const val TAG = "TaskScope"
    private val job = SupervisorJob()
    val scope = CoroutineScope(Dispatchers.Default + job)

    // 当前运行的任务 Job
    private var currentTaskJob: Job? = null

    // 当前任务的 AgentCore 引用
    private var currentAgentCore: AgentCore? = null

    // 清理回调列表
    private val cleanupCallbacks = mutableListOf<() -> Unit>()

    // 任务停止回调（用于通知 ViewModel 更新状态）
    private var onTaskStoppedCallback: (() -> Unit)? = null

    /**
     * 注册清理回调
     * 这些回调会在任务取消或完成时执行
     */
    fun registerCleanupCallback(callback: () -> Unit) {
        cleanupCallbacks.add(callback)
        Log.d(TAG, "Cleanup callback registered (total: ${cleanupCallbacks.size})")
    }

    /**
     * 设置任务停止回调
     * 用于通知 ViewModel 更新状态
     *
     * @param callback 任务停止时的回调函数
     */
    fun setOnTaskStoppedCallback(callback: () -> Unit) {
        onTaskStoppedCallback = callback
        Log.d(TAG, "Task stopped callback registered")
    }

    /**
     * 清除任务停止回调
     */
    fun clearOnTaskStoppedCallback() {
        onTaskStoppedCallback = null
        Log.d(TAG, "Task stopped callback cleared")
    }

    /**
     * 启动新任务
     * @param agentCore AgentCore 实例
     * @param block 任务执行块
     * @return 任务 Job
     */
    fun launchTask(agentCore: AgentCore, block: suspend CoroutineScope.() -> Unit): Job {
        // 先停止当前任务
        stopCurrentTask()

        currentAgentCore = agentCore
        cleanupCallbacks.clear()

        currentTaskJob = scope.launch(block = block)

        // 关键：使用 invokeOnCompletion 保证清理代码执行
        currentTaskJob?.invokeOnCompletion { cause ->
            Log.d(TAG, "Task completed with cause: ${cause?.javaClass?.simpleName}")

            // 执行所有清理回调
            try {
                cleanupCallbacks.forEach { callback ->
                    try {
                        callback()
                    } catch (e: Exception) {
                        Log.e(TAG, "Cleanup callback failed", e)
                    }
                }
                Log.d(TAG, "All cleanup callbacks executed (${cleanupCallbacks.size} callbacks)")
            } catch (e: Exception) {
                Log.e(TAG, "Error executing cleanup callbacks", e)
            }
        }

        Log.d(TAG, "New task launched: ${currentTaskJob?.hashCode()}")
        return currentTaskJob ?: throw IllegalStateException("Task job was not created")
    }

    /**
     * 停止当前任务
     * 使用 cancel() 非阻塞方式取消任务
     */
    fun stopCurrentTask() {
        if (currentTaskJob != null && currentTaskJob?.isActive == true) {
            Log.d(TAG, "Stopping current task: ${currentTaskJob?.hashCode()}")

            // 1. 停止 AgentCore
            currentAgentCore?.stop()

            // 2. 取消协程（非阻塞）
            currentTaskJob?.cancel()
            Log.d(TAG, "Task cancellation requested")

            // 3. 通知任务停止回调（关键修复：通知 ViewModel 更新状态）
            try {
                onTaskStoppedCallback?.invoke()
                Log.d(TAG, "Task stopped callback invoked")
            } catch (e: Exception) {
                Log.e(TAG, "Error invoking task stopped callback", e)
            }

            currentTaskJob = null
            currentAgentCore = null
        } else {
            Log.d(TAG, "No active task to stop")
        }
    }

    /**
     * 取消所有任务
     * 应该在应用退出时调用
     */
    fun cancelAll() {
        Log.d(TAG, "Cancelling all tasks")
        stopCurrentTask()
        job.cancel()
    }
}
