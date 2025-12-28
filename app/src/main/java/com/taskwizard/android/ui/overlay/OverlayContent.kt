package com.taskwizard.android.ui.overlay

import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.ripple.rememberRipple
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.taskwizard.android.data.OverlayDisplayState
import com.taskwizard.android.data.OverlayState
import com.taskwizard.android.ui.viewmodel.OverlayViewModel
import kotlinx.coroutines.delay

/**
 * OverlayContent - 悬浮窗主UI组件
 *
 * Material3风格的悬浮窗UI
 * 支持透明度变化、点击交互、状态显示
 *
 * @param viewModel 悬浮窗ViewModel
 * @param onExit 退出回调
 * @param onReturnToApp 返回应用回调
 */
@Composable
fun OverlayContent(
    viewModel: OverlayViewModel,
    onExit: () -> Unit,
    onReturnToApp: () -> Unit,
    onTakeoverComplete: (() -> Unit)? = null
) {
    val state by viewModel.state.collectAsState()

    // 自动恢复逻辑
    LaunchedEffect(state.displayState) {
        if (state.displayState == OverlayDisplayState.CONFIRM_EXIT) {
            delay(3000) // 3秒后自动恢复
            viewModel.handleAutoRestore()
        }
    }

    // Material3 Surface with transparency
    Surface(
        modifier = Modifier
            .width(140.dp)
            .wrapContentHeight()
            .graphicsLayer {
                // 使用graphicsLayer获得硬件加速
                alpha = state.getAlpha()
            }
            .clickable(
                indication = rememberRipple(),
                interactionSource = remember { MutableInteractionSource() }
            ) {
                handleClick(
                    state = state,
                    viewModel = viewModel,
                    onExit = onExit,
                    onReturnToApp = onReturnToApp,
                    onTakeoverComplete = onTakeoverComplete
                )
            },
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surfaceContainer,
        tonalElevation = 4.dp,
        shadowElevation = 8.dp
    ) {
        OverlayContentLayout(state)
    }
}

/**
 * 处理点击事件（阶段3修改）
 */
private fun handleClick(
    state: OverlayState,
    viewModel: OverlayViewModel,
    onExit: () -> Unit,
    onReturnToApp: () -> Unit,
    onTakeoverComplete: (() -> Unit)? = null
) {
    when (state.displayState) {
        OverlayDisplayState.TAKEOVER -> {
            // 人工接管状态：点击完成接管
            onTakeoverComplete?.invoke()
        }
        OverlayDisplayState.CONFIRM_EXIT -> {
            // 第3次点击：确认退出
            onExit()
        }
        OverlayDisplayState.COMPLETED -> {
            // 完成状态的点击处理
            if (state.getAlpha() < 1f) {
                // 第1次点击：半透明 → 不透明
                viewModel.handleClick()
            } else {
                // 第2次点击：触发放大动画并返回应用
                // 注意：这里需要通过OverlayService调用animateToFullScreen
                // 由于这里无法直接访问OverlayService，需要通过onReturnToApp回调
                onReturnToApp()
            }
        }
        else -> {
            // 正常状态切换
            viewModel.handleClick()
        }
    }
}

/**
 * 悬浮窗内容布局
 */
@Composable
private fun OverlayContentLayout(state: OverlayState) {
    Column(
        modifier = Modifier
            .padding(12.dp)
            .fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        // 状态文本
        Text(
            text = state.getDisplayText(),
            style = MaterialTheme.typography.bodySmall,
            color = if (state.errorMessage != null) {
                // 错误时使用红色
                MaterialTheme.colorScheme.error
            } else {
                MaterialTheme.colorScheme.onSurface
            },
            maxLines = 3,  // 错误消息可能需要更多行
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth()
        )

        // 进度指示
        if (state.shouldShowThinkingIndicator() || state.shouldShowActionIndicator()) {
            LinearProgressIndicator(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(2.dp),
                color = if (state.errorMessage != null) {
                    MaterialTheme.colorScheme.error
                } else {
                    MaterialTheme.colorScheme.primary
                }
            )
        }
    }
}
