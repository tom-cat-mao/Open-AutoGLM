package com.example.autoglm.ui.viewmodel

import android.app.Application
import android.graphics.Bitmap
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.autoglm.IAutoGLMService
import com.example.autoglm.api.ApiClient
import com.example.autoglm.core.ActionExecutor
import com.example.autoglm.core.AgentCore
import com.example.autoglm.data.*
import com.example.autoglm.manager.ShizukuManager
import com.example.autoglm.ui.theme.ThemeMode
import com.example.autoglm.utils.SettingsManager
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toPersistentList
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream

/**
 * MainViewModel - 主视图模型
 *
 * 负责管理应用的所有状态和业务逻辑
 * 使用StateFlow实现响应式状态管理
 *
 * 阶段6：集成AgentCore和ActionExecutor，实现完整的任务执行流程
 */
class MainViewModel(application: Application) : AndroidViewModel(application) {

    companion object {
        private const val TAG = "MainViewModel"
        private const val MAX_STEPS = 50 // 最大步骤数，防止无限循环
    }

    // ==================== 状态管理 ====================

    private val _state = MutableStateFlow(AppState())
    val state: StateFlow<AppState> = _state.asStateFlow()

    // 性能优化：专用于设置页面的状态，避免过度订阅
    private val _settingsState = MutableStateFlow(SettingsState())
    val settingsState: StateFlow<SettingsState> = _settingsState.asStateFlow()

    // SharedPreferences用于主题持久化
    private val prefs = application.getSharedPreferences("app_prefs", 0)

    // ==================== 业务逻辑组件 ====================

    private var agentCore: AgentCore? = null
    private var actionExecutor: ActionExecutor? = null
    private var taskJob: Job? = null
    private var service: IAutoGLMService? = null

    // 对话框状态
    private val _confirmationRequest = MutableStateFlow<String?>(null)
    val confirmationRequest: StateFlow<String?> = _confirmationRequest.asStateFlow()

    private val _takeOverRequest = MutableStateFlow<String?>(null)
    val takeOverRequest: StateFlow<String?> = _takeOverRequest.asStateFlow()

    // 对话框响应的CompletableDeferred
    private var confirmationDeferred: CompletableDeferred<Boolean>? = null
    private var takeOverDeferred: CompletableDeferred<Unit>? = null

    init {
        // 初始化SettingsManager
        SettingsManager.init(application)

        // 加载配置
        loadSettings()

        // 检查系统状态
        checkSystemStatus()

        // 初始化AgentCore，添加错误回调
        agentCore = AgentCore(application) { errorMsg ->
            // 使用withContext确保在主线程显示错误消息
            viewModelScope.launch {
                withContext(Dispatchers.Main) {
                    Log.d(TAG, "Error callback invoked: $errorMsg")
                    _state.update {
                        it.copy(messages = (it.messages + MessageItem.SystemMessage(content = errorMsg, type = SystemMessageType.ERROR)).toPersistentList())
                    }
                }
            }
        }
    }

    // ==================== 配置管理 ====================

    /**
     * 从SettingsManager加载配置
     * 性能优化：同时加载到 AppState 和 SettingsState
     */
    private fun loadSettings() {
        val apiKey = SettingsManager.apiKey
        val baseUrl = SettingsManager.baseUrl
        val model = SettingsManager.model
        val themeMode = loadThemeMode()
        val pureBlackEnabled = loadPureBlackEnabled()
        val timeoutSeconds = SettingsManager.timeoutSeconds
        val retryCount = SettingsManager.retryCount
        val debugMode = SettingsManager.debugMode

        // 更新 AppState
        _state.update { it.copy(
            apiKey = apiKey,
            baseUrl = baseUrl,
            model = model,
            themeMode = themeMode,
            pureBlackEnabled = pureBlackEnabled
        )}

        // 更新 SettingsState（包含验证状态）
        _settingsState.update {
            val isApiKeyValid = apiKey.isEmpty() || apiKey.length >= 10
            val isBaseUrlValid = baseUrl.isEmpty() || baseUrl.startsWith("http")
            it.copy(
                apiKey = apiKey,
                baseUrl = baseUrl,
                model = model,
                themeMode = themeMode,
                pureBlackEnabled = pureBlackEnabled,
                timeoutSeconds = timeoutSeconds,
                retryCount = retryCount,
                debugMode = debugMode,
                isApiKeyValid = isApiKeyValid,
                isBaseUrlValid = isBaseUrlValid,
                isSaveEnabled = calculateSaveEnabled(apiKey, baseUrl, model, isApiKeyValid, isBaseUrlValid)
            )
        }
    }

    /**
     * 计算保存按钮是否可用
     */
    private fun calculateSaveEnabled(
        apiKey: String,
        baseUrl: String,
        model: String,
        isApiKeyValid: Boolean,
        isBaseUrlValid: Boolean
    ): Boolean {
        return apiKey.isNotEmpty() &&
               baseUrl.isNotEmpty() &&
               model.isNotEmpty() &&
               isApiKeyValid &&
               isBaseUrlValid
    }

    /**
     * 更新API Key
     * 性能优化：同时更新 AppState 和 SettingsState，并计算验证状态
     */
    fun updateApiKey(value: String) {
        _state.update { it.copy(apiKey = value) }

        _settingsState.update { current ->
            val isValid = value.isEmpty() || value.length >= 10
            current.copy(
                apiKey = value,
                isApiKeyValid = isValid,
                isSaveEnabled = calculateSaveEnabled(
                    value, current.baseUrl, current.model, isValid, current.isBaseUrlValid
                )
            )
        }
    }

    /**
     * 更新Base URL
     * 性能优化：同时更新 AppState 和 SettingsState，并计算验证状态
     */
    fun updateBaseUrl(value: String) {
        _state.update { it.copy(baseUrl = value) }

        _settingsState.update { current ->
            val isValid = value.isEmpty() || value.startsWith("http")
            current.copy(
                baseUrl = value,
                isBaseUrlValid = isValid,
                isSaveEnabled = calculateSaveEnabled(
                    current.apiKey, value, current.model, current.isApiKeyValid, isValid
                )
            )
        }
    }

    /**
     * 更新Model名称
     * 性能优化：同时更新 AppState 和 SettingsState，并计算验证状态
     */
    fun updateModel(value: String) {
        _state.update { it.copy(model = value) }

        _settingsState.update { current ->
            current.copy(
                model = value,
                isSaveEnabled = calculateSaveEnabled(
                    current.apiKey, current.baseUrl, value,
                    current.isApiKeyValid, current.isBaseUrlValid
                )
            )
        }
    }

    /**
     * 保存配置到SettingsManager
     */
    fun saveSettings() {
        val current = _state.value
        SettingsManager.apiKey = current.apiKey
        SettingsManager.baseUrl = current.baseUrl
        SettingsManager.model = current.model

        // 立即初始化ApiClient，使新配置生效
        ApiClient.init(current.baseUrl, current.apiKey)
        Log.d(TAG, "ApiClient re-initialized after settings save")

        addSystemMessage("配置已保存", SystemMessageType.SUCCESS)
    }

    // ==================== 主题管理 ====================

    /**
     * 更新主题模式
     * 性能优化：同时更新 AppState 和 SettingsState
     */
    fun updateThemeMode(mode: ThemeMode) {
        _state.update { it.copy(themeMode = mode) }
        _settingsState.update { it.copy(themeMode = mode) }
        saveThemeMode(mode)
    }

    /**
     * 切换Pure Black模式
     * 性能优化：同时更新 AppState 和 SettingsState
     */
    fun togglePureBlack(enabled: Boolean) {
        _state.update { it.copy(pureBlackEnabled = enabled) }
        _settingsState.update { it.copy(pureBlackEnabled = enabled) }
        savePureBlackEnabled(enabled)
    }

    /**
     * 从SharedPreferences加载主题模式
     */
    private fun loadThemeMode(): ThemeMode {
        val mode = prefs.getString("theme_mode", "LIGHT") ?: "LIGHT"
        return try {
            ThemeMode.valueOf(mode)
        } catch (e: Exception) {
            ThemeMode.LIGHT
        }
    }

    /**
     * 保存主题模式到SharedPreferences
     */
    private fun saveThemeMode(mode: ThemeMode) {
        prefs.edit().putString("theme_mode", mode.name).apply()
    }

    /**
     * 从SharedPreferences加载Pure Black设置
     */
    private fun loadPureBlackEnabled(): Boolean {
        return prefs.getBoolean("pure_black_enabled", false)
    }

    /**
     * 保存Pure Black设置到SharedPreferences
     */
    private fun savePureBlackEnabled(enabled: Boolean) {
        prefs.edit().putBoolean("pure_black_enabled", enabled).apply()
    }

    // ==================== 高级设置管理（性能优化新增）====================

    /**
     * 更新超时时间
     * 性能优化：更新 SettingsState 并持久化
     */
    fun updateTimeout(seconds: Int) {
        _settingsState.update { it.copy(timeoutSeconds = seconds) }
        SettingsManager.timeoutSeconds = seconds
    }

    /**
     * 更新重试次数
     * 性能优化：更新 SettingsState 并持久化
     */
    fun updateRetryCount(count: Int) {
        _settingsState.update { it.copy(retryCount = count) }
        SettingsManager.retryCount = count
    }

    /**
     * 更新调试模式
     * 性能优化：更新 SettingsState 并持久化
     */
    fun updateDebugMode(enabled: Boolean) {
        _settingsState.update { it.copy(debugMode = enabled) }
        SettingsManager.debugMode = enabled
    }

    // ==================== UI状态管理 ====================

    /**
     * 切换设置页面显示状态
     */
    fun toggleSettings() {
        _state.update { it.copy(isSettingsOpen = !it.isSettingsOpen) }
    }

    /**
     * 更新任务描述
     */
    fun updateTask(value: String) {
        _state.update { it.copy(currentTask = value) }
    }

    /**
     * 清空任务描述
     */
    fun clearTask() {
        _state.update { it.copy(currentTask = "") }
    }

    // ==================== 消息管理 ====================

    /**
     * 添加AI思考消息
     * 性能优化：使用 ImmutableList plus 运算符，高效添加元素
     */
    fun addThinkMessage(content: String) {
        _state.update {
            it.copy(messages = (it.messages + MessageItem.ThinkMessage(content = content)).toPersistentList())
        }
    }

    /**
     * 添加操作消息
     * 性能优化：使用 ImmutableList plus 运算符，高效添加元素
     */
    fun addActionMessage(action: Action) {
        _state.update {
            it.copy(messages = (it.messages + MessageItem.ActionMessage(action = action)).toPersistentList())
        }
    }

    /**
     * 添加系统消息
     * 性能优化：使用 ImmutableList plus 运算符，高效添加元素
     */
    fun addSystemMessage(content: String, type: SystemMessageType) {
        _state.update {
            it.copy(messages = (it.messages + MessageItem.SystemMessage(content = content, type = type)).toPersistentList())
        }
    }

    /**
     * 清空所有消息
     * 性能优化：使用 persistentListOf() 创建空的 ImmutableList
     */
    fun clearMessages() {
        _state.update { it.copy(messages = kotlinx.collections.immutable.persistentListOf()) }
    }

    // ==================== 系统状态检查 ====================

    /**
     * 检查Shizuku和ADB Keyboard状态
     */
    fun checkSystemStatus() {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                // 检查Shizuku权限
                val hasShizuku = ShizukuManager.checkPermission()

                // 如果有Shizuku权限，检查ADB Keyboard
                val (isInstalled, isEnabled) = if (hasShizuku) {
                    try {
                        val service = ShizukuManager.bindService(getApplication())
                        val installed = service.isADBKeyboardInstalled()
                        val enabled = if (installed) {
                            service.getCurrentIME() == "com.android.adbkeyboard/.AdbIME"
                        } else {
                            false
                        }
                        Pair(installed, enabled)
                    } catch (e: Exception) {
                        Pair(false, false)
                    }
                } else {
                    Pair(false, false)
                }

                // 更新状态
                _state.update { it.copy(
                    hasShizukuPermission = hasShizuku,
                    isADBKeyboardInstalled = isInstalled,
                    isADBKeyboardEnabled = isEnabled
                )}
            } catch (e: Exception) {
                _state.update { it.copy(hasShizukuPermission = false) }
            }
        }
    }

    // ==================== 任务执行 ====================

    /**
     * 启动任务
     */
    fun startTask() {
        val task = _state.value.currentTask
        if (task.isBlank()) {
            addSystemMessage("请输入任务描述", SystemMessageType.WARNING)
            return
        }

        if (_state.value.apiKey.isBlank()) {
            addSystemMessage("请先配置API Key", SystemMessageType.ERROR)
            return
        }

        // 新增：验证Base URL格式
        if (_state.value.baseUrl.isBlank()) {
            addSystemMessage("请先配置Base URL", SystemMessageType.ERROR)
            return
        }

        if (!_state.value.baseUrl.startsWith("http://") && !_state.value.baseUrl.startsWith("https://")) {
            addSystemMessage("Base URL格式错误，必须以http://或https://开头", SystemMessageType.ERROR)
            return
        }

        if (!_state.value.hasShizukuPermission) {
            addSystemMessage("需要Shizuku权限才能执行任务", SystemMessageType.ERROR)
            return
        }

        // 启动任务
        _state.update { it.copy(isRunning = true) }
        addSystemMessage("任务启动: $task", SystemMessageType.INFO)

        // 新增：显示使用的API配置（用于调试）
        Log.d(TAG, "Starting task with API config:")
        Log.d(TAG, "  Base URL: ${_state.value.baseUrl}")
        Log.d(TAG, "  API Key: ${_state.value.apiKey.take(10)}...")
        Log.d(TAG, "  Model: ${_state.value.model}")

        // 在协程中执行任务
        taskJob = viewModelScope.launch(Dispatchers.IO) {
            try {
                executeTask(task)
            } catch (e: Exception) {
                Log.e(TAG, "Task execution failed", e)
                addSystemMessage("任务执行失败: ${e.message}", SystemMessageType.ERROR)
            } finally {
                // 清理资源
                cleanupTask()
            }
        }
    }

    /**
     * 停止任务
     */
    fun stopTask() {
        agentCore?.stop()
        taskJob?.cancel()
        taskJob = null

        _state.update { it.copy(isRunning = false) }
        addSystemMessage("任务已停止", SystemMessageType.INFO)

        // 还原IME
        actionExecutor?.restoreIME()
    }

    /**
     * 执行任务的主循环
     */
    private suspend fun executeTask(task: String) {
        // 1. 初始化ApiClient（使用当前配置）
        val currentState = _state.value
        ApiClient.init(currentState.baseUrl, currentState.apiKey)
        Log.d(TAG, "ApiClient initialized with baseUrl=${currentState.baseUrl}, apiKey=${currentState.apiKey.take(10)}...")

        // 2. 初始化AgentCore
        agentCore?.startSession(task)

        // 2. 绑定Shizuku服务
        service = try {
            ShizukuManager.bindService(getApplication())
        } catch (e: Exception) {
            Log.e(TAG, "Failed to bind Shizuku service", e)
            addSystemMessage("无法连接Shizuku服务", SystemMessageType.ERROR)
            return
        }

        // 3. 初始化ActionExecutor
        val screenSize = getScreenSize()
        actionExecutor = ActionExecutor(
            context = getApplication(),
            service = service!!,
            screenWidth = screenSize.first,
            screenHeight = screenSize.second,
            onTakeOver = { message ->
                handleTakeOver(message)
            },
            onNote = { note ->
                agentCore?.addNote(note)
                Log.d(TAG, "Note added: $note")
            },
            onConfirmation = { message ->
                handleConfirmation(message)
            }
        )

        // 4. 执行步骤循环
        var stepCount = 0
        var consecutiveFailures = 0  // 连续失败计数
        val MAX_CONSECUTIVE_FAILURES = 3  // 最大连续失败次数

        while (agentCore?.isSessionRunning() == true && stepCount < MAX_STEPS) {
            stepCount++
            Log.d(TAG, "Step $stepCount")

            // 4.1 截图
            val screenshot = captureScreenshot()
            if (screenshot == null) {
                addSystemMessage("截图失败", SystemMessageType.ERROR)
                break
            }

            // 4.2 调用AgentCore获取下一步动作
            val action = agentCore?.step(screenshot)

            // 4.3 显示AI思考内容
            agentCore?.lastThink?.let { think ->
                if (think.isNotBlank()) {
                    addThinkMessage(think)
                    consecutiveFailures = 0  // 有思考内容说明成功，重置计数
                }
            }

            // 4.4 执行动作
            if (action != null) {
                addActionMessage(action)
                consecutiveFailures = 0  // 有动作说明成功，重置计数

                // 检查是否是finish动作
                if (action.action?.lowercase() == "finish") {
                    val message = action.message ?: action.content ?: "任务完成"
                    addSystemMessage(message, SystemMessageType.SUCCESS)
                    break
                }

                // 执行动作，如果返回false表示用户取消
                val shouldContinue = actionExecutor?.execute(action) ?: true
                if (!shouldContinue) {
                    addSystemMessage("用户取消操作，任务已停止", SystemMessageType.WARNING)
                    break
                }

                // 等待一小段时间让UI更新
                delay(500)
            } else {
                // 处理action为null的情况
                consecutiveFailures++
                Log.w(TAG, "No action returned from AgentCore (failure $consecutiveFailures/$MAX_CONSECUTIVE_FAILURES)")

                if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                    addSystemMessage(
                        "连续${MAX_CONSECUTIVE_FAILURES}次获取动作失败，任务已停止。\n请检查：\n1. API配置是否正确\n2. 网络连接是否正常\n3. API服务是否可用",
                        SystemMessageType.ERROR
                    )
                    break  // 停止循环
                }

                delay(1000)
            }
        }

        if (stepCount >= MAX_STEPS) {
            addSystemMessage("达到最大步骤数限制", SystemMessageType.WARNING)
        }
    }

    /**
     * 清理任务资源
     */
    private fun cleanupTask() {
        actionExecutor?.restoreIME()
        _state.update { it.copy(isRunning = false) }
    }

    /**
     * 获取屏幕尺寸
     */
    private fun getScreenSize(): Pair<Int, Int> {
        return try {
            val displayMetrics = getApplication<Application>().resources.displayMetrics
            Pair(displayMetrics.widthPixels, displayMetrics.heightPixels)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to get screen size", e)
            Pair(1080, 2400) // 默认值
        }
    }

    /**
     * 截图
     */
    private suspend fun captureScreenshot(): ByteArray? {
        return try {
            val screenshotPath = service?.takeScreenshotToFile()
            if (screenshotPath != null && !screenshotPath.startsWith("ERROR")) {
                // 从文件读取截图
                val file = java.io.File(screenshotPath)
                if (file.exists()) {
                    val bitmap = android.graphics.BitmapFactory.decodeFile(screenshotPath)
                    if (bitmap != null) {
                        // 更新ActionExecutor的屏幕尺寸（使用截图实际尺寸）
                        actionExecutor?.updateScreenSize(bitmap.width, bitmap.height)

                        // 转换为JPEG字节数组
                        val outputStream = ByteArrayOutputStream()
                        bitmap.compress(Bitmap.CompressFormat.JPEG, 80, outputStream)
                        val bytes = outputStream.toByteArray()

                        // 清理临时文件
                        file.delete()

                        bytes
                    } else {
                        Log.e(TAG, "Failed to decode screenshot file")
                        null
                    }
                } else {
                    Log.e(TAG, "Screenshot file not found: $screenshotPath")
                    null
                }
            } else {
                Log.e(TAG, "Screenshot failed: $screenshotPath")
                null
            }
        } catch (e: Exception) {
            Log.e(TAG, "Screenshot failed", e)
            null
        }
    }

    /**
     * 处理Take Over请求
     */
    private suspend fun handleTakeOver(message: String) {
        Log.d(TAG, "Take over requested: $message")

        // 创建CompletableDeferred用于等待用户操作
        takeOverDeferred = CompletableDeferred()

        // 更新UI状态，显示对话框
        _takeOverRequest.value = message
        addSystemMessage("需要人工介入: $message", SystemMessageType.WARNING)

        // 等待用户完成操作
        try {
            takeOverDeferred?.await()
            Log.d(TAG, "User completed take over")
            addSystemMessage("继续执行任务", SystemMessageType.INFO)
        } catch (e: Exception) {
            Log.e(TAG, "Take over cancelled", e)
            addSystemMessage("任务已取消", SystemMessageType.WARNING)
        } finally {
            _takeOverRequest.value = null
            takeOverDeferred = null
        }
    }

    /**
     * 处理确认请求
     */
    private suspend fun handleConfirmation(message: String): Boolean {
        Log.d(TAG, "Confirmation requested: $message")

        // 创建CompletableDeferred用于等待用户确认
        confirmationDeferred = CompletableDeferred()

        // 更新UI状态，显示对话框
        _confirmationRequest.value = message

        // 等待用户确认
        return try {
            val result = confirmationDeferred?.await() ?: false
            Log.d(TAG, "User confirmation result: $result")
            result
        } catch (e: Exception) {
            Log.e(TAG, "Confirmation cancelled", e)
            false
        } finally {
            _confirmationRequest.value = null
            confirmationDeferred = null
        }
    }

    /**
     * 用户确认操作
     */
    fun confirmAction(confirmed: Boolean) {
        Log.d(TAG, "confirmAction called: $confirmed")
        confirmationDeferred?.complete(confirmed)
    }

    /**
     * 用户完成Take Over
     */
    fun completeTakeOver() {
        Log.d(TAG, "completeTakeOver called")
        takeOverDeferred?.complete(Unit)
    }

    /**
     * 用户取消Take Over
     */
    fun cancelTakeOver() {
        Log.d(TAG, "cancelTakeOver called")
        takeOverDeferred?.cancel()
        stopTask()
    }
}
