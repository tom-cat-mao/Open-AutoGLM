package com.example.autoglm.service

import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner

/**
 * ServiceLifecycleOwner - Service生命周期管理器
 *
 * 为Service提供完整的Lifecycle、ViewModelStore和SavedStateRegistry支持
 * 使得ComposeView可以在Service中正常工作
 *
 * 关键特性：
 * - 实现LifecycleOwner：提供生命周期事件
 * - 实现ViewModelStoreOwner：支持ViewModel
 * - 实现SavedStateRegistryOwner：支持状态保存和恢复
 *
 * 使用方式：
 * ```kotlin
 * val lifecycleOwner = ServiceLifecycleOwner()
 * composeView.setViewTreeLifecycleOwner(lifecycleOwner)
 * composeView.setViewTreeViewModelStoreOwner(lifecycleOwner)
 * composeView.setViewTreeSavedStateRegistryOwner(lifecycleOwner)
 * ```
 */
class ServiceLifecycleOwner : LifecycleOwner, ViewModelStoreOwner, SavedStateRegistryOwner {

    // ==================== Lifecycle管理 ====================

    private val lifecycleRegistry = LifecycleRegistry(this)

    override val lifecycle: Lifecycle
        get() = lifecycleRegistry

    // ==================== SavedStateRegistry管理 ====================

    private val savedStateRegistryController = SavedStateRegistryController.create(this)

    override val savedStateRegistry: SavedStateRegistry
        get() = savedStateRegistryController.savedStateRegistry

    // ==================== ViewModelStore管理 ====================

    private val store = ViewModelStore()

    override val viewModelStore: ViewModelStore
        get() = store

    // ==================== 初始化 ====================

    init {
        // 按照正确的顺序初始化生命周期
        // 1. 恢复SavedState
        savedStateRegistryController.performRestore(null)

        // 2. 触发生命周期事件
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
    }

    // ==================== 生命周期控制方法 ====================

    /**
     * 暂停生命周期
     * 当Service进入后台时调用
     */
    fun onPause() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
    }

    /**
     * 停止生命周期
     * 当Service即将停止时调用
     */
    fun onStop() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
    }

    /**
     * 销毁生命周期
     * 当Service销毁时调用，清理所有资源
     *
     * 重要：必须在Service.onDestroy()中调用此方法
     */
    fun onDestroy() {
        // 按照正确的顺序销毁
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)

        // 清理ViewModelStore
        store.clear()
    }

    /**
     * 恢复生命周期
     * 当Service从后台恢复时调用
     */
    fun onResume() {
        if (lifecycleRegistry.currentState == Lifecycle.State.STARTED) {
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        }
    }

    /**
     * 启动生命周期
     * 当Service启动时调用
     */
    fun onStart() {
        if (lifecycleRegistry.currentState == Lifecycle.State.CREATED) {
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
        }
    }

    // ==================== 状态查询 ====================

    /**
     * 获取当前生命周期状态
     */
    fun getCurrentState(): Lifecycle.State {
        return lifecycleRegistry.currentState
    }

    /**
     * 检查是否已初始化
     */
    fun isInitialized(): Boolean {
        return lifecycleRegistry.currentState.isAtLeast(Lifecycle.State.CREATED)
    }

    /**
     * 检查是否处于活跃状态
     */
    fun isActive(): Boolean {
        return lifecycleRegistry.currentState.isAtLeast(Lifecycle.State.STARTED)
    }

    /**
     * 检查是否已销毁
     */
    fun isDestroyed(): Boolean {
        return lifecycleRegistry.currentState == Lifecycle.State.DESTROYED
    }
}
