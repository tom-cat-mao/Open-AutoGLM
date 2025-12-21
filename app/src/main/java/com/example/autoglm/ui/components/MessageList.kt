package com.example.autoglm.ui.components

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ChatBubbleOutline
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.autoglm.data.MessageItem
import kotlinx.coroutines.launch

/**
 * 消息列表组件
 * 使用LazyColumn实现虚拟化列表，性能优化
 *
 * Material3动画特性：
 * - 消息出现时的淡入+滑入动画
 * - 列表项位置变化的平滑动画
 * - 自动滚动到最新消息
 *
 * 性能优化：使用唯一 ID 作为 key，确保列表项正确追踪
 *
 * @param messages 消息列表
 * @param modifier 修饰符
 */
@Composable
fun MessageList(
    messages: List<MessageItem>,
    modifier: Modifier = Modifier
) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    // 自动滚动到最新消息（带动画）
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            scope.launch {
                listState.animateScrollToItem(
                    index = messages.size - 1,
                    scrollOffset = 0
                )
            }
        }
    }

    if (messages.isEmpty()) {
        // 空状态显示
        EmptyMessagePlaceholder(modifier = modifier)
    } else {
        LazyColumn(
            state = listState,
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = modifier
        ) {
            items(
                items = messages,
                key = { it.id }  // ✅ 性能优化：使用唯一 ID 避免重组
            ) { message ->
                // Material3动画：消息出现时的淡入+滑入动画
                AnimatedVisibility(
                    visible = true,
                    enter = fadeIn(
                        animationSpec = tween(
                            durationMillis = 250,
                            easing = FastOutSlowInEasing
                        ),
                        initialAlpha = 0f
                    ) + slideInVertically(
                        animationSpec = tween(
                            durationMillis = 250,
                            easing = FastOutSlowInEasing
                        ),
                        initialOffsetY = { it / 4 } // 从下方1/4处滑入
                    )
                ) {
                    when (message) {
                        is MessageItem.ThinkMessage -> ThinkMessageBubble(message)
                        is MessageItem.ActionMessage -> ActionMessageBubble(message)
                        is MessageItem.SystemMessage -> SystemMessageBubble(message)
                    }
                }
            }
        }
    }
}

/**
 * 空状态占位符
 * 当没有消息时显示
 *
 * Material3动画：淡入动画
 */
@Composable
private fun EmptyMessagePlaceholder(
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        // 添加淡入动画
        AnimatedVisibility(
            visible = true,
            enter = fadeIn(
                animationSpec = tween(
                    durationMillis = 300,
                    easing = FastOutSlowInEasing
                )
            )
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Icon(
                    imageVector = Icons.Rounded.ChatBubbleOutline,
                    contentDescription = null,
                    modifier = Modifier.size(64.dp),
                    tint = MaterialTheme.colorScheme.outline
                )
                Text(
                    text = "输入任务开始对话",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Text(
                    text = "例如：打开微信发消息给张三",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.outline
                )
            }
        }
    }
}
