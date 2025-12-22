package com.taskwizard.android

import android.util.Log
import com.taskwizard.android.core.ResponseParser
import io.mockk.every
import io.mockk.mockkStatic
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*

/**
 * ResponseParser 的 Thinking 内容识别测试
 *
 * 测试三种场景：
 * 1. 有 <think> 标签的正常情况
 * 2. 无 <think> 标签的情况
 * 3. 有无混搭的连续情况
 */
class ResponseParserThinkingTest {

    @Before
    fun setup() {
        // Mock Android Log 类，避免单元测试中的 "Method not mocked" 错误
        mockkStatic(Log::class)
        every { Log.d(any(), any()) } returns 0
        every { Log.w(any(), any<String>()) } returns 0
        every { Log.e(any(), any<String>()) } returns 0
        every { Log.e(any(), any(), any()) } returns 0
    }

    @Test
    fun `test scenario 1 - with think tag - normal format`() {
        println("\n=== 测试场景 1: 有 <think> 标签的正常格式 ===")

        val response = """<think>在主屏幕找到哔哩哔哩应用，准备启动它以搜索影视飓风。</think><answer>do(action="Launch", app="哔哩哔哩")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: ${result.think}")
        println("  - action: ${result.action?.action}")
        println("  - content: ${result.action?.content}")

        // 验证
        assertNotNull("thinking 不应该为 null", result.think)
        assertEquals("在主屏幕找到哔哩哔哩应用，准备启动它以搜索影视飓风。", result.think)
        assertNotNull("action 不应该为 null", result.action)
        assertEquals("Launch", result.action?.action)
        assertEquals("哔哩哔哩", result.action?.content)

        println("✅ 测试通过: 正常格式解析成功")
    }

    @Test
    fun `test scenario 2a - without think tag - only answer`() {
        println("\n=== 测试场景 2a: 无 <think> 标签，只有 <answer> ===")

        val response = """<answer>do(action="Launch", app="哔哩哔哩")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: ${result.think}")
        println("  - action: ${result.action?.action}")
        println("  - content: ${result.action?.content}")

        // 验证
        assertNull("thinking 应该为 null（因为没有 <think> 标签且 <answer> 前没有文本）", result.think)
        assertNotNull("action 不应该为 null", result.action)
        assertEquals("Launch", result.action?.action)
        assertEquals("哔哩哔哩", result.action?.content)

        println("✅ 测试通过: 无 thinking 时 action 仍能正确解析")
    }

    @Test
    fun `test scenario 2b - without think tag - implicit thinking before answer`() {
        println("\n=== 测试场景 2b: 无 <think> 标签，但 <answer> 前有隐式 thinking ===")

        val response = """我需要启动哔哩哔哩应用来搜索内容。<answer>do(action="Launch", app="哔哩哔哩")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: ${result.think}")
        println("  - action: ${result.action?.action}")
        println("  - content: ${result.action?.content}")

        // 验证
        assertNotNull("thinking 不应该为 null（应该提取隐式 thinking）", result.think)
        assertEquals("我需要启动哔哩哔哩应用来搜索内容。", result.think)
        assertNotNull("action 不应该为 null", result.action)
        assertEquals("Launch", result.action?.action)
        assertEquals("哔哩哔哩", result.action?.content)

        println("✅ 测试通过: 隐式 thinking 提取成功")
    }

    @Test
    fun `test scenario 3 - mixed sequence - alternating with and without think`() {
        println("\n=== 测试场景 3: 有无混搭的连续情况 ===")

        // 第一次：有 thinking
        val response1 = """<think>首次思考：需要打开应用</think><answer>do(action="Launch", app="测试应用")</answer>"""
        val result1 = ResponseParser.parse(response1)

        println("\n第 1 次调用（有 thinking）:")
        println("输入: $response1")
        println("解析结果:")
        println("  - think: ${result1.think}")
        println("  - action: ${result1.action?.action}")

        assertNotNull("第 1 次: thinking 不应该为 null", result1.think)
        assertEquals("首次思考：需要打开应用", result1.think)
        assertEquals("Launch", result1.action?.action)

        // 第二次：无 thinking
        val response2 = """<answer>do(action="Tap", element=[500, 500])</answer>"""
        val result2 = ResponseParser.parse(response2)

        println("\n第 2 次调用（无 thinking）:")
        println("输入: $response2")
        println("解析结果:")
        println("  - think: ${result2.think}")
        println("  - action: ${result2.action?.action}")

        assertNull("第 2 次: thinking 应该为 null", result2.think)
        assertEquals("Tap", result2.action?.action)

        // 第三次：又有 thinking
        val response3 = """<think>第三次思考：需要点击按钮</think><answer>do(action="Tap", element=[300, 400])</answer>"""
        val result3 = ResponseParser.parse(response3)

        println("\n第 3 次调用（又有 thinking）:")
        println("输入: $response3")
        println("解析结果:")
        println("  - think: ${result3.think}")
        println("  - action: ${result3.action?.action}")

        assertNotNull("第 3 次: thinking 不应该为 null", result3.think)
        assertEquals("第三次思考：需要点击按钮", result3.think)
        assertEquals("Tap", result3.action?.action)

        // 第四次：再次无 thinking
        val response4 = """<answer>do(action="Type", text="搜索内容")</answer>"""
        val result4 = ResponseParser.parse(response4)

        println("\n第 4 次调用（再次无 thinking）:")
        println("输入: $response4")
        println("解析结果:")
        println("  - think: ${result4.think}")
        println("  - action: ${result4.action?.action}")

        assertNull("第 4 次: thinking 应该为 null", result4.think)
        assertEquals("Type", result4.action?.action)

        println("\n✅ 测试通过: 混搭序列解析正确")
    }

    @Test
    fun `test edge case - empty think tag`() {
        println("\n=== 边界测试: 空的 <think> 标签 ===")

        val response = """<think></think><answer>do(action="Launch", app="测试")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: '${result.think}'")
        println("  - action: ${result.action?.action}")

        // 空的 think 标签会被 trim() 处理为空字符串
        assertTrue("空的 thinking 应该为空字符串或 null",
            result.think == null || result.think.isEmpty())
        assertNotNull("action 不应该为 null", result.action)

        println("✅ 测试通过: 空 thinking 标签处理正确")
    }

    @Test
    fun `test edge case - whitespace only think tag`() {
        println("\n=== 边界测试: 只有空白的 <think> 标签 ===")

        val response = """<think>

        </think><answer>do(action="Launch", app="测试")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: '${result.think}'")
        println("  - action: ${result.action?.action}")

        // 只有空白的 think 标签会被 trim() 处理为空字符串
        assertTrue("只有空白的 thinking 应该为空字符串或 null",
            result.think == null || result.think.isEmpty())
        assertNotNull("action 不应该为 null", result.action)

        println("✅ 测试通过: 空白 thinking 标签处理正确")
    }

    @Test
    fun `test edge case - very short implicit thinking`() {
        println("\n=== 边界测试: 很短的隐式 thinking（应该被忽略）===")

        val response = """OK<answer>do(action="Launch", app="测试")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: ${result.think}")
        println("  - action: ${result.action?.action}")

        // 太短的隐式 thinking（<= 5 个字符）应该被忽略
        assertNull("太短的隐式 thinking 应该被忽略", result.think)
        assertNotNull("action 不应该为 null", result.action)

        println("✅ 测试通过: 太短的隐式 thinking 被正确忽略")
    }

    @Test
    fun `test real world - gemini flash response without think`() {
        println("\n=== 真实场景: Gemini-3-flash 省略 thinking 的响应 ===")

        // 这是从实际日志中提取的真实响应
        val response = """<answer>do(action="Launch", app="哔哩哔哩")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: ${result.think}")
        println("  - action: ${result.action?.action}")
        println("  - content: ${result.action?.content}")

        // 验证
        assertNull("Gemini 省略 thinking 时应该为 null", result.think)
        assertNotNull("action 必须能正确解析", result.action)
        assertEquals("Launch", result.action?.action)
        assertEquals("哔哩哔哩", result.action?.content)

        println("✅ 测试通过: Gemini-3-flash 响应解析正确")
    }

    @Test
    fun `test real world - autophone response with think`() {
        println("\n=== 真实场景: Autophone-phone 带 thinking 的响应 ===")

        // 这是从实际日志中提取的真实响应
        val response = """<think>在主屏幕找到哔哩哔哩应用，准备启动它以搜索影视飓风。</think><answer>do(action="Launch", app="哔哩哔哩")</answer>"""

        val result = ResponseParser.parse(response)

        println("输入: $response")
        println("解析结果:")
        println("  - think: ${result.think}")
        println("  - action: ${result.action?.action}")
        println("  - content: ${result.action?.content}")

        // 验证
        assertNotNull("Autophone thinking 不应该为 null", result.think)
        assertEquals("在主屏幕找到哔哩哔哩应用，准备启动它以搜索影视飓风。", result.think)
        assertNotNull("action 必须能正确解析", result.action)
        assertEquals("Launch", result.action?.action)
        assertEquals("哔哩哔哩", result.action?.content)

        println("✅ 测试通过: Autophone-phone 响应解析正确")
    }

    @Test
    fun `test comprehensive - all action types with and without think`() {
        println("\n=== 综合测试: 各种 action 类型的 thinking 处理 ===")

        val testCases = listOf(
            // 有 thinking 的情况
            Triple(
                """<think>需要点击屏幕</think><answer>do(action="Tap", element=[100, 200])</answer>""",
                "Tap",
                true
            ),
            Triple(
                """<think>需要滑动</think><answer>do(action="Swipe", start=[100, 200], end=[300, 400])</answer>""",
                "Swipe",
                true
            ),
            Triple(
                """<think>需要输入文字</think><answer>do(action="Type", text="测试内容")</answer>""",
                "Type",
                true
            ),
            Triple(
                """<think>任务完成</think><answer>finish(message="完成了")</answer>""",
                "finish",
                true
            ),
            // 无 thinking 的情况
            Triple(
                """<answer>do(action="Tap", element=[100, 200])</answer>""",
                "Tap",
                false
            ),
            Triple(
                """<answer>do(action="Back")</answer>""",
                "Back",
                false
            ),
            Triple(
                """<answer>do(action="Home")</answer>""",
                "Home",
                false
            )
        )

        testCases.forEachIndexed { index, (response, expectedAction, shouldHaveThink) ->
            println("\n测试用例 ${index + 1}:")
            println("  输入: $response")

            val result = ResponseParser.parse(response)

            println("  解析结果:")
            println("    - think: ${result.think}")
            println("    - action: ${result.action?.action}")

            assertEquals("Action 类型应该匹配", expectedAction, result.action?.action)

            if (shouldHaveThink) {
                assertNotNull("应该有 thinking", result.think)
            } else {
                assertNull("不应该有 thinking", result.think)
            }

            println("  ✅ 通过")
        }

        println("\n✅ 综合测试全部通过")
    }
}
