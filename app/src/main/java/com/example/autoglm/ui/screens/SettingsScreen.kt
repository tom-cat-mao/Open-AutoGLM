package com.example.autoglm.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Clear
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.example.autoglm.ui.theme.ThemeMode
import com.example.autoglm.ui.viewmodel.MainViewModel
import com.example.autoglm.utils.RecompositionCounter
import kotlinx.coroutines.launch

/**
 * 设置页面
 *
 * 全屏独立页面，包含：
 * - 主题设置（亮色/暗色 + Pure Black开关）
 * - API配置（API Key、Base URL、Model）
 * - 高级设置（可选）
 *
 * 性能优化：使用专用的 settingsState 而不是完整的 AppState
 * 这样可以避免主页面消息更新时触发设置页面的重组
 *
 * @param onNavigateBack 返回主页面的回调
 * @param viewModel 共享的ViewModel实例
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onNavigateBack: () -> Unit,
    viewModel: MainViewModel
) {
    // 性能监控：追踪重组次数
    RecompositionCounter("SettingsScreen")

    // ✅ 性能优化：只订阅设置相关的状态，避免过度订阅
    val state by viewModel.settingsState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    // 高级设置展开状态
    var advancedSettingsExpanded by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("设置") },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(
                            imageVector = Icons.Rounded.ArrowBack,
                            contentDescription = "返回"
                        )
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
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // 主题设置区域
            Card(
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "🎨 主题设置",
                        style = MaterialTheme.typography.titleMedium
                    )

                    // 亮色模式
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { viewModel.updateThemeMode(ThemeMode.LIGHT) }
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        RadioButton(
                            selected = state.themeMode == ThemeMode.LIGHT,
                            onClick = { viewModel.updateThemeMode(ThemeMode.LIGHT) }
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Text(
                            text = "亮色模式",
                            style = MaterialTheme.typography.bodyLarge
                        )
                    }

                    // 暗色模式
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { viewModel.updateThemeMode(ThemeMode.DARK) }
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        RadioButton(
                            selected = state.themeMode == ThemeMode.DARK,
                            onClick = { viewModel.updateThemeMode(ThemeMode.DARK) }
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Text(
                            text = "暗色模式",
                            style = MaterialTheme.typography.bodyLarge
                        )
                    }

                    // Pure Black开关（仅在暗色模式下显示）
                    AnimatedVisibility(
                        visible = state.themeMode == ThemeMode.DARK,
                        enter = expandVertically() + fadeIn(),
                        exit = shrinkVertically() + fadeOut()
                    ) {
                        Column {
                            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 4.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = "Pure Black (OLED优化)",
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                Switch(
                                    checked = state.pureBlackEnabled,
                                    onCheckedChange = { viewModel.togglePureBlack(it) }
                                )
                            }
                        }
                    }
                }
            }

            // API配置区域
            Card(
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Text(
                        text = "🔧 API配置",
                        style = MaterialTheme.typography.titleMedium
                    )

                    // API Key输入框（带清空按钮）
                    OutlinedTextField(
                        value = state.apiKey,
                        onValueChange = { viewModel.updateApiKey(it) },
                        label = { Text("API Key") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        trailingIcon = {
                            if (state.apiKey.isNotEmpty()) {
                                IconButton(onClick = { viewModel.updateApiKey("") }) {
                                    Icon(
                                        imageVector = Icons.Rounded.Clear,
                                        contentDescription = "清空"
                                    )
                                }
                            }
                        },
                        isError = !state.isApiKeyValid,
                        supportingText = {
                            if (!state.isApiKeyValid) {
                                Text("API Key长度至少10个字符")
                            }
                        }
                    )

                    // Base URL输入框（带清空按钮）
                    OutlinedTextField(
                        value = state.baseUrl,
                        onValueChange = { viewModel.updateBaseUrl(it) },
                        label = { Text("Base URL") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        trailingIcon = {
                            if (state.baseUrl.isNotEmpty()) {
                                IconButton(onClick = { viewModel.updateBaseUrl("") }) {
                                    Icon(
                                        imageVector = Icons.Rounded.Clear,
                                        contentDescription = "清空"
                                    )
                                }
                            }
                        },
                        isError = !state.isBaseUrlValid,
                        supportingText = {
                            if (!state.isBaseUrlValid) {
                                Text("Base URL必须以http://或https://开头")
                            }
                        }
                    )

                    // Model Name输入框（带清空按钮）
                    OutlinedTextField(
                        value = state.model,
                        onValueChange = { viewModel.updateModel(it) },
                        label = { Text("Model Name") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        trailingIcon = {
                            if (state.model.isNotEmpty()) {
                                IconButton(onClick = { viewModel.updateModel("") }) {
                                    Icon(
                                        imageVector = Icons.Rounded.Clear,
                                        contentDescription = "清空"
                                    )
                                }
                            }
                        }
                    )

                    // 保存按钮
                    // ✅ 性能优化：使用 state.isSaveEnabled，验证逻辑已在 ViewModel 中计算
                    Button(
                        onClick = {
                            viewModel.saveSettings()
                            // 显示成功提示
                            scope.launch {
                                snackbarHostState.showSnackbar(
                                    message = "配置已保存",
                                    duration = SnackbarDuration.Short
                                )
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = state.isSaveEnabled
                    ) {
                        Text("保存配置")
                    }
                }
            }

            // 高级设置区域（可折叠）
            Card(
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(16.dp)
                ) {
                    // 标题行（可点击展开/收起）
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { advancedSettingsExpanded = !advancedSettingsExpanded }
                            .padding(vertical = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "⚙️ 高级设置",
                            style = MaterialTheme.typography.titleMedium
                        )
                        Text(
                            text = if (advancedSettingsExpanded) "▲" else "▼",
                            style = MaterialTheme.typography.bodyLarge
                        )
                    }

                    // 可折叠内容
                    AnimatedVisibility(
                        visible = advancedSettingsExpanded,
                        enter = expandVertically() + fadeIn(),
                        exit = shrinkVertically() + fadeOut()
                    ) {
                        Column(
                            modifier = Modifier.padding(top = 12.dp),
                            verticalArrangement = Arrangement.spacedBy(16.dp)
                        ) {
                            HorizontalDivider()

                            // 超时设置（滑块）
                            // ✅ 性能优化：使用 state.timeoutSeconds，直接调用 ViewModel 更新
                            Column(
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Text(
                                    text = "请求超时时间",
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Slider(
                                        value = state.timeoutSeconds.toFloat(),
                                        onValueChange = { viewModel.updateTimeout(it.toInt()) },
                                        valueRange = 10f..120f,
                                        steps = 10,
                                        modifier = Modifier.weight(1f)
                                    )
                                    Spacer(modifier = Modifier.width(12.dp))
                                    Text(
                                        text = "${state.timeoutSeconds}秒",
                                        style = MaterialTheme.typography.bodyMedium,
                                        modifier = Modifier.width(50.dp)
                                    )
                                }
                            }

                            // 重试次数设置
                            // ✅ 性能优化：使用 state.retryCount，直接调用 ViewModel 更新
                            Column(
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Text(
                                    text = "失败重试次数",
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Slider(
                                        value = state.retryCount.toFloat(),
                                        onValueChange = { viewModel.updateRetryCount(it.toInt()) },
                                        valueRange = 0f..10f,
                                        steps = 9,
                                        modifier = Modifier.weight(1f)
                                    )
                                    Spacer(modifier = Modifier.width(12.dp))
                                    Text(
                                        text = "${state.retryCount}次",
                                        style = MaterialTheme.typography.bodyMedium,
                                        modifier = Modifier.width(50.dp)
                                    )
                                }
                            }

                            // 调试模式开关
                            // ✅ 性能优化：使用 state.debugMode，直接调用 ViewModel 更新
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = "调试模式",
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                Switch(
                                    checked = state.debugMode,
                                    onCheckedChange = { viewModel.updateDebugMode(it) }
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
