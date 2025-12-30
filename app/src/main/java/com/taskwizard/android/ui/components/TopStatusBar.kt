package com.taskwizard.android.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AddCircle
import androidx.compose.material.icons.rounded.History
import androidx.compose.material.icons.automirrored.rounded.PlaylistPlay
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * 顶部状态栏组件
 * 显示模型名称、系统状态指示器、设置按钮
 *
 * @param modelName 模型名称
 * @param hasShizuku Shizuku是否可用
 * @param hasTaskWizardIME TaskWizard 内置键盘是否已启用
 * @param hasADBKeyboard ADB Keyboard是否已启用
 * @param onSettingsClick 设置按钮点击回调
 * @param onHistoryClick 历史按钮点击回调
 * @param onTasksClick Tasks按钮点击回调
 * @param onNewConversationClick 新建对话按钮点击回调
 * @param onShizukuClick Shizuku状态芯片点击回调
 * @param onKeyboardClick 键盘状态芯片点击回调
 * @param modifier 修饰符
 */
@Composable
fun TopStatusBar(
    modelName: String,
    hasShizuku: Boolean,
    hasTaskWizardIME: Boolean,
    hasADBKeyboard: Boolean,
    onSettingsClick: () -> Unit,
    onHistoryClick: () -> Unit,
    onTasksClick: () -> Unit,
    onNewConversationClick: () -> Unit,
    onShizukuClick: () -> Unit = {},
    onKeyboardClick: () -> Unit = {},
    modifier: Modifier = Modifier
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
        tonalElevation = 3.dp,
        shadowElevation = 2.dp,
        modifier = modifier
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            // 左侧：模型名称和状态指示器
            Column(
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    text = modelName,
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    StatusChip(
                        label = "Shizuku",
                        isActive = hasShizuku,
                        onClick = onShizukuClick
                    )
                    // 性能优化：缓存键盘标签计算
                    val keyboardLabel = remember(hasTaskWizardIME, hasADBKeyboard) {
                        when {
                            hasTaskWizardIME -> "内置键盘"
                            hasADBKeyboard -> "ADB Keyboard"
                            else -> "键盘未启用"
                        }
                    }
                    val keyboardActive = hasTaskWizardIME || hasADBKeyboard
                    StatusChip(
                        label = keyboardLabel,
                        isActive = keyboardActive,
                        onClick = onKeyboardClick
                    )
                }
            }

            // 右侧：历史、Tasks、新建对话和设置按钮
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                IconButton(onClick = onHistoryClick) {
                    Icon(
                        imageVector = Icons.Rounded.History,
                        contentDescription = "历史",
                        tint = MaterialTheme.colorScheme.onSurface
                    )
                }
                IconButton(onClick = onTasksClick) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Rounded.PlaylistPlay,
                        contentDescription = "Tasks",
                        tint = MaterialTheme.colorScheme.onSurface
                    )
                }
                IconButton(onClick = onNewConversationClick) {
                    Icon(
                        imageVector = Icons.Rounded.AddCircle,
                        contentDescription = "新建对话",
                        tint = MaterialTheme.colorScheme.onSurface
                    )
                }
                IconButton(onClick = onSettingsClick) {
                    Icon(
                        imageVector = Icons.Rounded.Settings,
                        contentDescription = "设置",
                        tint = MaterialTheme.colorScheme.onSurface
                    )
                }
            }
        }
    }
}

/**
 * 状态指示器芯片
 * 显示系统状态（Shizuku、ADB Keyboard等）
 *
 * @param label 状态标签
 * @param isActive 是否激活
 * @param onClick 点击回调
 * @param modifier 修饰符
 */
@Composable
fun StatusChip(
    label: String,
    isActive: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Surface(
        color = if (isActive)
            MaterialTheme.colorScheme.primaryContainer
        else
            MaterialTheme.colorScheme.errorContainer,
        shape = MaterialTheme.shapes.small,
        modifier = modifier.clickable(onClick = onClick)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // 状态指示点
            Surface(
                color = if (isActive)
                    MaterialTheme.colorScheme.primary
                else
                    MaterialTheme.colorScheme.error,
                shape = MaterialTheme.shapes.extraSmall,
                modifier = Modifier.size(6.dp)
            ) {}

            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall
            )
        }
    }
}
