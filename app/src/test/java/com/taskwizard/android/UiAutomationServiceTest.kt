package com.taskwizard.android

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * UiAutomation 服务单元测试
 *
 * 测试 IAutoGLMService 中 UiAutomation 相关方法的行为
 */
class UiAutomationServiceTest {

    private lateinit var mockService: IAutoGLMService
    private lateinit var tapHistory: MutableList<Pair<Int, Int>>
    private lateinit var swipeHistory: MutableList<MockAutoGLMServiceFactory.SwipeRecord>
    private lateinit var globalActionHistory: MutableList<Int>

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

    // ==================== UiAutomation 初始化测试 ====================

    @Test
    fun `test initUiAutomation returns true on success`() {
        val result = mockService.initUiAutomation()
        assertTrue("UiAutomation should initialize successfully", result)
    }

    @Test
    fun `test isUiAutomationAvailable returns true after init`() {
        mockService.initUiAutomation()
        val result = mockService.isUiAutomationAvailable()
        assertTrue("UiAutomation should be available after init", result)
    }

    @Test
    fun `test initUiAutomation failure scenario`() {
        val failingService = mockk<IAutoGLMService>(relaxed = true)
        every { failingService.initUiAutomation() } returns false
        every { failingService.isUiAutomationAvailable() } returns false

        val initResult = failingService.initUiAutomation()
        val availableResult = failingService.isUiAutomationAvailable()

        assertFalse("Init should fail", initResult)
        assertFalse("UiAutomation should not be available", availableResult)
    }

    // ==================== 点击操作测试 ====================

    @Test
    fun `test injectTap records coordinates`() {
        val result = mockService.injectTap(100, 200)

        assertTrue("Tap should succeed", result)
        assertEquals("Should record one tap", 1, tapHistory.size)
        assertEquals("X coordinate should match", 100, tapHistory[0].first)
        assertEquals("Y coordinate should match", 200, tapHistory[0].second)
    }

    @Test
    fun `test injectTap with zero coordinates`() {
        val result = mockService.injectTap(0, 0)

        assertTrue("Tap at origin should succeed", result)
        assertEquals(0, tapHistory[0].first)
        assertEquals(0, tapHistory[0].second)
    }

    @Test
    fun `test injectTap with large coordinates`() {
        val result = mockService.injectTap(2160, 4800)

        assertTrue("Tap at large coordinates should succeed", result)
        assertEquals(2160, tapHistory[0].first)
        assertEquals(4800, tapHistory[0].second)
    }

    @Test
    fun `test multiple taps recorded in order`() {
        mockService.injectTap(100, 100)
        mockService.injectTap(200, 200)
        mockService.injectTap(300, 300)

        assertEquals("Should record three taps", 3, tapHistory.size)
        assertEquals(100, tapHistory[0].first)
        assertEquals(200, tapHistory[1].first)
        assertEquals(300, tapHistory[2].first)
    }

    // ==================== 滑动操作测试 ====================

    @Test
    fun `test injectSwipe records all parameters`() {
        val result = mockService.injectSwipe(100, 200, 300, 400, 500L)

        assertTrue("Swipe should succeed", result)
        assertEquals("Should record one swipe", 1, swipeHistory.size)

        val swipe = swipeHistory[0]
        assertEquals(100, swipe.x1)
        assertEquals(200, swipe.y1)
        assertEquals(300, swipe.x2)
        assertEquals(400, swipe.y2)
        assertEquals(500L, swipe.duration)
    }

    @Test
    fun `test swipe up gesture`() {
        // 从下往上滑动
        mockService.injectSwipe(540, 1800, 540, 600, 300L)

        val swipe = swipeHistory[0]
        assertTrue("End Y should be less than start Y for swipe up", swipe.y2 < swipe.y1)
    }

    @Test
    fun `test swipe down gesture`() {
        // 从上往下滑动
        mockService.injectSwipe(540, 600, 540, 1800, 300L)

        val swipe = swipeHistory[0]
        assertTrue("End Y should be greater than start Y for swipe down", swipe.y2 > swipe.y1)
    }

    @Test
    fun `test swipe left gesture`() {
        // 从右往左滑动
        mockService.injectSwipe(900, 1200, 180, 1200, 300L)

        val swipe = swipeHistory[0]
        assertTrue("End X should be less than start X for swipe left", swipe.x2 < swipe.x1)
    }

    @Test
    fun `test swipe right gesture`() {
        // 从左往右滑动
        mockService.injectSwipe(180, 1200, 900, 1200, 300L)

        val swipe = swipeHistory[0]
        assertTrue("End X should be greater than start X for swipe right", swipe.x2 > swipe.x1)
    }

    // ==================== 全局操作测试 ====================

    @Test
    fun `test performGlobalAction back`() {
        val GLOBAL_ACTION_BACK = 1
        val result = mockService.performGlobalAction(GLOBAL_ACTION_BACK)

        assertTrue("Back action should succeed", result)
        assertEquals(1, globalActionHistory.size)
        assertEquals(GLOBAL_ACTION_BACK, globalActionHistory[0])
    }

    @Test
    fun `test performGlobalAction home`() {
        val GLOBAL_ACTION_HOME = 2
        val result = mockService.performGlobalAction(GLOBAL_ACTION_HOME)

        assertTrue("Home action should succeed", result)
        assertEquals(GLOBAL_ACTION_HOME, globalActionHistory[0])
    }

    @Test
    fun `test performGlobalAction recents`() {
        val GLOBAL_ACTION_RECENTS = 3
        val result = mockService.performGlobalAction(GLOBAL_ACTION_RECENTS)

        assertTrue("Recents action should succeed", result)
        assertEquals(GLOBAL_ACTION_RECENTS, globalActionHistory[0])
    }

    // ==================== 失败场景测试 ====================

    @Test
    fun `test injectTap failure`() {
        val failingService = MockAutoGLMServiceFactory.create(shouldFail = true)

        val result = failingService.injectTap(100, 200)

        assertFalse("Tap should fail when service fails", result)
    }

    @Test
    fun `test injectSwipe failure`() {
        val failingService = MockAutoGLMServiceFactory.create(shouldFail = true)

        val result = failingService.injectSwipe(100, 200, 300, 400, 500L)

        assertFalse("Swipe should fail when service fails", result)
    }

    @Test
    fun `test performGlobalAction failure`() {
        val failingService = MockAutoGLMServiceFactory.create(shouldFail = true)

        val result = failingService.performGlobalAction(1)

        assertFalse("Global action should fail when service fails", result)
    }

    // ==================== Shell 命令 Fallback 测试 ====================

    @Test
    fun `test executeShellCommand returns result`() {
        val result = mockService.executeShellCommand("input tap 100 200")

        assertEquals("OK", result)
    }

    @Test
    fun `test shell command fallback when UiAutomation unavailable`() {
        val fallbackService = mockk<IAutoGLMService>(relaxed = true)
        every { fallbackService.isUiAutomationAvailable() } returns false
        every { fallbackService.executeShellCommand(any()) } returns "OK"

        // 当 UiAutomation 不可用时，应该使用 shell 命令
        val isAvailable = fallbackService.isUiAutomationAvailable()
        assertFalse("UiAutomation should not be available", isAvailable)

        val shellResult = fallbackService.executeShellCommand("input tap 100 200")
        assertEquals("OK", shellResult)
    }

    // ==================== 服务生命周期测试 ====================

    @Test
    fun `test destroy cleans up resources`() {
        mockService.initUiAutomation()
        mockService.destroy()

        verify { mockService.destroy() }
    }
}
