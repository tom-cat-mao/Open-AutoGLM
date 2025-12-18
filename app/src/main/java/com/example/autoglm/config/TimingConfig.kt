package com.example.autoglm.config

/**
 * Timing configuration for Phone Agent.
 * 
 * This module defines all configurable waiting times used throughout the application.
 * Strictly aligned with phone_agent/config/timing.py from Open-AutoGLM.
 * 
 * Phase 4: Safety & Timing
 */

/**
 * Configuration for action handler timing delays.
 * Aligned with ActionTimingConfig in timing.py
 */
data class ActionTimingConfig(
    // Text input related delays (in milliseconds)
    // Python default: 1.0 seconds = 1000 ms
    val keyboardSwitchDelay: Long = 1000,      // Delay after switching to ADB keyboard
    val textClearDelay: Long = 1000,           // Delay after clearing text
    val textInputDelay: Long = 1000,           // Delay after typing text
    val keyboardRestoreDelay: Long = 1000      // Delay after restoring original keyboard
)

/**
 * Configuration for device operation timing delays.
 * Aligned with DeviceTimingConfig in timing.py
 */
data class DeviceTimingConfig(
    // Default delays for various device operations (in milliseconds)
    // Python default: 1.0 seconds = 1000 ms
    val defaultTapDelay: Long = 1000,          // Default delay after tap
    val defaultDoubleTapDelay: Long = 1000,    // Default delay after double tap
    val doubleTapInterval: Long = 100,         // Interval between two taps in double tap (0.1s)
    val defaultLongPressDelay: Long = 1000,    // Default delay after long press
    val defaultSwipeDelay: Long = 1000,        // Default delay after swipe
    val defaultBackDelay: Long = 1000,         // Default delay after back button
    val defaultHomeDelay: Long = 1000,         // Default delay after home button
    val defaultLaunchDelay: Long = 1000        // Default delay after launching app
)

/**
 * Master timing configuration combining all timing settings.
 * Aligned with TimingConfig in timing.py
 * 
 * Global singleton instance accessible throughout the application.
 */
object TimingConfig {
    // 使用 val 直接初始化，避免重复定义
    val action: ActionTimingConfig = ActionTimingConfig()
    val device: DeviceTimingConfig = DeviceTimingConfig()
}
