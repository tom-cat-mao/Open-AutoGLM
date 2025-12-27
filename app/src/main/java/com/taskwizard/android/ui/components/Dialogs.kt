package com.taskwizard.android.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Warning
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties

/**
 * 对话框组件集合
 *
 * 包含：
 * 1. ConfirmDialog - 敏感操作确认对话框
 * 2. TakeOverDialog - 人工接管对话框
 * 3. PermissionDialog - 权限请求对话框
 */

/**
 * 确认对话框
 *
 * 用于敏感操作的确认，例如：
 * - 删除操作
 * - 支付操作
 * - 重要设置修改
 *
 * @param message 确认消息
 * @param onConfirm 用户确认回调
 * @param onDismiss 用户取消回调
 */
@Composable
fun ConfirmDialog(
    message: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = {
            Icon(
                imageVector = Icons.Rounded.Warning,
                contentDescription = "警告",
                tint = MaterialTheme.colorScheme.error,
                modifier = Modifier.size(48.dp)
            )
        },
        title = {
            Text(
                text = "确认操作",
                style = MaterialTheme.typography.headlineSmall,
                textAlign = TextAlign.Center
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    text = message,
                    style = MaterialTheme.typography.bodyLarge,
                    textAlign = TextAlign.Center
                )

                Spacer(modifier = Modifier.height(8.dp))

                Text(
                    text = "此操作可能具有风险，请确认是否继续",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center
                )
            }
        },
        confirmButton = {
            Button(
                onClick = onConfirm,
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error
                )
            ) {
                Text("确认执行")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        },
        properties = DialogProperties(
            dismissOnBackPress = true,
            dismissOnClickOutside = false // 防止误触
        )
    )
}

/**
 * 人工接管对话框
 *
 * 当AI需要人工介入时显示，例如：
 * - 需要输入验证码
 * - 需要人脸识别
 * - 需要手动操作某些步骤
 *
 * @param message 接管提示消息
 * @param onComplete 用户完成操作回调
 * @param onCancel 用户取消任务回调
 */
@Composable
fun TakeOverDialog(
    message: String,
    onComplete: () -> Unit,
    onCancel: () -> Unit
) {
    AlertDialog(
        onDismissRequest = { /* 不允许点击外部关闭 */ },
        icon = {
            Icon(
                imageVector = Icons.Rounded.Info,
                contentDescription = "信息",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(48.dp)
            )
        },
        title = {
            Text(
                text = "需要人工介入",
                style = MaterialTheme.typography.headlineSmall,
                textAlign = TextAlign.Center
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = message,
                    style = MaterialTheme.typography.bodyLarge,
                    textAlign = TextAlign.Center
                )

                Spacer(modifier = Modifier.height(8.dp))

                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer
                    )
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "操作步骤：",
                            style = MaterialTheme.typography.titleSmall,
                            color = MaterialTheme.colorScheme.onPrimaryContainer
                        )
                        Text(
                            text = "1. 请手动完成所需操作\n2. 完成后点击「继续任务」按钮\n3. AI将继续执行后续步骤",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onPrimaryContainer
                        )
                    }
                }
            }
        },
        confirmButton = {
            Button(onClick = onComplete) {
                Text("继续任务")
            }
        },
        dismissButton = {
            TextButton(onClick = onCancel) {
                Text("取消任务")
            }
        },
        properties = DialogProperties(
            dismissOnBackPress = false,
            dismissOnClickOutside = false
        )
    )
}

/**
 * 权限请求对话框
 *
 * 用于引导用户授予必要的权限，例如：
 * - Shizuku权限
 * - ADB Keyboard安装和启用
 *
 * @param title 对话框标题
 * @param message 权限说明消息
 * @param permissionType 权限类型（用于显示不同的引导步骤）
 * @param onGrant 用户同意授权回调
 * @param onDismiss 用户取消回调
 * @param onOpenShizukuApp 打开Shizuku应用的回调（可选）
 * @param onOpenIMESettings 打开输入法设置的回调（可选）
 * @param onOpenDownloadPage 打开ADB Keyboard下载页面的回调（可选）
 */
@Composable
fun PermissionDialog(
    title: String,
    message: String,
    permissionType: PermissionType,
    onGrant: () -> Unit,
    onDismiss: () -> Unit,
    onOpenShizukuApp: (() -> Unit)? = null,
    onOpenIMESettings: (() -> Unit)? = null,
    onOpenDownloadPage: (() -> Unit)? = null
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = {
            Icon(
                imageVector = Icons.Rounded.CheckCircle,
                contentDescription = "权限",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(48.dp)
            )
        },
        title = {
            Text(
                text = title,
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
                    text = message,
                    style = MaterialTheme.typography.bodyLarge
                )

                Spacer(modifier = Modifier.height(8.dp))

                // 根据权限类型显示不同的引导步骤
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.secondaryContainer
                    )
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = when (permissionType) {
                                PermissionType.SHIZUKU -> "授权步骤："
                                PermissionType.ADB_KEYBOARD -> "安装步骤："
                            },
                            style = MaterialTheme.typography.titleSmall,
                            color = MaterialTheme.colorScheme.onSecondaryContainer
                        )

                        Text(
                            text = when (permissionType) {
                                PermissionType.SHIZUKU -> """
                                    1. 确保已安装Shizuku应用
                                    2. 打开Shizuku并启动服务
                                    3. 在Shizuku中授权本应用
                                    4. 返回本应用重试
                                """.trimIndent()
                                PermissionType.ADB_KEYBOARD -> """
                                    1. 下载并安装ADB Keyboard
                                    2. 在系统设置中启用ADB Keyboard
                                    3. 返回本应用重试

                                    注：ADB Keyboard用于自动输入文本
                                """.trimIndent()
                            },
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSecondaryContainer
                        )
                    }
                }
            }
        },
        confirmButton = {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // Quick action buttons (if provided)
                when (permissionType) {
                    PermissionType.SHIZUKU -> {
                        onOpenShizukuApp?.let {
                            TextButton(onClick = it) {
                                Text("打开Shizuku")
                            }
                        }
                    }
                    PermissionType.ADB_KEYBOARD -> {
                        onOpenIMESettings?.let {
                            TextButton(onClick = it) {
                                Text("输入法设置")
                            }
                        }
                        onOpenDownloadPage?.let {
                            TextButton(onClick = it) {
                                Text("下载")
                            }
                        }
                    }
                }

                // Primary action button
                Button(onClick = onGrant) {
                    Text(
                        when (permissionType) {
                            PermissionType.SHIZUKU -> "去授权"
                            PermissionType.ADB_KEYBOARD -> "去设置"
                        }
                    )
                }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("稍后")
            }
        },
        properties = DialogProperties(
            dismissOnBackPress = true,
            dismissOnClickOutside = true
        )
    )
}

/**
 * 权限类型枚举
 */
enum class PermissionType {
    SHIZUKU,        // Shizuku权限
    ADB_KEYBOARD    // ADB Keyboard
}
