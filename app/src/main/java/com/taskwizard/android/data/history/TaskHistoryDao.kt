package com.taskwizard.android.data.history

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

/**
 * Data Access Object for TaskHistoryEntity
 *
 * Provides all database operations for task history including:
 * - CRUD operations
 * - Query and search
 * - Statistics
 * - Bulk operations
 */
@Dao
interface TaskHistoryDao {
    // ==================== Query Operations ====================

    /**
     * Get recent tasks ordered by start time (descending)
     * @param limit Maximum number of tasks to return
     * @return Flow emitting list of tasks
     */
    @Query("SELECT * FROM task_history ORDER BY startTime DESC LIMIT :limit")
    fun getRecentTasks(limit: Int = 50): Flow<List<TaskHistoryEntity>>

    /**
     * Get all tasks ordered by start time (descending)
     * @return Flow emitting list of all tasks
     */
    @Query("SELECT * FROM task_history ORDER BY startTime DESC")
    fun getAllTasks(): Flow<List<TaskHistoryEntity>>

    /**
     * Get a single task by ID
     * @param id Task ID
     * @return Task entity or null if not found
     */
    @Query("SELECT * FROM task_history WHERE id = :id")
    suspend fun getTaskById(id: Long): TaskHistoryEntity?

    /**
     * Get tasks by status
     * @param status Task status string to filter by
     * @return Flow emitting list of tasks with matching status
     */
    @Query("SELECT * FROM task_history WHERE status = :status ORDER BY startTime DESC")
    fun getTasksByStatus(status: String): Flow<List<TaskHistoryEntity>>

    /**
     * Search tasks by description
     * @param query Search query (partial match)
     * @return Flow emitting list of matching tasks
     */
    @Query("SELECT * FROM task_history WHERE taskDescription LIKE '%' || :query || '%' ORDER BY startTime DESC")
    fun searchTasks(query: String): Flow<List<TaskHistoryEntity>>

    /**
     * Get tasks within a date range
     * @param startTime Start timestamp (inclusive)
     * @param endTime End timestamp (inclusive)
     * @return Flow emitting list of tasks in date range
     */
    @Query("SELECT * FROM task_history WHERE startTime >= :startTime AND startTime <= :endTime ORDER BY startTime DESC")
    fun getTasksInDateRange(startTime: Long, endTime: Long): Flow<List<TaskHistoryEntity>>

    // ==================== Statistics ====================

    /**
     * Get total task count
     * @return Total number of tasks
     */
    @Query("SELECT COUNT(*) FROM task_history")
    suspend fun getTaskCount(): Int

    /**
     * Get count of completed tasks
     * @return Number of completed tasks
     */
    @Query("SELECT COUNT(*) FROM task_history WHERE status = 'COMPLETED'")
    suspend fun getCompletedTaskCount(): Int

    /**
     * Get count of failed tasks
     * @return Number of failed tasks
     */
    @Query("SELECT COUNT(*) FROM task_history WHERE status = 'FAILED'")
    suspend fun getFailedTaskCount(): Int

    /**
     * Get count of cancelled tasks
     * @return Number of cancelled tasks
     */
    @Query("SELECT COUNT(*) FROM task_history WHERE status = 'CANCELLED'")
    suspend fun getCancelledTaskCount(): Int

    // ==================== Insert Operations ====================

    /**
     * Insert a new task
     * @param task Task to insert
     * @return ID of inserted task
     */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertTask(task: TaskHistoryEntity): Long

    /**
     * Insert multiple tasks
     * @param tasks List of tasks to insert
     * @return List of inserted task IDs
     */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertTasks(tasks: List<TaskHistoryEntity>): List<Long>

    // ==================== Update Operations ====================

    /**
     * Update an existing task
     * @param task Task to update
     */
    @Update
    suspend fun updateTask(task: TaskHistoryEntity)

    /**
     * Update task status
     * @param taskId Task ID to update
     * @param status New status
     * @param statusMessage Optional status message
     */
    @Query("UPDATE task_history SET status = :status, statusMessage = :statusMessage WHERE id = :taskId")
    suspend fun updateTaskStatus(
        taskId: Long,
        status: String,
        statusMessage: String? = null
    )

    /**
     * Update task completion info
     * @param taskId Task ID to update
     * @param endTime End timestamp
     * @param durationMs Duration in milliseconds
     * @param stepCount Number of steps executed
     */
    @Query("UPDATE task_history SET endTime = :endTime, durationMs = :durationMs, stepCount = :stepCount WHERE id = :taskId")
    suspend fun updateTaskCompletion(
        taskId: Long,
        endTime: Long,
        durationMs: Long,
        stepCount: Int
    )

    /**
     * Update API context messages for conversation continuation
     * @param taskId Task ID to update
     * @param apiContextMessagesJson JSON string of API messages (last ~20 messages)
     */
    @Query("UPDATE task_history SET apiContextMessagesJson = :apiContextMessagesJson WHERE id = :taskId")
    suspend fun updateApiContextMessages(
        taskId: Long,
        apiContextMessagesJson: String
    )

    // ==================== Delete Operations ====================

    /**
     * Delete a task
     * @param task Task to delete
     */
    @Delete
    suspend fun deleteTask(task: TaskHistoryEntity)

    /**
     * Delete task by ID
     * @param id Task ID to delete
     */
    @Query("DELETE FROM task_history WHERE id = :id")
    suspend fun deleteTaskById(id: Long)

    /**
     * Delete multiple tasks
     * @param tasks List of tasks to delete
     */
    @Delete
    suspend fun deleteTasks(tasks: List<TaskHistoryEntity>)

    /**
     * Clear all task history
     */
    @Query("DELETE FROM task_history")
    suspend fun clearAllHistory()

    /**
     * Delete tasks older than a timestamp
     * @param timestamp Cutoff timestamp (exclusive)
     * @return Number of rows deleted
     */
    @Query("DELETE FROM task_history WHERE startTime < :timestamp")
    suspend fun deleteTasksOlderThan(timestamp: Long): Int

    /**
     * Delete tasks with a specific status
     * @param status Status of tasks to delete
     * @return Number of rows deleted
     */
    @Query("DELETE FROM task_history WHERE status = :status")
    suspend fun deleteTasksByStatus(status: String): Int
}
