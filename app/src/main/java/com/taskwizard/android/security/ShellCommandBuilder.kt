package com.taskwizard.android.security

import android.util.Log

/**
 * ShellCommandBuilder - Secure shell command builder with whitelisting and validation
 *
 * Prevents command injection attacks by:
 * 1. Whitelisting allowed commands
 * 2. Validating all parameters before building commands
 * 3. Sanitizing input to prevent injection attempts
 *
 * Security: Only allows predefined command patterns and validates all inputs
 */
object ShellCommandBuilder {
    private const val TAG = "ShellCommandBuilder"

    // Whitelist of allowed commands
    val ALLOWED_COMMANDS = setOf(
        "input",      // Input simulation (tap, swipe, keyevent)
        "monkey",     // App launching
        "ime",        // Input method management
        "pm",         // Package manager
        "settings"    // System settings
    )

    // Whitelist of allowed key events
    private val ALLOWED_KEYEVENTS = setOf(
        "KEYCODE_HOME",
        "KEYCODE_BACK",
        "KEYCODE_ENTER",
        "KEYCODE_MENU",
        "KEYCODE_SEARCH",
        "KEYCODE_VOLUME_UP",
        "KEYCODE_VOLUME_DOWN",
        "KEYCODE_POWER",
        "KEYCODE_DPAD_UP",
        "KEYCODE_DPAD_DOWN",
        "KEYCODE_DPAD_LEFT",
        "KEYCODE_DPAD_RIGHT",
        "KEYCODE_DPAD_CENTER",
        "KEYCODE_TAB",
        "KEYCODE_SPACE"
    )

    // Valid package name regex (Android package naming convention)
    private val PACKAGE_NAME_PATTERN = Regex("^([a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+)$")

    /**
     * Validate if a command is in the whitelist
     */
    fun isCommandAllowed(command: String): Boolean {
        val commandName = command.split(" ").firstOrNull()
        return commandName in ALLOWED_COMMANDS
    }

    /**
     * Build a tap command with coordinate validation
     *
     * @param x X coordinate (must be non-negative)
     * @param y Y coordinate (must be non-negative)
     * @return Safe tap command string
     * @throws IllegalArgumentException if coordinates are invalid
     */
    fun buildTapCommand(x: Int, y: Int): String {
        require(x >= 0) { "Invalid X coordinate: $x (must be >= 0)" }
        require(y >= 0) { "Invalid Y coordinate: $y (must be >= 0)" }

        return "input tap $x $y"
    }

    /**
     * Build a swipe command with parameter validation
     *
     * @param x1 Start X coordinate
     * @param y1 Start Y coordinate
     * @param x2 End X coordinate
     * @param y2 End Y coordinate
     * @param duration Swipe duration in milliseconds (must be between 0 and 10000)
     * @return Safe swipe command string
     * @throws IllegalArgumentException if parameters are invalid
     */
    fun buildSwipeCommand(x1: Int, y1: Int, x2: Int, y2: Int, duration: Int): String {
        require(x1 >= 0) { "Invalid X1 coordinate: $x1" }
        require(y1 >= 0) { "Invalid Y1 coordinate: $y1" }
        require(x2 >= 0) { "Invalid X2 coordinate: $x2" }
        require(y2 >= 0) { "Invalid Y2 coordinate: $y2" }
        require(duration in 0..10000) { "Invalid duration: $duration (must be 0-10000ms)" }

        return "input swipe $x1 $y1 $x2 $y2 $duration"
    }

    /**
     * Build a key event command with keycode validation
     *
     * @param keyEvent The keycode constant (e.g., KEYCODE_HOME)
     * @return Safe keyevent command string
     * @throws IllegalArgumentException if keycode is not in whitelist
     */
    fun buildKeyEventCommand(keyEvent: String): String {
        require(keyEvent in ALLOWED_KEYEVENTS) {
            "Invalid key event: $keyEvent (not in whitelist). Allowed: ${ALLOWED_KEYEVENTS.joinToString()}"
        }
        return "input keyevent $keyEvent"
    }

    /**
     * Build a monkey command for app launching with package name validation
     *
     * @param packageName The Android package name to launch
     * @return Safe monkey command string
     * @throws IllegalArgumentException if package name is invalid
     */
    fun buildMonkeyCommand(packageName: String): String {
        require(PACKAGE_NAME_PATTERN.matches(packageName)) {
            "Invalid package name: $packageName (must match Android naming convention)"
        }
        return "monkey -p $packageName -c android.intent.category.LAUNCHER 1"
    }

    /**
     * Validate a package name without building a command
     *
     * @param packageName The package name to validate
     * @return true if valid, false otherwise
     */
    fun isValidPackageName(packageName: String): Boolean {
        return PACKAGE_NAME_PATTERN.matches(packageName)
    }

    /**
     * Sanitize a string to prevent command injection
     * Removes dangerous characters that could be used for command chaining
     *
     * @param input The input string to sanitize
     * @return Sanitized string or empty string if input contains dangerous characters
     */
    fun sanitize(input: String): String {
        // Check for dangerous characters that could enable command injection
        val dangerousChars = setOf(';', '|', '&', '$', '`', '(', ')', '<', '>', '\n', '\r')
        if (input.any { it in dangerousChars }) {
            Log.w(TAG, "Rejected input containing dangerous characters: ${input.take(20)}...")
            return ""
        }
        return input
    }

    /**
     * Validate integer coordinates
     *
     * @param value The coordinate value to validate
     * @param maxAllowed The maximum allowed value (for screen bounds)
     * @return true if valid
     */
    fun isValidCoordinate(value: Int, maxAllowed: Int = 10000): Boolean {
        return value in 0..maxAllowed
    }
}
