package com.taskwizard.android.ui.components

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * 任务输入栏组件
 * 底部固定，包含输入框和启动/停止按钮
 *
 * @param task 当前任务描述
 * @param onTaskChange 任务描述变化回调
 * @param isRunning 是否正在运行
 * @param onStart 启动任务回调
 * @param onStop 停止任务回调
 * @param modifier 修饰符
 */
@Composable
fun TaskInputBar(
    task: String,
    onTaskChange: (String) -> Unit,
    isRunning: Boolean,
    onStart: () -> Unit,
    onStop: () -> Unit,
    modifier: Modifier = Modifier
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
        tonalElevation = 3.dp,
        shadowElevation = 8.dp,
        modifier = modifier
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // 输入框
            OutlinedTextField(
                value = task,
                onValueChange = onTaskChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("输入任务描述...") },
                enabled = !isRunning,
                maxLines = 3,
                shape = MaterialTheme.shapes.medium
            )

            // 启动/停止按钮（带动画过渡）
            // 性能优化：简化为纯淡入淡出，移除scale动画减少GPU负载
            AnimatedContent(
                targetState = isRunning,
                transitionSpec = {
                    fadeIn(animationSpec = tween(150)) togetherWith
                    fadeOut(animationSpec = tween(150))
                },
                label = "button_animation"
            ) { running ->
                if (running) {
                    // 停止按钮
                    FilledTonalButton(
                        onClick = onStop,
                        colors = ButtonDefaults.filledTonalButtonColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer,
                            contentColor = MaterialTheme.colorScheme.onErrorContainer
                        ),
                        // 性能优化：使用no-bouncy弹簧减少额外帧渲染
                        modifier = Modifier.animateContentSize(
                            animationSpec = spring(
                                dampingRatio = Spring.DampingRatioNoBouncy,
                                stiffness = Spring.StiffnessMedium
                            )
                        )
                    ) {
                        Icon(
                            imageVector = Icons.Rounded.Stop,
                            contentDescription = "停止",
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("停止")
                    }
                } else {
                    // 启动按钮
                    Button(
                        onClick = onStart,
                        enabled = task.isNotBlank(),
                        // 性能优化：使用no-bouncy弹簧减少额外帧渲染
                        modifier = Modifier.animateContentSize(
                            animationSpec = spring(
                                dampingRatio = Spring.DampingRatioNoBouncy,
                                stiffness = Spring.StiffnessMedium
                            )
                        )
                    ) {
                        Icon(
                            imageVector = Icons.Rounded.PlayArrow,
                            contentDescription = "启动",
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("启动")
                    }
                }
            }
        }
    }
}
