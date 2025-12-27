package com.taskwizard.android.core

import android.content.Context
import android.util.Log
import com.taskwizard.android.api.ApiClient
import com.taskwizard.android.data.*
import com.taskwizard.android.config.SystemPrompt
import com.taskwizard.android.config.AppMap
import com.taskwizard.android.manager.ShizukuManager
import com.taskwizard.android.utils.SettingsManager
import android.util.Base64
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class AgentCore(
    private val context: Context,
    private val onError: ((String) -> Unit)? = null  // 错误回调
) {
    private val history = mutableListOf<Message>()
    private var isRunning = false
    private var isFirstStep = true

    // Phase 2: Note 管理
    private val notes = mutableListOf<String>()

    // We only expose the last thought for UI
    var lastThink: String? = null

    // 保存上一次的 thinking，用于模型省略 thinking 时的 fallback
    private var previousThink: String? = null

    fun startSession(task: String) {
        history.clear()
        notes.clear()  // 清空 notes
        isFirstStep = true
        previousThink = null  // 重置 previousThink
        // Use the new dynamic System Prompt
        history.add(Message("system", SystemPrompt.get()))
        // Store the task, we'll add Screen Info in the first step
        history.add(Message("user", "Task: $task"))
        isRunning = true
    }

    fun stop() {
        isRunning = false
    }
    
    fun isSessionRunning(): Boolean {
        return isRunning
    }
    
    /**
     * Phase 2: 添加 Note
     */
    fun addNote(note: String) {
        notes.add(note)
        Log.d("AgentCore", "Note added: $note (total: ${notes.size})")
    }
    
    /**
     * Phase 2: 获取所有 Notes
     */
    fun getNotes(): List<String> {
        return notes.toList()
    }

    /**
     * Get API message history for context restoration
     * Returns a copy of the current history list
     */
    fun getApiHistory(): List<Message> {
        return history.toList()
    }

    /**
     * Restore a session from history with partial API context
     * @param task Original task description
     * @param apiHistory Partial API message history (last ~20 messages)
     */
    fun restoreSession(task: String, apiHistory: List<Message>) {
        history.clear()
        notes.clear()
        isFirstStep = true
        previousThink = null

        // Add system prompt
        history.add(Message("system", SystemPrompt.get()))

        // Add partial API history for context (exclude system prompt if present)
        apiHistory.filter { it.role != "system" }.forEach { history.add(it) }

        // Add task message
        history.add(Message("user", "Task: $task (continuing from history)"))

        // Don't auto-start, let user click start button
        isRunning = false

        Log.d("AgentCore", "Session restored with ${apiHistory.size} context messages, task: $task")
    }

    /**
     * 构建 Screen Info JSON 字符串
     * 对齐 Python 版本的 MessageBuilder.build_screen_info()
     */
    private suspend fun buildScreenInfo(): String {
        val info = JSONObject()
        
        // 1. 获取当前时间
        val currentTime = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())
        info.put("current_time", currentTime)
        
        // 2. 获取当前应用
        try {
            val service = ShizukuManager.bindService(context)
            val packageName = service.getCurrentPackage()
            
            val appName = if (packageName.isNotEmpty()) {
                // 使用 AppMap 将包名转换为应用名
                AppMap.getAppName(packageName) ?: packageName
            } else {
                "System Home"
            }
            
            info.put("current_app", appName)
            Log.d("AgentCore", "Current app: $appName (package: $packageName)")
        } catch (e: Exception) {
            Log.e("AgentCore", "Failed to get current app", e)
            info.put("current_app", "Unknown")
        }
        
        return info.toString()
    }
    
    /**
     * Phase 5: 从消息中移除图片内容以节省上下文空间
     * 严格对齐原版 Python client.py 的 MessageBuilder.remove_images_from_message()
     * 
     * @param message 原始消息
     * @return 移除图片后的消息（只保留文本内容）
     */
    private fun removeImagesFromMessage(message: Message): Message {
        // 如果 content 是 List<ContentPart>，过滤掉 image_url 类型
        if (message.content is List<*>) {
            val contentParts = message.content as List<ContentPart>
            val textOnlyParts = contentParts.filter { it.type == "text" }
            
            Log.d("AgentCore", "Removed images from message: ${contentParts.size} parts -> ${textOnlyParts.size} text parts")
            
            return Message(message.role, textOnlyParts)
        }
        
        // 如果 content 是 String，直接返回
        return message
    }
    
    /**
     * Phase 2: Call_API - 基于 Notes 请求摘要
     */
    suspend fun callAPI(instruction: String): String? {
        if (notes.isEmpty()) {
            Log.w("AgentCore", "No notes to summarize")
            return "没有记录的信息"
        }
        
        try {
            // 构建摘要请求
            val notesText = notes.joinToString("\n\n") { "- $it" }
            val prompt = """
                根据以下记录的信息，${instruction}：
                
                $notesText
                
                请提供简洁的总结或回答。
            """.trimIndent()
            
            // 创建临时消息列表用于摘要请求
            val summaryMessages = listOf(
                Message("system", "你是一个智能助手，擅长总结和分析信息。"),
                Message("user", prompt)
            )
            
            Log.d("AgentCore", "Calling API for summary with ${notes.size} notes")
            
            val response = ApiClient.getService().chatCompletion(
                OpenAIRequest(
                    model = SettingsManager.model,
                    messages = summaryMessages,
                    max_tokens = 512,
                    temperature = 0.3
                )
            )
            
            if (response.isSuccessful && response.body() != null) {
                val summary = response.body()!!.choices.first().message.content
                Log.d("AgentCore", "API summary: $summary")
                return summary
            } else {
                Log.e("AgentCore", "API Error: ${response.code()}")
                return null
            }
        } catch (e: Exception) {
            Log.e("AgentCore", "Call_API failed", e)
            return null
        }
    }

    suspend fun step(screenshotBytes: ByteArray): Action? {
        Log.d("AgentCore", "step() called, isRunning=$isRunning, screenshotSize=${screenshotBytes.size} bytes")

        // ✅ 添加：检查会话是否停止
        if (!isRunning) {
            Log.w("AgentCore", "Session stopped, returning null")
            return null
        }

        // ✅ 添加：协作式取消点
        kotlinx.coroutines.yield()

        // 1. Prepare Image
        val base64Image = Base64.encodeToString(screenshotBytes, Base64.NO_WRAP)
        val imageUrl = "data:image/jpeg;base64,$base64Image"
        Log.d("AgentCore", "Screenshot encoded, base64 length: ${base64Image.length}")

        // ✅ 添加：检查会话是否停止
        if (!isRunning) {
            Log.w("AgentCore", "Session stopped after encoding screenshot")
            return null
        }

        // 2. Build Screen Info (对齐 Python 版本)
        val screenInfo = buildScreenInfo()
        Log.d("AgentCore", "Screen info built: $screenInfo")

        // 3. Prepare User Message with Screen Info
        val textContent = if (isFirstStep) {
            // 首次消息：在任务描述后添加 Screen Info
            val taskMessage = history.lastOrNull { it.role == "user" }?.content as? String ?: ""
            isFirstStep = false
            "$taskMessage\n\n** Screen Info **\n\n$screenInfo"
        } else {
            // 后续消息：只包含 Screen Info
            "** Screen Info **\n\n$screenInfo"
        }

        // 4. Add User Message with Image
        val contentParts = listOf(
            ContentPart(type = "text", text = textContent),
            ContentPart(type = "image_url", image_url = ImageUrl(imageUrl))
        )

        // Optimization: Only keep the LAST image in history to save tokens
        // We remove previous user messages that contained images (lists)
        history.removeAll { it.role == "user" && it.content is List<*> }

        val currentMessage = Message("user", contentParts)
        // Note: We don't add currentMessage to history YET, we send it in request
        // But for history consistency in multi-turn, we usually store a summary.
        // Let's follow the Python logic: send full history.
        val requestMessages = history + currentMessage

        Log.d("AgentCore", "Prepared request with ${requestMessages.size} messages")

        // ✅ 添加：API 调用前检查会话是否停止
        if (!isRunning) {
            Log.w("AgentCore", "Session stopped before API call")
            return null
        }

        // 5. Call API
        Log.d("AgentCore", "Calling API with model=${SettingsManager.model}")
        try {
            val response = ApiClient.getService().chatCompletion(
                OpenAIRequest(
                    model = SettingsManager.model,
                    messages = requestMessages,
                    max_tokens = 1024,
                    temperature = 0.5,
                    top_p = 0.9
                )
            )

            Log.d("AgentCore", "API response received, code=${response.code()}, successful=${response.isSuccessful}")

            // ✅ 添加：API 调用后检查会话是否停止
            if (!isRunning) {
                Log.w("AgentCore", "Session stopped after API call")
                return null
            }

            if (response.isSuccessful && response.body() != null) {
                val responseMsg = response.body()!!.choices.first().message
                val content = responseMsg.content

                Log.d("AgentCore", "AI Response received, length=${content.length}")

                // Phase 5: 严格对齐原版 Python 的上下文管理
                // 1. 先添加当前的 user message（带图片）到历史
                history.add(currentMessage)

                // 2. 立即剥离图片，只保留文本（对齐原版 agent.py line 155）
                // self._context[-1] = MessageBuilder.remove_images_from_message(self._context[-1])
                val lastIndex = history.size - 1
                history[lastIndex] = removeImagesFromMessage(history[lastIndex])

                // 3. 添加 assistant 响应
                history.add(Message("assistant", content))

                Log.d("AgentCore", "AI Response: $content")

                // 6. Parse using the new Regex Parser
                val result = ResponseParser.parse(content)

                // 7. 智能选择 thinking 内容
                lastThink = when {
                    // 情况 1: 模型输出了 thinking
                    result.think != null -> {
                        Log.d("AgentCore", "Using current thinking from model")
                        previousThink = result.think  // 保存为 previousThink
                        result.think
                    }
                    // 情况 2: 模型没有输出 thinking，但有上一次的 thinking
                    previousThink != null -> {
                        Log.d("AgentCore", "Model omitted thinking, using previous thinking")
                        previousThink
                    }
                    // 情况 3: 两者都没有，使用默认文本
                    else -> {
                        Log.d("AgentCore", "No thinking available, using default")
                        "正在执行操作..."
                    }
                }

                Log.d("AgentCore", "Parsed result - think: ${result.think?.take(50)}, action: ${result.action?.action}, lastThink: ${lastThink?.take(50)}")

                if (result.action == null) {
                    Log.w("AgentCore", "Failed to parse action from: $content")
                }

                // Phase 2: 处理 Call_API 动作
                if (result.action?.action?.lowercase() == "call_api") {
                    val instruction = result.action.instruction ?: result.action.content ?: ""
                    val summary = callAPI(instruction)

                    if (summary != null) {
                        // 将摘要添加到历史中，供下一步使用
                        history.add(Message("user", "API Summary: $summary"))
                    }
                }

                return result.action
            } else {
                // API调用失败，提供详细的错误信息
                val errorBody = response.errorBody()?.string() ?: "无错误详情"
                val errorMsg = when (response.code()) {
                    401 -> "API认证失败"
                    403 -> "API访问被拒绝"
                    404 -> "API地址不存在"
                    429 -> "API限流"
                    500, 502, 503 -> "服务器错误"
                    else -> "API错误 (${response.code()})"
                }
                Log.e("AgentCore", "API Error: $errorMsg")
                Log.e("AgentCore", "Full error body: $errorBody")
                onError?.invoke(errorMsg)  // 通知ViewModel显示错误
                Log.d("AgentCore", "onError callback invoked with: $errorMsg")
            }
        } catch (e: Exception) {
            val errorMsg = when (e) {
                is java.net.SocketTimeoutException -> "网络超时"
                is java.net.UnknownHostException -> "网络连接失败"
                is javax.net.ssl.SSLException -> "SSL证书错误"
                else -> "网络错误: ${e.message}"
            }
            Log.e("AgentCore", "Network exception", e)
            Log.e("AgentCore", "Exception type: ${e.javaClass.simpleName}")
            Log.e("AgentCore", "Exception message: ${e.message}")
            onError?.invoke(errorMsg)  // 通知ViewModel显示错误
            Log.d("AgentCore", "onError callback invoked for exception: $errorMsg")
        }

        Log.d("AgentCore", "step() returning null")
        return null
    }
}
