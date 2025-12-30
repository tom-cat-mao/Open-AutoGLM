package com.taskwizard.android.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.taskwizard.android.data.template.TaskTemplateEntity
import com.taskwizard.android.ui.viewmodel.TasksViewModel
import java.text.SimpleDateFormat
import java.util.*

/**
 * Tasks 列表页面
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(
    onNavigateBack: () -> Unit,
    viewModel: TasksViewModel
) {
    val templates by viewModel.templates.collectAsStateWithLifecycle()
    val isExecuting by viewModel.isExecuting.collectAsStateWithLifecycle()
    val progress by viewModel.executionProgress.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("我的 Tasks") },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Rounded.ArrowBack,
                            contentDescription = "返回"
                        )
                    }
                }
            )
        }
    ) { padding ->
        if (templates.isEmpty()) {
            EmptyState(Modifier.padding(padding))
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(templates, key = { it.id }) { template ->
                    TaskCard(
                        template = template,
                        isExecuting = isExecuting,
                        onExecute = { viewModel.executeTemplate(template) },
                        onDelete = { viewModel.deleteTemplate(template) }
                    )
                }
            }
        }

        // 执行进度
        if (progress != null) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.BottomCenter
            ) {
                Snackbar(
                    modifier = Modifier.padding(16.dp)
                ) {
                    Text(progress ?: "")
                }
            }
        }
    }
}

@Composable
private fun EmptyState(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = "暂无 Tasks\n完成 AI 任务后可保存为 Task",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@Composable
private fun TaskCard(
    template: TaskTemplateEntity,
    isExecuting: Boolean,
    onExecute: () -> Unit,
    onDelete: () -> Unit
) {
    val dateFormat = remember { SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()) }

    Card(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = template.name,
                style = MaterialTheme.typography.titleMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )

            Spacer(modifier = Modifier.height(4.dp))

            Text(
                text = template.description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )

            Spacer(modifier = Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "${template.stepCount} 步 · 使用 ${template.useCount} 次",
                    style = MaterialTheme.typography.labelSmall
                )

                Row {
                    IconButton(onClick = onDelete, enabled = !isExecuting) {
                        Icon(Icons.Default.Delete, "删除")
                    }
                    IconButton(onClick = onExecute, enabled = !isExecuting) {
                        Icon(Icons.Default.PlayArrow, "执行")
                    }
                }
            }
        }
    }
}
