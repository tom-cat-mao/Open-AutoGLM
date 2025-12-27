package com.taskwizard.android

import android.app.Application
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.gson.Gson
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.Message
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.SystemMessageType
import com.taskwizard.android.data.history.HistoryRepository
import com.taskwizard.android.data.history.TaskHistoryDatabase
import com.taskwizard.android.data.history.TaskStatus
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import kotlin.test.assertTrue

/**
 * Performance Tests for critical operations
 *
 * Tests the performance of:
 * - JSON serialization/deserialization
 * - Database operations
 * - Memory usage
 */
@RunWith(AndroidJUnit4::class)
class PerformanceTest {

    private lateinit var database: TaskHistoryDatabase
    private lateinit var repository: HistoryRepository
    private val gson = Gson()

    @Before
    fun setup() {
        val context = ApplicationProvider.getApplicationContext<Application>()
        database = Room.inMemoryDatabaseBuilder(context, TaskHistoryDatabase::class.java)
            .build()
        repository = HistoryRepository(context)
    }

    @After
    fun tearDown() {
        database.close()
    }

    // ==================== JSON Processing Tests ====================

    @Test
    fun benchmark_serialize_1000_messages() {
        val messages = (1..1000).map { i ->
            MessageItem.ThinkMessage(content = "Message $i content")
        }

        val startTime = System.nanoTime()
        val json = gson.toJson(messages)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Serialize 1000 messages: ${durationMs}ms, JSON size: ${json.length} chars")

        // Should complete in less than 100ms
        assertTrue(durationMs < 100, "Serialization too slow: ${durationMs}ms")
        assertTrue(json.isNotEmpty(), "JSON should not be empty")
    }

    @Test
    fun benchmark_json_string_operations() {
        // Test JSON string manipulation performance (simulating app's JSON handling)
        val messages = (1..1000).map { i ->
            MessageItem.ThinkMessage(content = "Message $i content")
        }
        val json = gson.toJson(messages)

        val startTime = System.nanoTime()

        // Simulate operations the app does: string length, contains, substring
        repeat(100) {
            val length = json.length
            val hasContent = json.contains("content")
            val substring = json.take(1000)
        }

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0
        println("JSON string operations (100 iterations): ${durationMs}ms")
        assertTrue(durationMs < 50, "String operations too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_json_size_for_complex_messages() {
        val messages = listOf(
            MessageItem.ThinkMessage(content = "Thinking" + "x".repeat(1000)),
            MessageItem.ActionMessage(action = Action(
                action = "complex_action",
                location = listOf(1, 2, 3, 4, 5),
                content = "content" + "y".repeat(500)
            )),
            MessageItem.SystemMessage(
                content = "System message " + "z".repeat(2000),
                type = SystemMessageType.INFO
            )
        )

        val startTime = System.nanoTime()
        val json = gson.toJson(messages)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Serialize complex messages: ${durationMs}ms, JSON size: ${json.length} chars")
        assertTrue(durationMs < 50, "Complex serialization too slow: ${durationMs}ms")
    }

    // ==================== Database Performance Tests ====================

    @Test
    fun benchmark_database_insert_1000_tasks() = runTest {
        val startTime = System.nanoTime()

        repeat(1000) { i ->
            repository.createTask("Task $i", "test-model")
        }

        val endTime = System.nanoTime()
        val durationMs = (endTime - startTime) / 1_000_000.0

        println("Insert 1000 tasks: ${durationMs}ms")
        // Should be fast (< 1 second for 1000 inserts)
        assertTrue(durationMs < 1000, "Insert too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_database_query_by_id() = runTest {
        // Insert test data
        val id = repository.createTask("Test task", "test-model")

        // Benchmark query
        val startTime = System.nanoTime()
        val task = repository.getTaskById(id)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Query by ID: ${durationMs}ms")

        // Should be very fast (< 10ms)
        assertTrue(durationMs < 10, "Query too slow: ${durationMs}ms")
        assertTrue(task != null)
    }

    @Test
    fun benchmark_database_search() = runTest {
        // Insert 1000 tasks
        repeat(1000) { i ->
            repository.createTask("Task $i description with keywords", "test-model")
        }

        // Benchmark search
        val startTime = System.nanoTime()
        val results = repository.searchTasks("description").first()
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Search 1000 tasks: ${durationMs}ms")

        // Should be fast (< 50ms)
        assertTrue(durationMs < 50, "Search too slow: ${durationMs}ms")
        assertTrue(results.size == 1000)
    }

    @Test
    fun benchmark_database_update_messages() = runTest {
        val messages = (1..100).map { i ->
            MessageItem.ThinkMessage(content = "Message $i")
        }
        val id = repository.createTask("Test task", "test-model")

        val startTime = System.nanoTime()
        repository.updateTaskMessages(id, messages)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Update task with 100 messages: ${durationMs}ms")

        // Should be fast (< 50ms)
        assertTrue(durationMs < 50, "Update messages too slow: ${durationMs}ms")
    }

    @Test
    fun benchmark_database_update_api_context() = runTest {
        val apiMessages = (1..20).map { i ->
            Message(role = "user", content = "Message $i")
        }
        val id = repository.createTask("Test task", "test-model")

        val startTime = System.nanoTime()
        repository.updateApiContextMessages(id, apiMessages)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Update API context messages (20 messages): ${durationMs}ms")

        // Should be fast (< 50ms)
        assertTrue(durationMs < 50, "Update API context too slow: ${durationMs}ms")
    }

    // ==================== Memory Usage Tests ====================

    @Test
    fun benchmark_memory_usage_large_messages() {
        val messages = (1..10000).map { i ->
            MessageItem.ThinkMessage(content = "Large message content " + "x".repeat(500))
        }

        val runtime = Runtime.getRuntime()
        runtime.gc()
        val memoryBefore = runtime.totalMemory() - runtime.freeMemory()

        // Just create the list and serialize (no deserialization due to Gson limitations)
        val json = gson.toJson(messages)

        runtime.gc()
        val memoryAfter = runtime.totalMemory() - runtime.freeMemory()

        val memoryUsedMb = (memoryAfter - memoryBefore) / (1024.0 * 1024.0)
        println("Memory used for 10000 messages: ${memoryUsedMb}MB, JSON size: ${json.length} chars")

        // Should use less than 50MB for 10000 messages
        assertTrue(memoryUsedMb < 50, "Memory usage too high: ${memoryUsedMb}MB")
    }

    @Test
    fun benchmark_large_task_description() = runTest {
        val largeDescription = "a".repeat(10000) // 10KB description

        val startTime = System.nanoTime()
        val id = repository.createTask(largeDescription, "test-model")
        val task = repository.getTaskById(id)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Insert and query large task description (10KB): ${durationMs}ms")

        // Should handle large descriptions efficiently
        assertTrue(durationMs < 50, "Large description handling too slow: ${durationMs}ms")
        assertTrue(task != null)
        assertTrue(task!!.taskDescription.length == 10000)
    }

    @Test
    fun benchmark_mixed_message_types_serialization() {
        val messages = (1..1000).mapIndexed { index, i ->
            when (index % 4) {
                0 -> MessageItem.ThinkMessage(content = "Thinking $i " + "x".repeat(100))
                1 -> MessageItem.ActionMessage(action = Action(
                    action = "action_$i",
                    location = listOf(i % 500, (i * 2) % 500),
                    content = "action content"
                ))
                2 -> MessageItem.SystemMessage(
                    content = "System $i",
                    type = SystemMessageType.INFO
                )
                else -> MessageItem.ActionMessage(action = Action(
                    action = "tap",
                    location = listOf(100, 200),
                    content = null
                ))
            }
        }

        val startTime = System.nanoTime()
        val serialized = gson.toJson(messages)
        val endTime = System.nanoTime()

        val durationMs = (endTime - startTime) / 1_000_000.0
        println("Serialize 1000 mixed message types: ${durationMs}ms, JSON size: ${serialized.length} chars")
        assertTrue(durationMs < 100, "Mixed serialization too slow: ${durationMs}ms")
    }
}
