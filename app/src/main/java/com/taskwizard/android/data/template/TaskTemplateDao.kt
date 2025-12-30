package com.taskwizard.android.data.template

import androidx.room.*
import kotlinx.coroutines.flow.Flow

/**
 * 任务模板 DAO
 */
@Dao
interface TaskTemplateDao {

    @Query("SELECT * FROM task_templates ORDER BY CASE WHEN lastUsedAt IS NULL THEN 1 ELSE 0 END, lastUsedAt DESC, createdAt DESC")
    fun getAllTemplates(): Flow<List<TaskTemplateEntity>>

    @Query("SELECT * FROM task_templates WHERE id = :id")
    suspend fun getById(id: Long): TaskTemplateEntity?

    @Insert
    suspend fun insert(template: TaskTemplateEntity): Long

    @Update
    suspend fun update(template: TaskTemplateEntity)

    @Delete
    suspend fun delete(template: TaskTemplateEntity)

    @Query("DELETE FROM task_templates WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("UPDATE task_templates SET lastUsedAt = :timestamp, useCount = useCount + 1 WHERE id = :id")
    suspend fun updateUsage(id: Long, timestamp: Long = System.currentTimeMillis())

    @Query("SELECT COUNT(*) FROM task_templates")
    suspend fun getCount(): Int
}
