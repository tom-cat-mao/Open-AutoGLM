package com.example.autoglm.core

import android.content.Context
import android.util.Log
import com.example.autoglm.api.ApiClient
import com.example.autoglm.data.*
import com.example.autoglm.config.SystemPrompt
import com.example.autoglm.config.AppMap
import com.example.autoglm.manager.ShizukuManager
import com.example.autoglm.utils.SettingsManager
import android.util.Base64
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class AgentCore(private val context: Context) {
    private val history = mutableListOf<Message>()
    private var isRunning = false
    private var isFirstStep = true
    
    // Phase 2: Note 管理
    private val notes = mutableListOf<String>()
    
    // We only expose the last thought for UI
    var lastThink: String? = null

    fun startSession(task: String) {
        history.clear()
        notes.clear()  // 清空 notes
        isFirstStep = true
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
        if (!isRunning) return null

        // 1. Prepare Image
        val base64Image = Base64.encodeToString(screenshotBytes, Base64.NO_WRAP)
        val imageUrl = "data:image/jpeg;base64,$base64Image"

        // 2. Build Screen Info (对齐 Python 版本)
        val screenInfo = buildScreenInfo()
        
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

        // 5. Call API
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

            if (response.isSuccessful && response.body() != null) {
                val responseMsg = response.body()!!.choices.first().message
                val content = responseMsg.content
                
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
                lastThink = result.think
                
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
                Log.e("AgentCore", "API Error: ${response.code()} ${response.errorBody()?.string()}")
            }
        } catch (e: Exception) {
            Log.e("AgentCore", "Network Error", e)
        }
        
        return null
    }
}
