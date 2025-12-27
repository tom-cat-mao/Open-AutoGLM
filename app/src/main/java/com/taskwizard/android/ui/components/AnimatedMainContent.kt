package com.taskwizard.android.ui.components

import androidx.compose.animation.core.AnimationVector
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.VectorConverter
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.util.lerp

/**
 * AnimatedMainContent - 可缩放的主界面容器
 *
 * 用于实现App缩小到悬浮窗的动画效果
 *
 * 性能优化：使用单一动画变量计算所有变换，减少GPU负载
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
    // 性能优化：使用单一动画进度变量，一次性计算所有变换值
    // 将三个独立的 animateFloatAsState 合并为一个，减少动画开销
    val animationProgress by animateFloatAsState(
        targetValue = if (isAnimating) 1f else 0f,
        animationSpec = tween(durationMillis = 250),
        label = "overlay_animation"
    )

    Box(
        modifier = Modifier
            .fillMaxSize()
            .graphicsLayer {
                // 性能优化：使用lerp一次性计算所有变换值，避免每帧多次计算
                val scale = lerp(1f, 0.15f, animationProgress)
                val offsetX = lerp(0f, 0.45f, animationProgress)
                val offsetY = lerp(0f, -0.45f, animationProgress)

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
