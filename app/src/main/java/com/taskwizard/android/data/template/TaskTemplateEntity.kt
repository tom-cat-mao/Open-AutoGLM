package com.taskwizard.android.data.template

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 任务模板实体
 * 存储 AI 执行任务时录制的操作序列
 */
@Entity(tableName = "task_templates")
data class TaskTemplateEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,

    /** 模板名称 */
    val name: String,

    /** 模板描述（通常是原始任务描述） */
    val description: String,

    /** 操作序列 JSON（List<Action> 序列化） */
    val actionsJson: String,

    /** 录制时的屏幕宽度 */
    val screenWidth: Int,

    /** 录制时的屏幕高度 */
    val screenHeight: Int,

    /** 操作步数 */
    val stepCount: Int,

    /** 预估执行时间（毫秒） */
    val estimatedDurationMs: Long,

    /** 来源历史记录 ID */
    val createdFromHistoryId: Long? = null,

    /** 创建时间戳 */
    val createdAt: Long = System.currentTimeMillis(),

    /** 上次使用时间戳 */
    val lastUsedAt: Long? = null,

    /** 使用次数 */
    val useCount: Int = 0
)
