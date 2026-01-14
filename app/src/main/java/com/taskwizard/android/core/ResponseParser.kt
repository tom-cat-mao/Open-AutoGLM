package com.taskwizard.android.core

import android.util.Log
import java.util.regex.Pattern
import com.taskwizard.android.data.Action
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.toImmutableList

/**
 * Response Parser - Parses LLM responses into structured Actions
 *
 * Performance: Pre-compiled regex patterns for optimal parsing performance
 */
object ResponseParser {

    private const val TAG = "ResponseParser"

    data class ParseResult(
        val think: String?,
        val action: Action?
    )

    // Pre-compiled regex patterns for performance
    // Avoids recompiling patterns on every parse() call
    private val THINK_PATTERN = Pattern.compile("<think>(.*?)</think>", Pattern.DOTALL)
    private val ANSWER_PATTERN = Pattern.compile("<answer>(.*?)</answer>", Pattern.DOTALL)
    private val DO_PATTERN = Pattern.compile("do\\s*\\([^)]+\\)", Pattern.DOTALL)
    private val FINISH_PATTERN = Pattern.compile("finish\\s*\\([^)]+\\)", Pattern.DOTALL)
    private val PARAM_PATTERN = Pattern.compile("(\\w+)=[\"'](.*?)[\"']")
    private val COORD_PATTERN = Pattern.compile("(\\w+)=\\[(\\d+),\\s*(\\d+)\\]")

    fun parse(content: String): ParseResult {
        // 1. Extract Think
        var think = extractTag(content, THINK_PATTERN)

        // 2. If no <think> tag, try to extract implicit thinking
        if (think == null) {
            think = extractImplicitThinking(content)
            if (think != null) {
                Log.d(TAG, "Extracted implicit thinking: ${think.take(50)}...")
            }
        }

        // 3. Extract Answer
        var answer = extractTag(content, ANSWER_PATTERN)

        // 4. If no <answer> tag, try to find do(...) or finish(...) in the raw content
        if (answer == null) {
            Log.d(TAG, "No <answer> tag found, searching for action in raw content")
            answer = extractActionFromRawContent(content)
        }

        // 5. Parse Action String (e.g., do(action="Tap", element=[123,456]))
        val action = if (answer != null) {
            parseActionString(answer)
        } else {
            Log.w(TAG, "No action found in content")
            null
        }

        return ParseResult(think, action)
    }

    /**
     * Extract implicit thinking content before <answer> tag
     */
    private fun extractImplicitThinking(content: String): String? {
        val answerIndex = content.indexOf("<answer>")
        if (answerIndex <= 0) return null

        val beforeAnswer = content.substring(0, answerIndex).trim()
        return if (beforeAnswer.length > 5 && beforeAnswer.isNotBlank()) {
            Log.d(TAG, "Found implicit thinking before <answer> tag")
            beforeAnswer
        } else null
    }

    /**
     * Extract do(...) or finish(...) from raw content
     */
    private fun extractActionFromRawContent(content: String): String? {
        // Try do(...) first
        var matcher = DO_PATTERN.matcher(content)
        if (matcher.find()) {
            val action = matcher.group(0)
            Log.d(TAG, "Found do(...) action: $action")
            return action
        }
        
        // Try finish(...)
        matcher = FINISH_PATTERN.matcher(content)
        if (matcher.find()) {
            val action = matcher.group(0)
            Log.d(TAG, "Found finish(...) action: $action")
            return action
        }
        
        return null
    }

    /**
     * Extract content between XML-style tags
     */
    private fun extractTag(content: String, pattern: Pattern): String? {
        val matcher = pattern.matcher(content)
        return if (matcher.find()) {
            matcher.group(1)?.trim()
        } else null
    }

    /**
     * Parse action string into Action object
     */
    private fun parseActionString(actionStr: String): Action? {
        val trimmed = actionStr.trim()
        
        // Handle finish(message="...")
        if (trimmed.startsWith("finish")) {
            val msg = extractParam(trimmed, "message")
            Log.d(TAG, "Parsed finish action with message: $msg")
            return Action("finish", null, msg)
        }

        // Handle do(...)
        if (trimmed.startsWith("do")) {
            val actionType = extractParam(trimmed, "action")
            
            if (actionType == null) {
                Log.w(TAG, "Failed to extract action type from: $trimmed")
                return null
            }
            
            // Extract coordinates: element=[x,y] or start=[x,y], end=[x,y]
            val element = extractCoordinates(trimmed, "element")
            val start = extractCoordinates(trimmed, "start")
            val end = extractCoordinates(trimmed, "end")
            
            // Extract text/message/app
            val text = extractParam(trimmed, "text") 
                ?: extractParam(trimmed, "message") 
                ?: extractParam(trimmed, "app")
            
            val duration = extractParam(trimmed, "duration")?.toIntOrNull()
            val instruction = extractParam(trimmed, "instruction")

            val location = if (start != null && end != null) {
                (start + end).toImmutableList()
            } else {
                element?.toImmutableList()
            }

            Log.d(TAG, "Parsed action: type=$actionType, location=$location, text=$text, duration=$duration")
            
            return Action(
                action = actionType,
                location = location,
                content = text,
                duration = duration,
                instruction = instruction
            )
        }

        Log.w(TAG, "Unknown action format: $trimmed")
        return null
    }

    /**
     * Extract param="value" from action string
     */
    private fun extractParam(text: String, key: String): String? {
        val pattern = Pattern.compile("$key=[\"'](.*?)[\"']")
        val matcher = pattern.matcher(text)
        return if (matcher.find()) matcher.group(1) else null
    }

    /**
     * Extract key=[x,y] coordinates from action string
     */
    private fun extractCoordinates(text: String, key: String): List<Int>? {
        val pattern = Pattern.compile("$key=\\[(\\d+),\\s*(\\d+)\\]")
        val matcher = pattern.matcher(text)
        if (matcher.find()) {
            try {
                val x = matcher.group(1)?.toInt()
                val y = matcher.group(2)?.toInt()
                if (x != null && y != null) return listOf(x, y)
            } catch (e: Exception) { 
                Log.e(TAG, "Failed to parse coordinates", e)
            }
        }
        return null
    }
}
