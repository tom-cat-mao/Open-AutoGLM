package com.example.autoglm.ui.overlay

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.example.autoglm.data.OverlayState

/**
 * StatusIndicator - 状态指示器
 *
 * 根据悬浮窗状态显示不同的指示器
 * - Thinking: 圆形进度条
 * - Action: 圆形进度条
 * - Confirm Exit: 红色圆点
 * - Completed: 完成图标
 */
@Composable
fun StatusIndicator(state: OverlayState) {
    Box(
        modifier = Modifier.size(24.dp),
        contentAlignment = Alignment.Center
    ) {
        when {
            state.shouldShowCompletedIndicator() -> {
                CompletedIndicator()
            }
            state.shouldShowExitIndicator() -> {
                ExitConfirmIndicator()
            }
            state.shouldShowThinkingIndicator() -> {
                ThinkingIndicator()
            }
            state.shouldShowActionIndicator() -> {
                ActionIndicator()
            }
            else -> {
                // 默认状态：显示小圆点
                IdleIndicator()
            }
        }
    }
}

/**
 * Thinking指示器
 * 显示蓝色圆点，表示AI正在思考
 */
@Composable
private fun ThinkingIndicator() {
    Box(
        modifier = Modifier
            .size(12.dp)
            .background(
                color = MaterialTheme.colorScheme.primary,
                shape = CircleShape
            )
    )
}

/**
 * Action指示器
 * 显示绿色圆点，表示正在执行动作
 */
@Composable
private fun ActionIndicator() {
    Box(
        modifier = Modifier
            .size(12.dp)
            .background(
                color = MaterialTheme.colorScheme.secondary,
                shape = CircleShape
            )
    )
}

/**
 * 退出确认指示器
 * 显示红色圆点，表示等待用户确认退出
 */
@Composable
private fun ExitConfirmIndicator() {
    Box(
        modifier = Modifier
            .size(12.dp)
            .background(
                color = Color.Red,
                shape = CircleShape
            )
    )
}

/**
 * 完成指示器
 * 显示绿色对勾图标，表示任务完成
 */
@Composable
private fun CompletedIndicator() {
    Icon(
        imageVector = Icons.Default.CheckCircle,
        contentDescription = "完成",
        tint = MaterialTheme.colorScheme.primary,
        modifier = Modifier.size(24.dp)
    )
}

/**
 * 空闲指示器
 * 显示小圆点，表示就绪状态
 */
@Composable
private fun IdleIndicator() {
    Box(
        modifier = Modifier
            .size(8.dp)
            .background(
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                shape = CircleShape
            )
    )
}
