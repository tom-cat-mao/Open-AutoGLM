package com.taskwizard.android

import io.mockk.every
import io.mockk.mockk

/**
 * Mock AutoGLM Service factory for testing
 *
 * 由于 IAutoGLMService.Stub 依赖 Android Binder，无法在纯 JVM 单元测试中使用。
 * 使用 MockK 创建 mock 对象。
 */
object MockAutoGLMServiceFactory {

    data class SwipeRecord(
        val x1: Int, val y1: Int,
        val x2: Int, val y2: Int,
        val duration: Long
    )

    /**
     * 创建一个可追踪调用历史的 mock service
     */
    fun create(
        tapHistory: MutableList<Pair<Int, Int>> = mutableListOf(),
        swipeHistory: MutableList<SwipeRecord> = mutableListOf(),
        globalActionHistory: MutableList<Int> = mutableListOf(),
        shouldFail: Boolean = false
    ): IAutoGLMService {
        val mock = mockk<IAutoGLMService>(relaxed = true)

        every { mock.destroy() } returns Unit
        every { mock.executeShellCommand(any()) } returns "OK"
        every { mock.takeScreenshotToFile() } returns "/tmp/test.png"
        every { mock.injectInputBase64(any()) } returns Unit
        every { mock.getCurrentPackage() } returns "com.test.app"
        every { mock.getCurrentIME() } returns "com.test.ime/.TestIME"
        every { mock.setIME(any()) } returns true
        every { mock.isADBKeyboardInstalled() } returns true
        every { mock.isIMEEnabled(any()) } returns true
        every { mock.initUiAutomation() } returns true
        every { mock.isUiAutomationAvailable() } returns true

        every { mock.injectTap(any(), any()) } answers {
            if (shouldFail) {
                false
            } else {
                val x = firstArg<Int>()
                val y = secondArg<Int>()
                tapHistory.add(x to y)
                true
            }
        }

        every { mock.injectSwipe(any(), any(), any(), any(), any()) } answers {
            if (shouldFail) {
                false
            } else {
                swipeHistory.add(SwipeRecord(
                    arg(0), arg(1), arg(2), arg(3), arg(4)
                ))
                true
            }
        }

        every { mock.performGlobalAction(any()) } answers {
            if (shouldFail) {
                false
            } else {
                globalActionHistory.add(firstArg())
                true
            }
        }

        return mock
    }
}
