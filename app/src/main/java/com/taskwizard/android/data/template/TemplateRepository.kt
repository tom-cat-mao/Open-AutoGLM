package com.taskwizard.android.data.template

import kotlinx.coroutines.flow.Flow

/**
 * 任务模板仓库
 */
class TemplateRepository(private val dao: TaskTemplateDao) {

    fun getAllTemplates(): Flow<List<TaskTemplateEntity>> = dao.getAllTemplates()

    suspend fun getById(id: Long): TaskTemplateEntity? = dao.getById(id)

    suspend fun insert(template: TaskTemplateEntity): Long = dao.insert(template)

    suspend fun update(template: TaskTemplateEntity) = dao.update(template)

    suspend fun delete(template: TaskTemplateEntity) = dao.delete(template)

    suspend fun deleteById(id: Long) = dao.deleteById(id)

    suspend fun updateUsage(id: Long) = dao.updateUsage(id)

    suspend fun getCount(): Int = dao.getCount()
}
