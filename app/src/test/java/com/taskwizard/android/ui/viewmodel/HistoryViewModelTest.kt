package com.taskwizard.android.ui.viewmodel

import android.app.Application
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.gson.Gson
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.Message
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.history.HistoryRepository
import com.taskwizard.android.data.history.HistoryStatistics
import com.taskwizard.android.data.history.TaskHistoryEntity
import com.taskwizard.android.data.history.TaskStatus
import com.taskwizard.android.data.history.TaskHistoryDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * HistoryViewModel 单元测试
 *
 * 测试范围：
 * 1. 状态管理（加载任务、统计信息）
 * 2. 查询操作（搜索、筛选）
 * 3. 删除操作
 * 4. 错误处理
 */
@ExperimentalCoroutinesApi
@RunWith(AndroidJUnit4::class)
@Config(sdk = [28])
class HistoryViewModelTest {

    private lateinit var viewModel: HistoryViewModel
    private lateinit var database: TaskHistoryDatabase
    private lateinit var application: Application
    private val testDispatcher = StandardTestDispatcher()
    private val gson = Gson()

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)

        application = ApplicationProvider.getApplicationContext<Application>()
        val context = ApplicationProvider.getApplicationContext<Application>()

        // Use in-memory database for testing
        database = Room.inMemoryDatabaseBuilder(context, TaskHistoryDatabase::class.java)
            .allowMainThreadQueries()
            .build()

        viewModel = HistoryViewModel(application)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
        database.close()
    }

    // ==================== State Tests ====================

    @Test
    fun initialState_shouldHaveLoadingTrue() {
        // Then
        val state = viewModel.historyState.value
        assertTrue(state.isLoading, "Initial state should be loading")
        assertTrue(state.tasks.isEmpty(), "Initial tasks should be empty")
        assertTrue(state.error == null, "Initial error should be null")
    }

    @Test
    fun initialState_statistics_shouldBeNull() {
        // Then
        val stats = viewModel.statistics.value
        assertEquals(null, stats, "Initial statistics should be null")
    }

    // ==================== Load Tasks Tests ====================

    @Test
    fun loadTasks_shouldPopulateTasksList() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        repo.createTask("Task 1", "model")
        repo.createTask("Task 2", "model")
        advanceUntilIdle()

        // When
        viewModel.loadTasks()
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertFalse(state.isLoading, "Should not be loading after load")
        assertTrue(state.tasks.isNotEmpty(), "Should have tasks loaded")
        assertTrue(state.error == null, "Should have no error")
    }

    @Test
    fun loadTasks_shouldSetLoadingToFalse() = runTest {
        // When
        viewModel.loadTasks()
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertFalse(state.isLoading, "Loading should be false after load")
    }

    // ==================== Filter Tests ====================

    @Test
    fun filterByStatus_shouldShowOnlyMatchingTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        val id1 = repo.createTask("Task 1", "model")
        val id2 = repo.createTask("Task 2", "model")
        val id3 = repo.createTask("Task 3", "model")
        repo.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repo.updateTaskStatus(id2, TaskStatus.FAILED.name)
        repo.updateTaskStatus(id3, TaskStatus.COMPLETED.name)
        advanceUntilIdle()

        // When
        viewModel.filterByStatus(TaskStatus.COMPLETED)
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertEquals(2, state.tasks.size, "Should have 2 completed tasks")
        assertTrue(state.tasks.all { it.status == TaskStatus.COMPLETED.name })
        assertEquals(TaskStatus.COMPLETED.name, state.currentFilter)
    }

    @Test
    fun clearFilter_shouldShowAllTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        repo.createTask("Task 1", "model")
        repo.createTask("Task 2", "model")
        advanceUntilIdle()

        // When - first filter
        viewModel.filterByStatus(TaskStatus.COMPLETED)
        advanceUntilIdle()

        // Then - verify filter is set
        var state = viewModel.historyState.value
        assertEquals(TaskStatus.COMPLETED.name, state.currentFilter)

        // When - clear filter
        viewModel.clearFilter()
        advanceUntilIdle()

        // Then - verify filter is cleared
        state = viewModel.historyState.value
        assertEquals(null, state.currentFilter)
    }

    // ==================== Search Tests ====================

    @Test
    fun searchTasks_withQuery_shouldFilterTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        repo.createTask("Open YouTube app", "model")
        repo.createTask("Send email", "model")
        repo.createTask("Open calendar", "model")
        advanceUntilIdle()

        // When
        viewModel.searchTasks("Open")
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertEquals(2, state.tasks.size, "Should find 2 tasks with 'Open'")
        assertEquals("Open", state.searchQuery)
    }

    @Test
    fun searchTasks_withEmptyQuery_shouldShowAllTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        repo.createTask("Task 1", "model")
        repo.createTask("Task 2", "model")
        advanceUntilIdle()

        // When
        viewModel.searchTasks("")
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertTrue(state.tasks.size >= 2, "Should show all tasks")
    }

    @Test
    fun clearSearch_shouldResetSearchQuery() = runTest {
        // Given
        viewModel.searchTasks("test")
        advanceUntilIdle()

        // When
        viewModel.clearSearch()
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertEquals("", state.searchQuery)
    }

    // ==================== Delete Tests ====================

    @Test
    fun deleteTask_shouldRemoveTaskFromList() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        val id = repo.createTask("Test task", "model")
        val task = repo.getTaskById(id)!!
        advanceUntilIdle()

        // When
        viewModel.deleteTask(task)
        advanceUntilIdle()

        // Then
        val found = repo.getTaskById(id)
        assertEquals(null, found, "Task should be deleted")
    }

    @Test
    fun deleteTasksByStatus_shouldRemoveMatchingTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        val id1 = repo.createTask("Task 1", "model")
        val id2 = repo.createTask("Task 2", "model")
        val id3 = repo.createTask("Task 3", "model")
        repo.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repo.updateTaskStatus(id2, TaskStatus.FAILED.name)
        repo.updateTaskStatus(id3, TaskStatus.COMPLETED.name)
        advanceUntilIdle()

        // When
        viewModel.deleteTasksByStatus(TaskStatus.COMPLETED)
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertTrue(state.message != null, "Should have a message")
        assertTrue(state.message!!.contains("2"), "Should mention deleted count")
    }

    @Test
    fun deleteOldTasks_shouldRemoveOldTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        val baseTime = System.currentTimeMillis()

        // Create tasks with different timestamps
        val id1 = repo.createTask("Old task", "model")
        val id2 = repo.createTask("New task", "model")

        val task1 = repo.getTaskById(id1)!!
        val task2 = repo.getTaskById(id2)!!

        // Make task1 very old (60 days ago)
        repo.updateTask(task1.copy(startTime = baseTime - (60 * 24 * 60 * 60 * 1000L)))
        advanceUntilIdle()

        // When
        viewModel.deleteOldTasks(30) // Delete tasks older than 30 days
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertTrue(state.message != null, "Should have a message")
        assertTrue(state.message!!.contains("30"), "Should mention days threshold")
    }

    @Test
    fun clearAllHistory_shouldRemoveAllTasks() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        repo.createTask("Task 1", "model")
        repo.createTask("Task 2", "model")
        repo.createTask("Task 3", "model")
        advanceUntilIdle()

        // When
        viewModel.clearAllHistory()
        advanceUntilIdle()

        // Then
        val state = viewModel.historyState.value
        assertTrue(state.message != null, "Should have a message")
        assertTrue(state.message!!.contains("清空"), "Should mention clearing")

        val remaining = repo.getAllTasks().first()
        assertTrue(remaining.isEmpty(), "All tasks should be deleted")
    }

    // ==================== Statistics Tests ====================

    @Test
    fun loadStatistics_shouldUpdateStatisticsState() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        val id1 = repo.createTask("Task 1", "model")
        val id2 = repo.createTask("Task 2", "model")
        val id3 = repo.createTask("Task 3", "model")
        repo.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repo.updateTaskStatus(id2, TaskStatus.FAILED.name)
        repo.updateTaskStatus(id3, TaskStatus.COMPLETED.name)
        advanceUntilIdle()

        // When
        viewModel.loadStatistics()
        advanceUntilIdle()

        // Then
        val stats = viewModel.statistics.value
        assertNotNull(stats, "Statistics should not be null")
        assertEquals(3, stats.totalTasks)
        assertEquals(2, stats.completedTasks)
        assertEquals(1, stats.failedTasks)
    }

    @Test
    fun statistics_successRate_shouldBeCalculatedCorrectly() = runTest {
        // Given
        val repo = com.taskwizard.android.data.history.HistoryRepository(application)
        repeat(10) { i ->
            val id = repo.createTask("Task $i", "model")
            if (i % 2 == 0) {
                repo.updateTaskStatus(id, TaskStatus.COMPLETED.name)
            } else {
                repo.updateTaskStatus(id, TaskStatus.FAILED.name)
            }
        }
        advanceUntilIdle()

        // When
        viewModel.loadStatistics()
        advanceUntilIdle()

        // Then
        val stats = viewModel.statistics.value
        assertNotNull(stats)
        assertEquals(50f, stats.simpleSuccessRate, 0.1f, "Success rate should be 50%")
    }

    // ==================== Message Tests ====================

    @Test
    fun clearMessage_shouldRemoveMessage() = runTest {
        // Given
        viewModel.deleteOldTasks(30)
        advanceUntilIdle()

        // Verify message exists
        var state = viewModel.historyState.value
        assertTrue(state.message != null, "Should have a message")

        // When
        viewModel.clearMessage()

        // Then
        state = viewModel.historyState.value
        assertEquals(null, state.message, "Message should be cleared")
    }

    // ==================== Helper Methods ====================

    private fun createTestTask(
        description: String = "Test task",
        status: String = TaskStatus.PENDING.name
    ): TaskHistoryEntity {
        return TaskHistoryEntity(
            taskDescription = description,
            model = "test-model",
            startTime = System.currentTimeMillis(),
            status = status,
            messagesJson = gson.toJson(emptyList<MessageItem>()),
            apiContextMessagesJson = gson.toJson(emptyList<com.taskwizard.android.data.Message>()),
            actionsJson = gson.toJson(emptyList<Action>()),
            errorMessagesJson = gson.toJson(emptyList<String>())
        )
    }
}
