package com.taskwizard.android.ui.viewmodel

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.taskwizard.android.IAutoGLMService
import com.taskwizard.android.core.TemplateExecutor
import com.taskwizard.android.data.history.TaskHistoryDatabase
import com.taskwizard.android.data.template.TaskTemplateEntity
import com.taskwizard.android.data.template.TemplateRepository
import com.taskwizard.android.manager.ShizukuManager
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch

/**
 * Tasks 页面 ViewModel
 */
class TasksViewModel(application: Application) : AndroidViewModel(application) {

    companion object {
        private const val TAG = "TasksViewModel"
    }

    private val db = TaskHistoryDatabase.getDatabase(application)
    private val repository = TemplateRepository(db.templateDao())

    // 模板列表
    val templates: StateFlow<List<TaskTemplateEntity>> = repository.getAllTemplates()
        .stateIn(viewModelScope, SharingStarted.Lazily, emptyList())

    // 执行状态
    private val _isExecuting = MutableStateFlow(false)
    val isExecuting: StateFlow<Boolean> = _isExecuting.asStateFlow()

    private val _executionProgress = MutableStateFlow<String?>(null)
    val executionProgress: StateFlow<String?> = _executionProgress.asStateFlow()

    /**
     * 执行模板
     */
    fun executeTemplate(template: TaskTemplateEntity) {
        if (_isExecuting.value) {
            Log.w(TAG, "Already executing")
            return
        }

        viewModelScope.launch {
            _isExecuting.value = true
            _executionProgress.value = "准备执行..."

            try {
                // 绑定 Shizuku 服务
                val service = ShizukuManager.bindService(getApplication())

                // 获取实际屏幕尺寸
                val displayMetrics = getApplication<Application>().resources.displayMetrics
                val screenWidth = displayMetrics.widthPixels
                val screenHeight = displayMetrics.heightPixels
                Log.d(TAG, "Screen size: ${screenWidth}x${screenHeight}")

                // 创建执行器
                val executor = TemplateExecutor(
                    context = getApplication(),
                    service = service,
                    screenWidth = screenWidth,
                    screenHeight = screenHeight
                )

                // 执行模板
                val result = executor.execute(template) { step, total, action ->
                    _executionProgress.value = "执行中: $step/$total"
                }

                // 更新使用记录
                repository.updateUsage(template.id)

                // 处理结果
                when (result) {
                    is TemplateExecutor.ExecutionResult.Success -> {
                        _executionProgress.value = "完成 (${result.stepCount} 步)"
                        Log.i(TAG, "Template executed: ${template.name}")
                    }
                    is TemplateExecutor.ExecutionResult.Failure -> {
                        _executionProgress.value = "失败: ${result.error}"
                        Log.e(TAG, "Template failed: ${result.error}")
                    }
                    is TemplateExecutor.ExecutionResult.Cancelled -> {
                        _executionProgress.value = "已取消"
                    }
                }

            } catch (e: Exception) {
                Log.e(TAG, "Execution error", e)
                _executionProgress.value = "错误: ${e.message}"
            } finally {
                _isExecuting.value = false
            }
        }
    }

    /**
     * 删除模板
     */
    fun deleteTemplate(template: TaskTemplateEntity) {
        viewModelScope.launch {
            try {
                repository.delete(template)
                Log.i(TAG, "Template deleted: ${template.name}")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to delete template", e)
            }
        }
    }
}
