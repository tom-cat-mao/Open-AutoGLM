package com.taskwizard.android

import com.taskwizard.android.data.template.TaskTemplateDao
import com.taskwizard.android.data.template.TaskTemplateEntity
import com.taskwizard.android.data.template.TemplateRepository
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * TemplateRepository 单元测试
 */
class TemplateRepositoryTest {

    private lateinit var dao: TaskTemplateDao
    private lateinit var repository: TemplateRepository

    @Before
    fun setup() {
        dao = mockk(relaxed = true)
        repository = TemplateRepository(dao)
    }

    @Test
    fun `test insert template`() = runBlocking {
        val template = createTemplate()
        coEvery { dao.insert(any()) } returns 1L

        val id = repository.insert(template)

        assertEquals(1L, id)
        coVerify { dao.insert(template) }
    }

    @Test
    fun `test delete template`() = runBlocking {
        val template = createTemplate()

        repository.delete(template)

        coVerify { dao.delete(template) }
    }

    @Test
    fun `test update usage`() = runBlocking {
        repository.updateUsage(1L)

        coVerify { dao.updateUsage(1L, any()) }
    }

    private fun createTemplate() = TaskTemplateEntity(
        name = "Test",
        description = "Test",
        actionsJson = "[]",
        screenWidth = 1080,
        screenHeight = 2400,
        stepCount = 0,
        estimatedDurationMs = 0
    )
}
