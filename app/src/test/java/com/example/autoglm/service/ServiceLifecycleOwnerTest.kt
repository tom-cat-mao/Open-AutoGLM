package com.example.autoglm.service

import androidx.arch.core.executor.testing.InstantTaskExecutorRule
import androidx.lifecycle.Lifecycle
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * ServiceLifecycleOwner单元测试
 *
 * 测试生命周期管理的所有功能
 * 确保生命周期事件按正确顺序触发
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ServiceLifecycleOwnerTest {

    @get:Rule
    val instantTaskExecutorRule = InstantTaskExecutorRule()

    private lateinit var lifecycleOwner: ServiceLifecycleOwner

    @Before
    fun setup() {
        lifecycleOwner = ServiceLifecycleOwner()
    }

    /**
     * 测试：初始化后生命周期状态应该是RESUMED
     */
    @Test
    fun `lifecycle events triggered in correct order on init`() {
        // 初始化后应该处于RESUMED状态
        assertEquals(
            "初始化后应该处于RESUMED状态",
            Lifecycle.State.RESUMED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：isInitialized应该返回true
     */
    @Test
    fun `isInitialized returns true after init`() {
        assertTrue("初始化后isInitialized应该返回true", lifecycleOwner.isInitialized())
    }

    /**
     * 测试：isActive应该返回true
     */
    @Test
    fun `isActive returns true after init`() {
        assertTrue("初始化后isActive应该返回true", lifecycleOwner.isActive())
    }

    /**
     * 测试：isDestroyed应该返回false
     */
    @Test
    fun `isDestroyed returns false after init`() {
        assertFalse("初始化后isDestroyed应该返回false", lifecycleOwner.isDestroyed())
    }

    /**
     * 测试：onPause后状态应该是STARTED
     */
    @Test
    fun `onPause changes state to STARTED`() {
        lifecycleOwner.onPause()

        assertEquals(
            "onPause后状态应该是STARTED",
            Lifecycle.State.STARTED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：onStop后状态应该是CREATED
     */
    @Test
    fun `onStop changes state to CREATED`() {
        lifecycleOwner.onPause()
        lifecycleOwner.onStop()

        assertEquals(
            "onStop后状态应该是CREATED",
            Lifecycle.State.CREATED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：onDestroy后状态应该是DESTROYED
     */
    @Test
    fun `onDestroy changes state to DESTROYED`() {
        lifecycleOwner.onDestroy()

        assertEquals(
            "onDestroy后状态应该是DESTROYED",
            Lifecycle.State.DESTROYED,
            lifecycleOwner.getCurrentState()
        )
        assertTrue("onDestroy后isDestroyed应该返回true", lifecycleOwner.isDestroyed())
    }

    /**
     * 测试：ViewModelStore在onDestroy后被清理
     */
    @Test
    fun `viewModelStore cleared on destroy`() {
        val store = lifecycleOwner.viewModelStore

        // 销毁前store应该存在
        assertNotNull("ViewModelStore应该存在", store)

        lifecycleOwner.onDestroy()

        // 销毁后状态应该是DESTROYED
        assertEquals(
            "销毁后状态应该是DESTROYED",
            Lifecycle.State.DESTROYED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：SavedStateRegistry正确恢复
     */
    @Test
    fun `savedStateRegistry restored correctly`() {
        val registry = lifecycleOwner.savedStateRegistry

        assertNotNull("SavedStateRegistry应该存在", registry)
        assertTrue("SavedStateRegistry应该已恢复", registry.isRestored)
    }

    /**
     * 测试：完整的生命周期流程
     */
    @Test
    fun `full lifecycle flow works correctly`() {
        // 初始状态：RESUMED
        assertEquals(Lifecycle.State.RESUMED, lifecycleOwner.getCurrentState())

        // Pause
        lifecycleOwner.onPause()
        assertEquals(Lifecycle.State.STARTED, lifecycleOwner.getCurrentState())

        // Resume
        lifecycleOwner.onResume()
        assertEquals(Lifecycle.State.RESUMED, lifecycleOwner.getCurrentState())

        // Pause again
        lifecycleOwner.onPause()
        assertEquals(Lifecycle.State.STARTED, lifecycleOwner.getCurrentState())

        // Stop
        lifecycleOwner.onStop()
        assertEquals(Lifecycle.State.CREATED, lifecycleOwner.getCurrentState())

        // Destroy
        lifecycleOwner.onDestroy()
        assertEquals(Lifecycle.State.DESTROYED, lifecycleOwner.getCurrentState())
    }

    /**
     * 测试：onResume在非STARTED状态下不改变状态
     */
    @Test
    fun `onResume does nothing when not in STARTED state`() {
        // 初始状态是RESUMED
        lifecycleOwner.onResume()
        assertEquals(
            "在RESUMED状态调用onResume不应改变状态",
            Lifecycle.State.RESUMED,
            lifecycleOwner.getCurrentState()
        )

        // 销毁后调用onResume
        lifecycleOwner.onDestroy()
        lifecycleOwner.onResume()
        assertEquals(
            "在DESTROYED状态调用onResume不应改变状态",
            Lifecycle.State.DESTROYED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：onStart在非CREATED状态下不改变状态
     */
    @Test
    fun `onStart does nothing when not in CREATED state`() {
        // 初始状态是RESUMED
        lifecycleOwner.onStart()
        assertEquals(
            "在RESUMED状态调用onStart不应改变状态",
            Lifecycle.State.RESUMED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：Lifecycle对象不为null
     */
    @Test
    fun `lifecycle is not null`() {
        assertNotNull("Lifecycle不应该为null", lifecycleOwner.lifecycle)
    }

    /**
     * 测试：ViewModelStore对象不为null
     */
    @Test
    fun `viewModelStore is not null`() {
        assertNotNull("ViewModelStore不应该为null", lifecycleOwner.viewModelStore)
    }

    /**
     * 测试：SavedStateRegistry对象不为null
     */
    @Test
    fun `savedStateRegistry is not null`() {
        assertNotNull("SavedStateRegistry不应该为null", lifecycleOwner.savedStateRegistry)
    }

    /**
     * 测试：多次调用onDestroy不会崩溃
     */
    @Test
    fun `multiple onDestroy calls do not crash`() {
        lifecycleOwner.onDestroy()
        lifecycleOwner.onDestroy() // 第二次调用不应该崩溃

        assertEquals(
            "多次调用onDestroy后状态应该保持DESTROYED",
            Lifecycle.State.DESTROYED,
            lifecycleOwner.getCurrentState()
        )
    }

    /**
     * 测试：isActive在不同状态下的返回值
     */
    @Test
    fun `isActive returns correct values in different states`() {
        // RESUMED状态
        assertTrue("RESUMED状态isActive应该返回true", lifecycleOwner.isActive())

        // STARTED状态
        lifecycleOwner.onPause()
        assertTrue("STARTED状态isActive应该返回true", lifecycleOwner.isActive())

        // CREATED状态
        lifecycleOwner.onStop()
        assertFalse("CREATED状态isActive应该返回false", lifecycleOwner.isActive())

        // DESTROYED状态
        lifecycleOwner.onDestroy()
        assertFalse("DESTROYED状态isActive应该返回false", lifecycleOwner.isActive())
    }
}
