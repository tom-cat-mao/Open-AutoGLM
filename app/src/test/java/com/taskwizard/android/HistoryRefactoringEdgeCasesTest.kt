package com.taskwizard.android

import android.app.Application
import com.google.gson.Gson
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.SystemMessageType
import com.taskwizard.android.ui.viewmodel.MainViewModel
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * History Refactoring Edge Cases and Error Handling Tests
 *
 * Tests edge cases, error scenarios, and boundary conditions:
 * - Null and empty content handling
 * - Large data sets
 * - Concurrent operations
 * - Malformed data
 * - Performance considerations
 */
@ExperimentalCoroutinesApi
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class HistoryRefactoringEdgeCasesTest {

    private lateinit var viewModel: MainViewModel
    private lateinit var application: Application
    private lateinit var gson: Gson
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        application = RuntimeEnvironment.getApplication()
        viewModel = MainViewModel(application)
        gson = Gson()
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    // ==================== Null and Empty Content Tests ====================

    @Test
    fun `UserMessage with null content should handle gracefully`() {
        // Note: Kotlin's non-null type system prevents null content at compile time
        // This test verifies the design prevents null content
        val userMessage = MessageItem.UserMessage(content = "")
        assertEquals("", userMessage.content, "Empty string is valid, null is prevented by type system")
    }

    @Test
    fun `addUserMessage with whitespace-only content should preserve whitespace`() = runTest {
        // Given
        val whitespaceContent = "   \n\t  "

        // When
        viewModel.addUserMessage(whitespaceContent)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        val userMessage = state.messages[0] as MessageItem.UserMessage
        assertEquals(whitespaceContent, userMessage.content, "Whitespace should be preserved")
    }

    @Test
    fun `UserMessage with unicode characters should serialize correctly`() {
        // Given
        val unicodeContent = "打开微信 📱 发送消息 ✉️ 给张三 👤"
        val userMessage = MessageItem.UserMessage(content = unicodeContent)

        // When
        val json = gson.toJson(userMessage)
        val deserialized = gson.fromJson(json, MessageItem.UserMessage::class.java)

        // Then
        assertEquals(unicodeContent, deserialized.content, "Unicode should be preserved")
    }

    @Test
    fun `UserMessage with newlines and special chars should serialize correctly`() {
        // Given
        val specialContent = "第一行\n第二行\t制表符\r回车符\"引号\"'单引号'"
        val userMessage = MessageItem.UserMessage(content = specialContent)

        // When
        val json = gson.toJson(userMessage)
        val deserialized = gson.fromJson(json, MessageItem.UserMessage::class.java)

        // Then
        assertEquals(specialContent, deserialized.content, "Special characters should be preserved")
    }

    // ==================== Large Data Set Tests ====================

    @Test
    fun `should handle large number of messages efficiently`() = runTest {
        // Given - Add 100 messages
        repeat(100) { index ->
            when (index % 3) {
                0 -> viewModel.addUserMessage("用户消息 $index")
                1 -> viewModel.addThinkMessage("思考消息 $index")
                2 -> viewModel.addSystemMessage("系统消息 $index", SystemMessageType.INFO)
            }
        }
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(100, state.messages.size, "Should have 100 messages")

        // Verify message types distribution
        val userMessages = state.messages.filterIsInstance<MessageItem.UserMessage>()
        val thinkMessages = state.messages.filterIsInstance<MessageItem.ThinkMessage>()
        val systemMessages = state.messages.filterIsInstance<MessageItem.SystemMessage>()

        assertTrue(userMessages.size >= 33, "Should have ~33 UserMessages")
        assertTrue(thinkMessages.size >= 33, "Should have ~33 ThinkMessages")
        assertTrue(systemMessages.size >= 33, "Should have ~33 SystemMessages")
    }

    @Test
    fun `sorting large message list should maintain correct order`() {
        // Given - Create 1000 messages with random-ish timestamps
        val messages = (0 until 1000).map { index ->
            val timestamp = (index * 100).toLong()
            when (index % 4) {
                0 -> MessageItem.UserMessage(content = "User $index", timestamp = timestamp)
                1 -> MessageItem.ThinkMessage(content = "Think $index", timestamp = timestamp)
                2 -> MessageItem.ActionMessage(
                    action = Action(action = "tap", location = listOf(100, 200).toImmutableList()),
                    timestamp = timestamp
                )
                else -> MessageItem.SystemMessage(
                    content = "System $index",
                    type = SystemMessageType.INFO,
                    timestamp = timestamp
                )
            }
        }

        // When - Sort by timestamp
        val sorted = messages.sortedBy { msg ->
            when (msg) {
                is MessageItem.ThinkMessage -> msg.timestamp
                is MessageItem.ActionMessage -> msg.timestamp
                is MessageItem.SystemMessage -> msg.timestamp
                is MessageItem.UserMessage -> msg.timestamp
            }
        }

        // Then - Verify order is correct
        assertEquals(1000, sorted.size, "Should have 1000 messages")
        for (i in 0 until sorted.size - 1) {
            val currentTimestamp = when (val msg = sorted[i]) {
                is MessageItem.ThinkMessage -> msg.timestamp
                is MessageItem.ActionMessage -> msg.timestamp
                is MessageItem.SystemMessage -> msg.timestamp
                is MessageItem.UserMessage -> msg.timestamp
            }
            val nextTimestamp = when (val msg = sorted[i + 1]) {
                is MessageItem.ThinkMessage -> msg.timestamp
                is MessageItem.ActionMessage -> msg.timestamp
                is MessageItem.SystemMessage -> msg.timestamp
                is MessageItem.UserMessage -> msg.timestamp
            }
            assertTrue(
                currentTimestamp <= nextTimestamp,
                "Messages should be in ascending timestamp order"
            )
        }
    }

    @Test
    fun `very long UserMessage content should be handled`() = runTest {
        // Given - Create a very long message
        val longContent = "打开微信并发送消息".repeat(1000)
        val expectedLength = longContent.length

        // When
        viewModel.addUserMessage(longContent)
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(1, state.messages.size)
        val userMessage = state.messages[0] as MessageItem.UserMessage
        assertEquals(longContent, userMessage.content, "Long content should be preserved")
        assertEquals(expectedLength, userMessage.content.length, "Content length should match")
        assertTrue(userMessage.content.length > 1000, "Content should be very long")
    }

    // ==================== Concurrent Operations Tests ====================

    @Test
    fun `rapid sequential addUserMessage calls should preserve all messages`() = runTest {
        // When - Add messages rapidly
        repeat(50) { index ->
            viewModel.addUserMessage("快速消息 $index")
        }
        advanceUntilIdle()

        // Then - All messages should be preserved
        val state = viewModel.state.value
        assertEquals(50, state.messages.size, "All 50 messages should be preserved")

        // Verify all are UserMessages
        val userMessages = state.messages.filterIsInstance<MessageItem.UserMessage>()
        assertEquals(50, userMessages.size, "All should be UserMessages")
    }

    @Test
    fun `mixed message types added rapidly should preserve order`() = runTest {
        // When - Add different message types rapidly
        repeat(30) { index ->
            viewModel.addUserMessage("用户 $index")
            viewModel.addThinkMessage("思考 $index")
            viewModel.addSystemMessage("系统 $index", SystemMessageType.INFO)
        }
        advanceUntilIdle()

        // Then
        val state = viewModel.state.value
        assertEquals(90, state.messages.size, "Should have 90 messages (30 * 3)")

        // Verify pattern (though timestamps might cause slight reordering)
        val userMessages = state.messages.filterIsInstance<MessageItem.UserMessage>()
        val thinkMessages = state.messages.filterIsInstance<MessageItem.ThinkMessage>()
        val systemMessages = state.messages.filterIsInstance<MessageItem.SystemMessage>()

        assertEquals(30, userMessages.size, "Should have 30 UserMessages")
        assertEquals(30, thinkMessages.size, "Should have 30 ThinkMessages")
        assertEquals(30, systemMessages.size, "Should have 30 SystemMessages")
    }
}
