package com.taskwizard.android.data.history

import android.content.Context
import com.google.gson.Gson
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.Message
import com.taskwizard.android.data.MessageItem
import kotlinx.coroutines.flow.Flow

/**
 * Repository for task history data
 *
 * Provides a clean API for the UI layer to interact with the database.
 * Handles all data operations for task history including:
 * - CRUD operations
 * - Search and filtering
 * - Statistics
 * - Bulk operations
 *
 * @param context Application context
 */
class HistoryRepository(context: Context) {
    private val dao = TaskHistoryDatabase.getDatabase(context).historyDao()
    private val gson = Gson()

    // ==================== Query Operations ====================

    /**
     * Get all tasks (ordered by start time, most recent first)
     * @param limit Maximum number of tasks to return
     * @return Flow emitting list of tasks
     */
    fun getRecentTasks(limit: Int = 50): Flow<List<TaskHistoryEntity>> {
        return dao.getRecentTasks(limit)
    }

    /**
     * Get all tasks (no limit)
     * @return Flow emitting list of all tasks
     */
    fun getAllTasks(): Flow<List<TaskHistoryEntity>> {
        return dao.getAllTasks()
    }

    /**
     * Get a single task by ID
     * @param id Task ID
     * @return Task or null if not found
     */
    suspend fun getTaskById(id: Long): TaskHistoryEntity? {
        return dao.getTaskById(id)
    }

    /**
     * Get tasks by status
     * @param status Status to filter by
     * @return Flow emitting list of tasks with matching status
     */
    fun getTasksByStatus(status: String): Flow<List<TaskHistoryEntity>> {
        return dao.getTasksByStatus(status)
    }

    /**
     * Search tasks by description
     * @param query Search query
     * @return Flow emitting list of matching tasks
     */
    fun searchTasks(query: String): Flow<List<TaskHistoryEntity>> {
        return dao.searchTasks(query)
    }

    /**
     * Get tasks within a date range
     * @param startTime Start timestamp (inclusive)
     * @param endTime End timestamp (inclusive)
     * @return Flow emitting list of tasks in date range
     */
    fun getTasksInDateRange(startTime: Long, endTime: Long): Flow<List<TaskHistoryEntity>> {
        return dao.getTasksInDateRange(startTime, endTime)
    }

    // ==================== Statistics ====================

    /**
     * Get task statistics
     * @return HistoryStatistics containing counts and success rate
     */
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

    // ==================== Create Operations ====================

    /**
     * Create a new task history record
     * @param description Task description
     * @param model Model name
     * @return ID of created task
     */
    suspend fun createTask(
        description: String,
        model: String
    ): Long {
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

    // ==================== Update Operations ====================

    /**
     * Update an existing task
     * @param task Task to update
     */
    suspend fun updateTask(task: TaskHistoryEntity) {
        dao.updateTask(task)
    }

    /**
     * Update task status
     * @param taskId Task ID
     * @param status New status
     * @param statusMessage Optional status message
     */
    suspend fun updateTaskStatus(
        taskId: Long,
        status: String,
        statusMessage: String? = null
    ) {
        dao.updateTaskStatus(taskId, status, statusMessage)
    }

    /**
     * Update task completion info
     * @param taskId Task ID
     * @param endTime End timestamp
     * @param durationMs Duration in milliseconds
     * @param stepCount Number of steps
     */
    suspend fun updateTaskCompletion(
        taskId: Long,
        endTime: Long,
        durationMs: Long,
        stepCount: Int
    ) {
        dao.updateTaskCompletion(taskId, endTime, durationMs, stepCount)
    }

    /**
     * Update task step count
     * @param taskId Task ID
     * @param stepCount New step count
     */
    suspend fun updateTaskStepCount(taskId: Long, stepCount: Int) {
        val task = dao.getTaskById(taskId) ?: return
        dao.updateTask(task.copy(stepCount = stepCount))
    }

    /**
     * Update task actions
     * @param taskId Task ID
     * @param actions List of actions
     */
    suspend fun updateTaskActions(taskId: Long, actions: List<Action>) {
        val task = dao.getTaskById(taskId) ?: return
        dao.updateTask(task.copy(actionsJson = gson.toJson(actions)))
    }

    /**
     * Update task messages
     * @param taskId Task ID
     * @param messages List of messages
     */
    suspend fun updateTaskMessages(taskId: Long, messages: List<MessageItem>) {
        val task = dao.getTaskById(taskId) ?: return
        dao.updateTask(task.copy(messagesJson = gson.toJson(messages)))
    }

    /**
     * Update API context messages for conversation continuation
     * Stores the last ~20 API messages to enable AI context restoration
     * @param taskId Task ID
     * @param apiMessages List of API messages (will be truncated to last 20)
     */
    suspend fun updateApiContextMessages(taskId: Long, apiMessages: List<Message>) {
        // Keep only last 20 messages to save space and context window
        val recentMessages = apiMessages.takeLast(20)
        dao.updateApiContextMessages(taskId, gson.toJson(recentMessages))
    }

    /**
     * Add error message to task
     * @param taskId Task ID
     * @param errorMessage Error message to add
     */
    suspend fun addErrorMessage(taskId: Long, errorMessage: String) {
        val task = dao.getTaskById(taskId) ?: return
        val errorMessages = gson.fromJson(task.errorMessagesJson, Array<String>::class.java).toList()
        dao.updateTask(
            task.copy(
                errorMessagesJson = gson.toJson(errorMessages + errorMessage)
            )
        )
    }

    // ==================== Delete Operations ====================

    /**
     * Delete a task
     * @param task Task to delete
     */
    suspend fun deleteTask(task: TaskHistoryEntity) {
        dao.deleteTask(task)
    }

    /**
     * Delete task by ID
     * @param id Task ID to delete
     */
    suspend fun deleteTaskById(id: Long) {
        dao.deleteTaskById(id)
    }

    /**
     * Clear all task history
     */
    suspend fun clearAll() {
        dao.clearAllHistory()
    }

    /**
     * Delete old tasks (cleanup utility)
     * @param daysToKeep Number of days to keep (default 30)
     * @return Number of tasks deleted
     */
    suspend fun deleteOldTasks(daysToKeep: Int = 30): Int {
        val timestamp = System.currentTimeMillis() - (daysToKeep * 24 * 60 * 60 * 1000L)
        return dao.deleteTasksOlderThan(timestamp)
    }

    /**
     * Delete tasks by status
     * @param status Status of tasks to delete
     * @return Number of tasks deleted
     */
    suspend fun deleteTasksByStatus(status: String): Int {
        return dao.deleteTasksByStatus(status)
    }
}

/**
 * Task history statistics
 *
 * Contains aggregated statistics about task execution history
 */
data class HistoryStatistics(
    val totalTasks: Int,
    val completedTasks: Int,
    val failedTasks: Int,
    val cancelledTasks: Int = 0
) {
    /**
     * Success rate as percentage (0-100)
     * Considers completed tasks as successes, failed as failures,
     * cancelled as partial success (50%)
     */
    val successRate: Float
        get() {
            if (totalTasks == 0) return 0f

            val completedValue = completedTasks * 1.0f
            val cancelledValue = cancelledTasks * 0.5f
            val failedValue = failedTasks * 0.0f

            return ((completedValue + cancelledValue) / totalTasks.toFloat()) * 100f
        }

    /**
     * Simple success rate (completed / total)
     */
    val simpleSuccessRate: Float
        get() = if (totalTasks > 0) {
            (completedTasks.toFloat() / totalTasks.toFloat()) * 100f
        } else {
            0f
        }

    /**
     * Failure rate as percentage
     */
    val failureRate: Float
        get() = if (totalTasks > 0) {
            (failedTasks.toFloat() / totalTasks.toFloat()) * 100f
        } else {
            0f
        }
}
