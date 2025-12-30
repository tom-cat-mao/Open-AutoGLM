package com.taskwizard.android.ui.screens

import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.navArgument
import com.taskwizard.android.ui.viewmodel.HistoryViewModel
import com.taskwizard.android.ui.viewmodel.HistoryViewModelFactory
import com.taskwizard.android.ui.viewmodel.MainViewModel
import com.taskwizard.android.ui.viewmodel.TasksViewModel
import com.taskwizard.android.ui.viewmodel.TasksViewModelFactory

/**
 * 导航路由定义
 */
object NavRoutes {
    const val MAIN = "main"
    const val MAIN_WITH_HISTORY = "main?historyId={historyId}"
    const val SETTINGS = "settings"
    const val HISTORY = "history"
    const val TASKS = "tasks"
}

/**
 * 应用导航图
 *
 * 性能优化：动画时长从300ms降低到220ms，提升流畅度
 *
 * @param navController 导航控制器
 * @param viewModel 共享的ViewModel实例
 * @param modifier 修饰符
 */
@OptIn(ExperimentalAnimationApi::class)
@Composable
fun AppNavGraph(
    navController: NavHostController,
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    NavHost(
        navController = navController,
        startDestination = NavRoutes.MAIN,
        modifier = modifier,
        enterTransition = {
            // 从右向左滑入
            slideInHorizontally(
                initialOffsetX = { fullWidth -> fullWidth },
                animationSpec = tween(220)
            ) + fadeIn(animationSpec = tween(220))
        },
        exitTransition = {
            // 向左滑出并淡出
            slideOutHorizontally(
                targetOffsetX = { fullWidth -> -fullWidth / 4 },
                animationSpec = tween(220)
            ) + fadeOut(animationSpec = tween(220))
        },
        popEnterTransition = {
            // 从左向右滑入
            slideInHorizontally(
                initialOffsetX = { fullWidth -> -fullWidth / 4 },
                animationSpec = tween(220)
            ) + fadeIn(animationSpec = tween(220))
        },
        popExitTransition = {
            // 向右滑出
            slideOutHorizontally(
                targetOffsetX = { fullWidth -> fullWidth },
                animationSpec = tween(220)
            ) + fadeOut(animationSpec = tween(220))
        }
    ) {
        // Main screen with optional history ID parameter
        composable(
            route = NavRoutes.MAIN_WITH_HISTORY,
            arguments = listOf(
                navArgument("historyId") {
                    type = NavType.LongType
                    defaultValue = -1L
                }
            )
        ) { backStackEntry ->
            val historyId = backStackEntry?.arguments?.getLong("historyId") ?: -1L
            val effectiveHistoryId = if (historyId > 0) historyId else null

            MainScreen(
                onNavigateToSettings = {
                    navController.navigate(NavRoutes.SETTINGS)
                },
                onNavigateToHistory = {
                    navController.navigate(NavRoutes.HISTORY)
                },
                onNavigateToTasks = {
                    navController.navigate(NavRoutes.TASKS)
                },
                viewModel = viewModel,
                historyIdToLoad = effectiveHistoryId
            )
        }

        // Settings screen
        composable(NavRoutes.SETTINGS) {
            SettingsScreen(
                onNavigateBack = {
                    navController.popBackStack()
                },
                viewModel = viewModel
            )
        }

        // History screen
        composable(NavRoutes.HISTORY) {
            val historyViewModel = androidx.lifecycle.viewmodel.compose.viewModel<HistoryViewModel>(
                factory = HistoryViewModelFactory(
                    // Get Application from LocalContext in the composable
                    androidx.compose.ui.platform.LocalContext.current.applicationContext as android.app.Application
                )
            )
            HistoryScreen(
                onNavigateBack = {
                    navController.popBackStack()
                },
                onContinueConversation = { historyId ->
                    navController.navigate("main?historyId=$historyId") {
                        // Clear back stack to prevent going back to history while running
                        popUpTo(NavRoutes.HISTORY) { inclusive = false }
                    }
                },
                viewModel = historyViewModel
            )
        }

        // Tasks screen
        composable(NavRoutes.TASKS) {
            val tasksViewModel = androidx.lifecycle.viewmodel.compose.viewModel<TasksViewModel>(
                factory = TasksViewModelFactory(
                    androidx.compose.ui.platform.LocalContext.current.applicationContext as android.app.Application
                )
            )
            TasksScreen(
                onNavigateBack = {
                    navController.popBackStack()
                },
                viewModel = tasksViewModel
            )
        }
    }
}
