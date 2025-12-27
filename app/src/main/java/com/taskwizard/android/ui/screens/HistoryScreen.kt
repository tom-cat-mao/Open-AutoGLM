package com.taskwizard.android.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Clear
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.DeleteSweep
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.taskwizard.android.data.history.TaskStatus
import com.taskwizard.android.data.history.TaskHistoryEntity
import com.taskwizard.android.ui.viewmodel.HistoryViewModel
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*

/**
 * 历史记录页面
 *
 * 显示任务执行历史，包括：
 * - 统计信息概览
 * - 任务列表（带筛选和搜索）
 * - 任务详情
 * - 批量删除功能
 *
 * @param onNavigateBack 返回主页面的回调
 * @param viewModel HistoryViewModel实例
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(
    onNavigateBack: () -> Unit,
    onContinueConversation: (Long) -> Unit,  // Callback to continue from history
    viewModel: HistoryViewModel
) {
    val state by viewModel.historyState.collectAsStateWithLifecycle()
    val statistics by viewModel.statistics.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    // 搜索框状态
    var searchQuery by remember { mutableStateOf(TextFieldValue()) }

    // 筛选菜单展开状态
    var filterMenuExpanded by remember { mutableStateOf(false) }

    // 性能优化：删除对话框状态移到屏幕级别
    var taskToDelete by remember { mutableStateOf<TaskHistoryEntity?>(null) }

    // 显示消息
    LaunchedEffect(state.message) {
        state.message?.let {
            snackbarHostState.showSnackbar(
                message = it,
                duration = SnackbarDuration.Short
            )
            viewModel.clearMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("任务历史") },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(
                            imageVector = Icons.Rounded.ArrowBack,
                            contentDescription = "返回"
                        )
                    }
                },
                actions = {
                    // 筛选按钮
                    Box {
                        IconButton(onClick = { filterMenuExpanded = true }) {
                            Icon(
                                imageVector = Icons.Rounded.Search,
                                contentDescription = "筛选"
                            )
                        }
                        DropdownMenu(
                            expanded = filterMenuExpanded,
                            onDismissRequest = { filterMenuExpanded = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("全部") },
                                onClick = {
                                    viewModel.clearFilter()
                                    filterMenuExpanded = false
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("已完成") },
                                onClick = {
                                    viewModel.filterByStatus(TaskStatus.COMPLETED)
                                    filterMenuExpanded = false
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("失败") },
                                onClick = {
                                    viewModel.filterByStatus(TaskStatus.FAILED)
                                    filterMenuExpanded = false
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("已取消") },
                                onClick = {
                                    viewModel.filterByStatus(TaskStatus.CANCELLED)
                                    filterMenuExpanded = false
                                }
                            )
                        }
                    }
                }
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            // 统计信息卡片
            statistics?.let { stats ->
                StatisticsCard(
                    stats = stats,
                    modifier = Modifier.padding(16.dp)
                )
            }

            // 搜索框
            SearchBar(
                query = searchQuery,
                onQueryChange = {
                    searchQuery = it
                    viewModel.searchTasks(it.text)
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            )

            // 当前筛选提示
            if (state.currentFilter != null) {
                FilterChip(
                    selected = true,
                    onClick = { viewModel.clearFilter() },
                    label = { Text("筛选: ${getStatusDisplayName(state.currentFilter)}") },
                    modifier = Modifier.padding(horizontal = 16.dp)
                )
            }

            // 任务列表
            if (state.isLoading) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator()
                }
            } else if (state.tasks.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = if (state.searchQuery.isNotBlank()) {
                            "没有找到匹配的任务"
                        } else {
                            "暂无任务历史"
                        },
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    items(
                        items = state.tasks,
                        key = { it.id }
                    ) { task ->
                        TaskHistoryItem(
                            task = task,
                            onClick = {
                                // Navigate to main screen with history ID to continue conversation
                                onContinueConversation(task.id)
                            },
                            onRequestDelete = {
                                // 性能优化：只设置状态，不立即删除
                                taskToDelete = task
                            }
                        )
                    }
                }
            }
        }
    }

    // 删除确认对话框
    // 性能优化：单个对话框在屏幕级别管理，而不是每个列表项
    taskToDelete?.let { task ->
        AlertDialog(
            onDismissRequest = { taskToDelete = null },
            title = { Text("删除任务") },
            text = { Text("确定要删除此任务记录吗？\n\n${task.taskDescription}") },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.deleteTask(task)
                        taskToDelete = null
                    }
                ) {
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { taskToDelete = null }) {
                    Text("取消")
                }
            }
        )
    }
}

/**
 * 统计信息卡片
 */
@Composable
private fun StatisticsCard(
    stats: com.taskwizard.android.data.history.HistoryStatistics,
    modifier: Modifier = Modifier
) {
    Card(modifier = modifier) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text(
                text = "📊 统计概览",
                style = MaterialTheme.typography.titleMedium
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                StatItem(label = "总任务", value = stats.totalTasks.toString())
                StatItem(label = "已完成", value = stats.completedTasks.toString())
                StatItem(label = "失败", value = stats.failedTasks.toString())
                StatItem(label = "成功率", value = "${stats.simpleSuccessRate.toInt()}%")
            }
        }
    }
}

/**
 * 统计项
 */
@Composable
private fun StatItem(label: String, value: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = value,
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.primary
        )
        Text(
            text = label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

/**
 * 搜索框
 */
@Composable
private fun SearchBar(
    query: TextFieldValue,
    onQueryChange: (TextFieldValue) -> Unit,
    modifier: Modifier = Modifier
) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        modifier = modifier,
        placeholder = { Text("搜索任务描述...") },
        leadingIcon = {
            Icon(
                imageVector = Icons.Rounded.Search,
                contentDescription = "搜索"
            )
        },
        trailingIcon = {
            if (query.text.isNotEmpty()) {
                IconButton(onClick = { onQueryChange(TextFieldValue()) }) {
                    Icon(
                        imageVector = Icons.Rounded.Clear,
                        contentDescription = "清除"
                    )
                }
            }
        },
        singleLine = true
    )
}

/**
 * 任务历史项
 * 性能优化：移除per-item的dialog状态，改为使用回调
 */
@Composable
private fun TaskHistoryItem(
    task: TaskHistoryEntity,
    onClick: () -> Unit,
    onRequestDelete: () -> Unit  // 改为请求删除的回调，实际删除操作由上层处理
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            // 标题行：描述 + 状态 + 删除按钮
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = task.taskDescription,
                    style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.weight(1f)
                )

                StatusChip(status = task.getTaskStatus())

                IconButton(
                    onClick = onRequestDelete,
                    modifier = Modifier.size(40.dp)
                ) {
                    Icon(
                        imageVector = Icons.Rounded.Delete,
                        contentDescription = "删除",
                        tint = MaterialTheme.colorScheme.error
                    )
                }
            }

            // 信息行：时间 + 步骤数
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = formatTimestamp(task.startTime),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Text(
                    text = "${task.stepCount} 步",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            // 模型信息
            Text(
                text = "模型: ${task.model}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // 状态消息
            task.statusMessage?.let {
                Text(
                    text = it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

/**
 * 状态标签
 */
@Composable
private fun StatusChip(status: TaskStatus) {
    val (color, text) = when (status) {
        TaskStatus.COMPLETED -> MaterialTheme.colorScheme.primary to "已完成"
        TaskStatus.FAILED -> MaterialTheme.colorScheme.error to "失败"
        TaskStatus.CANCELLED -> MaterialTheme.colorScheme.onSurfaceVariant to "已取消"
        TaskStatus.RUNNING -> MaterialTheme.colorScheme.tertiary to "执行中"
        TaskStatus.PENDING -> MaterialTheme.colorScheme.onSurfaceVariant to "待执行"
        TaskStatus.TIMEOUT -> MaterialTheme.colorScheme.error to "超时"
    }

    Surface(
        color = color.copy(alpha = 0.1f),
        shape = MaterialTheme.shapes.small
    ) {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelSmall,
            color = color
        )
    }
}

/**
 * 格式化时间戳
 */
private fun formatTimestamp(timestamp: Long): String {
    val now = System.currentTimeMillis()
    val diff = now - timestamp

    return when {
        diff < 60_000 -> "刚刚"
        diff < 3600_000 -> "${diff / 60_000} 分钟前"
        diff < 86400_000 -> "${diff / 3600_000} 小时前"
        diff < 604800_000 -> "${diff / 86400_000} 天前"
        else -> {
            val sdf = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
            sdf.format(Date(timestamp))
        }
    }
}

/**
 * 获取状态显示名称
 */
private fun getStatusDisplayName(status: String?): String {
    return when (status) {
        TaskStatus.PENDING.name -> "待执行"
        TaskStatus.RUNNING.name -> "执行中"
        TaskStatus.COMPLETED.name -> "已完成"
        TaskStatus.FAILED.name -> "失败"
        TaskStatus.CANCELLED.name -> "已取消"
        TaskStatus.TIMEOUT.name -> "超时"
        else -> status ?: "未知"
    }
}
