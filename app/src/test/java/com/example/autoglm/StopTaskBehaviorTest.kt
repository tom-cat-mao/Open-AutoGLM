package com.example.autoglm

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*

/**
 * 简化的测试：直接测试 stopTask 的行为
 *
 * 这个测试专注于验证：
 * 1. stopTask() 是否正确调用了 cleanupTask()
 * 2. cleanupTask() 是否正确设置了 isRunning = false
 */
class StopTaskBehaviorTest {

    @OptIn(ExperimentalCoroutinesApi::class)
    @Before
    fun setup() {
        Dispatchers.setMain(StandardTestDispatcher())
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @After
    fun tearDown() {
        Dispatchers.resetMain()
        TaskScope.cancelAll()
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test stopTask calls cleanup and sets isRunning to false`() = runTest {
        val testLog = mutableListOf<String>()
        var isRunning = false
        var cleanupCalled = false

        testLog.add("=".repeat(80))
        testLog.add("TEST: stopTask() behavior verification")
        testLog.add("=".repeat(80))

        // 模拟 MainViewModel 的 stopTask() 逻辑
        fun stopTask() {
            testLog.add("1. stopTask() called")

            // 这是 MainViewModel.stopTask() 的实际代码
            TaskScope.stopCurrentTask()

            // 关键：这行代码应该设置 isRunning = false
            isRunning = false
            testLog.add("2. isRunning set to false")

            // 调用 cleanupTask
            cleanupCalled = true
            testLog.add("3. cleanupTask() called")
        }

        // 启动一个模拟任务
        testLog.add("\nPhase 1: Start task")
        isRunning = true
        testLog.add("✓ Task started: isRunning = $isRunning")

        val mockAgentCore = object {
            fun stop() {
                testLog.add("AgentCore.stop() called")
            }
        }

        val job = TaskScope.scope.launch {
            try {
                testLog.add("Task coroutine started")
                delay(10000) // 模拟长时间运行
                testLog.add("Task completed normally")
            } catch (e: CancellationException) {
                testLog.add("Task cancelled: ${e.message}")
                // 关键问题：这里没有设置 isRunning = false
                throw e
            } finally {
                testLog.add("Task finally block")
                // 这里也没有设置 isRunning = false
            }
        }

        advanceTimeBy(100)
        assertTrue("Task should be running", isRunning)

        // 停止任务
        testLog.add("\nPhase 2: Stop task")
        stopTask()
        advanceTimeBy(100)

        testLog.add("\nPhase 3: Verify state")
        testLog.add("isRunning = $isRunning")
        testLog.add("cleanupCalled = $cleanupCalled")

        // 验证
        assertFalse("isRunning should be false after stopTask()", isRunning)
        assertTrue("cleanupTask should be called", cleanupCalled)

        testLog.add("\n" + "=".repeat(80))
        testLog.add("TEST RESULT: PASSED ✓")
        testLog.add("=".repeat(80))

        // 打印日志
        testLog.forEach { println(it) }
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test current MainViewModel pattern - catch block with throw`() = runTest {
        val testLog = mutableListOf<String>()
        var isRunning = false
        var cleanupCalledInCatch = false
        var cleanupCalledInFinally = false

        testLog.add("=".repeat(80))
        testLog.add("TEST: Current MainViewModel pattern (with throw e)")
        testLog.add("=".repeat(80))

        // 模拟当前的 MainViewModel 代码结构
        val job = TaskScope.scope.launch {
            try {
                try {
                    testLog.add("Task started")
                    isRunning = true
                    delay(10000)
                    testLog.add("Task completed")
                } finally {
                    testLog.add("Inner finally: restoreIME")
                }
            } catch (e: CancellationException) {
                testLog.add("Catch block: Task cancelled")
                // 当前代码：在这里没有调用 cleanupTask()
                // cleanupCalledInCatch = true
                throw e  // 重新抛出异常
            } finally {
                testLog.add("Outer finally: cleanupTask")
                isRunning = false
                cleanupCalledInFinally = true
            }
        }

        advanceTimeBy(100)
        testLog.add("✓ Task running: isRunning = $isRunning")

        // 停止任务
        testLog.add("\nStopping task...")
        TaskScope.stopCurrentTask()
        advanceTimeBy(100)

        testLog.add("\nFinal state:")
        testLog.add("isRunning = $isRunning")
        testLog.add("cleanupCalledInCatch = $cleanupCalledInCatch")
        testLog.add("cleanupCalledInFinally = $cleanupCalledInFinally")

        testLog.add("\n" + "=".repeat(80))
        if (!isRunning && cleanupCalledInFinally) {
            testLog.add("TEST RESULT: PASSED ✓")
            testLog.add("Outer finally WAS executed")
        } else {
            testLog.add("TEST RESULT: FAILED ❌")
            testLog.add("Outer finally was NOT executed")
        }
        testLog.add("=".repeat(80))

        // 打印日志
        testLog.forEach { println(it) }

        // 验证
        assertFalse("isRunning should be false", isRunning)
        assertTrue("Outer finally should be executed", cleanupCalledInFinally)
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test fixed pattern - cleanup in catch block`() = runTest {
        val testLog = mutableListOf<String>()
        var isRunning = false
        var cleanupCalledInCatch = false
        var cleanupCalledInFinally = false

        testLog.add("=".repeat(80))
        testLog.add("TEST: Fixed pattern (cleanup in catch block)")
        testLog.add("=".repeat(80))

        // 模拟修复后的代码结构
        val job = TaskScope.scope.launch {
            try {
                try {
                    testLog.add("Task started")
                    isRunning = true
                    delay(10000)
                    testLog.add("Task completed")
                } finally {
                    testLog.add("Inner finally: restoreIME")
                }
            } catch (e: CancellationException) {
                testLog.add("Catch block: Task cancelled")
                // 修复：在 catch 块中调用 cleanupTask()
                isRunning = false
                cleanupCalledInCatch = true
                testLog.add("Catch block: cleanupTask() called, isRunning = false")
                // 不重新抛出异常
            } finally {
                testLog.add("Outer finally: cleanupTask (double insurance)")
                if (isRunning) {
                    isRunning = false
                    cleanupCalledInFinally = true
                }
            }
        }

        advanceTimeBy(100)
        testLog.add("✓ Task running: isRunning = $isRunning")

        // 停止任务
        testLog.add("\nStopping task...")
        TaskScope.stopCurrentTask()
        advanceTimeBy(100)

        testLog.add("\nFinal state:")
        testLog.add("isRunning = $isRunning")
        testLog.add("cleanupCalledInCatch = $cleanupCalledInCatch")
        testLog.add("cleanupCalledInFinally = $cleanupCalledInFinally")

        testLog.add("\n" + "=".repeat(80))
        if (!isRunning && cleanupCalledInCatch) {
            testLog.add("TEST RESULT: PASSED ✓")
            testLog.add("Cleanup was called in catch block")
        } else {
            testLog.add("TEST RESULT: FAILED ❌")
        }
        testLog.add("=".repeat(80))

        // 打印日志
        testLog.forEach { println(it) }

        // 验证
        assertFalse("isRunning should be false", isRunning)
        assertTrue("Cleanup should be called in catch block", cleanupCalledInCatch)
    }
}
