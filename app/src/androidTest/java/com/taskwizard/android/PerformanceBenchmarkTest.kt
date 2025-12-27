package com.taskwizard.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.SystemMessageType
import com.taskwizard.android.ui.components.MessageList
import kotlinx.collections.immutable.toPersistentList
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import kotlin.test.assertTrue

/**
 * UI Performance Benchmark Tests
 *
 * Tests LazyColumn rendering performance with large datasets
 */
@RunWith(AndroidJUnit4::class)
class PerformanceBenchmarkTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    // ==================== LazyColumn Performance Tests ====================

    @Test
    fun benchmark_scroll_1000_messages() {
        val messages = (1..1000).map { i ->
            MessageItem.ThinkMessage(content = "Message $i: Test content for scrolling performance")
        }.toPersistentList()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        val startTime = System.nanoTime()

        // Simulate scrolling through the list
        composeTestRule.onNode(hasText("Message 500"))
            .performScrollTo()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Scroll to middle (500 items): ${durationMs}ms")
        // Should be fast (< 100ms)
        assertTrue(durationMs < 100, "Scroll too slow: ${durationMs}ms")

        // Verify the message is displayed
        composeTestRule.onNode(hasText("Message 500")).assertIsDisplayed()
    }

    @Test
    fun benchmark_render_100_different_message_types() {
        val messages = (1..100).mapIndexed { index, i ->
            when (index % 3) {
                0 -> MessageItem.ThinkMessage(content = "Thinking $i")
                1 -> MessageItem.ActionMessage(action = Action(
                    action = "tap",
                    location = listOf(100, 200)
                ))
                else -> MessageItem.SystemMessage(
                    content = "System $i",
                    type = SystemMessageType.INFO
                )
            }
        }.toPersistentList()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        val startTime = System.nanoTime()

        // Trigger recomposition by scrolling
        composeTestRule.onNode(hasText("Thinking 50"))
            .performScrollTo()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Render and scroll 100 mixed messages: ${durationMs}ms")
        assertTrue(durationMs < 50, "Mixed message render too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_initial_render_100_messages() {
        val messages = (1..100).map { i ->
            MessageItem.ThinkMessage(content = "Message $i: Initial render test")
        }.toPersistentList()

        val startTime = System.nanoTime()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        composeTestRule.waitForIdle()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Initial render 100 messages: ${durationMs}ms")
        // Initial render should be reasonably fast
        assertTrue(durationMs < 200, "Initial render too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_scroll_to_bottom_1000_messages() {
        val messages = (1..1000).map { i ->
            MessageItem.ThinkMessage(content = "Message $i")
        }.toPersistentList()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        val startTime = System.nanoTime()

        // Scroll to the last message
        composeTestRule.onNode(hasText("Message 999"))
            .performScrollTo()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Scroll to bottom (1000 items): ${durationMs}ms")
        // Should still be reasonably fast
        assertTrue(durationMs < 150, "Scroll to bottom too slow: ${durationMs}ms")

        // Verify the last message is displayed
        composeTestRule.onNode(hasText("Message 999")).assertIsDisplayed()
    }

    @Test
    fun benchmark_composition_with_immutability() {
        // Test ImmutableList performance
        val baseMessages = (1..100).map {
            MessageItem.ThinkMessage(content = "Message $it")
        }

        val startTime = System.nanoTime()

        repeat(1000) {
            // Simulate adding messages (as happens during task execution)
            val newMessages = (baseMessages + MessageItem.ThinkMessage(content = "New")).toPersistentList()
        }

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("1000 persistent list additions: ${durationMs}ms")
        // ImmutableList operations should be fast
        assertTrue(durationMs < 100, "PersistentList operations too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_large_message_content() {
        val largeContent = "x".repeat(5000) // 5KB per message
        val messages = (1..50).map { i ->
            MessageItem.ThinkMessage(content = "Large message $i:\n$largeContent")
        }.toPersistentList()

        val startTime = System.nanoTime()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        composeTestRule.waitForIdle()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Render 50 large messages (5KB each): ${durationMs}ms")
        assertTrue(durationMs < 200, "Large message render too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_action_message_rendering() {
        val messages = (1..200).map { i ->
            MessageItem.ActionMessage(action = Action(
                action = when (i % 3) {
                    0 -> "tap"
                    1 -> "swipe"
                    else -> "input_text"
                },
                location = listOf(i % 500, (i * 2) % 500),
                content = if (i % 3 == 2) "Test input content" else null
            ))
        }.toPersistentList()

        val startTime = System.nanoTime()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        composeTestRule.waitForIdle()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Render 200 action messages: ${durationMs}ms")
        assertTrue(durationMs < 150, "Action message render too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_system_message_rendering() {
        val messages = (1..100).map { i ->
            MessageItem.SystemMessage(
                content = "System notification $i",
                type = when (i % 3) {
                    0 -> SystemMessageType.INFO
                    1 -> SystemMessageType.SUCCESS
                    else -> SystemMessageType.ERROR
                }
            )
        }.toPersistentList()

        val startTime = System.nanoTime()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        composeTestRule.waitForIdle()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Render 100 system messages: ${durationMs}ms")
        assertTrue(durationMs < 100, "System message render too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_rapid_state_updates() {
        // Simulate rapid message additions during task execution
        composeTestRule.setContent {
            var messages by kotlin.collections.mutableStateOf(
                (1..10).map {
                    MessageItem.ThinkMessage(content = "Initial message $it")
                }.toPersistentList()
            )

            // Simulate rapid updates
            val startTime = System.nanoTime()

            repeat(50) { i ->
                messages = messages + MessageItem.ThinkMessage(content = "New message $i")
            }

            val endTime = System.nanoTime()
            val durationMs = (endTime - startTime) / 1_000_000.0

            println("50 rapid message additions: ${durationMs}ms")
            assertTrue(durationMs < 200, "Rapid updates too slow: ${durationMs}ms")
        }
    }

    @Test
    fun benchmark_mixed_realistic_scenario() {
        // Simulate a realistic scenario with mixed message types
        val messages = buildList {
            // Initial thinking
            add(MessageItem.ThinkMessage(content = "Starting task execution..."))

            // Actions
            repeat(50) { i ->
                add(MessageItem.ThinkMessage(content = "Processing step $i"))
                add(MessageItem.ActionMessage(action = Action(
                    action = "tap",
                    location = listOf(100 + i, 200 + i)
                )))
            }

            // Some system messages
            add(MessageItem.SystemMessage(content = "Step completed", type = SystemMessageType.SUCCESS))
            add(MessageItem.SystemMessage(content = "Warning: element not found", type = SystemMessageType.ERROR))

            // More actions
            repeat(30) { i ->
                add(MessageItem.ActionMessage(action = Action(
                    action = "swipe",
                    location = listOf(i * 10, i * 20)
                )))
            }

            // Final status
            add(MessageItem.SystemMessage(content = "Task completed successfully", type = SystemMessageType.SUCCESS))
        }.toPersistentList()

        val startTime = System.nanoTime()

        composeTestRule.setContent {
            MessageList(messages = messages)
        }

        composeTestRule.waitForIdle()

        // Scroll through
        composeTestRule.onNode(hasText("Task completed successfully"))
            .performScrollTo()

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Realistic scenario (${messages.size} messages): ${durationMs}ms")
        assertTrue(durationMs < 200, "Realistic scenario too slow: ${durationMs}ms")
    }
}
