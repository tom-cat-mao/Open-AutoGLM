package com.example.autoglm

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Test
import org.junit.Assert.*

/**
 * 测试协程取消时 finally 块的执行顺序
 *
 * 这个测试用于验证当协程被取消时，内层和外层 finally 块的执行顺序
 */
class CancellationTest {

    @Test
    fun `test finally blocks execution order when coroutine is cancelled`() = runTest {
        val executionLog = mutableListOf<String>()

        val job = launch {
            try {
                try {
                    delay(1000) // 模拟长时间运行的任务
                    executionLog.add("Task completed")
                } catch (e: CancellationException) {
                    executionLog.add("Inner catch: Task cancelled")
                    throw e // 重新抛出异常
                } finally {
                    executionLog.add("Inner finally: Cleanup 1")
                }
            } catch (e: CancellationException) {
                executionLog.add("Outer catch: Task cancelled")
                throw e // 重新抛出异常
            } finally {
                executionLog.add("Outer finally: Cleanup 2")
            }
        }

        // 等待一小段时间后取消任务
        delay(100)
        job.cancel()
        job.join()

        // 验证执行顺序
        println("Execution log: $executionLog")

        // 预期：内层 finally -> 内层 catch -> 外层 finally
        // 但实际上：内层 finally -> 内层 catch -> 外层 catch -> 外层 finally
        assertTrue("Inner finally should be executed", executionLog.contains("Inner finally: Cleanup 1"))
        assertTrue("Inner catch should be executed", executionLog.contains("Inner catch: Task cancelled"))
        assertTrue("Outer finally should be executed", executionLog.contains("Outer finally: Cleanup 2"))
    }

    @Test
    fun `test finally blocks execution when NOT rethrowing exception`() = runTest {
        val executionLog = mutableListOf<String>()

        val job = launch {
            try {
                try {
                    delay(1000)
                    executionLog.add("Task completed")
                } catch (e: CancellationException) {
                    executionLog.add("Inner catch: Task cancelled")
                    // 不重新抛出异常
                } finally {
                    executionLog.add("Inner finally: Cleanup 1")
                }
            } finally {
                executionLog.add("Outer finally: Cleanup 2")
            }
        }

        delay(100)
        job.cancel()
        job.join()

        println("Execution log (no rethrow): $executionLog")

        // 验证：不重新抛出异常时，外层 finally 会被执行
        assertTrue("Inner finally should be executed", executionLog.contains("Inner finally: Cleanup 1"))
        assertTrue("Inner catch should be executed", executionLog.contains("Inner catch: Task cancelled"))
        assertTrue("Outer finally should be executed", executionLog.contains("Outer finally: Cleanup 2"))
    }

    @Test
    fun `test current MainViewModel pattern`() = runTest {
        var isRunning = true
        val executionLog = mutableListOf<String>()

        val job = launch {
            try {
                try {
                    delay(1000)
                    executionLog.add("Task completed")
                } finally {
                    executionLog.add("executeTask finally: restoreIME")
                }
            } catch (e: CancellationException) {
                executionLog.add("Outer catch: Task cancelled")
                throw e // 这里重新抛出异常
            } finally {
                executionLog.add("Outer finally: cleanupTask")
                isRunning = false // 这里应该设置 isRunning = false
            }
        }

        delay(100)
        job.cancel()
        job.join()

        println("Current pattern log: $executionLog")
        println("isRunning after cancellation: $isRunning")

        // 验证问题：外层 finally 是否被执行？
        assertTrue("executeTask finally should be executed",
            executionLog.contains("executeTask finally: restoreIME"))
        assertTrue("Outer catch should be executed",
            executionLog.contains("Outer catch: Task cancelled"))
        assertTrue("Outer finally MUST be executed",
            executionLog.contains("Outer finally: cleanupTask"))
        assertFalse("isRunning should be false after cleanup", isRunning)
    }
}
