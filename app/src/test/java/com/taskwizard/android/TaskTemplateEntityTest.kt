package com.taskwizard.android

import com.google.gson.Gson
import com.taskwizard.android.data.template.TaskTemplateEntity
import org.junit.Assert.*
import org.junit.Test

/**
 * TaskTemplateEntity 单元测试
 */
class TaskTemplateEntityTest {

    private val gson = Gson()

    @Test
    fun `test entity creation`() {
        val template = TaskTemplateEntity(
            name = "Test Task",
            description = "Test description",
            actionsJson = "[]",
            screenWidth = 1080,
            screenHeight = 2400,
            stepCount = 5,
            estimatedDurationMs = 5000
        )

        assertEquals("Test Task", template.name)
        assertEquals(5, template.stepCount)
        assertEquals(0, template.useCount)
    }

    @Test
    fun `test json serialization`() {
        val template = TaskTemplateEntity(
            id = 1,
            name = "Test",
            description = "Desc",
            actionsJson = """[{"action":"tap"}]""",
            screenWidth = 1080,
            screenHeight = 2400,
            stepCount = 1,
            estimatedDurationMs = 1000
        )

        val json = gson.toJson(template)
        val restored = gson.fromJson(json, TaskTemplateEntity::class.java)

        assertEquals(template.name, restored.name)
        assertEquals(template.actionsJson, restored.actionsJson)
    }
}
