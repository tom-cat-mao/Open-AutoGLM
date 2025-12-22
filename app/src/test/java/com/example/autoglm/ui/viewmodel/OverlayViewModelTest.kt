package com.example.autoglm.ui.viewmodel

import com.example.autoglm.data.OverlayDisplayState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * OverlayViewModel单元测试
 *
 * 测试悬浮窗状态管理的所有功能
 * 使用Kotlin Coroutines Test进行异步测试
 */
@OptIn(ExperimentalCoroutinesApi::class)
class OverlayViewModelTest {

    private lateinit var viewModel: OverlayViewModel
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        viewModel = OverlayViewModel()
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    /**
     * 测试：初始状态应该是TRANSPARENT
     */
    @Test
    fun `initial state is TRANSPARENT`() {
        assertEquals(
            "初始状态应该是TRANSPARENT",
            OverlayDisplayState.TRANSPARENT,
            viewModel.state.value.displayState
        )
    }

    /**
     * 测试：点击循环 - TRANSPARENT -> NORMAL
     */
    @Test
    fun `handleClick cycles from TRANSPARENT to NORMAL`() {
        // 初始状态：TRANSPARENT
        assertEquals(OverlayDisplayState.TRANSPARENT, viewModel.getCurrentDisplayState())

        // 点击一次
        viewModel.handleClick()

        // 应该变为NORMAL
        assertEquals(
            "点击后应该变为NORMAL",
            OverlayDisplayState.NORMAL,
            viewModel.getCurrentDisplayState()
        )
    }

    /**
     * 测试：点击循环 - NORMAL -> CONFIRM_EXIT
     */
    @Test
    fun `handleClick cycles from NORMAL to CONFIRM_EXIT`() {
        // 设置为NORMAL状态
        viewModel.handleClick() // TRANSPARENT -> NORMAL

        // 再次点击
        viewModel.handleClick()

        // 应该变为CONFIRM_EXIT
        assertEquals(
            "点击后应该变为CONFIRM_EXIT",
            OverlayDisplayState.CONFIRM_EXIT,
            viewModel.getCurrentDisplayState()
        )
    }

    /**
     * 测试：自动恢复 - CONFIRM_EXIT 3秒后恢复到TRANSPARENT
     */
    @Test
    fun `autoRestore after timeout`() = runTest {
        // 进入CONFIRM_EXIT状态
        viewModel.handleClick() // TRANSPARENT -> NORMAL
        viewModel.handleClick() // NORMAL -> CONFIRM_EXIT

        assertEquals(OverlayDisplayState.CONFIRM_EXIT, viewModel.getCurrentDisplayState())

        // 等待3秒并推进协程调度器
        advanceTimeBy(3000)
        testScheduler.advanceUntilIdle()

        // 应该自动恢复到TRANSPARENT
        assertEquals(
            "3秒后应该自动恢复到TRANSPARENT",
            OverlayDisplayState.TRANSPARENT,
            viewModel.getCurrentDisplayState()
        )
    }

    /**
     * 测试：updateThinkingState更新正确
     */
    @Test
    fun `updateThinkingState updates correctly`() {
        assertFalse("初始isThinking应该为false", viewModel.isThinking())

        viewModel.updateThinkingState(true)

        assertTrue("updateThinkingState(true)后应该为true", viewModel.isThinking())
        assertTrue("state.isThinking应该为true", viewModel.state.value.isThinking)

        viewModel.updateThinkingState(false)

        assertFalse("updateThinkingState(false)后应该为false", viewModel.isThinking())
    }

    /**
     * 测试：updateAction更新状态文本
     */
    @Test
    fun `updateAction updates statusText`() {
        val action = "点击 [100,200]"

        viewModel.updateAction(action)

        assertEquals(
            "currentAction应该被更新",
            action,
            viewModel.state.value.currentAction
        )
        assertFalse(
            "updateAction应该将isThinking设为false",
            viewModel.state.value.isThinking
        )
    }

    /**
     * 测试：updateStatus更新状态文本
     */
    @Test
    fun `updateStatus updates statusText`() {
        val status = "正在执行任务"

        viewModel.updateStatus(status)

        assertEquals(
            "statusText应该被更新",
            status,
            viewModel.state.value.statusText
        )
    }

    /**
     * 测试：markTaskCompleted设置完成状态
     */
    @Test
    fun `markTaskCompleted sets completed state`() {
        viewModel.markTaskCompleted()

        assertTrue("isTaskCompleted应该为true", viewModel.isCompleted())
        assertFalse("isTaskRunning应该为false", viewModel.isRunning())
        assertEquals(
            "displayState应该为COMPLETED",
            OverlayDisplayState.COMPLETED,
            viewModel.getCurrentDisplayState()
        )
        assertFalse("isThinking应该为false", viewModel.isThinking())
        assertNull("currentAction应该为null", viewModel.state.value.currentAction)
    }

    /**
     * 测试：markTaskStarted设置运行状态
     */
    @Test
    fun `markTaskStarted sets running state`() {
        viewModel.markTaskStarted()

        assertTrue("isTaskRunning应该为true", viewModel.isRunning())
        assertFalse("isTaskCompleted应该为false", viewModel.isCompleted())
        assertEquals(
            "displayState应该为TRANSPARENT",
            OverlayDisplayState.TRANSPARENT,
            viewModel.getCurrentDisplayState()
        )
    }

    /**
     * 测试：reset重置所有状态
     */
    @Test
    fun `reset resets all state`() {
        // 修改一些状态
        viewModel.markTaskStarted()
        viewModel.updateThinkingState(true)
        viewModel.updateAction("测试动作")
        viewModel.handleClick()

        // 重置
        viewModel.reset()

        // 验证所有状态都被重置
        assertEquals(
            "displayState应该被重置为TRANSPARENT",
            OverlayDisplayState.TRANSPARENT,
            viewModel.getCurrentDisplayState()
        )
        assertFalse("isThinking应该为false", viewModel.isThinking())
        assertFalse("isTaskRunning应该为false", viewModel.isRunning())
        assertFalse("isTaskCompleted应该为false", viewModel.isCompleted())
        assertEquals("statusText应该为空", "", viewModel.state.value.statusText)
        assertNull("currentAction应该为null", viewModel.state.value.currentAction)
    }

    /**
     * 测试：点击更新lastClickTimestamp
     */
    @Test
    fun `handleClick updates lastClickTimestamp`() {
        val beforeTimestamp = viewModel.state.value.lastClickTimestamp

        viewModel.handleClick()

        val afterTimestamp = viewModel.state.value.lastClickTimestamp

        assertTrue(
            "点击后lastClickTimestamp应该被更新",
            afterTimestamp > beforeTimestamp
        )
    }

    /**
     * 测试：markTaskCompleted取消自动恢复
     */
    @Test
    fun `markTaskCompleted cancels autoRestore`() = runTest {
        // 进入CONFIRM_EXIT状态
        viewModel.handleClick() // TRANSPARENT -> NORMAL
        viewModel.handleClick() // NORMAL -> CONFIRM_EXIT

        assertEquals(OverlayDisplayState.CONFIRM_EXIT, viewModel.getCurrentDisplayState())

        // 标记完成
        viewModel.markTaskCompleted()

        // 等待超过3秒
        advanceTimeBy(4000)

        // 应该保持COMPLETED状态，不会恢复到TRANSPARENT
        assertEquals(
            "markTaskCompleted后不应该自动恢复",
            OverlayDisplayState.COMPLETED,
            viewModel.getCurrentDisplayState()
        )
    }

    /**
     * 测试：getDisplayText返回正确的文本
     */
    @Test
    fun `getDisplayText returns correct text`() {
        // 默认状态
        assertEquals("就绪", viewModel.state.value.getDisplayText())

        // Thinking状态
        viewModel.updateThinkingState(true)
        assertEquals("Thinking...", viewModel.state.value.getDisplayText())

        // Action状态
        viewModel.updateAction("点击 [100,200]")
        assertEquals("点击 [100,200]", viewModel.state.value.getDisplayText())

        // CONFIRM_EXIT状态
        viewModel.handleClick() // TRANSPARENT -> NORMAL
        viewModel.handleClick() // NORMAL -> CONFIRM_EXIT
        assertEquals("确认退出?", viewModel.state.value.getDisplayText())

        // 完成状态
        viewModel.markTaskCompleted()
        assertEquals("已完成", viewModel.state.value.getDisplayText())
    }

    /**
     * 测试：getAlpha返回正确的透明度
     */
    @Test
    fun `getAlpha returns correct alpha value`() {
        // TRANSPARENT状态
        assertEquals(0.5f, viewModel.state.value.getAlpha(), 0.01f)

        // NORMAL状态
        viewModel.handleClick()
        assertEquals(1.0f, viewModel.state.value.getAlpha(), 0.01f)

        // CONFIRM_EXIT状态
        viewModel.handleClick()
        assertEquals(1.0f, viewModel.state.value.getAlpha(), 0.01f)

        // COMPLETED状态
        viewModel.markTaskCompleted()
        assertEquals(1.0f, viewModel.state.value.getAlpha(), 0.01f)
    }

    /**
     * 测试：shouldShowExitIndicator在CONFIRM_EXIT时返回true
     */
    @Test
    fun `shouldShowExitIndicator returns true in CONFIRM_EXIT state`() {
        assertFalse(
            "初始状态不应该显示退出指示器",
            viewModel.state.value.shouldShowExitIndicator()
        )

        viewModel.handleClick() // TRANSPARENT -> NORMAL
        viewModel.handleClick() // NORMAL -> CONFIRM_EXIT

        assertTrue(
            "CONFIRM_EXIT状态应该显示退出指示器",
            viewModel.state.value.shouldShowExitIndicator()
        )
    }

    /**
     * 测试：shouldShowCompletedIndicator在完成时返回true
     */
    @Test
    fun `shouldShowCompletedIndicator returns true when completed`() {
        assertFalse(
            "初始状态不应该显示完成指示器",
            viewModel.state.value.shouldShowCompletedIndicator()
        )

        viewModel.markTaskCompleted()

        assertTrue(
            "完成状态应该显示完成指示器",
            viewModel.state.value.shouldShowCompletedIndicator()
        )
    }

    /**
     * 测试：shouldShowThinkingIndicator在思考时返回true
     */
    @Test
    fun `shouldShowThinkingIndicator returns true when thinking`() {
        assertFalse(
            "初始状态不应该显示思考指示器",
            viewModel.state.value.shouldShowThinkingIndicator()
        )

        viewModel.updateThinkingState(true)

        assertTrue(
            "思考状态应该显示思考指示器",
            viewModel.state.value.shouldShowThinkingIndicator()
        )

        // 完成后不应该显示思考指示器
        viewModel.markTaskCompleted()

        assertFalse(
            "完成状态不应该显示思考指示器",
            viewModel.state.value.shouldShowThinkingIndicator()
        )
    }

    /**
     * 测试：shouldShowActionIndicator在有action时返回true
     */
    @Test
    fun `shouldShowActionIndicator returns true when action exists`() {
        assertFalse(
            "初始状态不应该显示动作指示器",
            viewModel.state.value.shouldShowActionIndicator()
        )

        viewModel.updateAction("点击 [100,200]")

        assertTrue(
            "有动作时应该显示动作指示器",
            viewModel.state.value.shouldShowActionIndicator()
        )

        // 思考时不应该显示动作指示器
        viewModel.updateThinkingState(true)

        assertFalse(
            "思考时不应该显示动作指示器",
            viewModel.state.value.shouldShowActionIndicator()
        )
    }

    /**
     * 测试：handleAutoRestore只在CONFIRM_EXIT状态生效
     */
    @Test
    fun `handleAutoRestore only works in CONFIRM_EXIT state`() {
        // TRANSPARENT状态
        viewModel.handleAutoRestore()
        assertEquals(
            "TRANSPARENT状态调用handleAutoRestore不应改变状态",
            OverlayDisplayState.TRANSPARENT,
            viewModel.getCurrentDisplayState()
        )

        // NORMAL状态
        viewModel.handleClick()
        viewModel.handleAutoRestore()
        assertEquals(
            "NORMAL状态调用handleAutoRestore不应改变状态",
            OverlayDisplayState.NORMAL,
            viewModel.getCurrentDisplayState()
        )

        // CONFIRM_EXIT状态
        viewModel.handleClick()
        assertEquals(OverlayDisplayState.CONFIRM_EXIT, viewModel.getCurrentDisplayState())
        viewModel.handleAutoRestore()
        assertEquals(
            "CONFIRM_EXIT状态调用handleAutoRestore应该恢复到TRANSPARENT",
            OverlayDisplayState.TRANSPARENT,
            viewModel.getCurrentDisplayState()
        )
    }

    // ==================== 错误处理测试 ====================

    /**
     * 测试：updateError设置错误消息
     */
    @Test
    fun `updateError sets error message`() {
        val errorMsg = "网络连接失败"

        viewModel.updateError(errorMsg)

        assertEquals(
            "errorMessage应该被设置",
            errorMsg,
            viewModel.state.value.errorMessage
        )
        assertEquals(
            "retryCount应该为0",
            0,
            viewModel.state.value.retryCount
        )
    }

    /**
     * 测试：updateError设置错误消息和重试次数
     */
    @Test
    fun `updateError sets error message with retry count`() {
        val errorMsg = "API调用失败"
        val retryCount = 2

        viewModel.updateError(errorMsg, retryCount)

        assertEquals(
            "errorMessage应该被设置",
            errorMsg,
            viewModel.state.value.errorMessage
        )
        assertEquals(
            "retryCount应该被设置",
            retryCount,
            viewModel.state.value.retryCount
        )
    }

    /**
     * 测试：clearError清除错误状态
     */
    @Test
    fun `clearError clears error state`() {
        // 先设置错误
        viewModel.updateError("测试错误", 1)
        assertEquals("测试错误", viewModel.state.value.errorMessage)

        // 清除错误
        viewModel.clearError()

        assertNull(
            "errorMessage应该为null",
            viewModel.state.value.errorMessage
        )
        assertEquals(
            "retryCount应该被重置为0",
            0,
            viewModel.state.value.retryCount
        )
    }

    /**
     * 测试：getDisplayText在有错误时优先显示错误
     */
    @Test
    fun `getDisplayText prioritizes error message`() {
        // 设置多个状态
        viewModel.updateThinkingState(true)
        viewModel.updateAction("点击按钮")
        viewModel.updateError("网络错误")

        val displayText = viewModel.state.value.getDisplayText()

        assertTrue(
            "显示文本应该包含错误信息",
            displayText.contains("错误: 网络错误")
        )
    }

    /**
     * 测试：getDisplayText显示错误和重试次数
     */
    @Test
    fun `getDisplayText shows error with retry count`() {
        viewModel.updateError("API超时", 2)

        val displayText = viewModel.state.value.getDisplayText()

        assertTrue(
            "显示文本应该包含错误信息",
            displayText.contains("错误: API超时")
        )
        assertTrue(
            "显示文本应该包含重试次数",
            displayText.contains("重试: 2/3")
        )
    }

    /**
     * 测试：getDisplayText在重试次数为0时不显示重试信息
     */
    @Test
    fun `getDisplayText shows error without retry count when retry is zero`() {
        viewModel.updateError("连接失败", 0)

        val displayText = viewModel.state.value.getDisplayText()

        assertTrue(
            "显示文本应该包含错误信息",
            displayText.contains("错误: 连接失败")
        )
        assertFalse(
            "显示文本不应该包含重试信息",
            displayText.contains("重试:")
        )
    }

    // ==================== 步骤管理测试 ====================

    /**
     * 测试：updateStep设置步骤数
     */
    @Test
    fun `updateStep sets step count`() {
        viewModel.updateStep(5)

        assertEquals(
            "currentStep应该被设置为5",
            5,
            viewModel.state.value.currentStep
        )
    }

    /**
     * 测试：incrementStep增加步骤数
     */
    @Test
    fun `incrementStep increments step count`() {
        assertEquals(0, viewModel.state.value.currentStep)

        viewModel.incrementStep()
        assertEquals(1, viewModel.state.value.currentStep)

        viewModel.incrementStep()
        assertEquals(2, viewModel.state.value.currentStep)

        viewModel.incrementStep()
        assertEquals(3, viewModel.state.value.currentStep)
    }

    /**
     * 测试：getDisplayText显示步骤数
     */
    @Test
    fun `getDisplayText shows step count`() {
        viewModel.updateStep(10)

        val displayText = viewModel.state.value.getDisplayText()

        assertEquals(
            "显示文本应该显示步骤数",
            "Step 10",
            displayText
        )
    }

    /**
     * 测试：getDisplayText步骤数优先级低于thinking和action
     */
    @Test
    fun `getDisplayText step count has lower priority than thinking and action`() {
        viewModel.updateStep(5)

        // 步骤数应该显示
        assertEquals("Step 5", viewModel.state.value.getDisplayText())

        // thinking优先级更高
        viewModel.updateThinkingState(true)
        assertEquals("Thinking...", viewModel.state.value.getDisplayText())

        // action优先级更高
        viewModel.updateThinkingState(false)
        viewModel.updateAction("点击按钮")
        assertEquals("点击按钮", viewModel.state.value.getDisplayText())
    }

    /**
     * 测试：reset重置步骤数和错误状态
     */
    @Test
    fun `reset clears step count and error state`() {
        // 设置步骤和错误
        viewModel.updateStep(10)
        viewModel.updateError("测试错误", 2)

        assertEquals(10, viewModel.state.value.currentStep)
        assertEquals("测试错误", viewModel.state.value.errorMessage)
        assertEquals(2, viewModel.state.value.retryCount)

        // 重置
        viewModel.reset()

        assertEquals(
            "currentStep应该被重置为0",
            0,
            viewModel.state.value.currentStep
        )
        assertNull(
            "errorMessage应该为null",
            viewModel.state.value.errorMessage
        )
        assertEquals(
            "retryCount应该被重置为0",
            0,
            viewModel.state.value.retryCount
        )
    }

    /**
     * 测试：错误状态不影响其他状态
     */
    @Test
    fun `error state does not affect other states`() {
        viewModel.markTaskStarted()
        viewModel.updateStep(3)
        viewModel.updateError("测试错误")

        assertTrue("isTaskRunning应该保持true", viewModel.isRunning())
        assertEquals("currentStep应该保持为3", 3, viewModel.state.value.currentStep)
        assertEquals("错误应该被设置", "测试错误", viewModel.state.value.errorMessage)
    }

    /**
     * 测试：步骤数在任务完成后保持
     */
    @Test
    fun `step count persists after task completion`() {
        viewModel.updateStep(15)
        viewModel.markTaskCompleted()

        assertEquals(
            "步骤数应该保持",
            15,
            viewModel.state.value.currentStep
        )
        assertEquals(
            "显示文本应该显示已完成",
            "已完成",
            viewModel.state.value.getDisplayText()
        )
    }
}
