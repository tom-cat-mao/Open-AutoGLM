package com.taskwizard.android

import com.taskwizard.android.core.TemplateExecutor
import com.taskwizard.android.data.template.TaskTemplateEntity
import io.mockk.mockk
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * TemplateExecutor 单元测试
 */
class TemplateExecutorTest {

    private lateinit var mockService: IAutoGLMService
    private lateinit var tapHistory: MutableList<Pair<Int, Int>>
    private lateinit var swipeHistory: MutableList<MockAutoGLMServiceFactory.SwipeRecord>
    private lateinit var globalActionHistory: MutableList<Int>
    private lateinit var executor: TemplateExecutor
    private val mockContext = mockk<android.content.Context>(relaxed = true)

    @Before
    fun setup() {
        tapHistory = mutableListOf()
        swipeHistory = mutableListOf()
        globalActionHistory = mutableListOf()
        mockService = MockAutoGLMServiceFactory.create(
            tapHistory = tapHistory,
            swipeHistory = swipeHistory,
            globalActionHistory = globalActionHistory
        )
    }

    @Test
    fun `test execute empty template`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val template = createTemplate(actionsJson = "[]")
        val result = executor.execute(template) { _, _, _ -> }

        assertTrue(result is TemplateExecutor.ExecutionResult.Failure)
    }

    @Test
    fun `test execute tap action`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val actionsJson = """[{"action":"tap","location":[500,500]}]"""
        val template = createTemplate(actionsJson = actionsJson)

        val result = executor.execute(template) { _, _, _ -> }

        assertTrue(result is TemplateExecutor.ExecutionResult.Success)
        assertEquals(1, tapHistory.size)
    }

    @Test
    fun `test coordinate scaling`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 2160,  // 2x original
            screenHeight = 4800
        )

        // 使用归一化坐标 (0-1000)
        val actionsJson = """[{"action":"tap","location":[500,500]}]"""
        val template = createTemplate(
            actionsJson = actionsJson,
            screenWidth = 1080,
            screenHeight = 2400
        )

        executor.execute(template) { _, _, _ -> }

        assertEquals(1, tapHistory.size)
        val (x, y) = tapHistory[0]
        // 归一化坐标转换: 500/1000 * screenWidth
        assertEquals(1080, x)  // 500/1000 * 2160
        assertEquals(2400, y)  // 500/1000 * 4800
    }

    // ==================== UiAutomation 路径测试 ====================

    @Test
    fun `test execute swipe action`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        // TemplateExecutor expects location with 4 values: [x1, y1, x2, y2]
        val actionsJson = """[{"action":"swipe","location":[100,200,300,400]}]"""
        val template = createTemplate(actionsJson = actionsJson)

        val result = executor.execute(template) { _, _, _ -> }

        assertTrue(result is TemplateExecutor.ExecutionResult.Success)
        assertEquals(1, swipeHistory.size)
    }

    @Test
    fun `test execute back action`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val actionsJson = """[{"action":"back"}]"""
        val template = createTemplate(actionsJson = actionsJson)

        val result = executor.execute(template) { _, _, _ -> }

        assertTrue(result is TemplateExecutor.ExecutionResult.Success)
        assertEquals(1, globalActionHistory.size)
        assertEquals(1, globalActionHistory[0]) // GLOBAL_ACTION_BACK = 1
    }

    @Test
    fun `test execute home action`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val actionsJson = """[{"action":"home"}]"""
        val template = createTemplate(actionsJson = actionsJson)

        val result = executor.execute(template) { _, _, _ -> }

        assertTrue(result is TemplateExecutor.ExecutionResult.Success)
        assertEquals(1, globalActionHistory.size)
        assertEquals(2, globalActionHistory[0]) // GLOBAL_ACTION_HOME = 2
    }

    @Test
    fun `test execute multiple actions in sequence`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val actionsJson = """[
            {"action":"tap","location":[100,100]},
            {"action":"tap","location":[200,200]},
            {"action":"back"}
        ]"""
        val template = createTemplate(actionsJson = actionsJson, stepCount = 3)

        val result = executor.execute(template) { _, _, _ -> }

        assertTrue(result is TemplateExecutor.ExecutionResult.Success)
        assertEquals(2, tapHistory.size)
        assertEquals(1, globalActionHistory.size)
    }

    @Test
    fun `test progress callback is called`() = runBlocking {
        executor = TemplateExecutor(
            context = mockContext,
            service = mockService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val actionsJson = """[
            {"action":"tap","location":[100,100]},
            {"action":"tap","location":[200,200]}
        ]"""
        val template = createTemplate(actionsJson = actionsJson, stepCount = 2)

        var progressCalls = 0
        executor.execute(template) { current, total, _ ->
            progressCalls++
            assertTrue(current <= total)
        }

        assertTrue(progressCalls >= 2)
    }

    @Test
    fun `test service failure does not stop execution`() = runBlocking {
        // Note: Current TemplateExecutor implementation doesn't check injectTap return value
        // This test documents the current behavior
        val failingService = MockAutoGLMServiceFactory.create(shouldFail = true)
        executor = TemplateExecutor(
            context = mockContext,
            service = failingService,
            screenWidth = 1080,
            screenHeight = 2400
        )

        val actionsJson = """[{"action":"tap","location":[100,100]}]"""
        val template = createTemplate(actionsJson = actionsJson)

        val result = executor.execute(template) { _, _, _ -> }

        // Current implementation always returns Success for tap actions
        // because it doesn't check the return value of injectTap
        assertTrue(result is TemplateExecutor.ExecutionResult.Success)
    }

    private fun createTemplate(
        actionsJson: String,
        screenWidth: Int = 1080,
        screenHeight: Int = 2400,
        stepCount: Int = 1
    ) = TaskTemplateEntity(
        id = 1,
        name = "Test",
        description = "Test template",
        actionsJson = actionsJson,
        screenWidth = screenWidth,
        screenHeight = screenHeight,
        stepCount = stepCount,
        estimatedDurationMs = 1000,
        createdAt = System.currentTimeMillis()
    )
}
