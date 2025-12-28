package com.taskwizard.android.data

import androidx.compose.runtime.Stable

/**
 * OverlayDisplayState - 悬浮窗显示状态
 *
 * 定义悬浮窗的四种显示状态，控制透明度和交互行为
 */
enum class OverlayDisplayState {
    /**
     * 半透明状态
     * - 透明度：50%
     * - 可透视后面内容
     * - 文字清晰可见
     * - 默认状态
     */
    TRANSPARENT,

    /**
     * 正常不透明状态
     * - 透明度：100%
     * - 完全不透明
     * - 单击TRANSPARENT后进入此状态
     */
    NORMAL,

    /**
     * 确认退出状态
     * - 透明度：100%
     * - 显示红色圆点
     * - 单击NORMAL后进入此状态
     * - 3秒无操作自动恢复到TRANSPARENT
     */
    CONFIRM_EXIT,

    /**
     * 任务完成状态
     * - 透明度：100%
     * - 显示完成图标
     * - 点击可返回应用
     */
    COMPLETED,

    /**
     * 人工接管状态
     * - 透明度：100%
     * - 显示倒计时
     * - 等待用户手动操作完成
     * - 点击悬浮窗完成接管
     */
    TAKEOVER
}

/**
 * OverlayState - 悬浮窗状态
 *
 * 包含悬浮窗的所有状态信息
 * 使用data class确保Compose可以正确追踪状态变化
 */
@Stable
data class OverlayState(
    /**
     * 显示状态
     * 控制透明度和交互行为
     */
    val displayState: OverlayDisplayState = OverlayDisplayState.TRANSPARENT,

    /**
     * 状态文本
     * 显示在悬浮窗中的文本内容
     */
    val statusText: String = "",

    /**
     * 是否正在思考
     * true: 显示"Thinking..."
     * false: 显示action或statusText
     */
    val isThinking: Boolean = false,

    /**
     * 当前动作
     * 显示简化的动作描述
     * 例如："点击 [100,200]", "输入文本", "滑动"
     */
    val currentAction: String? = null,

    /**
     * 任务是否正在运行
     */
    val isTaskRunning: Boolean = false,

    /**
     * 任务是否已完成
     */
    val isTaskCompleted: Boolean = false,

    /**
     * 上次点击时间戳
     * 用于实现自动恢复逻辑
     */
    val lastClickTimestamp: Long = 0L,

    /**
     * 当前错误消息
     */
    val errorMessage: String? = null,

    /**
     * 当前步骤数
     */
    val currentStep: Int = 0,

    /**
     * 重试次数
     */
    val retryCount: Int = 0,

    /**
     * 最大重试次数
     */
    val maxRetries: Int = 3,

    /**
     * 是否处于人工接管状态
     */
    val isTakeover: Boolean = false,

    /**
     * 人工接管消息
     */
    val takeoverMessage: String? = null,

    /**
     * 人工接管剩余秒数
     */
    val takeoverRemainingSeconds: Int = 0,

    /**
     * 人工接管总超时秒数
     */
    val takeoverTotalSeconds: Int = 180
) {
    /**
     * 获取显示文本
     * 根据当前状态返回应该显示的文本
     */
    fun getDisplayText(): String {
        return when {
            // 人工接管优先级最高
            isTakeover -> {
                val minutes = takeoverRemainingSeconds / 60
                val seconds = takeoverRemainingSeconds % 60
                "人工接管\n时间：${minutes}:${String.format("%02d", seconds)}"
            }
            // 错误优先级次之
            errorMessage != null -> {
                if (retryCount > 0) {
                    "错误: $errorMessage\n重试: $retryCount/$maxRetries"
                } else {
                    "错误: $errorMessage"
                }
            }
            isTaskCompleted -> "已完成"
            displayState == OverlayDisplayState.CONFIRM_EXIT -> "确认退出?"
            isThinking -> "Thinking..."
            currentAction != null -> currentAction
            currentStep > 0 -> "Step $currentStep"
            else -> statusText.ifEmpty { "就绪" }
        }
    }

    /**
     * 获取透明度
     * 根据显示状态返回对应的透明度值
     */
    fun getAlpha(): Float {
        return when (displayState) {
            OverlayDisplayState.TRANSPARENT -> 0.5f
            else -> 1.0f
        }
    }

    /**
     * 是否应该显示退出确认指示器（红点）
     */
    fun shouldShowExitIndicator(): Boolean {
        return displayState == OverlayDisplayState.CONFIRM_EXIT
    }

    /**
     * 是否应该显示完成指示器
     */
    fun shouldShowCompletedIndicator(): Boolean {
        return isTaskCompleted
    }

    /**
     * 是否应该显示思考指示器
     */
    fun shouldShowThinkingIndicator(): Boolean {
        return isThinking && !isTaskCompleted
    }

    /**
     * 是否应该显示动作指示器
     */
    fun shouldShowActionIndicator(): Boolean {
        return !isThinking && !isTaskCompleted && !isTakeover && currentAction != null
    }

    /**
     * 是否应该显示人工接管指示器
     */
    fun shouldShowTakeoverIndicator(): Boolean {
        return isTakeover
    }
}
