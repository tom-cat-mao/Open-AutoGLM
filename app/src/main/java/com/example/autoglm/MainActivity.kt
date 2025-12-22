package com.example.autoglm

import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Modifier
import androidx.navigation.compose.rememberNavController
import com.example.autoglm.ui.screens.AppNavGraph
import com.example.autoglm.ui.theme.AutoGLMTheme
import com.example.autoglm.ui.viewmodel.MainViewModel

/**
 * MainActivity - Compose版本
 *
 * 阶段3：集成Navigation Compose导航系统
 * - 使用NavHost管理页面导航
 * - 支持主页面和设置页面切换
 * - 流畅的转场动画
 * - 响应主题变化
 */
class MainActivity : ComponentActivity() {

    // 关键修改：在 Activity 级别创建 ViewModel，确保整个生命周期中只有一个实例
    private val viewModel: MainViewModel by viewModels()

    // 用于触发 Compose 重组的状态变量
    private var refreshTrigger by mutableStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        Log.d("MainActivity", "onCreate called")
        Log.d("MainActivity", "Intent extras: ${intent.extras}")

        // 隐藏ActionBar（如果存在）
        actionBar?.hide()

        setContent {
            // 监听 refreshTrigger 变化，触发重组
            val trigger = refreshTrigger
            Log.d("MainActivity", "Compose recomposition triggered, trigger=$trigger")

            // 关键修改：直接使用 Activity 级别的 viewModel，不再调用 viewModel()
            val state by viewModel.state.collectAsStateWithLifecycle()

            // 监听moveToBackground信号
            val shouldMoveToBackground by viewModel.shouldMoveToBackground.collectAsStateWithLifecycle()

            // 当需要移到后台时，执行moveTaskToBack（不finish，保留ViewModel和消息历史）
            androidx.compose.runtime.LaunchedEffect(shouldMoveToBackground) {
                if (shouldMoveToBackground) {
                    // 移到后台，不销毁Activity
                    moveTaskToBack(true)
                    // 关键修复：立即重置动画状态，防止Activity被标记为透明
                    viewModel.setAnimatingToOverlay(false)
                    // 重置标志
                    viewModel.resetMoveToBackgroundFlag()
                }
            }

            // 创建导航控制器
            val navController = rememberNavController()

            // 应用主题（响应ViewModel状态变化）
            AutoGLMTheme(
                themeMode = state.themeMode,
                pureBlackEnabled = state.pureBlackEnabled
            ) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    // 导航图（传递共享的ViewModel）
                    AppNavGraph(
                        navController = navController,
                        viewModel = viewModel
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // 关键：更新 Activity 的 Intent，确保 getIntent() 返回最新的 Intent
        setIntent(intent)

        Log.d("MainActivity", "onNewIntent called with extras: ${intent.extras}")

        // 处理从悬浮窗返回的场景
        val fromOverlay = intent.getBooleanExtra("FROM_OVERLAY", false)
        if (fromOverlay) {
            Log.d("MainActivity", "Returned from overlay, triggering UI refresh")
            // 清除标志，避免重复处理
            intent.removeExtra("FROM_OVERLAY")

            // 关键：更新 refreshTrigger 触发 Compose 重组
            refreshTrigger++
            Log.d("MainActivity", "refreshTrigger updated to $refreshTrigger")
        }
    }

    override fun onResume() {
        super.onResume()
        Log.d("MainActivity", "onResume called")

        // 关键修复：强制刷新状态，确保UI与ViewModel同步
        // 解决从后台返回时，任务已停止但UI未更新的问题
        viewModel.refreshState()
        Log.d("MainActivity", "State refreshed after onResume")
    }
}
