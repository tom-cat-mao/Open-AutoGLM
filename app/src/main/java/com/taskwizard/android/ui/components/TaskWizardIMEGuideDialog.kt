package com.taskwizard.android.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Keyboard
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties

/**
 * 键盘选择引导对话框
 *
 * 提供两种键盘选择：
 * 1. 内置键盘（推荐）- TaskWizard Keyboard
 * 2. ADB Keyboard - 需要额外安装
 *
 * @param onDismiss 用户关闭对话框回调
 * @param onUseBuiltIn 选择使用内置键盘回调
 * @param onUseADBKeyboard 选择使用 ADB Keyboard 回调
 * @param onDownloadADBKeyboard 下载 ADB Keyboard 回调
 */
@Composable
fun TaskWizardIMEGuideDialog(
    onDismiss: () -> Unit,
    onUseBuiltIn: () -> Unit,
    onUseADBKeyboard: () -> Unit = {},
    onDownloadADBKeyboard: () -> Unit = {}
) {
    var selectedOption by remember { mutableIntStateOf(0) } // 0: 内置, 1: ADB Keyboard

    AlertDialog(
        onDismissRequest = onDismiss,
        icon = {
            Icon(
                imageVector = Icons.Rounded.Keyboard,
                contentDescription = "输入法",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(48.dp)
            )
        },
        title = {
            Text(
                text = "选择输入法",
                style = MaterialTheme.typography.headlineSmall,
                textAlign = TextAlign.Center
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "TaskWizard 需要输入法来自动输入文字，请选择一种方式：",
                    style = MaterialTheme.typography.bodyMedium
                )

                // 选项1：内置键盘（推荐）
                KeyboardOptionCard(
                    title = "内置键盘（推荐）",
                    description = "使用 TaskWizard 内置的输入法，无需额外安装",
                    isSelected = selectedOption == 0,
                    isRecommended = true,
                    onClick = { selectedOption = 0 },
                    steps = listOf(
                        "点击下方按钮打开输入法设置",
                        "找到并启用「TaskWizard Keyboard」",
                        "在弹出的警告对话框中点击「确定」",
                        "返回 TaskWizard 即可使用"
                    )
                )

                // 选项2：ADB Keyboard
                KeyboardOptionCard(
                    title = "ADB Keyboard",
                    description = "使用外部 ADB Keyboard 应用，需要额外下载安装",
                    isSelected = selectedOption == 1,
                    isRecommended = false,
                    onClick = { selectedOption = 1 },
                    steps = listOf(
                        "下载并安装 ADB Keyboard 应用",
                        "在系统设置中启用 ADB Keyboard",
                        "返回 TaskWizard 即可使用"
                    ),
                    extraAction = {
                        TextButton(
                            onClick = onDownloadADBKeyboard,
                            modifier = Modifier.padding(top = 4.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Rounded.Download,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("下载 ADB Keyboard")
                        }
                    }
                )

                // 隐私说明
                Text(
                    text = "注意：这些输入法仅用于自动化任务，不会收集您的输入数据。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (selectedOption == 0) {
                        onUseBuiltIn()
                    } else {
                        onUseADBKeyboard()
                    }
                }
            ) {
                Text("打开输入法设置")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("稍后设置")
            }
        },
        properties = DialogProperties(
            dismissOnBackPress = true,
            dismissOnClickOutside = true
        )
    )
}

/**
 * 键盘选项卡片
 */
@Composable
private fun KeyboardOptionCard(
    title: String,
    description: String,
    isSelected: Boolean,
    isRecommended: Boolean,
    onClick: () -> Unit,
    steps: List<String>,
    extraAction: @Composable (() -> Unit)? = null
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (isSelected)
                MaterialTheme.colorScheme.primaryContainer
            else
                MaterialTheme.colorScheme.surfaceVariant
        ),
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                RadioButton(
                    selected = isSelected,
                    onClick = onClick
                )
                Column(modifier = Modifier.weight(1f)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        Text(
                            text = title,
                            style = MaterialTheme.typography.titleSmall,
                            color = if (isSelected)
                                MaterialTheme.colorScheme.onPrimaryContainer
                            else
                                MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        if (isRecommended) {
                            Icon(
                                imageVector = Icons.Rounded.Star,
                                contentDescription = "推荐",
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(16.dp)
                            )
                        }
                    }
                    Text(
                        text = description,
                        style = MaterialTheme.typography.bodySmall,
                        color = if (isSelected)
                            MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f)
                        else
                            MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                }
            }

            // 展开显示步骤（仅当选中时）
            if (isSelected) {
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 4.dp),
                    color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.2f)
                )
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        text = "操作步骤：",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                    steps.forEachIndexed { index, step ->
                        Text(
                            text = "${index + 1}. $step",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.8f)
                        )
                    }
                }
                extraAction?.invoke()
            }
        }
    }
}
