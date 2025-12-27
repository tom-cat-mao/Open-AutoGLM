package com.taskwizard.android.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.taskwizard.android.ui.components.*
import com.taskwizard.android.ui.utils.AppLauncher
import com.taskwizard.android.ui.viewmodel.MainViewModel
import kotlinx.coroutines.delay

/**
 * 主页面
 *
 * 这是应用的主界面，包含：
 * - 顶部状态区域（模型名称、系统状态、设置按钮）
 * - 中间消息列表区域
 * - 底部任务输入栏
 * - 对话框（确认对话框、人工接管对话框）
 *
 * @param onNavigateToSettings 导航到设置页面的回调
 * @param onNavigateToHistory 导航到历史页面的回调
 * @param viewModel 共享的ViewModel实例
 * @param historyIdToLoad 要加载的历史记录ID（用于继续对话）
 */
@Composable
fun MainScreen(
    onNavigateToSettings: () -> Unit,
    onNavigateToHistory: () -> Unit,
    viewModel: MainViewModel,
    historyIdToLoad: Long? = null
) {
    val context = LocalContext.current

    // 收集状态
    val state by viewModel.state.collectAsStateWithLifecycle()
    val confirmationRequest by viewModel.confirmationRequest.collectAsStateWithLifecycle()
    val takeOverRequest by viewModel.takeOverRequest.collectAsStateWithLifecycle()
    val showShizukuGuide by viewModel.showShizukuGuide.collectAsStateWithLifecycle()
    val showADBKeyboardGuide by viewModel.showADBKeyboardGuide.collectAsStateWithLifecycle()
    val showOverlayPermissionGuide by viewModel.showOverlayPermissionGuide.collectAsStateWithLifecycle()

    // Load historical conversation if historyId is provided
    LaunchedEffect(historyIdToLoad) {
        historyIdToLoad?.let { historyId ->
            viewModel.loadHistoricalConversation(historyId)
        }
    }

    // Auto-show guidance on startup (with delay to avoid overwhelming user)
    LaunchedEffect(Unit) {
        delay(1000)  // Wait 1 second before showing
        if (!state.hasShizukuPermission) {
            viewModel.showShizukuGuide()
        } else if (!(state.isADBKeyboardInstalled && state.isADBKeyboardEnabled)) {
            viewModel.showADBKeyboardGuide()
        }
    }

    // 阶段2新增：收集动画状态
    val isAnimatingToOverlay by viewModel.isAnimatingToOverlay.collectAsStateWithLifecycle()

    // 检测IME（键盘）是否可见
    val density = LocalDensity.current
    val ime = WindowInsets.ime
    val isImeVisible = remember {
        derivedStateOf {
            ime.getBottom(density) > 0
        }
    }.value

    // 阶段2新增：使用AnimatedMainContent包裹整个界面
    AnimatedMainContent(isAnimating = isAnimatingToOverlay) {
        Scaffold(
        topBar = {
            Column {
                TopStatusBar(
                    modelName = state.model,
                    hasShizuku = state.hasShizukuPermission,
                    hasADBKeyboard = state.isADBKeyboardInstalled && state.isADBKeyboardEnabled,
                    onSettingsClick = onNavigateToSettings,
                    onHistoryClick = onNavigateToHistory,
                    onNewConversationClick = { viewModel.newConversation() },
                    onShizukuClick = {
                        if (!state.hasShizukuPermission) {
                            viewModel.showShizukuGuide()
                        }
                    },
                    onADBKeyboardClick = {
                        if (!(state.isADBKeyboardInstalled && state.isADBKeyboardEnabled)) {
                            viewModel.showADBKeyboardGuide()
                        }
                    }
                )
            }
        },
        bottomBar = {
            TaskInputBar(
                task = state.currentTask,
                onTaskChange = { viewModel.updateTask(it) },
                isRunning = state.isRunning,
                onStart = { viewModel.startTask() },
                onStop = { viewModel.stopTask() },
                modifier = Modifier.imePadding()  // 添加IME padding到输入栏
            )
        }
    ) { innerPadding ->
        // 消息列表
        MessageList(
            messages = state.messages,
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
        )
    }

        // ==================== 对话框 ====================

        // 确认对话框（敏感操作）
        confirmationRequest?.let { message ->
            ConfirmDialog(
                message = message,
                onConfirm = {
                    viewModel.confirmAction(true)
                },
                onDismiss = {
                    viewModel.confirmAction(false)
                }
            )
        }

        // 人工接管对话框
        takeOverRequest?.let { message ->
            TakeOverDialog(
                message = message,
                onComplete = {
                    viewModel.completeTakeOver()
                },
                onCancel = {
                    viewModel.cancelTakeOver()
                }
            )
        }

        // Shizuku引导对话框
        if (showShizukuGuide) {
            PermissionDialog(
                title = "需要 Shizuku 权限",
                message = "本应用需要 Shizuku 权限才能执行自动化任务。Shizuku 是一个无需 root 权限的系统工具。",
                permissionType = PermissionType.SHIZUKU,
                onGrant = {
                    // Request Shizuku permission
                    com.taskwizard.android.manager.ShizukuManager.requestPermission(
                        object : rikka.shizuku.Shizuku.OnRequestPermissionResultListener {
                            override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
                                // Re-check status after permission request
                                viewModel.checkSystemStatus()
                            }
                        }
                    )
                    viewModel.dismissShizukuGuide()
                },
                onDismiss = {
                    viewModel.dismissShizukuGuide()
                },
                onOpenShizukuApp = {
                    AppLauncher.openShizukuApp(context)
                }
            )
        }

        // ADB Keyboard引导对话框
        if (showADBKeyboardGuide) {
            PermissionDialog(
                title = "需要 ADB Keyboard",
                message = "本应用需要 ADB Keyboard 来实现自动输入文本功能。",
                permissionType = PermissionType.ADB_KEYBOARD,
                onGrant = {
                    AppLauncher.openIMESettings(context)
                    viewModel.dismissADBKeyboardGuide()
                },
                onDismiss = {
                    viewModel.dismissADBKeyboardGuide()
                },
                onOpenIMESettings = {
                    AppLauncher.openIMESettings(context)
                },
                onOpenDownloadPage = {
                    AppLauncher.openADBKeyboardDownload(context)
                }
            )
        }

        // 悬浮窗权限引导对话框
        if (showOverlayPermissionGuide) {
            OverlayPermissionDialog(
                onRequestPermission = {
                    AppLauncher.openOverlayPermissionSettings(context)
                    viewModel.dismissOverlayPermissionGuide()
                },
                onDismiss = {
                    viewModel.dismissOverlayPermissionGuide()
                }
            )
        }
    } // AnimatedMainContent结束
}
