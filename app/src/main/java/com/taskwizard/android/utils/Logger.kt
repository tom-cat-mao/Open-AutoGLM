package com.taskwizard.android.utils

import android.util.Log
import com.taskwizard.android.BuildConfig

/**
 * Logger - Conditional logging utility for performance optimization
 *
 * This logger only outputs logs in DEBUG builds, reducing overhead in production.
 * In release builds, log calls become no-ops for better performance.
 *
 * Usage:
 * - Logger.d("MyTag", "Debug message")  // Only logged in DEBUG builds
 * - Logger.w("MyTag", "Warning message") // Always logged
 * - Logger.e("MyTag", "Error", exception) // Always logged
 */
object Logger {
    /**
     * Debug level logging - only in DEBUG builds
     */
    fun d(tag: String, msg: String) {
        if (BuildConfig.DEBUG) {
            Log.d(tag, msg)
        }
    }

    /**
     * Debug level logging with throwable - only in DEBUG builds
     */
    fun d(tag: String, msg: String, tr: Throwable) {
        if (BuildConfig.DEBUG) {
            Log.d(tag, msg, tr)
        }
    }

    /**
     * Info level logging - only in DEBUG builds
     */
    fun i(tag: String, msg: String) {
        if (BuildConfig.DEBUG) {
            Log.i(tag, msg)
        }
    }

    /**
     * Info level logging with throwable - only in DEBUG builds
     */
    fun i(tag: String, msg: String, tr: Throwable) {
        if (BuildConfig.DEBUG) {
            Log.i(tag, msg, tr)
        }
    }

    /**
     * Warning level logging - always logged
     */
    fun w(tag: String, msg: String) {
        Log.w(tag, msg)
    }

    /**
     * Warning level logging with throwable - always logged
     */
    fun w(tag: String, msg: String, tr: Throwable) {
        Log.w(tag, msg, tr)
    }

    /**
     * Warning level logging with throwable only - always logged
     */
    fun w(tag: String, tr: Throwable) {
        Log.w(tag, tr)
    }

    /**
     * Error level logging - always logged
     */
    fun e(tag: String, msg: String) {
        Log.e(tag, msg)
    }

    /**
     * Error level logging with throwable - always logged
     */
    fun e(tag: String, msg: String, tr: Throwable) {
        Log.e(tag, msg, tr)
    }

    /**
     * Verbose level logging - only in DEBUG builds
     */
    fun v(tag: String, msg: String) {
        if (BuildConfig.DEBUG) {
            Log.v(tag, msg)
        }
    }

    /**
     * Verbose level logging with throwable - only in DEBUG builds
     */
    fun v(tag: String, msg: String, tr: Throwable) {
        if (BuildConfig.DEBUG) {
            Log.v(tag, msg, tr)
        }
    }

    /**
     * Assert logging - only in DEBUG builds
     */
    fun wtf(tag: String, msg: String) {
        if (BuildConfig.DEBUG) {
            Log.wtf(tag, msg)
        }
    }

    /**
     * Assert logging with throwable - only in DEBUG builds
     */
    fun wtf(tag: String, msg: String, tr: Throwable) {
        if (BuildConfig.DEBUG) {
            Log.wtf(tag, msg, tr)
        }
    }

    /**
     * Check if logging is enabled for the given tag
     */
    fun isLoggable(tag: String, level: Int): Boolean {
        return Log.isLoggable(tag, level)
    }
}
