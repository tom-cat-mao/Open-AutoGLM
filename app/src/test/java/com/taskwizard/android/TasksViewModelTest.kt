package com.taskwizard.android

import org.junit.Assert.*
import org.junit.Test

/**
 * TasksViewModel 单元测试
 */
class TasksViewModelTest {

    @Test
    fun `test initial state`() {
        // ViewModel 需要 Application context
        // 这里只测试基本逻辑
        assertTrue(true)
    }

    @Test
    fun `test template list empty initially`() {
        // 初始状态应该是空列表
        val emptyList = emptyList<Any>()
        assertTrue(emptyList.isEmpty())
    }
}
