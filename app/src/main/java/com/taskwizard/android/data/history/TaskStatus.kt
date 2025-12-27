package com.taskwizard.android.data.history

/**
 * Task execution status enum
 *
 * Represents the current state of a task execution
 */
enum class TaskStatus {
    /**
     * Task has been created but not started
     */
    PENDING,

    /**
     * Task is currently executing
     */
    RUNNING,

    /**
     * Task completed successfully
     */
    COMPLETED,

    /**
     * Task failed due to an error
     */
    FAILED,

    /**
     * Task was cancelled by user
     */
    CANCELLED,

    /**
     * Task timed out
     */
    TIMEOUT
}
