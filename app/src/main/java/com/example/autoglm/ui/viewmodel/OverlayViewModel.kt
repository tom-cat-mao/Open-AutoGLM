package com.example.autoglm.ui.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.autoglm.data.OverlayDisplayState
import com.example.autoglm.data.OverlayState
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * OverlayViewModel - 悬浮窗状态管理
 *
 * 负责管理悬浮窗的所有状态和交互逻辑
 * 使用StateFlow实现响应式状态管理
 *
 * 关键功能：
 * - 状态切换：TRANSPARENT -> NORMAL -> CONFIRM_EXIT -> 退出
 * - 自动恢复：CONFIRM_EXIT状态3秒后自动恢复到TRANSPARENT
 * - Thinking状态管理
 * - Action状态管理
 * - 任务完成状态管理
 */
class OverlayViewModel : ViewModel() {

    companion object {
        private const val AUTO_RESTORE_DELAY = 3000L // 3秒自动恢复
    }

    // ==================== 状态管理 ====================

    private val _state = MutableStateFlow(OverlayState())
    val state: StateFlow<OverlayState> = _state.asStateFlow()

    // 自动恢复Job
    private var autoRestoreJob: Job? = null

    // ==================== 点击处理 ====================

    /**
     * 处理点击事件
     * 实现状态循环：TRANSPARENT -> NORMAL -> CONFIRM_EXIT -> 退出
     */
    fun handleClick() {
        val currentState = _state.value.displayState

        when (currentState) {
            OverlayDisplayState.TRANSPARENT -> {
                // 半透明 -> 不透明
                updateDisplayState(OverlayDisplayState.NORMAL)
            }

            OverlayDisplayState.NORMAL -> {
                // 不透明 -> 确认退出（显示红点）
                updateDisplayState(OverlayDisplayState.CONFIRM_EXIT)
                startAutoRestore()
            }

            OverlayDisplayState.CONFIRM_EXIT -> {
                // 确认退出 -> 触发退出回调
                // 实际退出由Service处理
                cancelAutoRestore()
            }

            OverlayDisplayState.COMPLETED -> {
                // 完成状态 -> 触发返回应用回调
                // 实际返回由Service处理
            }
        }

        // 更新点击时间戳
        _state.update { it.copy(lastClickTimestamp = System.currentTimeMillis()) }
    }

    /**
     * 更新显示状态
     */
    private fun updateDisplayState(newState: OverlayDisplayState) {
        _state.update { it.copy(displayState = newState) }
    }

    /**
     * 启动自动恢复计时器
     * CONFIRM_EXIT状态3秒后自动恢复到TRANSPARENT
     */
    private fun startAutoRestore() {
        cancelAutoRestore()
        autoRestoreJob = viewModelScope.launch {
            delay(AUTO_RESTORE_DELAY)
            handleAutoRestore()
        }
    }

    /**
     * 取消自动恢复计时器
     */
    private fun cancelAutoRestore() {
        autoRestoreJob?.cancel()
        autoRestoreJob = null
    }

    /**
     * 自动恢复到TRANSPARENT状态
     */
    fun handleAutoRestore() {
        if (_state.value.displayState == OverlayDisplayState.CONFIRM_EXIT) {
            updateDisplayState(OverlayDisplayState.TRANSPARENT)
        }
    }

    // ==================== 状态更新方法 ====================

    /**
     * 更新Thinking状态
     *
     * @param isThinking true表示正在思考，false表示思考结束
     */
    fun updateThinkingState(isThinking: Boolean) {
        _state.update { it.copy(isThinking = isThinking) }
    }

    /**
     * 更新Action
     *
     * @param action 动作描述，例如："点击 [100,200]", "输入文本"
     */
    fun updateAction(action: String) {
        _state.update {
            it.copy(
                currentAction = action,
                isThinking = false
            )
        }
    }

    /**
     * 更新状态文本
     *
     * @param status 状态文本
     */
    fun updateStatus(status: String) {
        _state.update { it.copy(statusText = status) }
    }

    /**
     * 标记任务完成
     */
    fun markTaskCompleted() {
        _state.update {
            it.copy(
                isTaskCompleted = true,
                isTaskRunning = false,
                displayState = OverlayDisplayState.COMPLETED,
                isThinking = false,
                currentAction = null
            )
        }
        cancelAutoRestore()
    }

    /**
     * 标记任务开始
     */
    fun markTaskStarted() {
        _state.update {
            it.copy(
                isTaskRunning = true,
                isTaskCompleted = false,
                displayState = OverlayDisplayState.TRANSPARENT
            )
        }
    }

    /**
     * 重置状态
     */
    fun reset() {
        cancelAutoRestore()
        _state.value = OverlayState()
    }

    // ==================== 清理 ====================

    override fun onCleared() {
        super.onCleared()
        cancelAutoRestore()
    }

    // ==================== 状态查询 ====================

    /**
     * 获取当前显示状态
     */
    fun getCurrentDisplayState(): OverlayDisplayState {
        return _state.value.displayState
    }

    /**
     * 是否正在思考
     */
    fun isThinking(): Boolean {
        return _state.value.isThinking
    }

    /**
     * 是否已完成
     */
    fun isCompleted(): Boolean {
        return _state.value.isTaskCompleted
    }

    /**
     * 是否正在运行
     */
    fun isRunning(): Boolean {
        return _state.value.isTaskRunning
    }
}
