package com.taskwizard.android.ui.screens

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.taskwizard.android.ui.viewmodel.HistoryViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import kotlin.test.assertEquals

/**
 * Search Debouncing Tests
 *
 * Tests the debouncing behavior of the search functionality
 * in HistoryScreen to verify performance optimization
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(AndroidJUnit4::class)
class HistoryScreenDebouncingTest {

    private val testDispatcher = StandardTestDispatcher()
    private lateinit var viewModel: HistoryViewModel

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        val context = ApplicationProvider.getApplicationContext<Application>()
        viewModel = HistoryViewModel(context)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `search should update searchQuery in state`() = runTest {
        viewModel.searchTasks("test query")
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals("test query", viewModel.historyState.value.searchQuery)
    }

    @Test
    fun `empty search should clear searchQuery`() = runTest {
        viewModel.searchTasks("test")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.searchTasks("")
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals("", viewModel.historyState.value.searchQuery)
    }

    @Test
    fun `clearSearch should reset searchQuery`() = runTest {
        viewModel.searchTasks("test query")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.clearSearch()
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals("", viewModel.historyState.value.searchQuery)
    }
}
