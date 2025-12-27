package com.taskwizard.android.data.history

import android.app.Application
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.gson.Gson
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.Message
import com.taskwizard.android.data.MessageItem
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * TaskHistoryDao 单元测试
 *
 * 测试范围：
 * 1. CRUD操作（插入、查询、更新、删除）
 * 2. 查询操作（按状态、搜索、日期范围）
 * 3. 统计查询
 * 4. 批量删除操作
 */
@RunWith(AndroidJUnit4::class)
class TaskHistoryDaoTest {

    private lateinit var database: TaskHistoryDatabase
    private lateinit var dao: TaskHistoryDao
    private val gson = Gson()

    @Before
    fun setup() {
        val context = ApplicationProvider.getApplicationContext<Application>()
        database = Room.inMemoryDatabaseBuilder(context, TaskHistoryDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        dao = database.historyDao()
    }

    @After
    fun tearDown() {
        database.close()
    }

    // ==================== Insert Tests ====================

    @Test
    fun insertTask_shouldReturnValidId() = runTest {
        // Given
        val task = createTestTask(description = "Test task")

        // When
        val id = dao.insertTask(task)

        // Then
        assertTrue(id > 0, "Inserted task should have valid ID")
    }

    @Test
    fun insertMultipleTasks_shouldReturnMultipleIds() = runTest {
        // Given
        val tasks = listOf(
            createTestTask(description = "Task 1"),
            createTestTask(description = "Task 2"),
            createTestTask(description = "Task 3")
        )

        // When
        val ids = dao.insertTasks(tasks)

        // Then
        assertEquals(3, ids.size, "Should return 3 IDs")
        assertTrue(ids.all { it > 0 }, "All IDs should be valid")
    }

    // ==================== Query Tests ====================

    @Test
    fun getTaskById_shouldReturnCorrectTask() = runTest {
        // Given
        val task = createTestTask(description = "Test task")
        val id = dao.insertTask(task)

        // When
        val found = dao.getTaskById(id)

        // Then
        assertNotNull(found, "Task should be found")
        assertEquals("Test task", found.taskDescription)
    }

    @Test
    fun getTaskById_withInvalidId_shouldReturnNull() = runTest {
        // When
        val found = dao.getTaskById(9999)

        // Then
        assertNull(found, "Should return null for invalid ID")
    }

    @Test
    fun getRecentTasks_shouldReturnTasksOrderedByStartTime() = runTest {
        // Given
        val task1 = createTestTask(description = "Task 1", startTime = 1000)
        val task2 = createTestTask(description = "Task 2", startTime = 3000)
        val task3 = createTestTask(description = "Task 3", startTime = 2000)

        dao.insertTask(task1)
        dao.insertTask(task2)
        dao.insertTask(task3)

        // When
        val tasks = dao.getRecentTasks(10).first()

        // Then
        assertEquals(3, tasks.size, "Should return all 3 tasks")
        assertEquals("Task 2", tasks[0].taskDescription, "First task should be most recent")
        assertEquals("Task 3", tasks[1].taskDescription)
        assertEquals("Task 1", tasks[2].taskDescription, "Last task should be oldest")
    }

    @Test
    fun getRecentTasks_withLimit_shouldReturnOnlyLimitedTasks() = runTest {
        // Given
        repeat(10) { i ->
            dao.insertTask(createTestTask(description = "Task $i", startTime = i.toLong()))
        }

        // When
        val tasks = dao.getRecentTasks(5).first()

        // Then
        assertEquals(5, tasks.size, "Should return only 5 tasks")
    }

    @Test
    fun getTasksByStatus_shouldReturnOnlyMatchingTasks() = runTest {
        // Given
        dao.insertTask(createTestTask(description = "Task 1", status = TaskStatus.COMPLETED.name))
        dao.insertTask(createTestTask(description = "Task 2", status = TaskStatus.FAILED.name))
        dao.insertTask(createTestTask(description = "Task 3", status = TaskStatus.COMPLETED.name))

        // When
        val completedTasks = dao.getTasksByStatus(TaskStatus.COMPLETED.name).first()
        val failedTasks = dao.getTasksByStatus(TaskStatus.FAILED.name).first()

        // Then
        assertEquals(2, completedTasks.size, "Should have 2 completed tasks")
        assertEquals(1, failedTasks.size, "Should have 1 failed task")
        assertTrue(completedTasks.all { it.status == TaskStatus.COMPLETED.name })
        assertTrue(failedTasks.all { it.status == TaskStatus.FAILED.name })
    }

    @Test
    fun searchTasks_shouldReturnMatchingTasks() = runTest {
        // Given
        dao.insertTask(createTestTask(description = "Open YouTube app"))
        dao.insertTask(createTestTask(description = "Send email to John"))
        dao.insertTask(createTestTask(description = "Open calendar"))

        // When
        val results = dao.searchTasks("Open").first()

        // Then
        assertEquals(2, results.size, "Should find 2 tasks containing 'Open'")
        assertTrue(results.all { it.taskDescription.contains("Open") })
    }

    @Test
    fun getTasksInDateRange_shouldReturnOnlyTasksInRange() = runTest {
        // Given
        val id1 = dao.insertTask(createTestTask(description = "Task 1", startTime = 1000))
        val id2 = dao.insertTask(createTestTask(description = "Task 2", startTime = 2000))
        val id3 = dao.insertTask(createTestTask(description = "Task 3", startTime = 3000))
        val id4 = dao.insertTask(createTestTask(description = "Task 4", startTime = 4000))

        // The query filters by startTime, so tasks with startTime in range [1500, 3500] should match
        // Task 1: startTime = 1000 (out of range)
        // Task 2: startTime = 2000 (in range)
        // Task 3: startTime = 3000 (in range)
        // Task 4: startTime = 4000 (out of range)

        // When
        val tasks = dao.getTasksInDateRange(1500, 3500).first()

        // Then
        assertEquals(2, tasks.size, "Should find 2 tasks in range")
        // Results are ordered by startTime DESC
        assertEquals("Task 3", tasks[0].taskDescription) // startTime=3000
        assertEquals("Task 2", tasks[1].taskDescription) // startTime=2000
    }

    // ==================== Statistics Tests ====================

    @Test
    fun getTaskCount_shouldReturnCorrectCount() = runTest {
        // Given
        repeat(5) { dao.insertTask(createTestTask()) }

        // When
        val count = dao.getTaskCount()

        // Then
        assertEquals(5, count, "Should count 5 tasks")
    }

    @Test
    fun getCompletedTaskCount_shouldReturnOnlyCompleted() = runTest {
        // Given
        dao.insertTask(createTestTask(status = TaskStatus.COMPLETED.name))
        dao.insertTask(createTestTask(status = TaskStatus.COMPLETED.name))
        dao.insertTask(createTestTask(status = TaskStatus.FAILED.name))

        // When
        val count = dao.getCompletedTaskCount()

        // Then
        assertEquals(2, count, "Should count 2 completed tasks")
    }

    @Test
    fun getFailedTaskCount_shouldReturnOnlyFailed() = runTest {
        // Given
        dao.insertTask(createTestTask(status = TaskStatus.FAILED.name))
        dao.insertTask(createTestTask(status = TaskStatus.FAILED.name))
        dao.insertTask(createTestTask(status = TaskStatus.COMPLETED.name))

        // When
        val count = dao.getFailedTaskCount()

        // Then
        assertEquals(2, count, "Should count 2 failed tasks")
    }

    @Test
    fun getCancelledTaskCount_shouldReturnOnlyCancelled() = runTest {
        // Given
        dao.insertTask(createTestTask(status = TaskStatus.CANCELLED.name))
        dao.insertTask(createTestTask(status = TaskStatus.CANCELLED.name))
        dao.insertTask(createTestTask(status = TaskStatus.COMPLETED.name))

        // When
        val count = dao.getCancelledTaskCount()

        // Then
        assertEquals(2, count, "Should count 2 cancelled tasks")
    }

    // ==================== Update Tests ====================

    @Test
    fun updateTask_shouldModifyExistingTask() = runTest {
        // Given
        val task = createTestTask(description = "Original description")
        val id = dao.insertTask(task)

        // When
        val updated = task.copy(
            id = id,
            taskDescription = "Updated description",
            status = TaskStatus.COMPLETED.name
        )
        dao.updateTask(updated)

        // Then
        val found = dao.getTaskById(id)
        assertNotNull(found)
        assertEquals("Updated description", found.taskDescription)
        assertEquals(TaskStatus.COMPLETED.name, found.status)
    }

    @Test
    fun updateTaskStatus_shouldUpdateOnlyStatus() = runTest {
        // Given
        val task = createTestTask(status = TaskStatus.PENDING.name)
        val id = dao.insertTask(task)

        // When
        dao.updateTaskStatus(id, TaskStatus.RUNNING.name, "Task started")

        // Then
        val found = dao.getTaskById(id)
        assertNotNull(found)
        assertEquals(TaskStatus.RUNNING.name, found.status)
        assertEquals("Task started", found.statusMessage)
    }

    @Test
    fun updateTaskCompletion_shouldUpdateCompletionFields() = runTest {
        // Given
        val task = createTestTask()
        val id = dao.insertTask(task)

        // When
        dao.updateTaskCompletion(id, endTime = 5000, durationMs = 4000, stepCount = 10)

        // Then
        val found = dao.getTaskById(id)
        assertNotNull(found)
        assertEquals(5000, found.endTime)
        assertEquals(4000, found.durationMs)
        assertEquals(10, found.stepCount)
    }

    // ==================== Delete Tests ====================

    @Test
    fun deleteTask_shouldRemoveTask() = runTest {
        // Given
        val task = createTestTask()
        val id = dao.insertTask(task)

        // When
        dao.deleteTask(task.copy(id = id))

        // Then
        val found = dao.getTaskById(id)
        assertNull(found, "Task should be deleted")
    }

    @Test
    fun deleteTaskById_shouldRemoveTask() = runTest {
        // Given
        val id = dao.insertTask(createTestTask())

        // When
        dao.deleteTaskById(id)

        // Then
        val found = dao.getTaskById(id)
        assertNull(found, "Task should be deleted")
    }

    @Test
    fun clearAllHistory_shouldRemoveAllTasks() = runTest {
        // Given
        repeat(5) { dao.insertTask(createTestTask()) }

        // When
        dao.clearAllHistory()

        // Then
        val count = dao.getTaskCount()
        assertEquals(0, count, "All tasks should be deleted")
    }

    @Test
    fun deleteTasksOlderThan_shouldRemoveOnlyOldTasks() = runTest {
        // Given
        dao.insertTask(createTestTask(startTime = 1000))
        dao.insertTask(createTestTask(startTime = 2000))
        dao.insertTask(createTestTask(startTime = 3000))
        dao.insertTask(createTestTask(startTime = 4000))

        // When - delete tasks older than 2500
        val deleted = dao.deleteTasksOlderThan(2500)

        // Then
        assertEquals(2, deleted, "Should delete 2 tasks")
        val remaining = dao.getAllTasks().first()
        assertEquals(2, remaining.size, "Should have 2 tasks remaining")
    }

    @Test
    fun deleteTasksByStatus_shouldRemoveOnlyMatchingTasks() = runTest {
        // Given
        dao.insertTask(createTestTask(status = TaskStatus.COMPLETED.name))
        dao.insertTask(createTestTask(status = TaskStatus.FAILED.name))
        dao.insertTask(createTestTask(status = TaskStatus.COMPLETED.name))

        // When
        val deleted = dao.deleteTasksByStatus(TaskStatus.COMPLETED.name)

        // Then
        assertEquals(2, deleted, "Should delete 2 completed tasks")
        val remaining = dao.getAllTasks().first()
        assertEquals(1, remaining.size, "Should have 1 task remaining")
        assertEquals(TaskStatus.FAILED.name, remaining[0].status)
    }

    // ==================== Helper Methods ====================

    private fun createTestTask(
        description: String = "Test task",
        status: String = TaskStatus.PENDING.name,
        startTime: Long = System.currentTimeMillis()
    ) = TaskHistoryEntity(
        taskDescription = description,
        model = "test-model",
        startTime = startTime,
        status = status,
        messagesJson = gson.toJson(emptyList<MessageItem>()),
        apiContextMessagesJson = gson.toJson(emptyList<Message>()),
        actionsJson = gson.toJson(emptyList<Action>()),
        errorMessagesJson = gson.toJson(emptyList<String>())
    )
}
