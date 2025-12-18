package com.example.autoglm.core

import android.util.Log
import com.example.autoglm.api.ApiClient
import com.example.autoglm.data.*
import com.example.autoglm.config.SystemPrompt
import com.example.autoglm.utils.SettingsManager
import android.util.Base64

class AgentCore {
    private val history = mutableListOf<Message>()
    private var isRunning = false
    
    // We only expose the last thought for UI
    var lastThink: String? = null

    fun startSession(task: String) {
        history.clear()
        // Use the new dynamic System Prompt
        history.add(Message("system", SystemPrompt.get()))
        history.add(Message("user", "Task: $task"))
        isRunning = true
    }

    fun stop() {
        isRunning = false
    }
    
    fun isSessionRunning(): Boolean {
        return isRunning
    }

    suspend fun step(screenshotBytes: ByteArray): Action? {
        if (!isRunning) return null

        // 1. Prepare Image
        val base64Image = Base64.encodeToString(screenshotBytes, Base64.NO_WRAP)
        val imageUrl = "data:image/jpeg;base64,$base64Image"

        // 2. Add User Message with Image
        val contentParts = listOf(
            ContentPart(type = "text", text = "Current Screenshot:"),
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

        // 3. Call API
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
                
                // Add assistant response to history
                // To save memory/tokens, we record that we processed an image
                history.add(Message("user", "Screenshot processed.")) 
                history.add(Message("assistant", content))

                Log.d("AgentCore", "AI Response: $content")
                
                // 4. Parse using the new Regex Parser
                val result = ResponseParser.parse(content)
                lastThink = result.think
                
                if (result.action == null) {
                    Log.w("AgentCore", "Failed to parse action from: $content")
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
