package com.example.autoglm.ui.theme

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/**
 * 主题模式枚举
 */
enum class ThemeMode {
    LIGHT,  // 亮色模式
    DARK    // 暗色模式
}

/**
 * 亮色主题配色方案
 * 基于Material Design 3规范，使用完整的颜色token系统
 */
private val LightColorScheme = lightColorScheme(
    // Primary - 主色调
    primary = md_theme_light_primary,
    onPrimary = md_theme_light_onPrimary,
    primaryContainer = md_theme_light_primaryContainer,
    onPrimaryContainer = md_theme_light_onPrimaryContainer,

    // Secondary - 次要色调
    secondary = md_theme_light_secondary,
    onSecondary = md_theme_light_onSecondary,
    secondaryContainer = md_theme_light_secondaryContainer,
    onSecondaryContainer = md_theme_light_onSecondaryContainer,

    // Tertiary - 第三色调
    tertiary = md_theme_light_tertiary,
    onTertiary = md_theme_light_onTertiary,
    tertiaryContainer = md_theme_light_tertiaryContainer,
    onTertiaryContainer = md_theme_light_onTertiaryContainer,

    // Error - 错误色调
    error = md_theme_light_error,
    onError = md_theme_light_onError,
    errorContainer = md_theme_light_errorContainer,
    onErrorContainer = md_theme_light_onErrorContainer,

    // Background - 背景色
    background = md_theme_light_background,
    onBackground = md_theme_light_onBackground,

    // Surface - 表面色
    surface = md_theme_light_surface,
    onSurface = md_theme_light_onSurface,
    surfaceVariant = md_theme_light_surfaceVariant,
    onSurfaceVariant = md_theme_light_onSurfaceVariant,

    // Surface Container - 容器层级系统（Material3新增）
    surfaceContainerLowest = md_theme_light_surfaceContainerLowest,
    surfaceContainerLow = md_theme_light_surfaceContainerLow,
    surfaceContainer = md_theme_light_surfaceContainer,
    surfaceContainerHigh = md_theme_light_surfaceContainerHigh,
    surfaceContainerHighest = md_theme_light_surfaceContainerHighest,

    // Outline - 轮廓和分隔线
    outline = md_theme_light_outline,
    outlineVariant = md_theme_light_outlineVariant,

    // Inverse - 反色（用于Snackbar等）
    inverseSurface = md_theme_light_inverseSurface,
    inverseOnSurface = md_theme_light_inverseOnSurface,
    inversePrimary = md_theme_light_inversePrimary,

    // Scrim - 遮罩层
    scrim = md_theme_light_scrim
)

/**
 * 暗色主题配色方案（深灰背景）
 * 基于Material Design 3规范，使用完整的颜色token系统
 */
private val DarkColorScheme = darkColorScheme(
    // Primary - 主色调
    primary = md_theme_dark_primary,
    onPrimary = md_theme_dark_onPrimary,
    primaryContainer = md_theme_dark_primaryContainer,
    onPrimaryContainer = md_theme_dark_onPrimaryContainer,

    // Secondary - 次要色调
    secondary = md_theme_dark_secondary,
    onSecondary = md_theme_dark_onSecondary,
    secondaryContainer = md_theme_dark_secondaryContainer,
    onSecondaryContainer = md_theme_dark_onSecondaryContainer,

    // Tertiary - 第三色调
    tertiary = md_theme_dark_tertiary,
    onTertiary = md_theme_dark_onTertiary,
    tertiaryContainer = md_theme_dark_tertiaryContainer,
    onTertiaryContainer = md_theme_dark_onTertiaryContainer,

    // Error - 错误色调
    error = md_theme_dark_error,
    onError = md_theme_dark_onError,
    errorContainer = md_theme_dark_errorContainer,
    onErrorContainer = md_theme_dark_onErrorContainer,

    // Background - 背景色
    background = md_theme_dark_background,
    onBackground = md_theme_dark_onBackground,

    // Surface - 表面色
    surface = md_theme_dark_surface,
    onSurface = md_theme_dark_onSurface,
    surfaceVariant = md_theme_dark_surfaceVariant,
    onSurfaceVariant = md_theme_dark_onSurfaceVariant,

    // Surface Container - 容器层级系统（Material3新增）
    surfaceContainerLowest = md_theme_dark_surfaceContainerLowest,
    surfaceContainerLow = md_theme_dark_surfaceContainerLow,
    surfaceContainer = md_theme_dark_surfaceContainer,
    surfaceContainerHigh = md_theme_dark_surfaceContainerHigh,
    surfaceContainerHighest = md_theme_dark_surfaceContainerHighest,

    // Outline - 轮廓和分隔线
    outline = md_theme_dark_outline,
    outlineVariant = md_theme_dark_outlineVariant,

    // Inverse - 反色（用于Snackbar等）
    inverseSurface = md_theme_dark_inverseSurface,
    inverseOnSurface = md_theme_dark_inverseOnSurface,
    inversePrimary = md_theme_dark_inversePrimary,

    // Scrim - 遮罩层
    scrim = md_theme_dark_scrim
)

/**
 * Pure Black主题配色方案（OLED优化）
 * 继承暗色主题，只修改背景和表面颜色为纯黑
 * 适用于OLED屏幕，可以节省电量并提供更深邃的视觉效果
 */
private val PureBlackColorScheme = darkColorScheme(
    // Primary - 主色调（继承暗色主题）
    primary = md_theme_dark_primary,
    onPrimary = md_theme_dark_onPrimary,
    primaryContainer = md_theme_dark_primaryContainer,
    onPrimaryContainer = md_theme_dark_onPrimaryContainer,

    // Secondary - 次要色调（继承暗色主题）
    secondary = md_theme_dark_secondary,
    onSecondary = md_theme_dark_onSecondary,
    secondaryContainer = md_theme_dark_secondaryContainer,
    onSecondaryContainer = md_theme_dark_onSecondaryContainer,

    // Tertiary - 第三色调（继承暗色主题）
    tertiary = md_theme_dark_tertiary,
    onTertiary = md_theme_dark_onTertiary,
    tertiaryContainer = md_theme_dark_tertiaryContainer,
    onTertiaryContainer = md_theme_dark_onTertiaryContainer,

    // Error - 错误色调（继承暗色主题）
    error = md_theme_dark_error,
    onError = md_theme_dark_onError,
    errorContainer = md_theme_dark_errorContainer,
    onErrorContainer = md_theme_dark_onErrorContainer,

    // Background - 纯黑背景
    background = md_theme_pure_black_background,
    onBackground = md_theme_dark_onBackground,

    // Surface - 纯黑表面
    surface = md_theme_pure_black_surface,
    onSurface = md_theme_dark_onSurface,
    surfaceVariant = md_theme_pure_black_surface,  // 纯黑 #000000
    onSurfaceVariant = md_theme_dark_onSurfaceVariant,

    // Surface Container - Pure Black容器层级系统
    surfaceContainerLowest = md_theme_pure_black_surfaceContainerLowest,
    surfaceContainerLow = md_theme_pure_black_surfaceContainerLow,
    surfaceContainer = md_theme_pure_black_surfaceContainer,
    surfaceContainerHigh = md_theme_pure_black_surfaceContainerHigh,
    surfaceContainerHighest = md_theme_pure_black_surfaceContainerHighest,

    // Outline - 轮廓和分隔线（继承暗色主题）
    outline = md_theme_dark_outline,
    outlineVariant = md_theme_dark_outlineVariant,

    // Inverse - 反色（Pure Black版本）
    inverseSurface = md_theme_pure_black_inverseSurface,
    inverseOnSurface = md_theme_pure_black_inverseOnSurface,
    inversePrimary = md_theme_dark_inversePrimary,

    // Scrim - 遮罩层（继承暗色主题）
    scrim = md_theme_dark_scrim,

    // ✅ 关键：禁用Surface Tint（保持纯黑）
    // Material 3的Tonal Elevation会在有elevation的组件上覆盖Primary颜色
    // 在Pure Black模式下必须禁用，否则黑色背景会变成深蓝色
    surfaceTint = Color.Transparent
)

/**
 * AutoGLM应用主题
 *
 * 支持Material Design 3的完整特性：
 * - 亮色/暗色主题
 * - Pure Black模式（OLED优化）
 * - 动态颜色（Android 12+）
 * - 完整的Surface Container层级系统
 *
 * @param themeMode 主题模式（亮色/暗色）
 * @param pureBlackEnabled 是否启用Pure Black（仅在暗色模式下生效）
 * @param dynamicColor 是否启用动态颜色（Android 12+）
 * @param content 应用内容
 */
@Composable
fun AutoGLMTheme(
    themeMode: ThemeMode = ThemeMode.LIGHT,
    pureBlackEnabled: Boolean = false,
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit
) {
    val context = LocalContext.current
    val isDark = themeMode == ThemeMode.DARK

    // ✅ 性能优化：缓存 colorScheme 计算结果，避免每次重组时重新计算
    val colorScheme = remember(themeMode, pureBlackEnabled, dynamicColor) {
        when {
            // Pure Black模式优先（仅暗色主题）
            // Pure Black模式下不使用动态颜色，以确保纯黑背景
            isDark && pureBlackEnabled -> PureBlackColorScheme

            // Android 12+ 支持动态颜色
            dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
                if (isDark) {
                    dynamicDarkColorScheme(context)
                } else {
                    dynamicLightColorScheme(context)
                }
            }

            // 标准暗色主题
            isDark -> DarkColorScheme

            // 标准亮色主题
            else -> LightColorScheme
        }
    }

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            // 安全检查：只在 Activity Context 中设置系统栏
            val context = view.context
            if (context is Activity) {
                val window = context.window

                // 设置状态栏颜色（使用主题的surface颜色）
                window.statusBarColor = colorScheme.surface.toArgb()

                // 设置导航栏颜色（使用主题的surface颜色）
                window.navigationBarColor = colorScheme.surface.toArgb()

                // 禁用边到边显示，避免内容被系统栏遮挡
                WindowCompat.setDecorFitsSystemWindows(window, true)

                // 设置状态栏和导航栏图标颜色
                val insetsController = WindowCompat.getInsetsController(window, view)
                insetsController.isAppearanceLightStatusBars = !isDark
                insetsController.isAppearanceLightNavigationBars = !isDark
            }
            // WindowContext 或其他 Context 类型：跳过系统栏设置
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        shapes = Shapes,
        content = content
    )
}
