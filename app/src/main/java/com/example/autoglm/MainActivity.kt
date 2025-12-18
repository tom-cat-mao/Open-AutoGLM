package com.example.autoglm

import android.graphics.BitmapFactory
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.autoglm.api.ApiClient
import com.example.autoglm.core.ActionExecutor
import com.example.autoglm.core.AgentCore
import com.example.autoglm.manager.ShizukuManager
import com.example.autoglm.utils.SettingsManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.suspendCancellableCoroutine
import rikka.shizuku.Shizuku
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

class MainActivity : AppCompatActivity(), Shizuku.OnRequestPermissionResultListener {

    private lateinit var statusText: TextView
    private lateinit var imageView: ImageView
    
    private lateinit var etApiKey: EditText
    private lateinit var etBaseUrl: EditText
    private lateinit var etModel: EditText
    private lateinit var etTask: EditText
    
    private lateinit var btnStep: Button
    private lateinit var btnAutoLoop: Button
    
    // Components
    private lateinit var agentCore: AgentCore
    private var actionExecutor: ActionExecutor? = null
    
    // Auto Loop Control
    private val isLooping = AtomicBoolean(false)
    private val MAX_STEPS = 15
    private val MAX_RETRIES = 3  // 网络错误最大重试次数

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Init Utils
        SettingsManager.init(this)
        
        // Init AgentCore with Context
        agentCore = AgentCore(this)

        // Bind Views
        statusText = findViewById(R.id.tv_status)
        imageView = findViewById(R.id.iv_preview)
        etApiKey = findViewById(R.id.et_api_key)
        etBaseUrl = findViewById(R.id.et_base_url)
        etModel = findViewById(R.id.et_model)
        etTask = findViewById(R.id.et_task)
        
        btnStep = findViewById(R.id.btn_step)
        btnAutoLoop = findViewById(R.id.btn_auto_loop)

        // Load Settings
        etApiKey.setText(SettingsManager.apiKey)
        etBaseUrl.setText(SettingsManager.baseUrl)
        etModel.setText(SettingsManager.model)

        // Setup Clear Buttons
        setupClearButton(R.id.btn_clear_api_key, etApiKey)
        setupClearButton(R.id.btn_clear_base_url, etBaseUrl)
        setupClearButton(R.id.btn_clear_model, etModel)
        setupClearButton(R.id.btn_clear_task, etTask)

        // Setup Main Buttons
        findViewById<Button>(R.id.btn_save_settings).setOnClickListener {
            saveSettings()
        }

        btnStep.setOnClickListener {
            runOneStep()
        }
        
        // Enable Auto Loop Button
        btnAutoLoop.isEnabled = true
        btnAutoLoop.setOnClickListener {
            toggleAutoLoop()
        }

        checkAndRequestPermission()
        
        // Phase 3: 检查 ADB Keyboard 是否已安装
        checkADBKeyboard()
    }

    private fun setupClearButton(btnId: Int, targetEditText: EditText) {
        findViewById<Button>(btnId).setOnClickListener {
            targetEditText.setText("")
        }
    }

    private fun saveSettings() {
        val apiKey = etApiKey.text.toString().trim()
        val baseUrl = etBaseUrl.text.toString().trim()
        val model = etModel.text.toString().trim()
        
        SettingsManager.apiKey = apiKey
        SettingsManager.baseUrl = baseUrl
        SettingsManager.model = model
        
        // Re-init API Client
        ApiClient.init(baseUrl, apiKey)
        Toast.makeText(this, "Settings Saved", Toast.LENGTH_SHORT).show()
    }

    private fun checkAndRequestPermission() {
        if (ShizukuManager.checkPermission()) {
            statusText.text = "Status: Ready (Shizuku Granted)"
            initExecutor()
        } else {
            statusText.text = "Status: Requesting Shizuku..."
            ShizukuManager.requestPermission(this)
        }
    }
    
    private fun initExecutor() {
        // Executor will be initialized when needed
    }

    override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
        if (grantResult == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            statusText.text = "Status: Ready"
        } else {
            statusText.text = "Status: Permission Denied"
        }
    }
    
    // ==================== Phase 3: IME Management ====================
    
    /**
     * Phase 3: 检查 ADB Keyboard 是否已安装
     */
    private fun checkADBKeyboard() {
        lifecycleScope.launch {
            try {
                // 等待 Shizuku 权限就绪
                delay(500)
                
                if (!ShizukuManager.checkPermission()) {
                    Log.d("MainActivity", "Shizuku permission not granted yet, skipping ADB Keyboard check")
                    return@launch
                }
                
                val service = ShizukuManager.bindService(this@MainActivity)
                val isInstalled = service.isADBKeyboardInstalled()
                
                if (!isInstalled) {
                    withContext(Dispatchers.Main) {
                        showADBKeyboardGuide()
                    }
                } else {
                    Log.i("MainActivity", "ADB Keyboard is installed")
                }
            } catch (e: Exception) {
                Log.e("MainActivity", "Failed to check ADB Keyboard", e)
            }
        }
    }
    
    /**
     * Phase 3: 显示 ADB Keyboard 安装引导
     */
    private fun showADBKeyboardGuide() {
        AlertDialog.Builder(this)
            .setTitle("需要安装 ADB Keyboard")
            .setMessage("""
                为了实现文本输入功能，需要安装 ADB Keyboard 应用。
                
                安装步骤：
                1. 下载 ADB Keyboard APK
                2. 安装到手机
                3. 在系统设置中启用 ADB Keyboard
                
                下载地址：
                https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk
            """.trimIndent())
            .setPositiveButton("我知道了") { dialog, _ ->
                dialog.dismiss()
            }
            .setNegativeButton("不再提示") { dialog, _ ->
                // 可以添加一个标记，不再显示此对话框
                dialog.dismiss()
            }
            .show()
    }
    
    /**
     * Phase 3: 还原输入法
     * 在任务完成、错误、停止时调用
     */
    private fun restoreIMEIfNeeded() {
        try {
            actionExecutor?.restoreIME()
            Log.d("MainActivity", "IME restoration attempted")
        } catch (e: Exception) {
            Log.e("MainActivity", "Failed to restore IME", e)
        }
    }
    
    /**
     * Phase 2: Take_over 回调 - 显示对话框暂停等待用户操作
     */
    private suspend fun handleTakeOver(message: String) = suspendCancellableCoroutine<Unit> { continuation ->
        runOnUiThread {
            AlertDialog.Builder(this)
                .setTitle("需要人工介入")
                .setMessage(message)
                .setCancelable(false)
                .setPositiveButton("继续") { dialog, _ ->
                    dialog.dismiss()
                    continuation.resume(Unit)
                }
                .show()
        }
    }
    
    /**
     * Phase 2: Interact 回调 - 显示选项让用户选择
     */
    private suspend fun handleInteract(message: String): String? = suspendCancellableCoroutine { continuation ->
        runOnUiThread {
            val input = EditText(this)
            input.hint = "请输入您的选择"
            
            AlertDialog.Builder(this)
                .setTitle("用户选择")
                .setMessage(message)
                .setView(input)
                .setCancelable(false)
                .setPositiveButton("确定") { dialog, _ ->
                    val result = input.text.toString()
                    dialog.dismiss()
                    continuation.resume(result)
                }
                .setNegativeButton("取消") { dialog, _ ->
                    dialog.dismiss()
                    continuation.resume(null)
                }
                .show()
        }
    }
    
    /**
     * Phase 2: Note 回调 - 记录页面信息
     */
    private fun handleNote(note: String) {
        agentCore.addNote(note)
        Log.d("MainActivity", "Note recorded: $note")
    }
    
    // ==================== Phase 4: Safety & Timing ====================
    
    /**
     * Phase 4: 敏感操作确认回调
     * 严格对齐原版 handler.py 的 confirmation_callback
     * 
     * @param message 敏感操作描述信息
     * @return Boolean - true 表示用户确认，false 表示用户取消
     */
    private suspend fun handleConfirmation(message: String): Boolean = suspendCancellableCoroutine { continuation ->
        runOnUiThread {
            AlertDialog.Builder(this)
                .setTitle("⚠️ 敏感操作确认")
                .setMessage("检测到敏感操作：\n\n$message\n\n是否继续执行？")
                .setCancelable(false)
                .setPositiveButton("确认执行") { dialog, _ ->
                    Log.i("MainActivity", "User confirmed sensitive operation: $message")
                    dialog.dismiss()
                    continuation.resume(true)
                }
                .setNegativeButton("取消") { dialog, _ ->
                    Log.i("MainActivity", "User cancelled sensitive operation: $message")
                    dialog.dismiss()
                    continuation.resume(false)
                }
                .show()
        }
    }
    
    private fun toggleAutoLoop() {
        if (isLooping.get()) {
            stopLoop()
        } else {
            startLoop()
        }
    }
    
    private fun stopLoop() {
        isLooping.set(false)
        agentCore.stop()
        btnAutoLoop.text = "Auto Loop"
        statusText.text = "Status: Stopped by User"
        btnStep.isEnabled = true
        
        // Phase 3: 还原输入法
        restoreIMEIfNeeded()
    }
    
    private fun startLoop() {
        val task = etTask.text.toString().trim()
        if (task.isEmpty()) {
            Toast.makeText(this, "Please enter a task", Toast.LENGTH_SHORT).show()
            return
        }
        if (SettingsManager.apiKey.isEmpty()) {
             Toast.makeText(this, "Please set API Key", Toast.LENGTH_SHORT).show()
             return
        }

        isLooping.set(true)
        btnAutoLoop.text = "STOP Loop"
        btnStep.isEnabled = false // Disable single step while looping
        
        lifecycleScope.launch {
            try {
                // 1. Bind Service Once
                val service = ShizukuManager.bindService(this@MainActivity)
                
                // Phase 2: 创建 ActionExecutor 并传入回调
                // Phase 4: 添加 onConfirmation 回调
                if (actionExecutor == null) {
                    val metrics = resources.displayMetrics
                    actionExecutor = ActionExecutor(
                        context = this@MainActivity,
                        service = service,
                        screenWidth = metrics.widthPixels,
                        screenHeight = metrics.heightPixels,
                        onTakeOver = { message ->
                            // Take_over 需要暂停循环并等待用户操作
                            lifecycleScope.launch {
                                handleTakeOver(message)
                            }
                        },
                        onInteract = { message ->
                            // Interact 需要获取用户输入（同步调用）
                            null // 暂时返回 null，实际应该使用 runBlocking 或其他方式
                        },
                        onNote = { note ->
                            handleNote(note)
                        },
                        onConfirmation = { message ->
                            // Phase 4: 敏感操作确认回调
                            handleConfirmation(message)
                        }
                    )
                }

                // 2. Start Session
                agentCore.startSession(task)
                var stepCount = 0
                
                // 3. Loop
                while (isLooping.get() && stepCount < MAX_STEPS) {
                    stepCount++
                    
                    withContext(Dispatchers.Main) {
                        statusText.text = "Step $stepCount: Capturing Screenshot..."
                    }
                    
                    // A. Screenshot (使用文件系统方案)
                    val screenshotPath = service.takeScreenshotToFile()
                    
                    // 检查是否有错误
                    if (screenshotPath.startsWith("ERROR")) {
                        withContext(Dispatchers.Main) {
                            statusText.text = "Error: $screenshotPath"
                            stopLoop()
                        }
                        return@launch
                    }
                    
                    // B. 读取文件
                    val bytes = withContext(Dispatchers.IO) {
                        try {
                            java.io.File(screenshotPath).readBytes().also {
                                // 读取后立即删除临时文件
                                java.io.File(screenshotPath).delete()
                                Log.d("MainActivity", "Screenshot loaded and deleted: $screenshotPath")
                            }
                        } catch (e: Exception) {
                            Log.e("MainActivity", "Failed to read screenshot file", e)
                            ByteArray(0)
                        }
                    }
                    
                    if (bytes.isEmpty()) {
                        withContext(Dispatchers.Main) {
                            statusText.text = "Error: Failed to read screenshot file"
                            stopLoop()
                        }
                        return@launch
                    }
                    
                    // Update Preview and get actual screenshot dimensions
                    withContext(Dispatchers.Main) {
                         val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                         imageView.setImageBitmap(bitmap)
                         
                         // 🔧 坐标修复：从截图获取实际尺寸并更新 ActionExecutor
                         val actualWidth = bitmap.width
                         val actualHeight = bitmap.height
                         actionExecutor?.updateScreenSize(actualWidth, actualHeight)
                         Log.d("MainActivity", "Screenshot size: ${actualWidth}x${actualHeight}")
                         
                         statusText.text = "Step $stepCount: Thinking..."
                    }
                    
                    // C. Agent Step (Network) - 带重试机制
                    var action = withContext(Dispatchers.IO) {
                        agentCore.step(bytes)
                    }
                    
                    // 如果第一次失败，进行重试
                    var retryCount = 0
                    while (action == null && retryCount < MAX_RETRIES && isLooping.get()) {
                        retryCount++
                        withContext(Dispatchers.Main) {
                            statusText.text = "Step $stepCount: Network Error, Retrying ($retryCount/$MAX_RETRIES)..."
                        }
                        Log.w("MainActivity", "Network error, retry attempt $retryCount/$MAX_RETRIES")
                        
                        // 等待一段时间再重试（指数退避）
                        delay(1000L * retryCount)  // 1s, 2s, 3s
                        
                        action = withContext(Dispatchers.IO) {
                            agentCore.step(bytes)
                        }
                    }
                    
                    // D. Handle Result
                    if (action != null) {
                         // 重试成功或第一次就成功
                         if (retryCount > 0) {
                             Log.i("MainActivity", "Network retry succeeded after $retryCount attempts")
                         }
                         
                         val think = agentCore.lastThink ?: "No thought"
                         withContext(Dispatchers.Main) {
                             statusText.text = "Step $stepCount Action: ${action.action}"
                         }
                         
                         // Check Finish
                         if (action.action == "finish" || action.action == "task_complete") {
                             withContext(Dispatchers.Main) {
                                 statusText.text = "Task Completed!"
                                 Toast.makeText(this@MainActivity, "Task Completed!", Toast.LENGTH_LONG).show()
                                 stopLoop()
                             }
                             break
                         }
                         
                         // E. Execute
                         // Phase 4: 检查 execute 返回值，如果用户取消敏感操作则停止任务
                         val shouldContinue = actionExecutor?.execute(action) ?: true
                         
                         if (!shouldContinue) {
                             // 用户取消了敏感操作，停止任务
                             withContext(Dispatchers.Main) {
                                 statusText.text = "Task Cancelled: User declined sensitive operation"
                                 Toast.makeText(this@MainActivity, "Task Cancelled by User", Toast.LENGTH_LONG).show()
                                 stopLoop()
                             }
                             break
                         }
                         
                         // F. Wait
                         delay(2000)
                    } else {
                        // 重试多次后仍然失败
                        withContext(Dispatchers.Main) {
                             statusText.text = "Error: Network failed after $MAX_RETRIES retries"
                             Log.e("MainActivity", "Network error persists after $MAX_RETRIES retries, stopping")
                             stopLoop()
                        }
                        break
                    }
                }
                
                if (stepCount >= MAX_STEPS) {
                    withContext(Dispatchers.Main) {
                        statusText.text = "Max Steps Reached"
                        stopLoop()
                    }
                }

            } catch (e: Exception) {
                Log.e("MainActivity", "Loop Failed", e)
                withContext(Dispatchers.Main) {
                    statusText.text = "Error: ${e.message}"
                    stopLoop()
                }
            }
        }
    }

    private fun runOneStep() {
        val task = etTask.text.toString().trim()
        if (task.isEmpty()) {
            Toast.makeText(this, "Please enter a task", Toast.LENGTH_SHORT).show()
            return
        }
        
        statusText.text = "Status: Capturing Screenshot..."
        
        lifecycleScope.launch {
            try {
                val service = ShizukuManager.bindService(this@MainActivity)
                // Phase 2: 创建 ActionExecutor 并传入回调
                // Phase 4: 添加 onConfirmation 回调
                if (actionExecutor == null) {
                    val metrics = resources.displayMetrics
                    actionExecutor = ActionExecutor(
                        context = this@MainActivity,
                        service = service,
                        screenWidth = metrics.widthPixels,
                        screenHeight = metrics.heightPixels,
                        onTakeOver = { message ->
                            lifecycleScope.launch {
                                handleTakeOver(message)
                            }
                        },
                        onInteract = { message ->
                            null // 单步模式暂不支持 Interact
                        },
                        onNote = { note ->
                            handleNote(note)
                        },
                        onConfirmation = { message ->
                            // Phase 4: 敏感操作确认回调
                            handleConfirmation(message)
                        }
                    )
                }

                // 使用新的文件系统方案
                val screenshotPath = service.takeScreenshotToFile()
                
                if (screenshotPath.startsWith("ERROR")) {
                    statusText.text = "Error: $screenshotPath"
                    return@launch
                }
                
                val bytes = withContext(Dispatchers.IO) {
                    try {
                        java.io.File(screenshotPath).readBytes().also {
                            java.io.File(screenshotPath).delete()
                            Log.d("MainActivity", "Screenshot loaded for one step: $screenshotPath")
                        }
                    } catch (e: Exception) {
                        Log.e("MainActivity", "Failed to read screenshot in runOneStep", e)
                        ByteArray(0)
                    }
                }
                
                if (bytes.isEmpty()) {
                    statusText.text = "Error: Failed to read screenshot"
                    return@launch
                }
                
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                imageView.setImageBitmap(bitmap)
                
                // 🔧 坐标修复：从截图获取实际尺寸并更新 ActionExecutor
                val actualWidth = bitmap.width
                val actualHeight = bitmap.height
                actionExecutor?.updateScreenSize(actualWidth, actualHeight)
                Log.d("MainActivity", "Screenshot size: ${actualWidth}x${actualHeight}")
                
                statusText.text = "Status: Thinking (API Call)..."

                withContext(Dispatchers.IO) {
                    // One Step mode always starts a fresh session for debugging
                    agentCore.startSession(task) 
                    val action = agentCore.step(bytes)
                    
                    withContext(Dispatchers.Main) {
                        if (action != null) {
                            val think = agentCore.lastThink ?: "No thought"
                            statusText.text = "Think: $think\nAction: ${action.action} ${action.location ?: ""}"
                            actionExecutor?.execute(action)
                        } else {
                            statusText.text = "Status: No Action or Error"
                        }
                    }
                }
            } catch (e: Exception) {
                Log.e("MainActivity", "Step Failed", e)
                statusText.text = "Error: ${e.message}"
            }
        }
    }
    
    override fun onDestroy() {
        super.onDestroy()
        Shizuku.removeRequestPermissionResultListener(this)
        ShizukuManager.unbind()
    }
}
