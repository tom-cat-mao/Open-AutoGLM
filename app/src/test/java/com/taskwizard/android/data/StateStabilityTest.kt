package com.taskwizard.android.data

import com.taskwizard.android.ui.viewmodel.HistoryState
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * State Stability Tests
 *
 * Tests the stability and immutability of state classes
 * after performance optimizations with @Stable and ImmutableList
 */
class StateStabilityTest {

    // ==================== OverlayState Tests ====================

    @Test
    fun `OverlayState should have default values`() {
        val state = OverlayState()

        assertEquals(OverlayDisplayState.TRANSPARENT, state.displayState)
        assertEquals("", state.statusText)
        assertEquals(false, state.isThinking)
        assertEquals(null, state.currentAction)
        assertEquals(false, state.isTaskRunning)
        assertEquals(false, state.isTaskCompleted)
        assertEquals(0, state.currentStep)
    }

    @Test
    fun `OverlayState copy should create new instance`() {
        val original = OverlayState(isThinking = true)
        val copy = original.copy(isThinking = false)

        assertTrue(original.isThinking)
        assertTrue(!copy.isThinking)
    }

    @Test
    fun `OverlayState getDisplayText should return correct text`() {
        val thinkingState = OverlayState(isThinking = true)
        assertEquals("Thinking...", thinkingState.getDisplayText())

        val completedState = OverlayState(isTaskCompleted = true)
        assertEquals("已完成", completedState.getDisplayText())

        val errorState = OverlayState(errorMessage = "Test error")
        assertEquals("错误: Test error", errorState.getDisplayText())
    }

    // ==================== Action Tests ====================

    @Test
    fun `Action should accept ImmutableList for location`() {
        val location = listOf(100, 200).toImmutableList()
        val action = Action(
            action = "tap",
            location = location,
            content = null
        )

        assertNotNull(action.location)
        assertEquals(2, action.location?.size)
        assertEquals(100, action.location?.get(0))
        assertEquals(200, action.location?.get(1))
    }

    @Test
    fun `Action with null location should work`() {
        val action = Action(action = "back", location = null)

        assertEquals("back", action.action)
        assertEquals(null, action.location)
    }

    @Test
    fun `Action copy should preserve ImmutableList`() {
        val location = listOf(100, 200).toImmutableList()
        val original = Action(action = "tap", location = location)
        val copy = original.copy(action = "double tap")

        assertEquals("double tap", copy.action)
        assertEquals(location, copy.location)
    }

    // ==================== HistoryState Tests ====================

    @Test
    fun `HistoryState should have empty ImmutableList by default`() {
        val state = HistoryState()

        assertTrue(state.tasks.isEmpty())
        assertEquals(true, state.isLoading)
        assertEquals(null, state.error)
    }

    @Test
    fun `HistoryState copy should work with ImmutableList`() {
        val state = HistoryState(isLoading = false)
        val newState = state.copy(isLoading = true)

        assertTrue(!state.isLoading)
        assertTrue(newState.isLoading)
    }

    @Test
    fun `HistoryState tasks should be ImmutableList type`() {
        val state = HistoryState()

        // Verify it's an ImmutableList (persistentListOf returns ImmutableList)
        assertTrue(state.tasks is kotlinx.collections.immutable.ImmutableList)
    }
}
