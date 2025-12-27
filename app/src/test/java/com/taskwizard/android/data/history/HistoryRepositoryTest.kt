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
import kotlin.test.assertTrue

/**
 * HistoryRepository 单元测试
 *
 * 测试范围：
 * 1. CRUD操作
 * 2. 查询操作（搜索、筛选）
 * 3. 统计查询
 * 4. 批量删除操作
 */
@RunWith(AndroidJUnit4::class)
class HistoryRepositoryTest {

    private lateinit var database: TaskHistoryDatabase
    private lateinit var repository: TestHistoryRepository
    private val gson = Gson()

    @Before
    fun setup() {
        val context = ApplicationProvider.getApplicationContext<Application>()
        database = Room.inMemoryDatabaseBuilder(context, TaskHistoryDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        // Use test repository that uses the test database
        repository = TestHistoryRepository(database)
    }

    @After
    fun tearDown() {
        database.close()
    }

    // ==================== Create Tests ====================

    @Test
    fun createTask_shouldReturnValidId() = runTest {
        // When
        val id = repository.createTask("Test task", "test-model")

        // Then
        assertTrue(id > 0, "Created task should have valid ID")
    }

    @Test
    fun createTask_shouldInitializeWithPendingStatus() = runTest {
        // When
        val id = repository.createTask("Test task", "test-model")

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        assertEquals(TaskStatus.PENDING.name, task.status)
        assertEquals("Test task", task.taskDescription)
        assertEquals("test-model", task.model)
        assertEquals(0, task.stepCount)
    }

    // ==================== Query Tests ====================

    @Test
    fun getTaskById_shouldReturnCorrectTask() = runTest {
        // Given
        val id = repository.createTask("Test task", "test-model")

        // When
        val task = repository.getTaskById(id)

        // Then
        assertNotNull(task)
        assertEquals("Test task", task.taskDescription)
    }

    @Test
    fun getRecentTasks_shouldReturnTasksOrderedByTime() = runTest {
        // Given - create tasks with different start times
        val id1 = repository.createTask("Task 1", "model")
        Thread.sleep(10) // Small delay to ensure different timestamps
        val id2 = repository.createTask("Task 2", "model")
        Thread.sleep(10)
        val id3 = repository.createTask("Task 3", "model")

        // When
        val tasks = repository.getRecentTasks(10).first()

        // Then
        assertEquals(3, tasks.size)
        // Tasks should be ordered by start time (descending)
        assertEquals("Task 3", tasks[0].taskDescription)
        assertEquals("Task 2", tasks[1].taskDescription)
        assertEquals("Task 1", tasks[2].taskDescription)
    }

    @Test
    fun getTasksByStatus_shouldReturnOnlyMatchingTasks() = runTest {
        // Given
        val id1 = repository.createTask("Task 1", "model")
        val id2 = repository.createTask("Task 2", "model")
        val id3 = repository.createTask("Task 3", "model")

        repository.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repository.updateTaskStatus(id2, TaskStatus.FAILED.name)
        repository.updateTaskStatus(id3, TaskStatus.COMPLETED.name)

        // When
        val completedTasks = repository.getTasksByStatus(TaskStatus.COMPLETED.name).first()
        val failedTasks = repository.getTasksByStatus(TaskStatus.FAILED.name).first()

        // Then
        assertEquals(2, completedTasks.size)
        assertEquals(1, failedTasks.size)
    }

    @Test
    fun searchTasks_shouldReturnMatchingTasks() = runTest {
        // Given
        repository.createTask("Open YouTube app", "model")
        repository.createTask("Send email to John", "model")
        repository.createTask("Open calendar", "model")

        // When
        val results = repository.searchTasks("Open").first()

        // Then
        assertEquals(2, results.size)
        assertTrue(results.all { it.taskDescription.contains("Open") })
    }

    @Test
    fun getTasksInDateRange_shouldReturnOnlyTasksInRange() = runTest {
        // Given
        val baseTime = System.currentTimeMillis()
        val id1 = repository.createTask("Task 1", "model")
        val id2 = repository.createTask("Task 2", "model")
        val id3 = repository.createTask("Task 3", "model")

        // Update times manually by updating the tasks
        val task1 = repository.getTaskById(id1)!!
        val task2 = repository.getTaskById(id2)!!
        val task3 = repository.getTaskById(id3)!!

        repository.updateTask(task1.copy(startTime = baseTime - 5000))
        repository.updateTask(task2.copy(startTime = baseTime))
        repository.updateTask(task3.copy(startTime = baseTime + 5000))

        // When - search in range [baseTime - 1000, baseTime + 1000]
        val tasks = repository.getTasksInDateRange(baseTime - 1000, baseTime + 1000).first()

        // Then
        assertEquals(1, tasks.size)
        assertEquals("Task 2", tasks[0].taskDescription)
    }

    // ==================== Statistics Tests ====================

    @Test
    fun getStatistics_shouldReturnCorrectCounts() = runTest {
        // Given
        val id1 = repository.createTask("Task 1", "model")
        val id2 = repository.createTask("Task 2", "model")
        val id3 = repository.createTask("Task 3", "model")
        val id4 = repository.createTask("Task 4", "model")

        repository.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repository.updateTaskStatus(id2, TaskStatus.FAILED.name)
        repository.updateTaskStatus(id3, TaskStatus.COMPLETED.name)
        repository.updateTaskStatus(id4, TaskStatus.CANCELLED.name)

        // When
        val stats = repository.getStatistics()

        // Then
        assertEquals(4, stats.totalTasks)
        assertEquals(2, stats.completedTasks)
        assertEquals(1, stats.failedTasks)
        assertEquals(1, stats.cancelledTasks)
    }

    @Test
    fun getStatistics_successRate_shouldBeCalculatedCorrectly() = runTest {
        // Given
        val id1 = repository.createTask("Task 1", "model")
        val id2 = repository.createTask("Task 2", "model")
        val id3 = repository.createTask("Task 3", "model")
        val id4 = repository.createTask("Task 4", "model")

        repository.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repository.updateTaskStatus(id2, TaskStatus.COMPLETED.name)
        repository.updateTaskStatus(id3, TaskStatus.FAILED.name)
        repository.updateTaskStatus(id4, TaskStatus.FAILED.name)

        // When
        val stats = repository.getStatistics()

        // Then
        assertEquals(50f, stats.simpleSuccessRate, 0.1f)
    }

    // ==================== Update Tests ====================

    @Test
    fun updateTaskStatus_shouldUpdateStatus() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")

        // When
        repository.updateTaskStatus(id, TaskStatus.RUNNING.name, "Task started")

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        assertEquals(TaskStatus.RUNNING.name, task.status)
        assertEquals("Task started", task.statusMessage)
    }

    @Test
    fun updateTaskCompletion_shouldUpdateCompletionFields() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")

        // When
        repository.updateTaskCompletion(id, 5000, 4000, 10)

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        assertEquals(5000, task.endTime)
        assertEquals(4000, task.durationMs)
        assertEquals(10, task.stepCount)
    }

    @Test
    fun updateTaskStepCount_shouldUpdateStepCount() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")

        // When
        repository.updateTaskStepCount(id, 15)

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        assertEquals(15, task.stepCount)
    }

    @Test
    fun updateTaskActions_shouldUpdateActions() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")
        val actions = listOf(
            Action(action = "tap", location = listOf(100, 200)),
            Action(action = "swipe", location = listOf(300, 400))
        )

        // When
        repository.updateTaskActions(id, actions)

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        // Actions are stored as JSON, verify the JSON contains our actions
        assertTrue(task.actionsJson.contains("tap"))
        assertTrue(task.actionsJson.contains("swipe"))
    }

    @Test
    fun updateTaskMessages_shouldUpdateMessages() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")
        val messages = listOf(
            MessageItem.ThinkMessage(content = "Thinking 1"),
            MessageItem.ThinkMessage(content = "Thinking 2")
        )

        // When
        repository.updateTaskMessages(id, messages)

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        // Messages are stored as JSON, verify the JSON contains our messages
        assertTrue(task.messagesJson.contains("Thinking 1"))
        assertTrue(task.messagesJson.contains("Thinking 2"))
    }

    @Test
    fun addErrorMessage_shouldAppendToErrors() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")

        // When
        repository.addErrorMessage(id, "Error 1")
        repository.addErrorMessage(id, "Error 2")

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        // Errors are stored as JSON, verify the JSON contains our errors
        assertTrue(task.errorMessagesJson.contains("Error 1"))
        assertTrue(task.errorMessagesJson.contains("Error 2"))
    }

    // ==================== Delete Tests ====================

    @Test
    fun deleteTask_shouldRemoveTask() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")
        val task = repository.getTaskById(id)!!

        // When
        repository.deleteTask(task)

        // Then
        val found = repository.getTaskById(id)
        assertEquals(null, found)
    }

    @Test
    fun deleteTaskById_shouldRemoveTask() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")

        // When
        repository.deleteTaskById(id)

        // Then
        val found = repository.getTaskById(id)
        assertEquals(null, found)
    }

    @Test
    fun clearAll_shouldRemoveAllTasks() = runTest {
        // Given
        repeat(5) { repository.createTask("Task $it", "model") }

        // When
        repository.clearAll()

        // Then
        val tasks = repository.getAllTasks().first()
        assertTrue(tasks.isEmpty())
    }

    @Test
    fun deleteOldTasks_shouldRemoveOnlyOldTasks() = runTest {
        // Given
        val baseTime = System.currentTimeMillis()
        val id1 = repository.createTask("Old task 1", "model")
        val id2 = repository.createTask("Old task 2", "model")
        val id3 = repository.createTask("Recent task", "model")

        // Manually set times - use 31 days for old tasks (more than 30)
        val task1 = repository.getTaskById(id1)!!
        val task2 = repository.getTaskById(id2)!!
        val task3 = repository.getTaskById(id3)!!

        repository.updateTask(task1.copy(startTime = baseTime - (31 * 24 * 60 * 60 * 1000L))) // 31 days ago
        repository.updateTask(task2.copy(startTime = baseTime - (32 * 24 * 60 * 60 * 1000L))) // 32 days ago
        repository.updateTask(task3.copy(startTime = baseTime - (1 * 24 * 60 * 60 * 1000L))) // 1 day ago

        // When - delete tasks older than 30 days
        val deleted = repository.deleteOldTasks(30)

        // Then
        assertEquals(2, deleted, "Should delete 2 old tasks")
        val remaining = repository.getAllTasks().first()
        assertEquals(1, remaining.size)
        assertEquals("Recent task", remaining[0].taskDescription)
    }

    @Test
    fun deleteTasksByStatus_shouldRemoveOnlyMatchingTasks() = runTest {
        // Given
        val id1 = repository.createTask("Task 1", "model")
        val id2 = repository.createTask("Task 2", "model")
        val id3 = repository.createTask("Task 3", "model")

        repository.updateTaskStatus(id1, TaskStatus.COMPLETED.name)
        repository.updateTaskStatus(id2, TaskStatus.FAILED.name)
        repository.updateTaskStatus(id3, TaskStatus.COMPLETED.name)

        // When
        val deleted = repository.deleteTasksByStatus(TaskStatus.COMPLETED.name)

        // Then
        assertEquals(2, deleted)
        val remaining = repository.getAllTasks().first()
        assertEquals(1, remaining.size)
        assertEquals(TaskStatus.FAILED.name, remaining[0].status)
    }

    // ==================== API Context Messages Tests ====================

    @Test
    fun updateApiContextMessages_shouldStoreMessagesAsJson() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")
        val apiMessages = listOf(
            Message("system", "System prompt"),
            Message("user", "Task: test task"),
            Message("assistant", "I'll help you with that")
        )

        // When
        repository.updateApiContextMessages(id, apiMessages)

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        // Verify JSON contains our messages
        assertTrue(task.apiContextMessagesJson.contains("System prompt"))
        assertTrue(task.apiContextMessagesJson.contains("test task"))
        assertTrue(task.apiContextMessagesJson.contains("I'll help you"))
    }

    @Test
    fun updateApiContextMessages_shouldLimitToLast20Messages() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")
        val apiMessages = (1..30).map { i ->
            Message("user", "Message $i")
        }

        // When
        repository.updateApiContextMessages(id, apiMessages)

        // Then
        val task = repository.getTaskById(id)
        assertNotNull(task)
        // Verify only last 20 messages are stored (messages 11-30)
        assertTrue(!task.apiContextMessagesJson.contains("Message 1"))
        assertTrue(!task.apiContextMessagesJson.contains("Message 10"))
        assertTrue(task.apiContextMessagesJson.contains("Message 11"))
        assertTrue(task.apiContextMessagesJson.contains("Message 30"))
    }

    @Test
    fun getTaskById_shouldReturnTaskWithApiContextMessages() = runTest {
        // Given
        val id = repository.createTask("Test task", "model")
        val apiMessages = listOf(
            Message("system", "System prompt"),
            Message("user", "Task: test task")
        )
        repository.updateApiContextMessages(id, apiMessages)

        // When
        val task = repository.getTaskById(id)

        // Then
        assertNotNull(task)
        assertEquals("Test task", task.taskDescription)
        assertTrue(task.apiContextMessagesJson.contains("System prompt"))
        assertTrue(task.apiContextMessagesJson.contains("test task"))
    }

    @Test
    fun getTaskById_withNonExistentId_shouldReturnNull() = runTest {
        // When
        val task = repository.getTaskById(99999L)

        // Then
        assertEquals(null, task)
    }
}

/**
 * Test repository that uses the provided database instead of the singleton
 */
class TestHistoryRepository(private val database: TaskHistoryDatabase) {
    private val dao = database.historyDao()

    suspend fun createTask(description: String, model: String): Long {
        val gson = Gson()
        val task = TaskHistoryEntity(
            taskDescription = description,
            model = model,
            startTime = System.currentTimeMillis(),
            endTime = null,
            durationMs = null,
            status = TaskStatus.PENDING.name,
            statusMessage = null,
            stepCount = 0,
            messagesJson = gson.toJson(emptyList<MessageItem>()),
            apiContextMessagesJson = gson.toJson(emptyList<Message>()),
            actionsJson = gson.toJson(emptyList<Action>()),
            errorMessagesJson = gson.toJson(emptyList<String>()),
            screenshotCount = 0
        )
        return dao.insertTask(task)
    }

    suspend fun getTaskById(id: Long): TaskHistoryEntity? {
        return dao.getTaskById(id)
    }

    fun getRecentTasks(limit: Int = 50): kotlinx.coroutines.flow.Flow<List<TaskHistoryEntity>> {
        return dao.getRecentTasks(limit)
    }

    fun getAllTasks(): kotlinx.coroutines.flow.Flow<List<TaskHistoryEntity>> {
        return dao.getAllTasks()
    }

    fun getTasksByStatus(status: String): kotlinx.coroutines.flow.Flow<List<TaskHistoryEntity>> {
        return dao.getTasksByStatus(status)
    }

    fun searchTasks(query: String): kotlinx.coroutines.flow.Flow<List<TaskHistoryEntity>> {
        return dao.searchTasks(query)
    }

    fun getTasksInDateRange(startTime: Long, endTime: Long): kotlinx.coroutines.flow.Flow<List<TaskHistoryEntity>> {
        return dao.getTasksInDateRange(startTime, endTime)
    }

    suspend fun getStatistics(): HistoryStatistics {
        val total = dao.getTaskCount()
        val completed = dao.getCompletedTaskCount()
        val failed = dao.getFailedTaskCount()
        val cancelled = dao.getCancelledTaskCount()

        return HistoryStatistics(
            totalTasks = total,
            completedTasks = completed,
            failedTasks = failed,
            cancelledTasks = cancelled
        )
    }

    suspend fun updateTask(task: TaskHistoryEntity) {
        dao.updateTask(task)
    }

    suspend fun updateTaskStatus(taskId: Long, status: String, statusMessage: String? = null) {
        dao.updateTaskStatus(taskId, status, statusMessage)
    }

    suspend fun updateTaskCompletion(taskId: Long, endTime: Long, durationMs: Long, stepCount: Int) {
        dao.updateTaskCompletion(taskId, endTime, durationMs, stepCount)
    }

    suspend fun updateTaskStepCount(taskId: Long, stepCount: Int) {
        val task = dao.getTaskById(taskId) ?: return
        dao.updateTask(task.copy(stepCount = stepCount))
    }

    suspend fun updateTaskActions(taskId: Long, actions: List<Action>) {
        val task = dao.getTaskById(taskId) ?: return
        val gson = Gson()
        dao.updateTask(task.copy(actionsJson = gson.toJson(actions)))
    }

    suspend fun updateTaskMessages(taskId: Long, messages: List<MessageItem>) {
        val task = dao.getTaskById(taskId) ?: return
        val gson = Gson()
        dao.updateTask(task.copy(messagesJson = gson.toJson(messages)))
    }

    suspend fun addErrorMessage(taskId: Long, errorMessage: String) {
        val task = dao.getTaskById(taskId) ?: return
        val gson = Gson()
        val errorMessages = gson.fromJson(task.errorMessagesJson, Array<String>::class.java).toList()
        dao.updateTask(
            task.copy(
                errorMessagesJson = gson.toJson(errorMessages + errorMessage)
            )
        )
    }

    suspend fun deleteTask(task: TaskHistoryEntity) {
        dao.deleteTask(task)
    }

    suspend fun deleteTaskById(id: Long) {
        dao.deleteTaskById(id)
    }

    suspend fun clearAll() {
        dao.clearAllHistory()
    }

    suspend fun deleteOldTasks(daysToKeep: Int = 30): Int {
        val timestamp = System.currentTimeMillis() - (daysToKeep * 24 * 60 * 60 * 1000L)
        return dao.deleteTasksOlderThan(timestamp)
    }

    suspend fun deleteTasksByStatus(status: String): Int {
        return dao.deleteTasksByStatus(status)
    }

    suspend fun updateApiContextMessages(taskId: Long, apiMessages: List<Message>) {
        // Keep only last 20 messages to save space and context window
        val gson = Gson()
        val recentMessages = apiMessages.takeLast(20)
        dao.updateApiContextMessages(taskId, gson.toJson(recentMessages))
    }
}
