package com.taskwizard.android.ui.viewmodel

import android.app.Application
import android.graphics.Bitmap
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.taskwizard.android.IAutoGLMService
import com.taskwizard.android.TaskScope
import com.taskwizard.android.api.ApiClient
import com.taskwizard.android.core.ActionExecutor
import com.taskwizard.android.core.AgentCore
import com.taskwizard.android.data.*
import com.taskwizard.android.data.history.HistoryRepository
import com.taskwizard.android.data.history.TaskStatus
import com.taskwizard.android.data.template.TaskTemplateEntity
import com.taskwizard.android.data.template.TemplateRepository
import com.taskwizard.android.manager.OverlayPermissionManager
import com.taskwizard.android.manager.ShizukuManager
import com.taskwizard.android.service.OverlayService
import com.taskwizard.android.ui.theme.ThemeMode
import com.taskwizard.android.utils.SecureSettingsManager
import com.taskwizard.android.utils.SettingsManager
import android.content.Intent
import android.os.Build
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toPersistentList
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
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

    // ==================== 消息数据库批量写入优化 ====================

    // 性能优化：批量写入数据库，避免频繁的IO操作
    private val pendingMessagesToSave = mutableListOf<MessageItem>()
    private val pendingActionsToSave = mutableListOf<Action>()
    private var isBatching = false

    /**
     * 获取所有动作（已保存 + 待保存）
     * 由于批量处理有100ms延迟，需要合并两个列表
     */
    private fun getAllActions(): List<Action> {
        return taskActions + pendingActionsToSave
    }

    // SharedPreferences用于主题持久化
    private val prefs = application.getSharedPreferences("app_prefs", 0)

    // ==================== 业务逻辑组件 ====================

    private var agentCore: AgentCore? = null
    private var actionExecutor: ActionExecutor? = null
    private var taskJob: Job? = null
    private var service: IAutoGLMService? = null

    // ==================== 历史记录追踪 ====================

    private val historyRepository = HistoryRepository(application)
    private val templateRepository: TemplateRepository
    private val gson = Gson()
    private var currentTaskHistoryId: Long? = null
    private var taskStartTime: Long = 0
    private val taskMessages = mutableListOf<MessageItem>()
    private val taskActions = mutableListOf<Action>()
    private val taskErrors = mutableListOf<String>()
    private val apiContextMessages = mutableListOf<Message>()  // For AI context restoration

    // 截图尺寸记录（用于模板保存时保持坐标一致性）
    private var lastScreenshotWidth: Int = 0
    private var lastScreenshotHeight: Int = 0

    // 对话框状态
    private val _confirmationRequest = MutableStateFlow<String?>(null)
    val confirmationRequest: StateFlow<String?> = _confirmationRequest.asStateFlow()

    private val _takeOverRequest = MutableStateFlow<String?>(null)
    val takeOverRequest: StateFlow<String?> = _takeOverRequest.asStateFlow()

    // 对话框响应的CompletableDeferred
    private var confirmationDeferred: CompletableDeferred<Boolean>? = null
    private var takeOverDeferred: CompletableDeferred<Unit>? = null

    // 保存模板对话框状态
    private val _showSaveTemplateDialog = MutableStateFlow(false)
    val showSaveTemplateDialog: StateFlow<Boolean> = _showSaveTemplateDialog.asStateFlow()

    // 悬浮窗控制
    private val _overlayEnabled = MutableStateFlow(false)
    val overlayEnabled: StateFlow<Boolean> = _overlayEnabled.asStateFlow()

    // ==================== 引导对话框状态 ====================

    /**
     * 是否显示Shizuku引导对话框
     */
    private val _showShizukuGuide = MutableStateFlow(false)
    val showShizukuGuide: StateFlow<Boolean> = _showShizukuGuide.asStateFlow()

    /**
     * 是否显示ADB Keyboard引导对话框
     */
    private val _showADBKeyboardGuide = MutableStateFlow(false)
    val showADBKeyboardGuide: StateFlow<Boolean> = _showADBKeyboardGuide.asStateFlow()

    /**
     * 显示Shizuku引导对话框
     */
    fun showShizukuGuide() {
        _showShizukuGuide.value = true
    }

    /**
     * 显示ADB Keyboard引导对话框
     */
    fun showADBKeyboardGuide() {
        _showADBKeyboardGuide.value = true
    }

    /**
     * 关闭Shizuku引导对话框
     */
    fun dismissShizukuGuide() {
        _showShizukuGuide.value = false
    }

    /**
     * 关闭ADB Keyboard引导对话框
     */
    fun dismissADBKeyboardGuide() {
        _showADBKeyboardGuide.value = false
    }

    /**
     * 是否显示 TaskWizard IME 引导对话框
     */
    private val _showTaskWizardIMEGuide = MutableStateFlow(false)
    val showTaskWizardIMEGuide: StateFlow<Boolean> = _showTaskWizardIMEGuide.asStateFlow()

    /**
     * 显示 TaskWizard IME 引导对话框
     */
    fun showTaskWizardIMEGuide() {
        _showTaskWizardIMEGuide.value = true
    }

    /**
     * 关闭 TaskWizard IME 引导对话框
     */
    fun dismissTaskWizardIMEGuide() {
        _showTaskWizardIMEGuide.value = false
    }

    // ==================== 悬浮窗权限引导对话框状态 ====================

    /**
     * 是否显示悬浮窗权限引导对话框
     */
    private val _showOverlayPermissionGuide = MutableStateFlow(false)
    val showOverlayPermissionGuide: StateFlow<Boolean> = _showOverlayPermissionGuide.asStateFlow()

    /**
     * 显示悬浮窗权限引导对话框
     */
    fun showOverlayPermissionGuide() {
        _showOverlayPermissionGuide.value = true
    }

    /**
     * 关闭悬浮窗权限引导对话框
     */
    fun dismissOverlayPermissionGuide() {
        _showOverlayPermissionGuide.value = false
    }

    // ==================== 动画状态管理（阶段1新增）====================

    /**
     * 是否正在执行缩小到悬浮窗的动画
     */
    private val _isAnimatingToOverlay = MutableStateFlow(false)
    val isAnimatingToOverlay: StateFlow<Boolean> = _isAnimatingToOverlay.asStateFlow()

    /**
     * 是否正在从悬浮窗放大回来的动画
     */
    private val _isAnimatingFromOverlay = MutableStateFlow(false)
    val isAnimatingFromOverlay: StateFlow<Boolean> = _isAnimatingFromOverlay.asStateFlow()

    /**
     * 是否应该将Activity移到后台
     * 用于在动画完成后通知MainActivity执行moveTaskToBack
     */
    private val _shouldMoveToBackground = MutableStateFlow(false)
    val shouldMoveToBackground: StateFlow<Boolean> = _shouldMoveToBackground.asStateFlow()

    init {
        // 初始化 TemplateRepository
        val db = com.taskwizard.android.data.history.TaskHistoryDatabase.getDatabase(application)
        templateRepository = TemplateRepository(db.templateDao())

        // 初始化SettingsManager
        SettingsManager.init(application)

        // 初始化SecureSettingsManager (用于加密存储API Key)
        try {
            SecureSettingsManager.init(application)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize SecureSettingsManager", e)
        }

        // 迁移现有的API Key到加密存储（一次性迁移）
        migrateApiKeyToSecureStorage()

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
                    // ✅ 修改：只更新悬浮窗，不再添加到主界面消息列表
                    // 因为主界面在任务执行时已经关闭
                    OverlayService.instance?.updateError(errorMsg)
                }
            }
        }
    }

    /**
     * 迁移现有的API Key从普通SharedPreferences到加密存储
     * 这是一次性操作，迁移后会清除普通SharedPreferences中的API Key
     */
    private fun migrateApiKeyToSecureStorage() {
        try {
            // 从普通存储读取API Key
            val plainApiKey = SettingsManager.apiKey

            // 如果普通存储中有API Key
            if (plainApiKey.isNotEmpty()) {
                // 检查加密存储中是否已有API Key
                if (!SecureSettingsManager.hasApiKey()) {
                    // 迁移到加密存储
                    SecureSettingsManager.apiKey = plainApiKey
                    Log.d(TAG, "API Key migrated to encrypted storage")

                    // 清除普通存储中的API Key
                    SettingsManager.apiKey = ""
                    Log.d(TAG, "API Key cleared from plaintext storage")
                } else {
                    // 加密存储中已有API Key，保留加密存储的版本
                    // 清除普通存储中的版本（如果存在）
                    SettingsManager.apiKey = ""
                    Log.d(TAG, "API Key already in encrypted storage, cleared plaintext")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to migrate API Key to secure storage", e)
        }
    }

    // ==================== 配置管理 ====================

    /**
     * 从SettingsManager加载配置
     * 性能优化：同时加载到 AppState 和 SettingsState
     * 安全改进：API Key从加密存储读取
     */
    private fun loadSettings() {
        // 从加密存储读取API Key
        val apiKey = try {
            SecureSettingsManager.apiKey
        } catch (e: Exception) {
            Log.e(TAG, "Failed to read API key from secure storage", e)
            // Fallback to regular storage if secure storage fails
            SettingsManager.apiKey
        }
        val baseUrl = SettingsManager.baseUrl
        val model = SettingsManager.model
        val themeMode = loadThemeMode()
        val pureBlackEnabled = loadPureBlackEnabled()
        val timeoutSeconds = SettingsManager.timeoutSeconds
        val retryCount = SettingsManager.retryCount
        val debugMode = SettingsManager.debugMode
        val takeoverTimeoutSeconds = SettingsManager.takeoverTimeoutSeconds

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
                takeoverTimeoutSeconds = takeoverTimeoutSeconds,
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
     * 安全改进：API Key保存到加密存储
     */
    fun saveSettings() {
        val current = _state.value

        // 保存API Key到加密存储
        try {
            SecureSettingsManager.apiKey = current.apiKey
        } catch (e: Exception) {
            Log.e(TAG, "Failed to save API key to secure storage", e)
            // Fallback to regular storage if secure storage fails
            SettingsManager.apiKey = current.apiKey
        }

        // 其他配置保存到普通存储
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
     * 更新人工接管超时时间
     */
    fun updateTakeoverTimeout(seconds: Int) {
        _settingsState.update { it.copy(takeoverTimeoutSeconds = seconds) }
        SettingsManager.takeoverTimeoutSeconds = seconds
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
     * 性能优化：立即更新UI，批量写入数据库
     */
    fun addThinkMessage(content: String) {
        val message = MessageItem.ThinkMessage(content = content)
        _state.update {
            it.copy(messages = (it.messages + message).toPersistentList())
        }
        // 批量写入数据库
        batchRecordTaskMessage(message)
    }

    /**
     * 添加操作消息
     * 性能优化：立即更新UI，批量写入数据库
     */
    fun addActionMessage(action: Action) {
        _state.update {
            it.copy(messages = (it.messages + MessageItem.ActionMessage(action = action)).toPersistentList())
        }
        // 批量写入数据库
        batchRecordTaskAction(action)
    }

    /**
     * 添加系统消息
     * 性能优化：立即更新UI，批量写入数据库
     */
    fun addSystemMessage(content: String, type: SystemMessageType) {
        val message = MessageItem.SystemMessage(content = content, type = type)
        _state.update {
            it.copy(messages = (it.messages + message).toPersistentList())
        }
        // 系统消息不需要保存到历史记录
    }

    /**
     * 添加保存Task提示消息
     * 在任务完成后显示，允许用户选择是否保存为可重复执行的Task
     */
    fun addSaveTaskPrompt(stepCount: Int) {
        val message = MessageItem.SystemMessage(
            content = "任务完成！是否保存为可重复执行的Task？",
            type = SystemMessageType.SAVE_TASK_PROMPT,
            stepCount = stepCount
        )
        _state.update {
            it.copy(messages = (it.messages + message).toPersistentList())
        }
    }

    /**
     * 处理从消息气泡中点击"保存为Task"按钮
     * 显示保存对话框（复用现有的 SaveTemplateDialog）
     */
    fun handleSaveAsTaskFromMessage() {
        val allActions = getAllActions()
        if (allActions.isNotEmpty()) {
            _state.update { it.copy(templateStepCount = allActions.size) }
            showSaveTemplateDialog()
        }
    }

    /**
     * 批量写入消息到数据库
     * 性能优化：100ms内的消息会被批量写入，减少IO操作
     */
    private fun batchRecordTaskMessage(message: MessageItem) {
        pendingMessagesToSave.add(message)

        if (!isBatching) {
            isBatching = true
            viewModelScope.launch(Dispatchers.IO) {
                delay(100) // 批量100ms内的消息
                val messagesToSave = pendingMessagesToSave.toList()
                pendingMessagesToSave.clear()
                isBatching = false

                messagesToSave.forEach { recordTaskMessage(it) }
            }
        }
    }

    /**
     * 批量写入操作到数据库
     * 性能优化：100ms内的操作会被批量写入，减少IO操作
     */
    private fun batchRecordTaskAction(action: Action) {
        pendingActionsToSave.add(action)

        if (!isBatching) {
            isBatching = true
            viewModelScope.launch(Dispatchers.IO) {
                delay(100) // 批量100ms内的操作
                val actionsToSave = pendingActionsToSave.toList()
                pendingActionsToSave.clear()
                isBatching = false

                actionsToSave.forEach { recordTaskAction(it) }
            }
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

                // 如果有Shizuku权限，检查输入法状态
                val (isInstalled, isEnabled, isTaskWizardIMEEnabled) = if (hasShizuku) {
                    try {
                        val service = ShizukuManager.bindService(getApplication())
                        val installed = service.isADBKeyboardInstalled()
                        val enabled = if (installed) {
                            service.isIMEEnabled("com.android.adbkeyboard/.AdbIME")
                        } else {
                            false
                        }
                        // 检查 TaskWizard IME 是否已启用
                        val taskWizardEnabled = service.isIMEEnabled("com.taskwizard.android/.ime.TaskWizardIME")
                        Triple(installed, enabled, taskWizardEnabled)
                    } catch (e: Exception) {
                        Triple(false, false, false)
                    }
                } else {
                    Triple(false, false, false)
                }

                // 更新状态
                _state.update { it.copy(
                    hasShizukuPermission = hasShizuku,
                    isADBKeyboardInstalled = isInstalled,
                    isADBKeyboardEnabled = isEnabled,
                    isTaskWizardIMEEnabled = isTaskWizardIMEEnabled
                )}
            } catch (e: Exception) {
                _state.update { it.copy(hasShizukuPermission = false) }
            }
        }
    }

    // ==================== 任务执行 ====================

    /**
     * 启动任务
     * 修改：添加网络预检查，在动画前验证网络连接和API配置
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

        // 新增：检查悬浮窗权限
        if (!OverlayPermissionManager.checkPermission(getApplication())) {
            addSystemMessage("需要悬浮窗权限才能执行任务", SystemMessageType.ERROR)
            showOverlayPermissionGuide()
            return
        }

        // ✅ 新增：启动前网络预检查
        addSystemMessage("正在检查网络连接...", SystemMessageType.INFO)

        viewModelScope.launch {
            try {
                // 1. 初始化 ApiClient
                val currentState = _state.value
                ApiClient.init(currentState.baseUrl, currentState.apiKey)

                // 2. 执行预检查请求（简单的健康检查）
                val testResult = withContext(Dispatchers.IO) {
                    performNetworkPreCheck()
                }

                if (!testResult.success) {
                    // 预检查失败，显示错误并停止
                    addSystemMessage(
                        "网络预检查失败: ${testResult.errorMessage}\n请检查：\n1. 网络连接是否正常\n2. Base URL 是否正确\n3. API Key 是否有效",
                        SystemMessageType.ERROR
                    )
                    return@launch
                }

                // 3. 预检查成功，显示成功消息
                addSystemMessage("网络连接正常，准备启动任务", SystemMessageType.SUCCESS)
                delay(500) // 让用户看到成功消息

                // 4. 启动任务（原有逻辑）
                startTaskAfterPreCheck(task)

            } catch (e: Exception) {
                Log.e(TAG, "Pre-check failed", e)
                addSystemMessage(
                    "启动前检查失败: ${e.message}",
                    SystemMessageType.ERROR
                )
            }
        }
    }

    /**
     * 停止任务
     */
    fun stopTask() {
        Log.d(TAG, "stopTask() called")

        // 立即还原IME（同步调用，确保执行）
        Log.d(TAG, "Restoring IME immediately before task cancellation")
        actionExecutor?.restoreIME()

        // 使用 TaskScope 停止任务
        TaskScope.stopCurrentTask()

        taskJob = null
        _state.update { it.copy(isRunning = false) }

        // ✅ 修复：用户手动停止后清空输入框
        clearTask()

        addSystemMessage("任务已停止", SystemMessageType.INFO)

        // 取消历史记录
        cancelTaskHistory()

        // 停止悬浮窗服务
        stopOverlayService()
    }

    /**
     * 执行网络预检查
     * 发送一个简单的测试请求来验证网络连接和API配置
     * @return 预检查结果
     */
    private suspend fun performNetworkPreCheck(): PreCheckResult {
        return try {
            // 发送一个简单的测试请求
            val testMessages = listOf(
                Message("system", "You are a helpful assistant."),
                Message("user", "Hello")
            )

            val response = ApiClient.getService().chatCompletion(
                OpenAIRequest(
                    model = _state.value.model,
                    messages = testMessages,
                    max_tokens = 5,
                    temperature = 0.0
                )
            )

            if (response.isSuccessful) {
                Log.d(TAG, "Network pre-check successful")
                PreCheckResult(success = true)
            } else {
                val errorMsg = when (response.code()) {
                    401 -> "API Key 无效或已过期"
                    403 -> "API 访问被拒绝，请检查权限"
                    404 -> "API 地址不存在，请检查 Base URL"
                    429 -> "API 请求频率超限，请稍后再试"
                    500, 502, 503 -> "API 服务器错误，请稍后再试"
                    else -> "API 错误 (${response.code()})"
                }
                Log.e(TAG, "Network pre-check failed: $errorMsg (code=${response.code()})")
                PreCheckResult(success = false, errorMessage = errorMsg)
            }
        } catch (e: Exception) {
            val errorMsg = when (e) {
                is java.net.SocketTimeoutException -> "网络连接超时"
                is java.net.UnknownHostException -> "无法连接到服务器，请检查网络"
                is javax.net.ssl.SSLException -> "SSL 证书错误"
                else -> "网络错误: ${e.message}"
            }
            Log.e(TAG, "Network pre-check exception: $errorMsg", e)
            PreCheckResult(success = false, errorMessage = errorMsg)
        }
    }

    /**
     * 预检查通过后启动任务
     * 这是原有的启动逻辑，从 startTask() 中提取出来
     */
    private fun startTaskAfterPreCheck(task: String) {
        // Check if continuing from history
        val isContinuation = currentTaskHistoryId != null && _state.value.isContinuedConversation

        if (!isContinuation) {
            // New task - create new history record
            createTaskHistory(task, _state.value.model)
        } else {
            // Continuing from history - update existing record to RUNNING
            viewModelScope.launch {
                try {
                    currentTaskHistoryId?.let { id ->
                        historyRepository.updateTaskStatus(id, TaskStatus.RUNNING.name, "继续任务")
                    }
                    addSystemMessage("继续执行任务", SystemMessageType.INFO)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to update task status", e)
                }
            }
        }

        // 启动任务
        _state.update { it.copy(isRunning = true) }

        // ✅ 修复：任务启动后立即清空输入框
        clearTask()

        addSystemMessage("任务启动: $task", SystemMessageType.INFO)

        // 更新任务状态为运行中 (only for new tasks)
        if (!isContinuation) {
            updateTaskStatusRunning()
        }

        Log.d(TAG, "Starting task with API config:")
        Log.d(TAG, "  Base URL: ${_state.value.baseUrl}")
        Log.d(TAG, "  API Key: ${_state.value.apiKey.take(10)}...")
        Log.d(TAG, "  Model: ${_state.value.model}")

        // 关键修复：注册任务停止回调，用于状态同步
        TaskScope.setOnTaskStoppedCallback {
            // 在主线程更新状态
            viewModelScope.launch(Dispatchers.Main) {
                Log.d(TAG, "Task stopped callback invoked from TaskScope")
                updateTaskStoppedState()
            }
        }

        // 启动缩小动画流程
        viewModelScope.launch {
            // 1. 触发缩小动画
            _isAnimatingToOverlay.value = true
            Log.d(TAG, "Animation to overlay started")

            // 2. 等待动画完成（300ms动画 + 50ms缓冲）
            delay(350)
            Log.d(TAG, "Animation to overlay completed")

            // 3. 通知MainActivity移到后台（不finish，保留ViewModel）
            withContext(Dispatchers.Main) {
                _shouldMoveToBackground.value = true
            }
            Log.d(TAG, "Move to background signal sent")
        }

        // 4. 使用 TaskScope.launchTask 启动任务
        val core = agentCore ?: run {
            Log.e(TAG, "AgentCore not initialized")
            return
        }
        taskJob = TaskScope.launchTask(core) {
            try {
                executeTask(task)
            } catch (e: kotlinx.coroutines.CancellationException) {
                Log.d(TAG, "Task cancelled: ${e.message}")
                withContext(Dispatchers.Main) {
                    addSystemMessage("任务已取消", SystemMessageType.INFO)
                    // 关键修复：在取消时也要调用 cleanupTask()
                    cleanupTask()
                }
                // 不重新抛出异常，让 finally 块正常执行
            } catch (e: Exception) {
                Log.e(TAG, "Task execution failed", e)
                withContext(Dispatchers.Main) {
                    addSystemMessage("任务执行失败: ${e.message}", SystemMessageType.ERROR)
                }
            } finally {
                withContext(Dispatchers.Main) {
                    cleanupTask()
                }
            }
        }
    }

    /**
     * 执行任务的主循环
     */
    private suspend fun executeTask(task: String) {
        try {
            // 1. 初始化ApiClient（使用当前配置）
            val currentState = _state.value
            ApiClient.init(currentState.baseUrl, currentState.apiKey)
            Log.d(TAG, "ApiClient initialized with baseUrl=${currentState.baseUrl}, apiKey=${currentState.apiKey.take(10)}...")

            // 2. 启动悬浮窗服务
            withContext(Dispatchers.Main) {
                startOverlayService()
            }
            // 标记任务开始
            OverlayService.instance?.markTaskStarted()

            // 3. 初始化AgentCore
            agentCore?.startSession(task)

            // 4. 绑定Shizuku服务
            val shizukuService = try {
                ShizukuManager.bindService(getApplication())
            } catch (e: Exception) {
                Log.e(TAG, "Failed to bind Shizuku service", e)
                withContext(Dispatchers.Main) {
                    addSystemMessage("无法连接Shizuku服务", SystemMessageType.ERROR)
                }
                return
            }
            service = shizukuService

            // 5. 初始化ActionExecutor
            val screenSize = getScreenSize()
            actionExecutor = ActionExecutor(
                context = getApplication(),
                service = shizukuService,
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

            // 关键：注册清理回调到 TaskScope
            TaskScope.registerCleanupCallback {
                Log.d(TAG, "Cleanup callback invoked - restoring IME")
                actionExecutor?.restoreIME()
            }

            // 6. 执行步骤循环
            var stepCount = 0
            var consecutiveFailures = 0  // 连续失败计数
            val MAX_CONSECUTIVE_FAILURES = 3  // 最大连续失败次数

            while (agentCore?.isSessionRunning() == true && stepCount < MAX_STEPS) {
                stepCount++

                // 更新悬浮窗步骤数
                OverlayService.instance?.updateStep(stepCount)

                Log.d(TAG, "Step $stepCount")

                // 6.1 截图
                val screenshot = captureScreenshot()
                if (screenshot == null) {
                    // ✅ 修改：不再添加到主界面消息列表，因为主界面已经关闭
                    Log.e(TAG, "Screenshot failed at step $stepCount")
                    break
                }

                // 6.2 更新悬浮窗：开始思考
                OverlayService.instance?.updateThinking(true)

                // 6.3 调用AgentCore获取下一步动作
                val action = agentCore?.step(screenshot)

                // 6.4 更新悬浮窗：结束思考
                OverlayService.instance?.updateThinking(false)

                // 6.5 显示AI思考内容
                agentCore?.lastThink?.let { think ->
                    if (think.isNotBlank()) {
                        // ✅ 恢复：添加到主界面消息列表，因为Activity不再finish，ViewModel保留
                        withContext(Dispatchers.Main) {
                            addThinkMessage("[$stepCount] $think")
                        }
                        Log.d(TAG, "[$stepCount] Think: $think")
                        consecutiveFailures = 0  // 有思考内容说明成功，重置计数
                    }
                }

                // 6.6 执行动作
                if (action != null) {
                    // ✅ 恢复：添加到主界面消息列表
                    withContext(Dispatchers.Main) {
                        addActionMessage(action)
                    }
                    Log.d(TAG, "[$stepCount] Action: ${action.action}")
                    consecutiveFailures = 0  // 有动作说明成功，重置计数

                    // 清除错误状态
                    OverlayService.instance?.clearError()

                    // 更新悬浮窗：显示动作（跳过 take_over，因为它有专门的 UI）
                    if (action.action?.lowercase() != "take_over") {
                        val actionText = formatActionForOverlay(action)
                        OverlayService.instance?.updateAction(actionText)
                    }

                    // 检查是否是finish动作
                    if (action.action?.lowercase() == "finish") {
                        val message = action.message ?: action.content ?: "任务完成"
                        // 计算总动作数量（使用 getAllActions() 合并已保存和待保存的动作）
                        val actionCount = getAllActions().size

                        // 添加到主界面消息列表
                        withContext(Dispatchers.Main) {
                            addSystemMessage(message, SystemMessageType.SUCCESS)
                            // 添加保存Task提示消息（如果有动作可保存）
                            if (actionCount > 0) {
                                addSaveTaskPrompt(actionCount)
                            }
                        }
                        Log.d(TAG, "Task finished: $message")
                        // 标记任务完成
                        OverlayService.instance?.markCompleted()
                        // 完成历史记录
                        completeTaskHistory(message)
                        break
                    }

                    // 执行动作，获取执行结果
                    val result = actionExecutor?.execute(action) ?: ActionExecutor.ExecuteResult(success = true)

                    // 检查是否有错误
                    if (!result.success && result.errorMessage != null) {
                        // 显示错误信息
                        withContext(Dispatchers.Main) {
                            addSystemMessage(
                                "执行失败: ${result.errorMessage}",
                                SystemMessageType.ERROR
                            )
                        }
                        Log.e(TAG, "Action execution failed: ${result.errorMessage}")

                        // 更新悬浮窗错误状态
                        OverlayService.instance?.updateError(
                            "执行失败",
                            consecutiveFailures + 1
                        )

                        consecutiveFailures++

                        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                            withContext(Dispatchers.Main) {
                                addSystemMessage(
                                    "连续失败${MAX_CONSECUTIVE_FAILURES}次，任务已停止",
                                    SystemMessageType.ERROR
                                )
                                // ✅ 修复：任务失败停止后清空输入框
                                clearTask()
                            }
                            Log.e(TAG, "Max consecutive failures reached, stopping task")
                            // 停止悬浮窗服务
                            delay(3000)
                            val intent = Intent(getApplication(), OverlayService::class.java)
                            getApplication<Application>().stopService(intent)
                            // 标记历史为失败
                            failTaskHistory("连续失败${MAX_CONSECUTIVE_FAILURES}次")
                            break
                        }

                        // 继续下一次循环，让 AI 重新尝试
                        delay(500)
                        continue
                    }

                    // 检查用户是否取消
                    if (!result.shouldContinue) {
                        // ✅ 恢复：添加到主界面消息列表
                        withContext(Dispatchers.Main) {
                            addSystemMessage("用户取消操作", SystemMessageType.WARNING)
                        }
                        Log.d(TAG, "User cancelled operation")
                        break
                    }

                    // 执行成功，重置失败计数
                    consecutiveFailures = 0

                    // Save API context messages periodically for conversation continuation
                    agentCore?.getApiHistory()?.let { apiHistory ->
                        apiContextMessages.clear()
                        apiContextMessages.addAll(apiHistory)
                        // Save to database periodically
                        currentTaskHistoryId?.let { id ->
                            viewModelScope.launch(Dispatchers.IO) {
                                try {
                                    historyRepository.updateApiContextMessages(id, apiContextMessages.toList())
                                } catch (e: Exception) {
                                    Log.e(TAG, "Failed to save API context messages", e)
                                }
                            }
                        }
                    }

                    // 等待一小段时间让UI更新
                    delay(500)
                } else {
                    // 处理action为null的情况
                    consecutiveFailures++
                    Log.w(TAG, "No action returned from AgentCore (failure $consecutiveFailures/$MAX_CONSECUTIVE_FAILURES)")

                    // 更新悬浮窗错误状态
                    OverlayService.instance?.updateError(
                        "获取动作失败",
                        consecutiveFailures
                    )

                    if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                        // ✅ 恢复：添加到主界面消息列表
                        withContext(Dispatchers.Main) {
                            addSystemMessage(
                                "连续失败${MAX_CONSECUTIVE_FAILURES}次，任务已停止",
                                SystemMessageType.ERROR
                            )
                        }
                        Log.e(TAG, "Max consecutive failures reached, stopping task")

                        // 停止悬浮窗服务
                        delay(3000)  // 显示错误 3 秒
                        withContext(Dispatchers.Main) {
                            stopOverlayService()
                        }

                        break  // 停止循环
                    }

                    delay(1000)
                }
            }

            if (stepCount >= MAX_STEPS) {
                // ✅ 恢复：添加到主界面消息列表
                withContext(Dispatchers.Main) {
                    addSystemMessage("已达到最大步骤数($MAX_STEPS)，任务停止", SystemMessageType.WARNING)
                }
                Log.w(TAG, "Max steps reached")
                // 标记历史为失败
                failTaskHistory("达到最大步骤数")
            }
        } finally {
            // 关键：finally 块保证清理代码执行
            Log.d(TAG, "executeTask finally block - restoring IME")
            actionExecutor?.restoreIME()
        }
    }

    /**
     * 清理任务资源
     */
    private fun cleanupTask() {
        actionExecutor?.restoreIME()
        _state.update { it.copy(isRunning = false) }
        // 注意：不在这里停止悬浮窗，让用户点击"已完成"后返回应用
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

                        // 记录截图尺寸（用于模板保存时保持坐标一致性）
                        lastScreenshotWidth = bitmap.width
                        lastScreenshotHeight = bitmap.height

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
     * 在悬浮窗显示人工接管状态，等待用户点击完成或超时
     */
    private suspend fun handleTakeOver(message: String) {
        Log.d(TAG, "Take over requested: $message")

        val timeoutSeconds = SettingsManager.takeoverTimeoutSeconds
        takeOverDeferred = CompletableDeferred()

        // 设置回调：用户点击悬浮窗时完成接管
        OverlayService.onTakeoverCompleteCallback = {
            takeOverDeferred?.complete(Unit)
        }

        // 检查 OverlayService 是否可用
        if (OverlayService.instance == null) {
            Log.e(TAG, "handleTakeOver: OverlayService.instance is null!")
            addSystemMessage("悬浮窗服务不可用，无法进行人工接管", SystemMessageType.ERROR)
            return
        }

        // 在悬浮窗显示 takeover 状态
        OverlayService.instance?.startTakeover(message, timeoutSeconds)
        Log.d(TAG, "handleTakeOver: startTakeover called with timeout=$timeoutSeconds")
        addSystemMessage("需要人工介入: $message", SystemMessageType.WARNING)

        // 启动超时计时器
        val timeoutJob = viewModelScope.launch {
            delay(timeoutSeconds * 1000L)
            takeOverDeferred?.cancel()
        }

        try {
            takeOverDeferred?.await()
            Log.d(TAG, "User completed take over")
            addSystemMessage("继续执行任务", SystemMessageType.INFO)
        } catch (e: CancellationException) {
            Log.e(TAG, "Take over timeout or cancelled")
            addSystemMessage("人工接管超时，任务已取消", SystemMessageType.WARNING)
            stopTask()
        } catch (e: Exception) {
            Log.e(TAG, "Take over error", e)
            addSystemMessage("任务已取消", SystemMessageType.WARNING)
        } finally {
            timeoutJob.cancel()
            OverlayService.onTakeoverCompleteCallback = null
            OverlayService.instance?.completeTakeover()
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

    // ==================== 悬浮窗控制 ====================

    /**
     * 启动悬浮窗服务
     */
    fun startOverlayService() {
        if (!OverlayPermissionManager.checkPermission(getApplication())) {
            addSystemMessage("需要悬浮窗权限", SystemMessageType.ERROR)
            return
        }

        val intent = Intent(getApplication(), OverlayService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getApplication<Application>().startForegroundService(intent)
        } else {
            getApplication<Application>().startService(intent)
        }
        _overlayEnabled.value = true
        Log.d(TAG, "Overlay service started")
    }

    /**
     * 停止悬浮窗服务
     */
    fun stopOverlayService() {
        val intent = Intent(getApplication(), OverlayService::class.java)
        getApplication<Application>().stopService(intent)
        _overlayEnabled.value = false
        Log.d(TAG, "Overlay service stopped")
    }

    // ==================== 动画控制方法（阶段1新增）====================

    /**
     * 设置是否正在执行缩小到悬浮窗的动画
     */
    fun setAnimatingToOverlay(isAnimating: Boolean) {
        _isAnimatingToOverlay.value = isAnimating
        Log.d(TAG, "setAnimatingToOverlay: $isAnimating")
    }

    /**
     * 设置是否正在从悬浮窗放大回来的动画
     */
    fun setAnimatingFromOverlay(isAnimating: Boolean) {
        _isAnimatingFromOverlay.value = isAnimating
        Log.d(TAG, "setAnimatingFromOverlay: $isAnimating")
    }

    /**
     * 重置moveToBackground标志
     * 在Activity移到后台后调用，避免重复操作
     */
    fun resetMoveToBackgroundFlag() {
        _shouldMoveToBackground.value = false
        Log.d(TAG, "resetMoveToBackgroundFlag")
    }

    /**
     * 格式化Action为悬浮窗显示文本
     */
    private fun formatActionForOverlay(action: Action): String {
        return when (action.action?.lowercase()) {
            "tap" -> "点击 ${action.location}"
            "double tap" -> "双击 ${action.location}"
            "long press" -> "长按 ${action.location}"
            "type", "type_name" -> "输入文本"
            "swipe" -> "滑动"
            "launch" -> "启动应用"
            "back" -> "返回"
            "home" -> "主屏幕"
            "enter" -> "确认"
            "wait" -> "等待"
            "note" -> "记录"
            "take_over" -> "人工接管"
            else -> action.action ?: "执行操作"
        }
    }

    // ==================== 状态同步方法（修复后台状态更新问题）====================

    /**
     * 强制刷新状态
     *
     * 用于解决 Activity 从后台恢复时状态不同步的问题。
     *
     * 问题场景：
     * 1. 任务在悬浮窗中停止，MainActivity 在后台
     * 2. collectAsStateWithLifecycle() 在 STOPPED 状态暂停收集
     * 3. MainActivity 恢复时，虽然重新开始收集，但不会触发重组
     *
     * 解决方案：
     * 通过创建新的状态副本，强制触发 StateFlow 更新，从而触发 Compose 重组
     */
    fun refreshState() {
        Log.d(TAG, "refreshState() called - forcing state update")
        _state.update { it.copy() }
        _settingsState.update { it.copy() }
    }

    /**
     * 更新任务停止状态
     *
     * 由 TaskScope 的回调调用，用于在任务停止时更新 ViewModel 状态
     * 这确保了无论从哪里停止任务（主 app 或悬浮窗），状态都会正确更新
     */
    fun updateTaskStoppedState() {
        Log.d(TAG, "updateTaskStoppedState() called")
        _state.update { it.copy(isRunning = false) }
        taskJob = null
        addSystemMessage("任务已停止", SystemMessageType.INFO)
    }

    // ==================== 历史记录追踪方法 ====================

    /**
     * 创建任务历史记录
     */
    private fun createTaskHistory(task: String, model: String) {
        viewModelScope.launch {
            try {
                currentTaskHistoryId = historyRepository.createTask(task, model)
                taskStartTime = System.currentTimeMillis()
                // 清空之前的追踪数据
                taskMessages.clear()
                taskActions.clear()
                taskErrors.clear()
                Log.d(TAG, "Created task history with ID: $currentTaskHistoryId")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to create task history", e)
            }
        }
    }

    /**
     * 更新任务状态为运行中
     */
    private fun updateTaskStatusRunning() {
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    historyRepository.updateTaskStatus(id, TaskStatus.RUNNING.name)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to update task status to running", e)
            }
        }
    }

    /**
     * 记录任务消息
     */
    fun recordTaskMessage(message: MessageItem) {
        taskMessages.add(message)
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    historyRepository.updateTaskMessages(id, taskMessages.toList())
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to record task message", e)
            }
        }
    }

    /**
     * 记录任务动作
     */
    fun recordTaskAction(action: Action) {
        taskActions.add(action)
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    historyRepository.updateTaskActions(id, taskActions.toList())
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to record task action", e)
            }
        }
    }

    // ==================== 模板保存功能 ====================

    /**
     * 显示保存模板对话框
     */
    fun showSaveTemplateDialog() {
        if (getAllActions().isNotEmpty()) {
            _showSaveTemplateDialog.value = true
        }
    }

    /**
     * 隐藏保存模板对话框
     */
    fun hideSaveTemplateDialog() {
        _showSaveTemplateDialog.value = false
        // 移除保存提示消息
        removeSaveTaskPromptMessage()
        // 用户取消保存后，清理任务状态
        clearTask()
    }

    /**
     * 移除保存Task提示消息
     * 在保存成功或取消后调用
     */
    private fun removeSaveTaskPromptMessage() {
        _state.update { state ->
            state.copy(
                messages = state.messages.filterNot {
                    it is MessageItem.SystemMessage &&
                    it.type == SystemMessageType.SAVE_TASK_PROMPT
                }.toPersistentList()
            )
        }
    }

    /**
     * 保存当前任务为模板
     */
    fun saveAsTemplate(name: String) {
        viewModelScope.launch {
            try {
                val allActions = getAllActions()
                if (allActions.isEmpty()) {
                    Log.w(TAG, "No actions to save as template")
                    return@launch
                }

                // 调试日志：打印保存的动作内容
                Log.d(TAG, "=== Saving template: $name ===")
                Log.d(TAG, "Total actions: ${allActions.size}")
                allActions.forEachIndexed { i, action ->
                    Log.d(TAG, "  [$i] ${action.action} location=${action.location} content=${action.content?.take(20)}")
                }

                // 使用截图尺寸（与 AI 任务执行时的坐标转换保持一致）
                // 如果没有截图尺寸记录，则使用 displayMetrics 作为后备
                val screenWidth = if (lastScreenshotWidth > 0) lastScreenshotWidth else {
                    getApplication<Application>().resources.displayMetrics.widthPixels
                }
                val screenHeight = if (lastScreenshotHeight > 0) lastScreenshotHeight else {
                    getApplication<Application>().resources.displayMetrics.heightPixels
                }
                Log.d(TAG, "Screen size for template: ${screenWidth}x${screenHeight} (from screenshot: ${lastScreenshotWidth > 0})")

                val actionsJson = gson.toJson(allActions)
                val template = TaskTemplateEntity(
                    name = name,
                    description = _state.value.currentTask,
                    actionsJson = actionsJson,
                    screenWidth = screenWidth,
                    screenHeight = screenHeight,
                    stepCount = allActions.size,
                    estimatedDurationMs = allActions.size * 1000L,
                    createdFromHistoryId = currentTaskHistoryId
                )

                val id = templateRepository.insert(template)
                Log.i(TAG, "Template saved: $name (id=$id, screen=${screenWidth}x${screenHeight})")

                _showSaveTemplateDialog.value = false
                // 移除保存提示消息
                removeSaveTaskPromptMessage()
                // 保存成功后清理任务状态
                clearTask()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to save template", e)
            }
        }
    }

    /**
     * 记录任务错误
     */
    fun recordTaskError(error: String) {
        taskErrors.add(error)
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    historyRepository.addErrorMessage(id, error)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to record task error", e)
            }
        }
    }

    /**
     * 更新任务步骤数
     */
    fun updateTaskStepCount(stepCount: Int) {
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    historyRepository.updateTaskStepCount(id, stepCount)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to update task step count", e)
            }
        }
    }

    /**
     * 完成任务历史记录（成功）
     */
    private fun completeTaskHistory(message: String?) {
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    val endTime = System.currentTimeMillis()
                    val duration = endTime - taskStartTime
                    val stepCount = taskMessages.count { it is MessageItem.ThinkMessage }

                    historyRepository.updateTaskCompletion(
                        taskId = id,
                        endTime = endTime,
                        durationMs = duration,
                        stepCount = stepCount
                    )
                    historyRepository.updateTaskStatus(
                        taskId = id,
                        status = TaskStatus.COMPLETED.name,
                        statusMessage = message
                    )
                    Log.d(TAG, "Completed task history: $id")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to complete task history", e)
            } finally {
                currentTaskHistoryId = null
            }
        }
    }

    /**
     * 标记任务历史记录为失败
     */
    private fun failTaskHistory(error: String?) {
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    val endTime = System.currentTimeMillis()
                    val duration = endTime - taskStartTime
                    val stepCount = taskMessages.count { it is MessageItem.ThinkMessage }

                    historyRepository.updateTaskCompletion(
                        taskId = id,
                        endTime = endTime,
                        durationMs = duration,
                        stepCount = stepCount
                    )
                    historyRepository.updateTaskStatus(
                        taskId = id,
                        status = TaskStatus.FAILED.name,
                        statusMessage = error
                    )
                    Log.d(TAG, "Failed task history: $id")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to mark task history as failed", e)
            } finally {
                currentTaskHistoryId = null
            }
        }
    }

    /**
     * 取消任务历史记录
     */
    private fun cancelTaskHistory() {
        viewModelScope.launch {
            try {
                currentTaskHistoryId?.let { id ->
                    val endTime = System.currentTimeMillis()
                    val duration = endTime - taskStartTime
                    val stepCount = taskMessages.count { it is MessageItem.ThinkMessage }

                    historyRepository.updateTaskCompletion(
                        taskId = id,
                        endTime = endTime,
                        durationMs = duration,
                        stepCount = stepCount
                    )
                    historyRepository.updateTaskStatus(
                        taskId = id,
                        status = TaskStatus.CANCELLED.name,
                        statusMessage = "用户取消"
                    )
                    Log.d(TAG, "Cancelled task history: $id")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to cancel task history", e)
            } finally {
                currentTaskHistoryId = null
            }
        }
    }

    // ==================== 历史对话继续功能 ====================

    /**
     * Load historical conversation for continuation
     * @param historyId ID of the historical task to load
     */
    fun loadHistoricalConversation(historyId: Long) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val task = historyRepository.getTaskById(historyId)
                if (task == null) {
                    withContext(Dispatchers.Main) {
                        addSystemMessage("无法找到历史记录", SystemMessageType.ERROR)
                    }
                    return@launch
                }

                // 1. Deserialize Think messages from messagesJson
                val thinkMessages = try {
                    gson.fromJson(task.messagesJson, Array<MessageItem>::class.java).toList()
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to deserialize messages", e)
                    emptyList()
                }

                // 2. Deserialize Action messages from actionsJson
                val actions = try {
                    gson.fromJson(task.actionsJson, Array<Action>::class.java).toList()
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to deserialize actions", e)
                    emptyList()
                }

                // 3. Convert actions to ActionMessage items
                val actionMessages = actions.map { MessageItem.ActionMessage(action = it) }

                // 4. Merge all messages - combine think and action messages
                val allMessages = thinkMessages + actionMessages

                // 5. Restore UI messages to AppState
                withContext(Dispatchers.Main) {
                    _state.update {
                        it.copy(
                            messages = allMessages.toPersistentList(),
                            currentTask = task.taskDescription,
                            model = task.model,
                            isContinuedConversation = true,
                            originalTaskId = historyId
                        )
                    }
                }

                // 6. Set current task history ID to update existing record
                currentTaskHistoryId = task.id
                taskStartTime = System.currentTimeMillis()

                // 7. Restore tracking lists
                taskMessages.clear()
                taskMessages.addAll(allMessages)
                taskActions.clear()
                taskErrors.clear()

                // 8. Restore partial API context
                val apiMessages = try {
                    val type = object : TypeToken<List<Message>>() {}.type
                    gson.fromJson<List<Message>>(task.apiContextMessagesJson, type) ?: emptyList()
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to deserialize API context messages", e)
                    emptyList<Message>()
                }
                apiContextMessages.clear()
                apiContextMessages.addAll(apiMessages)

                // 9. Restore AgentCore context with partial history
                agentCore?.restoreSession(task.taskDescription, apiMessages)

                // 10. Show continuation message
                withContext(Dispatchers.Main) {
                    addSystemMessage(
                        "继续历史任务: ${task.taskDescription}",
                        SystemMessageType.INFO
                    )
                }

                Log.d(TAG, "Loaded historical conversation: ${task.id}, ${allMessages.size} messages (${thinkMessages.size} think + ${actionMessages.size} action)")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to load historical conversation", e)
                withContext(Dispatchers.Main) {
                    addSystemMessage("加载历史对话失败: ${e.message}", SystemMessageType.ERROR)
                }
            }
        }
    }

    /**
     * Start a new conversation - clears all state
     */
    fun newConversation() {
        // Clear all messages and state
        _state.update {
            it.copy(
                messages = persistentListOf(),
                currentTask = "",
                isContinuedConversation = false,
                originalTaskId = null,
                isRunning = false
            )
        }

        // Clear tracking variables
        taskMessages.clear()
        taskActions.clear()
        taskErrors.clear()
        apiContextMessages.clear()
        currentTaskHistoryId = null

        // Clear AgentCore session
        agentCore?.stop()

        Log.d(TAG, "New conversation started")
    }
}

/**
 * 预检查结果数据类
 * 用于表示网络预检查的结果
 */
data class PreCheckResult(
    val success: Boolean,
    val errorMessage: String? = null
)
