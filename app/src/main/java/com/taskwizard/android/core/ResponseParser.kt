package com.taskwizard.android.core

import android.util.Log
import java.util.regex.Pattern
import com.taskwizard.android.data.Action

object ResponseParser {

    private const val TAG = "ResponseParser"

    data class ParseResult(
        val think: String?,
        val action: Action?
    )

    fun parse(content: String): ParseResult {
        // 1. Extract Think
        var think = extractTag(content, "think")

        // 2. If no <think> tag, try to extract implicit thinking
        if (think == null) {
            think = extractImplicitThinking(content)
            if (think != null) {
                Log.d(TAG, "Extracted implicit thinking: ${think.take(50)}...")
            }
        }

        // 3. Extract Answer
        var answer = extractTag(content, "answer")

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
     * 提取隐式 thinking 内容
     * 当模型没有使用 <think> 标签，但在 <answer> 之前有文本时，提取该文本作为 thinking
     */
    private fun extractImplicitThinking(content: String): String? {
        // 查找第一个 <answer> 标签的位置
        val answerIndex = content.indexOf("<answer>")
        if (answerIndex <= 0) {
            // 没有 <answer> 标签，或者 <answer> 在开头
            return null
        }

        // 提取 <answer> 之前的所有文本
        val beforeAnswer = content.substring(0, answerIndex).trim()

        // 如果文本有意义（长度 > 5 且不全是空白），返回它
        if (beforeAnswer.length > 5 && beforeAnswer.isNotBlank()) {
            Log.d(TAG, "Found implicit thinking before <answer> tag")
            return beforeAnswer
        }

        return null
    }

    /**
     * 从原始内容中提取 do(...) 或 finish(...) 指令
     * 用于处理模型没有使用 <answer> 标签的情况
     */
    private fun extractActionFromRawContent(content: String): String? {
        // 尝试匹配 do(...) 或 finish(...)
        // 使用更宽松的正则，匹配整个函数调用
        val doPattern = Pattern.compile("do\\s*\\([^)]+\\)", Pattern.DOTALL)
        val finishPattern = Pattern.compile("finish\\s*\\([^)]+\\)", Pattern.DOTALL)
        
        // 先尝试匹配 do(...)
        var matcher = doPattern.matcher(content)
        if (matcher.find()) {
            val action = matcher.group(0)
            Log.d(TAG, "Found do(...) action: $action")
            return action
        }
        
        // 再尝试匹配 finish(...)
        matcher = finishPattern.matcher(content)
        if (matcher.find()) {
            val action = matcher.group(0)
            Log.d(TAG, "Found finish(...) action: $action")
            return action
        }
        
        return null
    }

    private fun extractTag(content: String, tag: String): String? {
        val pattern = Pattern.compile("<$tag>(.*?)</$tag>", Pattern.DOTALL)
        val matcher = pattern.matcher(content)
        return if (matcher.find()) {
            matcher.group(1)?.trim()
        } else {
            null
        }
    }

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
            
            // For Swipe
            val start = extractCoordinates(trimmed, "start")
            val end = extractCoordinates(trimmed, "end")
            
            // Extract text/message/app
            val text = extractParam(trimmed, "text") 
                ?: extractParam(trimmed, "message") 
                ?: extractParam(trimmed, "app")
            
            // Phase 2: Extract duration and instruction
            val duration = extractParam(trimmed, "duration")?.toIntOrNull()
            val instruction = extractParam(trimmed, "instruction")

            // Combine for Action object
            // If it's swipe, location needs 4 coords
            val location = if (start != null && end != null) {
                start + end
            } else {
                element
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

    // Helper to extract param="value"
    private fun extractParam(text: String, key: String): String? {
        // Matches key="value" or key='value'
        val pattern = Pattern.compile("$key=[\"'](.*?)[\"']")
        val matcher = pattern.matcher(text)
        if (matcher.find()) return matcher.group(1)
        return null
    }

    // Helper to extract key=[x,y]
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
