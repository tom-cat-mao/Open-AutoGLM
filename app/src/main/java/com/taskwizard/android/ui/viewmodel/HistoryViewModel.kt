package com.taskwizard.android.ui.viewmodel

import android.app.Application
import androidx.compose.runtime.Stable
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.taskwizard.android.data.history.HistoryRepository
import com.taskwizard.android.data.history.HistoryStatistics
import com.taskwizard.android.data.history.TaskHistoryEntity
import com.taskwizard.android.data.history.TaskStatus
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * HistoryViewModel - 任务历史视图模型
 *
 * 负责管理任务历史功能的状态和业务逻辑
 * 提供历史记录查询、统计、删除等功能
 */
class HistoryViewModel(application: Application) : AndroidViewModel(application) {

    companion object {
        private const val TAG = "HistoryViewModel"
    }

    // ==================== Repository ====================

    private val repository = HistoryRepository(application)

    // ==================== State ====================

    /**
     * 历史记录列表状态
     */
    private val _historyState = MutableStateFlow(HistoryState())
    val historyState: StateFlow<HistoryState> = _historyState.asStateFlow()

    /**
     * 统计信息状态
     */
    private val _statistics = MutableStateFlow<HistoryStatistics?>(null)
    val statistics: StateFlow<HistoryStatistics?> = _statistics.asStateFlow()

    // ==================== Initialization ====================

    init {
        loadTasks()
        loadStatistics()
    }

    // ==================== Query Operations ====================

    /**
     * 加载所有任务历史
     */
    fun loadTasks() {
        viewModelScope.launch {
            try {
                repository.getRecentTasks(100).collect { tasks ->
                    _historyState.value = _historyState.value.copy(
                        tasks = tasks.toImmutableList(),
                        isLoading = false,
                        error = null
                    )
                }
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    isLoading = false,
                    error = e.message
                )
            }
        }
    }

    /**
     * 加载统计信息
     */
    fun loadStatistics() {
        viewModelScope.launch {
            try {
                val stats = repository.getStatistics()
                _statistics.value = stats
            } catch (e: Exception) {
                // 统计加载失败不影响主功能，只记录日志
            }
        }
    }

    /**
     * 根据状态筛选任务
     */
    fun filterByStatus(status: TaskStatus) {
        viewModelScope.launch {
            try {
                repository.getTasksByStatus(status.name).collect { tasks ->
                    _historyState.value = _historyState.value.copy(
                        tasks = tasks.toImmutableList(),
                        currentFilter = status.name
                    )
                }
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    error = e.message
                )
            }
        }
    }

    /**
     * 清除筛选，显示所有任务
     */
    fun clearFilter() {
        loadTasks()
        _historyState.value = _historyState.value.copy(currentFilter = null)
    }

    /**
     * 搜索任务
     */
    fun searchTasks(query: String) {
        if (query.isBlank()) {
            loadTasks()
            return
        }

        viewModelScope.launch {
            try {
                repository.searchTasks(query).collect { tasks ->
                    _historyState.value = _historyState.value.copy(
                        tasks = tasks.toImmutableList(),
                        searchQuery = query
                    )
                }
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    error = e.message
                )
            }
        }
    }

    /**
     * 清除搜索
     */
    fun clearSearch() {
        _historyState.value = _historyState.value.copy(searchQuery = "")
        loadTasks()
    }

    // ==================== Delete Operations ====================

    /**
     * 删除单个任务
     */
    fun deleteTask(task: TaskHistoryEntity) {
        viewModelScope.launch {
            try {
                repository.deleteTask(task)
                // 刷新统计
                loadStatistics()
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    error = e.message
                )
            }
        }
    }

    /**
     * 根据状态删除任务
     */
    fun deleteTasksByStatus(status: TaskStatus) {
        viewModelScope.launch {
            try {
                val count = repository.deleteTasksByStatus(status.name)
                _historyState.value = _historyState.value.copy(
                    message = "已删除 $count 个${getStatusDisplayName(status)}任务"
                )
                // 刷新统计
                loadStatistics()
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    error = e.message
                )
            }
        }
    }

    /**
     * 删除旧任务
     */
    fun deleteOldTasks(daysToKeep: Int = 30) {
        viewModelScope.launch {
            try {
                val count = repository.deleteOldTasks(daysToKeep)
                _historyState.value = _historyState.value.copy(
                    message = "已删除 $count 个超过 $daysToKeep 天的旧任务"
                )
                // 刷新统计
                loadStatistics()
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    error = e.message
                )
            }
        }
    }

    /**
     * 清空所有历史
     */
    fun clearAllHistory() {
        viewModelScope.launch {
            try {
                repository.clearAll()
                _historyState.value = _historyState.value.copy(
                    message = "已清空所有历史记录"
                )
                // 刷新统计
                loadStatistics()
            } catch (e: Exception) {
                _historyState.value = _historyState.value.copy(
                    error = e.message
                )
            }
        }
    }

    // ==================== Helper Methods ====================

    /**
     * 清除消息
     */
    fun clearMessage() {
        _historyState.value = _historyState.value.copy(message = null)
    }

    /**
     * 获取状态显示名称
     */
    private fun getStatusDisplayName(status: TaskStatus): String {
        return when (status) {
            TaskStatus.PENDING -> "待执行"
            TaskStatus.RUNNING -> "执行中"
            TaskStatus.COMPLETED -> "已完成"
            TaskStatus.FAILED -> "失败"
            TaskStatus.CANCELLED -> "已取消"
            TaskStatus.TIMEOUT -> "超时"
        }
    }
}

/**
 * 历史记录状态
 *
 * @param tasks 任务列表
 * @param isLoading 是否正在加载
 * @param error 错误信息
 * @param message 操作消息
 * @param currentFilter 当前筛选状态
 * @param searchQuery 当前搜索查询
 */
@Stable
data class HistoryState(
    val tasks: ImmutableList<TaskHistoryEntity> = persistentListOf(),
    val isLoading: Boolean = true,
    val error: String? = null,
    val message: String? = null,
    val currentFilter: String? = null,
    val searchQuery: String = ""
)

/**
 * Factory for creating HistoryViewModel instances
 */
class HistoryViewModelFactory(
    private val application: Application
) : androidx.lifecycle.ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(HistoryViewModel::class.java)) {
            return HistoryViewModel(application) as T
        }
        throw IllegalArgumentException("Unknown ViewModel class")
    }
}
