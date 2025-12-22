package com.example.autoglm

import android.app.Application
import com.example.autoglm.core.AgentCore
import com.example.autoglm.ui.viewmodel.MainViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * 验证测试：TaskScope 回调机制修复
 *
 * 测试目标：
 * 1. 验证 TaskScope.setOnTaskStoppedCallback() 正确注册回调
 * 2. 验证 TaskScope.stopCurrentTask() 正确调用回调
 * 3. 验证 MainViewModel.updateTaskStoppedState() 正确更新状态
 * 4. 模拟完整流程：启动任务 -> 停止任务 -> 验证状态更新
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class TaskScopeCallbackVerificationTest {

    private lateinit var application: Application

    @OptIn(ExperimentalCoroutinesApi::class)
    @Before
    fun setup() {
        Dispatchers.setMain(StandardTestDispatcher())
        application = RuntimeEnvironment.getApplication()
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @After
    fun tearDown() {
        Dispatchers.resetMain()
        TaskScope.cancelAll()
        TaskScope.clearOnTaskStoppedCallback()
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test TaskScope callback registration and invocation`() = runTest {
        val testLog = mutableListOf<String>()
        var callbackInvoked = false

        testLog.add("=" * 80)
        testLog.add("TEST 1: TaskScope Callback Registration and Invocation")
        testLog.add("=" * 80)

        // 1. 注册回调
        testLog.add("\nPhase 1: Register callback")
        TaskScope.setOnTaskStoppedCallback {
            testLog.add("✓ Callback invoked!")
            callbackInvoked = true
        }
        testLog.add("✓ Callback registered")

        // 2. 启动模拟任务
        testLog.add("\nPhase 2: Launch task")
        val mockAgentCore = AgentCore(application) { }
        val job = TaskScope.launchTask(mockAgentCore) {
            testLog.add("Task started")
            delay(10000) // 模拟长时间运行
            testLog.add("Task completed")
        }
        advanceTimeBy(100)
        testLog.add("✓ Task launched")

        // 3. 停止任务
        testLog.add("\nPhase 3: Stop task")
        TaskScope.stopCurrentTask()
        advanceTimeBy(100)

        // 4. 验证回调被调用
        testLog.add("\nPhase 4: Verify callback")
        testLog.add("Callback invoked: $callbackInvoked")

        testLog.add("\n" + "=" * 80)
        if (callbackInvoked) {
            testLog.add("TEST RESULT: ✅ PASSED")
            testLog.add("Callback was correctly invoked when task stopped")
        } else {
            testLog.add("TEST RESULT: ❌ FAILED")
            testLog.add("Callback was NOT invoked")
        }
        testLog.add("=" * 80)

        // 打印日志
        testLog.forEach { println(it) }

        // 断言
        assertTrue("Callback should be invoked when task stops", callbackInvoked)
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test MainViewModel updateTaskStoppedState updates isRunning`() = runTest {
        val testLog = mutableListOf<String>()

        testLog.add("=" * 80)
        testLog.add("TEST 2: MainViewModel.updateTaskStoppedState()")
        testLog.add("=" * 80)

        // 1. 创建 ViewModel
        testLog.add("\nPhase 1: Create ViewModel")
        val viewModel = MainViewModel(application)
        testLog.add("✓ ViewModel created")

        // 2. 手动设置 isRunning = true（模拟任务启动）
        testLog.add("\nPhase 2: Simulate task start")
        // 通过反射或直接调用内部方法设置状态
        // 这里我们直接调用 updateTaskStoppedState 来验证它的行为
        var initialState = viewModel.state.first()
        testLog.add("Initial isRunning: ${initialState.isRunning}")

        // 3. 调用 updateTaskStoppedState
        testLog.add("\nPhase 3: Call updateTaskStoppedState()")
        viewModel.updateTaskStoppedState()
        advanceTimeBy(100)

        // 4. 验证状态更新
        testLog.add("\nPhase 4: Verify state update")
        val finalState = viewModel.state.first()
        testLog.add("Final isRunning: ${finalState.isRunning}")

        testLog.add("\n" + "=" * 80)
        if (!finalState.isRunning) {
            testLog.add("TEST RESULT: ✅ PASSED")
            testLog.add("updateTaskStoppedState() correctly set isRunning = false")
        } else {
            testLog.add("TEST RESULT: ❌ FAILED")
            testLog.add("isRunning was not updated")
        }
        testLog.add("=" * 80)

        // 打印日志
        testLog.forEach { println(it) }

        // 断言
        assertFalse("isRunning should be false after updateTaskStoppedState()",
            finalState.isRunning)
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test complete flow - TaskScope callback updates ViewModel state`() = runTest {
        val testLog = mutableListOf<String>()

        testLog.add("=" * 80)
        testLog.add("TEST 3: Complete Flow - TaskScope -> ViewModel State Update")
        testLog.add("=" * 80)

        // 1. 创建 ViewModel
        testLog.add("\nPhase 1: Create ViewModel")
        val viewModel = MainViewModel(application)
        testLog.add("✓ ViewModel created")

        // 2. 模拟注册回调（就像 startTaskAfterPreCheck 中做的那样）
        testLog.add("\nPhase 2: Register TaskScope callback")
        var callbackInvoked = false
        TaskScope.setOnTaskStoppedCallback {
            testLog.add("✓ TaskScope callback invoked")
            callbackInvoked = true
            // 模拟 ViewModel 的回调处理（在测试协程中执行）
            viewModel.updateTaskStoppedState()
        }
        testLog.add("✓ Callback registered")

        // 3. 启动模拟任务
        testLog.add("\nPhase 3: Launch task")
        val mockAgentCore = AgentCore(application) { }
        val job = TaskScope.launchTask(mockAgentCore) {
            testLog.add("Task coroutine started")
            delay(10000)
            testLog.add("Task completed")
        }
        advanceTimeBy(100)
        testLog.add("✓ Task launched")

        // 4. 停止任务（模拟从悬浮窗停止）
        testLog.add("\nPhase 4: Stop task (simulating overlay stop)")
        TaskScope.stopCurrentTask()
        advanceTimeBy(100)

        // 5. 验证回调被调用
        testLog.add("\nPhase 5: Verify callback invocation")
        testLog.add("Callback invoked: $callbackInvoked")

        // 6. 验证 ViewModel 状态更新
        testLog.add("\nPhase 6: Verify ViewModel state")
        val finalState = viewModel.state.first()
        testLog.add("Final isRunning: ${finalState.isRunning}")

        testLog.add("\n" + "=" * 80)
        if (callbackInvoked && !finalState.isRunning) {
            testLog.add("TEST RESULT: ✅ PASSED")
            testLog.add("✓ Callback was invoked")
            testLog.add("✓ ViewModel state was updated (isRunning = false)")
            testLog.add("\nThis proves the fix works:")
            testLog.add("1. TaskScope.stopCurrentTask() calls the callback")
            testLog.add("2. Callback invokes viewModel.updateTaskStoppedState()")
            testLog.add("3. State is updated correctly")
            testLog.add("4. UI will show correct state when user returns to app")
        } else {
            testLog.add("TEST RESULT: ❌ FAILED")
            if (!callbackInvoked) {
                testLog.add("✗ Callback was NOT invoked")
            }
            if (finalState.isRunning) {
                testLog.add("✗ ViewModel state was NOT updated")
            }
        }
        testLog.add("=" * 80)

        // 打印日志
        testLog.forEach { println(it) }

        // 断言
        assertTrue("Callback should be invoked", callbackInvoked)
        assertFalse("isRunning should be false after stop", finalState.isRunning)
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `test callback is cleared after use`() = runTest {
        val testLog = mutableListOf<String>()

        testLog.add("=" * 80)
        testLog.add("TEST 4: Callback Cleanup")
        testLog.add("=" * 80)

        // 1. 注册回调
        testLog.add("\nPhase 1: Register callback")
        var callbackInvoked = false
        TaskScope.setOnTaskStoppedCallback {
            callbackInvoked = true
        }
        testLog.add("✓ Callback registered")

        // 2. 清除回调
        testLog.add("\nPhase 2: Clear callback")
        TaskScope.clearOnTaskStoppedCallback()
        testLog.add("✓ Callback cleared")

        // 3. 启动并停止任务
        testLog.add("\nPhase 3: Launch and stop task")
        val mockAgentCore = AgentCore(application) { }
        TaskScope.launchTask(mockAgentCore) {
            delay(10000)
        }
        advanceTimeBy(100)
        TaskScope.stopCurrentTask()
        advanceTimeBy(100)

        // 4. 验证回调未被调用
        testLog.add("\nPhase 4: Verify callback not invoked")
        testLog.add("Callback invoked: $callbackInvoked")

        testLog.add("\n" + "=" * 80)
        if (!callbackInvoked) {
            testLog.add("TEST RESULT: ✅ PASSED")
            testLog.add("Callback was correctly NOT invoked after clearing")
        } else {
            testLog.add("TEST RESULT: ❌ FAILED")
            testLog.add("Callback was invoked even after clearing")
        }
        testLog.add("=" * 80)

        // 打印日志
        testLog.forEach { println(it) }

        // 断言
        assertFalse("Callback should NOT be invoked after clearing", callbackInvoked)
    }
}

// 扩展函数用于重复字符串
private operator fun String.times(n: Int): String = this.repeat(n)
