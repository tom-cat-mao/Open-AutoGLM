package com.taskwizard.android

import android.app.Application
import com.taskwizard.android.data.SystemMessageType
import com.taskwizard.android.ui.theme.ThemeMode
import com.taskwizard.android.ui.viewmodel.MainViewModel
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.Mock
import org.mockito.MockitoAnnotations
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * MainViewModel单元测试
 *
 * 测试范围：
 * 1. 主题管理（亮色/暗色/Pure Black切换）
 * 2. API配置管理（保存和加载）
 * 3. 任务状态管理（启动/停止）
 * 4. 消息管理（添加和清空）
 */
@ExperimentalCoroutinesApi
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class MainViewModelTest {

    private lateinit var viewModel: MainViewModel
    private lateinit var application: Application
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setup() {
        MockitoAnnotations.openMocks(this)
        Dispatchers.setMain(testDispatcher)

        // 使用Robolectric的Application
        application = RuntimeEnvironment.getApplication()
        viewModel = MainViewModel(application)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    // ==================== 主题管理测试 ====================

    @Test
    fun `初始主题应该是亮色模式`() {
        val state = viewModel.state.value
        assertEquals(ThemeMode.LIGHT, state.themeMode)
        assertFalse(state.pureBlackEnabled)
    }

    @Test
    fun `切换到暗色模式应该更新状态`() = runTest {
        // When
        viewModel.updateThemeMode(ThemeMode.DARK)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(ThemeMode.DARK, state.themeMode)
    }

    @Test
    fun `切换到亮色模式应该更新状态`() = runTest {
        // Given
        viewModel.updateThemeMode(ThemeMode.DARK)
        advanceUntilIdle()

        // When
        viewModel.updateThemeMode(ThemeMode.LIGHT)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(ThemeMode.LIGHT, state.themeMode)
    }

    @Test
    fun `启用Pure Black应该更新状态`() = runTest {
        // When
        viewModel.togglePureBlack(true)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertTrue(state.pureBlackEnabled)
    }

    @Test
    fun `禁用Pure Black应该更新状态`() = runTest {
        // Given
        viewModel.togglePureBlack(true)
        advanceUntilIdle()

        // When
        viewModel.togglePureBlack(false)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.pureBlackEnabled)
    }

    // ==================== API配置管理测试 ====================

    @Test
    fun `更新API Key应该更新状态`() = runTest {
        // When
        val testApiKey = "test-api-key-12345"
        viewModel.updateApiKey(testApiKey)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(testApiKey, state.apiKey)
    }

    @Test
    fun `更新Base URL应该更新状态`() = runTest {
        // When
        val testUrl = "https://test.example.com"
        viewModel.updateBaseUrl(testUrl)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(testUrl, state.baseUrl)
    }

    @Test
    fun `更新Model名称应该更新状态`() = runTest {
        // When
        val testModel = "test-model"
        viewModel.updateModel(testModel)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(testModel, state.model)
    }

    @Test
    fun `保存配置应该添加成功消息`() = runTest {
        // Given
        viewModel.updateApiKey("test-key")
        viewModel.updateBaseUrl("https://test.com")
        viewModel.updateModel("test-model")
        advanceUntilIdle()

        // When
        viewModel.saveSettings()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertTrue(state.messages.isNotEmpty())
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.SUCCESS
        })
    }

    // ==================== 任务状态管理测试 ====================

    @Test
    fun `初始任务状态应该是未运行`() {
        val state = viewModel.state.value
        assertFalse(state.isRunning)
        assertEquals("", state.currentTask)
    }

    @Test
    fun `更新任务描述应该更新状态`() = runTest {
        // When
        val testTask = "测试任务"
        viewModel.updateTask(testTask)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(testTask, state.currentTask)
    }

    @Test
    fun `清空任务描述应该清空状态`() = runTest {
        // Given
        viewModel.updateTask("测试任务")
        advanceUntilIdle()

        // When
        viewModel.clearTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals("", state.currentTask)
    }

    @Test
    fun `启动任务时没有API Key应该显示错误`() = runTest {
        // Given
        viewModel.updateTask("测试任务")
        viewModel.updateApiKey("") // 清空API Key
        advanceUntilIdle()

        // When
        viewModel.startTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.isRunning)
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.ERROR
        })
    }

    @Test
    fun `启动任务时没有任务描述应该显示警告`() = runTest {
        // Given
        viewModel.updateTask("") // 清空任务
        advanceUntilIdle()

        // When
        viewModel.startTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.isRunning)
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.WARNING
        })
    }

    @Test
    fun `停止任务应该更新运行状态`() = runTest {
        // Given
        viewModel.updateTask("测试任务")
        viewModel.updateApiKey("test-key")
        viewModel.startTask()
        advanceUntilIdle()

        // When
        viewModel.stopTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.isRunning)
    }

    // ==================== 消息管理测试 ====================

    @Test
    fun `添加思考消息应该更新消息列表`() = runTest {
        // When
        viewModel.addThinkMessage("AI正在思考...")
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        assertTrue(state.messages[0] is com.taskwizard.android.data.MessageItem.ThinkMessage)
    }

    @Test
    fun `添加系统消息应该更新消息列表`() = runTest {
        // When
        viewModel.addSystemMessage("系统提示", SystemMessageType.INFO)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        assertTrue(state.messages[0] is com.taskwizard.android.data.MessageItem.SystemMessage)
    }

    @Test
    fun `清空消息应该清空消息列表`() = runTest {
        // Given
        viewModel.addThinkMessage("消息1")
        viewModel.addSystemMessage("消息2", SystemMessageType.INFO)
        advanceUntilIdle()

        // When
        viewModel.clearMessages()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertTrue(state.messages.isEmpty())
    }

    @Test
    fun `多条消息应该按顺序添加`() = runTest {
        // When
        viewModel.addThinkMessage("消息1")
        viewModel.addThinkMessage("消息2")
        viewModel.addThinkMessage("消息3")
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(3, state.messages.size)
    }

    // ==================== 步骤前缀和消息格式测试 ====================

    @Test
    fun `思考消息应该包含正确的内容`() = runTest {
        // When
        val messageContent = "[1] 正在分析屏幕内容"
        viewModel.addThinkMessage(messageContent)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        val message = state.messages[0] as com.taskwizard.android.data.MessageItem.ThinkMessage
        assertEquals(messageContent, message.content)
    }

    @Test
    fun `带步骤前缀的思考消息应该正确显示`() = runTest {
        // When
        viewModel.addThinkMessage("[1] 第一步思考")
        viewModel.addThinkMessage("[2] 第二步思考")
        viewModel.addThinkMessage("[3] 第三步思考")
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(3, state.messages.size)

        val msg1 = state.messages[0] as com.taskwizard.android.data.MessageItem.ThinkMessage
        assertTrue(msg1.content.startsWith("[1]"))

        val msg2 = state.messages[1] as com.taskwizard.android.data.MessageItem.ThinkMessage
        assertTrue(msg2.content.startsWith("[2]"))

        val msg3 = state.messages[2] as com.taskwizard.android.data.MessageItem.ThinkMessage
        assertTrue(msg3.content.startsWith("[3]"))
    }

    @Test
    fun `操作消息应该包含正确的Action对象`() = runTest {
        // Given
        val action = com.taskwizard.android.data.Action(
            action = "tap",
            location = listOf(100, 200).toImmutableList(),
            content = null,
            message = "点击按钮"
        )

        // When
        viewModel.addActionMessage(action)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        val message = state.messages[0] as com.taskwizard.android.data.MessageItem.ActionMessage
        assertEquals(action, message.action)
    }

    @Test
    fun `混合消息类型应该按顺序添加`() = runTest {
        // Given
        val action = com.taskwizard.android.data.Action(
            action = "tap",
            location = listOf(100, 200).toImmutableList()
        )

        // When
        viewModel.addThinkMessage("[1] 思考中")
        viewModel.addActionMessage(action)
        viewModel.addSystemMessage("操作成功", SystemMessageType.SUCCESS)
        viewModel.addThinkMessage("[2] 继续思考")
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(4, state.messages.size)
        assertTrue(state.messages[0] is com.taskwizard.android.data.MessageItem.ThinkMessage)
        assertTrue(state.messages[1] is com.taskwizard.android.data.MessageItem.ActionMessage)
        assertTrue(state.messages[2] is com.taskwizard.android.data.MessageItem.SystemMessage)
        assertTrue(state.messages[3] is com.taskwizard.android.data.MessageItem.ThinkMessage)
    }

    @Test
    fun `消息应该包含时间戳`() = runTest {
        // Given
        val beforeTime = System.currentTimeMillis()

        // When
        viewModel.addThinkMessage("测试消息")
        advanceUntilIdle()

        // Then
        val afterTime = System.currentTimeMillis()
        val state = viewModel.state.value
        val message = state.messages[0] as com.taskwizard.android.data.MessageItem.ThinkMessage

        assertTrue(
            message.timestamp >= beforeTime && message.timestamp <= afterTime,
            "消息时间戳应该在合理范围内"
        )
    }

    @Test
    fun `每条消息应该有唯一的ID`() = runTest {
        // When
        viewModel.addThinkMessage("消息1")
        viewModel.addThinkMessage("消息2")
        viewModel.addThinkMessage("消息3")
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        val ids = state.messages.map { it.id }

        assertEquals(
            ids.toSet().size,
            ids.size,
            "所有消息ID应该唯一"
        )
    }

    // ==================== moveToBackground状态测试 ====================

    @Test
    fun `初始shouldMoveToBackground应该为false`() {
        val shouldMoveToBackground = viewModel.shouldMoveToBackground.value
        assertFalse(shouldMoveToBackground, "初始shouldMoveToBackground应该为false")
    }

    @Test
    fun `resetMoveToBackgroundFlag应该重置标志`() = runTest {
        // 模拟设置标志（通过反射或其他方式，这里简化测试）
        // 实际场景中，startTask会设置这个标志
        viewModel.resetMoveToBackgroundFlag()
        advanceUntilIdle()

        // Then
        val shouldMoveToBackground = viewModel.shouldMoveToBackground.value
        assertFalse(shouldMoveToBackground, "resetMoveToBackgroundFlag后应该为false")
    }

    // ==================== 消息记录持久化测试 ====================

    @Test
    fun `任务执行期间添加的消息应该保留在ViewModel中`() = runTest {
        // Given - 模拟任务执行期间添加消息
        viewModel.addThinkMessage("[1] 正在分析屏幕")
        viewModel.addActionMessage(
            com.taskwizard.android.data.Action(
                action = "tap",
                location = listOf(100, 200).toImmutableList()
            )
        )
        viewModel.addSystemMessage("操作完成", SystemMessageType.SUCCESS)
        advanceUntilIdle()

        // Then - 验证消息被保留
        val state = viewModel.state.value
        assertEquals(3, state.messages.size, "应该有3条消息")
        assertTrue(state.messages[0] is com.taskwizard.android.data.MessageItem.ThinkMessage)
        assertTrue(state.messages[1] is com.taskwizard.android.data.MessageItem.ActionMessage)
        assertTrue(state.messages[2] is com.taskwizard.android.data.MessageItem.SystemMessage)
    }

    @Test
    fun `多次任务执行的消息应该累积`() = runTest {
        // Given - 第一次任务
        viewModel.addThinkMessage("[1] 第一次任务思考")
        viewModel.addSystemMessage("第一次任务完成", SystemMessageType.SUCCESS)
        advanceUntilIdle()

        // When - 第二次任务
        viewModel.addThinkMessage("[1] 第二次任务思考")
        viewModel.addSystemMessage("第二次任务完成", SystemMessageType.SUCCESS)
        advanceUntilIdle()

        // Then - 消息应该累积
        val state = viewModel.state.value
        assertEquals(4, state.messages.size, "应该有4条消息（2次任务各2条）")
    }

    @Test
    fun `任务失败时的错误消息应该被记录`() = runTest {
        // When - 模拟任务失败
        viewModel.addSystemMessage("连续失败3次，任务已停止", SystemMessageType.ERROR)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        val message = state.messages[0] as com.taskwizard.android.data.MessageItem.SystemMessage
        assertEquals(SystemMessageType.ERROR, message.type)
        assertTrue(message.content.contains("失败"))
    }

    @Test
    fun `达到最大步骤数的警告消息应该被记录`() = runTest {
        // When - 模拟达到最大步骤数
        viewModel.addSystemMessage("已达到最大步骤数(50)，任务停止", SystemMessageType.WARNING)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        val message = state.messages[0] as com.taskwizard.android.data.MessageItem.SystemMessage
        assertEquals(SystemMessageType.WARNING, message.type)
        assertTrue(message.content.contains("最大步骤数"))
    }

    // ==================== 错误处理测试 ====================

    @Test
    fun `启动任务时API Key为空应该显示错误消息`() = runTest {
        // Given
        viewModel.updateTask("测试任务")
        viewModel.updateApiKey("") // 清空API Key
        advanceUntilIdle()

        // When
        viewModel.startTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.isRunning, "任务不应该启动")
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.ERROR &&
            it.content.contains("API Key")
        }, "应该有错误消息")
    }

    @Test
    fun `启动任务时Base URL为空应该显示错误消息`() = runTest {
        // Given
        viewModel.updateTask("测试任务")
        viewModel.updateApiKey("test-key")
        viewModel.updateBaseUrl("") // 清空Base URL
        advanceUntilIdle()

        // When
        viewModel.startTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.isRunning, "任务不应该启动")
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.ERROR &&
            it.content.contains("Base URL")
        }, "应该有错误消息")
    }

    @Test
    fun `启动任务时Base URL格式错误应该显示错误消息`() = runTest {
        // Given
        viewModel.updateTask("测试任务")
        viewModel.updateApiKey("test-key")
        viewModel.updateBaseUrl("invalid-url") // 无效的URL格式
        advanceUntilIdle()

        // When
        viewModel.startTask()
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertFalse(state.isRunning, "任务不应该启动")
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.ERROR &&
            it.content.contains("格式错误")
        }, "应该有Base URL格式错误消息")
    }

    // ==================== 网络预检查测试 ====================
    // 注意：网络预检查需要真实的网络请求，完整功能在功能测试中验证
    // 这里只测试 PreCheckResult 数据类

    @Test
    fun `PreCheckResult成功时success应该为true`() {
        // When
        val result = com.taskwizard.android.ui.viewmodel.PreCheckResult(success = true)

        // Then
        assertTrue(result.success, "success应该为true")
        assertTrue(result.errorMessage == null, "errorMessage应该为null")
    }

    @Test
    fun `PreCheckResult失败时应该包含错误消息`() {
        // When
        val errorMsg = "网络连接失败"
        val result = com.taskwizard.android.ui.viewmodel.PreCheckResult(success = false, errorMessage = errorMsg)

        // Then
        assertFalse(result.success, "success应该为false")
        assertEquals(result.errorMessage, errorMsg, "errorMessage应该被设置")
    }

    // ==================== 历史对话继续功能测试 ====================

    @Test
    fun `加载历史对话应该设置isContinuedConversation标志`() = runTest {
        // Note: This test requires a working database with history data
        // For now, we test the state changes when calling loadHistoricalConversation
        // The actual data loading is tested in HistoryRepositoryTest

        // Given
        val historyId = 123L

        // When - Call loadHistoricalConversation
        // Note: This will fail to find the history, but we can test the error handling
        viewModel.loadHistoricalConversation(historyId)
        advanceUntilIdle()

        // Then - Should show error message (since history doesn't exist)
        val state = viewModel.state.value
        assertTrue(state.messages.any {
            it is com.taskwizard.android.data.MessageItem.SystemMessage &&
            it.type == SystemMessageType.ERROR &&
            it.content.contains("无法找到历史记录")
        }, "应该显示历史记录未找到的错误")
    }

    // TODO: Re-enable when clearContinuationState is implemented
    // @Test
    // fun `清除继续对话状态应该重置标志和任务ID`() = runTest {
    //     // Given - Set continuation state (manually for testing)
    //     viewModel.loadHistoricalConversation(123L) // Will fail, but initializes the coroutine
    //     advanceUntilIdle()
    //
    //     // When
    //     viewModel.clearContinuationState()
    //     advanceUntilIdle()
    //
    //     // Then
    //     val state = viewModel.state.value
    //     assertFalse(state.isContinuedConversation, "isContinuedConversation应该为false")
    //     assertEquals(null, state.originalTaskId, "originalTaskId应该为null")
    // }

    @Test
    fun `初始状态isContinuedConversation应该为false`() {
        // Then
        val state = viewModel.state.value
        assertFalse(state.isContinuedConversation, "初始状态下isContinuedConversation应该为false")
        assertEquals(null, state.originalTaskId, "初始状态下originalTaskId应该为null")
    }
}
