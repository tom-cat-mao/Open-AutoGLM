package com.example.autoglm

import android.app.Application
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
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
 * 集成测试：模拟完整的任务停止流程
 *
 * 测试场景：
 * 1. 主程序启动任务
 * 2. 主程序退至后台（Activity STOPPED）
 * 3. 悬浮窗出现，任务继续运行
 * 4. 在悬浮窗中点击停止按钮
 * 5. 切回主 app（Activity RESUMED）
 * 6. 验证 UI 状态是否正确显示任务已停止
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class TaskStopIntegrationTest {

    private lateinit var viewModel: MainViewModel
    private lateinit var application: Application
    private lateinit var testScope: TestScope
    private lateinit var lifecycleOwner: TestLifecycleOwner

    @Before
    fun setup() {
        // 设置测试环境
        Dispatchers.setMain(StandardTestDispatcher())
        testScope = TestScope()

        // 创建 Application 实例
        application = RuntimeEnvironment.getApplication()

        // 创建 ViewModel
        viewModel = MainViewModel(application)

        // 创建测试用的 LifecycleOwner
        lifecycleOwner = TestLifecycleOwner()
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
        TaskScope.cancelAll()
    }

    @Test
    fun `test complete task stop flow - main app to overlay to main app`() = runTest {
        val testLog = mutableListOf<String>()

        // ==================== 阶段 1: 主程序启动任务 ====================
        testLog.add("Phase 1: Starting task in main app")

        // 模拟配置
        viewModel.updateApiKey("test-api-key-1234567890")
        viewModel.updateBaseUrl("https://api.test.com")
        viewModel.updateModel("test-model")
        viewModel.saveSettings()

        // 模拟任务输入
        viewModel.updateTask("测试任务")

        // 检查初始状态
        var state = viewModel.state.first()
        assertFalse("Initial state: isRunning should be false", state.isRunning)
        testLog.add("✓ Initial state: isRunning = ${state.isRunning}")

        // 启动任务（模拟，不实际执行网络请求）
        testLog.add("Starting task...")

        // 直接设置 isRunning 状态来模拟任务启动
        // 因为实际的 startTask() 需要网络和 Shizuku 权限
        simulateTaskStart()
        advanceTimeBy(100)

        state = viewModel.state.first()
        assertTrue("After start: isRunning should be true", state.isRunning)
        testLog.add("✓ Task started: isRunning = ${state.isRunning}")

        // ==================== 阶段 2: 主程序退至后台 ====================
        testLog.add("\nPhase 2: Main app moves to background")

        // 模拟 Activity 进入 STOPPED 状态
        lifecycleOwner.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        advanceTimeBy(100)

        testLog.add("✓ Activity state: STOPPED")
        testLog.add("✓ Overlay window appears (simulated)")

        // 验证状态仍然是 running（因为任务在后台继续）
        state = viewModel.state.first()
        assertTrue("While in background: isRunning should still be true", state.isRunning)
        testLog.add("✓ Task still running in background: isRunning = ${state.isRunning}")

        // ==================== 阶段 3: 在悬浮窗中停止任务 ====================
        testLog.add("\nPhase 3: Stop task from overlay window")

        // 模拟在悬浮窗中点击停止按钮
        testLog.add("Clicking stop button in overlay...")
        viewModel.stopTask()
        advanceTimeBy(100)

        // 检查状态是否更新
        state = viewModel.state.first()
        testLog.add("After stopTask(): isRunning = ${state.isRunning}")

        // 关键验证：stopTask() 后 isRunning 应该立即变为 false
        assertFalse("After stopTask(): isRunning should be false", state.isRunning)
        testLog.add("✓ Task stopped: isRunning = ${state.isRunning}")

        // ==================== 阶段 4: 切回主 app ====================
        testLog.add("\nPhase 4: Return to main app")

        // 模拟 Activity 恢复到前台
        lifecycleOwner.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        advanceTimeBy(100)

        testLog.add("✓ Activity state: RESUMED")

        // 调用 refreshState() 模拟 MainActivity.onResume()
        viewModel.refreshState()
        advanceTimeBy(100)

        testLog.add("✓ refreshState() called")

        // ==================== 阶段 5: 验证最终状态 ====================
        testLog.add("\nPhase 5: Verify final UI state")

        state = viewModel.state.first()
        testLog.add("Final state: isRunning = ${state.isRunning}")

        // 关键验证：UI 应该显示任务已停止
        assertFalse("Final state: isRunning should be false (UI should show 'Start' button)",
            state.isRunning)
        testLog.add("✓ UI state correct: Task stopped")

        // ==================== 打印测试报告 ====================
        println("\n" + "=".repeat(80))
        println("TASK STOP INTEGRATION TEST REPORT")
        println("=".repeat(80))
        testLog.forEach { println(it) }
        println("=".repeat(80))
        println("TEST RESULT: ${if (state.isRunning) "FAILED ❌" else "PASSED ✓"}")
        println("=".repeat(80) + "\n")
    }

    @Test
    fun `test task stop with real TaskScope cancellation`() = runTest {
        val testLog = mutableListOf<String>()

        testLog.add("Testing TaskScope cancellation behavior")

        // 创建一个模拟的 AgentCore
        val mockAgentCore = AgentCore(application) { error ->
            testLog.add("Error callback: $error")
        }

        // 启动一个模拟任务
        var isRunning = true
        var cleanupCalled = false

        val job = TaskScope.launchTask(mockAgentCore) {
            try {
                testLog.add("Task started")
                isRunning = true

                // 模拟长时间运行的任务
                delay(10000)
                testLog.add("Task completed normally")
            } catch (e: CancellationException) {
                testLog.add("Task cancelled: ${e.message}")
                // 关键：这里应该设置 isRunning = false
                isRunning = false
                cleanupCalled = true
            } finally {
                testLog.add("Task finally block")
                // 这里也应该设置 isRunning = false（双重保险）
                if (!cleanupCalled) {
                    isRunning = false
                    cleanupCalled = true
                }
            }
        }

        advanceTimeBy(100)
        assertTrue("Task should be running", isRunning)
        testLog.add("✓ Task is running: isRunning = $isRunning")

        // 停止任务
        testLog.add("Calling TaskScope.stopCurrentTask()...")
        TaskScope.stopCurrentTask()
        advanceTimeBy(100)

        // 验证清理是否被调用
        testLog.add("After stop: isRunning = $isRunning, cleanupCalled = $cleanupCalled")

        assertTrue("Cleanup should be called", cleanupCalled)
        assertFalse("isRunning should be false after stop", isRunning)

        // 打印日志
        println("\n" + "=".repeat(80))
        println("TASKSCOPE CANCELLATION TEST REPORT")
        println("=".repeat(80))
        testLog.forEach { println(it) }
        println("=".repeat(80) + "\n")
    }

    @Test
    fun `test state update propagation after background stop`() = runTest {
        val testLog = mutableListOf<String>()

        testLog.add("Testing state update propagation")

        // 1. 启动任务
        simulateTaskStart()
        advanceTimeBy(100)

        var state = viewModel.state.first()
        assertTrue("Task should be running", state.isRunning)
        testLog.add("✓ Task started: isRunning = ${state.isRunning}")

        // 2. Activity 进入后台
        lifecycleOwner.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        advanceTimeBy(100)
        testLog.add("✓ Activity STOPPED")

        // 3. 在后台停止任务
        testLog.add("Stopping task while in background...")
        viewModel.stopTask()
        advanceTimeBy(100)

        // 4. 立即检查状态（不等待 Activity 恢复）
        state = viewModel.state.first()
        testLog.add("State immediately after stop: isRunning = ${state.isRunning}")

        // 关键验证：即使在后台，stopTask() 也应该立即更新状态
        assertFalse("State should be updated immediately", state.isRunning)
        testLog.add("✓ State updated immediately: isRunning = ${state.isRunning}")

        // 5. Activity 恢复
        lifecycleOwner.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        viewModel.refreshState()
        advanceTimeBy(100)
        testLog.add("✓ Activity RESUMED, refreshState() called")

        // 6. 验证状态仍然正确
        state = viewModel.state.first()
        testLog.add("State after resume: isRunning = ${state.isRunning}")

        assertFalse("State should still be false after resume", state.isRunning)
        testLog.add("✓ State remains correct: isRunning = ${state.isRunning}")

        // 打印日志
        println("\n" + "=".repeat(80))
        println("STATE PROPAGATION TEST REPORT")
        println("=".repeat(80))
        testLog.forEach { println(it) }
        println("=".repeat(80) + "\n")
    }

    // ==================== 辅助方法 ====================

    /**
     * 模拟任务启动（不实际执行网络请求）
     */
    private fun simulateTaskStart() {
        // 直接调用 ViewModel 的内部方法来设置状态
        // 因为实际的 startTask() 需要网络和 Shizuku 权限
        viewModel.updateTask("测试任务")

        // 使用反射或者直接调用 stopTask 来验证状态管理
        // 这里我们通过启动一个简单的协程来模拟
        TaskScope.scope.launch {
            // 模拟任务运行
            delay(10000)
        }
    }

    /**
     * 测试用的 LifecycleOwner
     */
    class TestLifecycleOwner : LifecycleOwner {
        private val lifecycleRegistry = LifecycleRegistry(this)

        init {
            lifecycleRegistry.currentState = Lifecycle.State.INITIALIZED
        }

        override val lifecycle: Lifecycle
            get() = lifecycleRegistry

        fun handleLifecycleEvent(event: Lifecycle.Event) {
            lifecycleRegistry.handleLifecycleEvent(event)
        }
    }
}
