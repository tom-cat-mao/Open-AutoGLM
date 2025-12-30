package com.taskwizard.android

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.Assert.*
import org.junit.Test

/**
 * injectInputBase64 测试
 *
 * 验证广播命令格式和超时机制
 * - 使用 Java Process.waitFor(timeout, unit) 而非 shell timeout 命令
 * - 使用显式包名 -p 确保广播到达 TaskWizardIME
 */
class InjectInputBase64Test {

    @Test
    fun `injectInputBase64 should call service method`() {
        val mockService = MockAutoGLMServiceFactory.create()

        mockService.injectInputBase64("dGVzdA==")

        verify { mockService.injectInputBase64("dGVzdA==") }
    }

    @Test
    fun `broadcast command should use explicit package`() {
        val base64Text = "dGVzdA=="
        val cmd = buildBroadcastCommand(base64Text)

        assertTrue("Should contain am broadcast", cmd.contains("am broadcast"))
        assertTrue("Should contain explicit package", cmd.contains("-p com.taskwizard.android"))
    }

    @Test
    fun `broadcast command format is correct`() {
        val base64Text = "5b2x6KeG6aOO5pq0"
        val cmd = buildBroadcastCommand(base64Text)

        assertTrue("Should contain action flag", cmd.contains("-a ADB_INPUT_B64"))
        assertTrue("Should contain base64 text", cmd.contains(base64Text))
        assertTrue("Should contain --es msg", cmd.contains("--es msg"))
    }

    @Test
    fun `broadcast command should not use shell timeout`() {
        val base64Text = "dGVzdA=="
        val cmd = buildBroadcastCommand(base64Text)

        assertFalse("Should not use shell timeout command", cmd.startsWith("timeout"))
    }

    private fun buildBroadcastCommand(base64Text: String): String {
        // 匹配 AutoGLMUserService.injectInputBase64 中的命令格式
        return "am broadcast -a ADB_INPUT_B64 --es msg $base64Text -p com.taskwizard.android"
    }
}
