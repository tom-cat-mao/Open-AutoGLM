package com.taskwizard.android

import android.content.Context
import android.util.Log
import com.taskwizard.android.IAutoGLMService
import com.taskwizard.android.core.ActionExecutor
import com.taskwizard.android.data.Action
import io.mockk.*
import kotlinx.coroutines.runBlocking
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*

/**
 * ActionExecutor 错误处理测试
 *
 * 测试场景：
 * 1. Launch 动作使用错误的应用名（AppMap 中不存在）
 * 2. Launch 动作使用正确的应用名（AppMap 中存在）
 * 3. 验证错误信息的内容和格式
 */
class ActionExecutorErrorHandlingTest {

    private lateinit var context: Context
    private lateinit var service: IAutoGLMService
    private lateinit var executor: ActionExecutor

    @Before
    fun setup() {
        // Mock Android Log
        mockkStatic(Log::class)
        every { Log.d(any(), any()) } returns 0
        every { Log.w(any(), any<String>()) } returns 0
        every { Log.e(any(), any<String>()) } returns 0
        every { Log.i(any(), any()) } returns 0

        // Mock Context
        context = mockk(relaxed = true)

        // Mock IAutoGLMService
        service = mockk(relaxed = true)
        every { service.executeShellCommand(any()) } returns ""

        // Create ActionExecutor
        executor = ActionExecutor(
            context = context,
            service = service,
            screenWidth = 1080,
            screenHeight = 2400
        )
    }

    @Test
    fun `test Launch with wrong app name - should return error`() = runBlocking {
        println("\n=== 测试场景 1: Launch 使用错误的应用名 ===")

        // 创建 Launch action，使用 AppMap 中不存在的应用名
        val action = Action(
            action = "Launch",
            content = "哔哩哔哩"  // AppMap 中只有 "bilibili"，没有 "哔哩哔哩"
        )

        println("输入 Action: ${action.action}, content: ${action.content}")

        // 执行 action
        val result = executor.execute(action)

        println("执行结果:")
        println("  - success: ${result.success}")
        println("  - shouldContinue: ${result.shouldContinue}")
        println("  - errorMessage: ${result.errorMessage}")

        // 验证结果
        assertFalse("执行应该失败", result.success)
        assertTrue("应该继续任务（让 AI 重试）", result.shouldContinue)
        assertNotNull("应该有错误信息", result.errorMessage)
        assertTrue(
            "错误信息应该包含应用名",
            result.errorMessage!!.contains("哔哩哔哩")
        )
        assertTrue(
            "错误信息应该提示未找到",
            result.errorMessage!!.contains("未在系统中找到") ||
            result.errorMessage!!.contains("未找到")
        )

        // 验证没有执行 shell 命令
        verify(exactly = 0) { service.executeShellCommand(match { it.contains("monkey") }) }

        println("✅ 测试通过: 错误应用名正确返回错误")
    }

    @Test
    fun `test Launch with correct app name - should succeed`() = runBlocking {
        println("\n=== 测试场景 2: Launch 使用正确的应用名 ===")

        // 创建 Launch action，使用 AppMap 中存在的应用名
        val action = Action(
            action = "Launch",
            content = "bilibili"  // AppMap 中存在
        )

        println("输入 Action: ${action.action}, content: ${action.content}")

        // 执行 action
        val result = executor.execute(action)

        println("执行结果:")
        println("  - success: ${result.success}")
        println("  - shouldContinue: ${result.shouldContinue}")
        println("  - errorMessage: ${result.errorMessage}")

        // 验证结果
        assertTrue("执行应该成功", result.success)
        assertTrue("应该继续任务", result.shouldContinue)
        assertNull("不应该有错误信息", result.errorMessage)

        // 验证执行了 shell 命令
        verify(atLeast = 1) {
            service.executeShellCommand(match {
                it.contains("monkey") && it.contains("tv.danmaku.bili")
            })
        }

        println("✅ 测试通过: 正确应用名成功执行")
    }

    @Test
    fun `test Launch with multiple wrong app names - verify error messages`() = runBlocking {
        println("\n=== 测试场景 3: 测试多个错误应用名的错误信息 ===")

        val wrongAppNames = listOf(
            "哔哩哔哩",
            "B站",
            "不存在的应用",
            "随便什么应用",
            "测试应用123"
        )

        wrongAppNames.forEachIndexed { index, appName ->
            println("\n测试 ${index + 1}: $appName")

            val action = Action(
                action = "Launch",
                content = appName
            )

            val result = executor.execute(action)

            println("  结果: success=${result.success}, error=${result.errorMessage}")

            // 验证
            assertFalse("应该失败", result.success)
            assertNotNull("应该有错误信息", result.errorMessage)
            assertTrue(
                "错误信息应该包含应用名",
                result.errorMessage!!.contains(appName)
            )

            println("  ✅ 通过")
        }

        println("\n✅ 所有错误应用名测试通过")
    }

    @Test
    fun `test Launch error message format`() = runBlocking {
        println("\n=== 测试场景 4: 验证错误信息格式 ===")

        val action = Action(
            action = "Launch",
            content = "测试应用"
        )

        val result = executor.execute(action)

        println("错误信息: ${result.errorMessage}")

        // 验证错误信息格式
        assertNotNull("应该有错误信息", result.errorMessage)

        val errorMsg = result.errorMessage!!

        // 错误信息应该包含以下要素：
        // 1. 应用名
        assertTrue("应该包含应用名", errorMsg.contains("测试应用"))

        // 2. 明确的错误描述
        assertTrue(
            "应该有明确的错误描述",
            errorMsg.contains("未在系统中找到") ||
            errorMsg.contains("未找到") ||
            errorMsg.contains("不存在")
        )

        // 3. 友好的提示
        assertTrue(
            "应该有友好的提示",
            errorMsg.contains("请检查") ||
            errorMsg.contains("确认") ||
            errorMsg.contains("请")
        )

        println("✅ 测试通过: 错误信息格式正确")
    }

    @Test
    fun `test other actions still work normally`() = runBlocking {
        println("\n=== 测试场景 5: 验证其他 action 不受影响 ===")

        // 测试 Tap action
        val tapAction = Action(
            action = "Tap",
            location = listOf(500, 500)
        )

        val tapResult = executor.execute(tapAction)
        println("Tap 结果: success=${tapResult.success}")
        assertTrue("Tap 应该成功", tapResult.success)

        // 测试 Back action
        val backAction = Action(
            action = "Back"
        )

        val backResult = executor.execute(backAction)
        println("Back 结果: success=${backResult.success}")
        assertTrue("Back 应该成功", backResult.success)

        // 测试 Home action
        val homeAction = Action(
            action = "Home"
        )

        val homeResult = executor.execute(homeAction)
        println("Home 结果: success=${homeResult.success}")
        assertTrue("Home 应该成功", homeResult.success)

        println("✅ 测试通过: 其他 action 正常工作")
    }

    @Test
    fun `test Launch with case variations`() = runBlocking {
        println("\n=== 测试场景 6: 测试大小写变体 ===")

        val testCases = listOf(
            "bilibili" to true,      // 正确
            "Bilibili" to true,      // 大小写不敏感应该成功
            "BILIBILI" to true,      // 全大写应该成功
            "BiLiBiLi" to true,      // 混合大小写应该成功
            "哔哩哔哩" to false,      // 中文名不存在应该失败
            "B站" to false           // 简称不存在应该失败
        )

        testCases.forEach { (appName, shouldSucceed) ->
            println("\n测试: $appName (预期${if (shouldSucceed) "成功" else "失败"})")

            val action = Action(
                action = "Launch",
                content = appName
            )

            val result = executor.execute(action)

            println("  结果: success=${result.success}")

            if (shouldSucceed) {
                assertTrue("$appName 应该成功", result.success)
                assertNull("不应该有错误", result.errorMessage)
            } else {
                assertFalse("$appName 应该失败", result.success)
                assertNotNull("应该有错误信息", result.errorMessage)
            }

            println("  ✅ 通过")
        }

        println("\n✅ 大小写变体测试通过")
    }

    @Test
    fun `test ExecuteResult data class`() {
        println("\n=== 测试场景 7: 验证 ExecuteResult 数据类 ===")

        // 测试成功结果
        val successResult = ActionExecutor.ExecuteResult(
            success = true,
            shouldContinue = true,
            errorMessage = null
        )

        assertTrue(successResult.success)
        assertTrue(successResult.shouldContinue)
        assertNull(successResult.errorMessage)
        println("✅ 成功结果正确")

        // 测试失败结果
        val failureResult = ActionExecutor.ExecuteResult(
            success = false,
            shouldContinue = true,
            errorMessage = "测试错误"
        )

        assertFalse(failureResult.success)
        assertTrue(failureResult.shouldContinue)
        assertEquals("测试错误", failureResult.errorMessage)
        println("✅ 失败结果正确")

        // 测试用户取消
        val cancelResult = ActionExecutor.ExecuteResult(
            success = true,
            shouldContinue = false,
            errorMessage = null
        )

        assertTrue(cancelResult.success)
        assertFalse(cancelResult.shouldContinue)
        assertNull(cancelResult.errorMessage)
        println("✅ 取消结果正确")

        println("\n✅ ExecuteResult 数据类测试通过")
    }
}
