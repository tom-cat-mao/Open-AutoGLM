package com.example.autoglm.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer

/**
 * AnimatedMainContent - 可缩放的主界面容器
 *
 * 用于实现App缩小到悬浮窗的动画效果
 *
 * 关键特性：
 * - 缩放动画：从1.0缩小到0.1（10%大小）
 * - 位移动画：移动到右上角
 * - transformOrigin：以右上角为缩放中心
 * - 硬件加速：使用graphicsLayer确保流畅
 *
 * @param isAnimating 是否正在执行缩小动画
 * @param content 要显示的内容
 */
@Composable
fun AnimatedMainContent(
    isAnimating: Boolean,
    content: @Composable () -> Unit
) {
    // 缩放动画：1.0 -> 0.1
    val scale by animateFloatAsState(
        targetValue = if (isAnimating) 0.1f else 1f,
        animationSpec = tween(durationMillis = 300),
        label = "scale"
    )

    // X轴偏移动画：移动到右侧
    // 0.45 表示向右移动屏幕宽度的45%
    val offsetX by animateFloatAsState(
        targetValue = if (isAnimating) 0.45f else 0f,
        animationSpec = tween(durationMillis = 300),
        label = "offsetX"
    )

    // Y轴偏移动画：移动到顶部
    // -0.45 表示向上移动屏幕高度的45%
    val offsetY by animateFloatAsState(
        targetValue = if (isAnimating) -0.45f else 0f,
        animationSpec = tween(durationMillis = 300),
        label = "offsetY"
    )

    Box(
        modifier = Modifier
            .fillMaxSize()
            .graphicsLayer {
                // 应用缩放
                scaleX = scale
                scaleY = scale

                // 应用位移（相对于屏幕尺寸）
                translationX = size.width * offsetX
                translationY = size.height * offsetY

                // 关键：设置缩放中心为右上角
                // (1f, 0f) = (右边, 顶部)
                transformOrigin = TransformOrigin(1f, 0f)

                // 关键修复：保持 alpha 为 1.0，防止 Activity 被标记为透明
                // 即使在动画过程中也不改变透明度，避免系统杀死 Activity
                alpha = 1.0f
            }
    ) {
        content()
    }
}
