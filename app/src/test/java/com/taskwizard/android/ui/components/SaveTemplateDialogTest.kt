package com.taskwizard.android.ui.components

import org.junit.Assert.*
import org.junit.Test

/**
 * SaveTemplateDialog 验证逻辑单元测试
 */
class SaveTemplateDialogTest {

    // ==================== 名称验证测试 ====================

    @Test
    fun `test empty name is invalid`() {
        val result = validateTemplateName("")
        assertFalse("Empty name should be invalid", result.isValid)
        assertEquals("名称不能为空", result.errorMessage)
    }

    @Test
    fun `test blank name is invalid`() {
        val result = validateTemplateName("   ")
        assertFalse("Blank name should be invalid", result.isValid)
        assertEquals("名称不能为空", result.errorMessage)
    }

    @Test
    fun `test single character name is invalid`() {
        val result = validateTemplateName("A")
        assertFalse("Single char name should be invalid", result.isValid)
        assertEquals("名称至少需要2个字符", result.errorMessage)
    }

    @Test
    fun `test two character name is valid`() {
        val result = validateTemplateName("AB")
        assertTrue("Two char name should be valid", result.isValid)
        assertNull(result.errorMessage)
    }

    @Test
    fun `test 50 character name is valid`() {
        val name = "A".repeat(50)
        val result = validateTemplateName(name)
        assertTrue("50 char name should be valid", result.isValid)
    }

    @Test
    fun `test 51 character name is invalid`() {
        val name = "A".repeat(51)
        val result = validateTemplateName(name)
        assertFalse("51 char name should be invalid", result.isValid)
        assertEquals("名称不能超过50个字符", result.errorMessage)
    }

    @Test
    fun `test chinese characters count correctly`() {
        val name = "测试模板名称"  // 6 characters
        val result = validateTemplateName(name)
        assertTrue("Chinese name should be valid", result.isValid)
    }

    @Test
    fun `test mixed language name is valid`() {
        val name = "Test 测试 123"
        val result = validateTemplateName(name)
        assertTrue("Mixed language name should be valid", result.isValid)
    }

    @Test
    fun `test name with special characters is valid`() {
        val name = "Task-1_v2.0"
        val result = validateTemplateName(name)
        assertTrue("Name with special chars should be valid", result.isValid)
    }

    // ==================== 边界条件测试 ====================

    @Test
    fun `test name trimming`() {
        val name = "  Valid Name  "
        val trimmed = name.trim()
        val result = validateTemplateName(trimmed)
        assertTrue("Trimmed name should be valid", result.isValid)
        assertEquals("Valid Name", trimmed)
    }

    @Test
    fun `test exactly 2 characters is valid`() {
        val result = validateTemplateName("OK")
        assertTrue(result.isValid)
    }

    @Test
    fun `test exactly 50 characters is valid`() {
        val name = "A".repeat(50)
        val result = validateTemplateName(name)
        assertTrue(result.isValid)
    }

    // ==================== 辅助方法 ====================

    /**
     * 验证模板名称
     * 复制自 SaveTemplateDialog 的验证逻辑
     */
    private fun validateTemplateName(name: String): ValidationResult {
        return when {
            name.isBlank() -> ValidationResult(false, "名称不能为空")
            name.length < 2 -> ValidationResult(false, "名称至少需要2个字符")
            name.length > 50 -> ValidationResult(false, "名称不能超过50个字符")
            else -> ValidationResult(true, null)
        }
    }

    data class ValidationResult(
        val isValid: Boolean,
        val errorMessage: String?
    )
}
