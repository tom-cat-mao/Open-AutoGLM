package com.taskwizard.android.data.history

import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.TypeConverters
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.SystemMessageType

/**
 * Task history entity for Room database
 *
 * Stores complete task execution history including:
 * - Task description and model used
 * - Execution timestamps and duration
 * - Status tracking (PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT)
 * - Step count, messages, actions, and errors
 */
@Entity(tableName = "task_history")
data class TaskHistoryEntity(
    /**
     * Primary key - auto-generated
     */
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,

    /**
     * Task description provided by user
     */
    val taskDescription: String,

    /**
     * Model name used for execution
     */
    val model: String,

    /**
     * Task start timestamp (milliseconds since epoch)
     */
    val startTime: Long,

    /**
     * Task end timestamp (null if not completed)
     */
    val endTime: Long? = null,

    /**
     * Task duration in milliseconds (null if not completed)
     */
    val durationMs: Long? = null,

    /**
     * Current task status (stored as string)
     */
    val status: String = TaskStatus.PENDING.name,

    /**
     * Status message (e.g., completion message, error message)
     */
    val statusMessage: String? = null,

    /**
     * Number of steps executed
     */
    val stepCount: Int = 0,

    /**
     * All messages generated during task execution (stored as JSON)
     */
    val messagesJson: String = "[]",

    /**
     * Partial API message history for context restoration (last ~20 messages)
     * Stored as JSON to enable AI to continue from historical conversations
     */
    val apiContextMessagesJson: String = "[]",

    /**
     * All actions executed during task (stored as JSON)
     */
    val actionsJson: String = "[]",

    /**
     * Error messages encountered during execution (stored as JSON)
     */
    val errorMessagesJson: String = "[]",

    /**
     * Number of screenshots taken
     */
    val screenshotCount: Int = 0,

    /**
     * Database record creation timestamp
     */
    val createdAt: Long = System.currentTimeMillis()
) {
    /**
     * Calculate success rate for this task
     * Returns 1.0 for completed, 0.0 for failed, 0.5 for cancelled
     */
    val successValue: Float
        get() = when (status) {
            TaskStatus.COMPLETED.name -> 1.0f
            TaskStatus.FAILED.name, TaskStatus.TIMEOUT.name -> 0.0f
            TaskStatus.CANCELLED.name -> 0.5f
            else -> 0.0f
        }

    /**
     * Get TaskStatus enum from string
     */
    fun getTaskStatus(): TaskStatus {
        return try {
            TaskStatus.valueOf(status)
        } catch (e: IllegalArgumentException) {
            TaskStatus.PENDING
        }
    }
}
