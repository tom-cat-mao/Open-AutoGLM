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

    // ==================== UI Components ====================
    private lateinit var statusText: TextView
    private lateinit var imageView: ImageView
    private lateinit var etApiKey: EditText
    private lateinit var etBaseUrl: EditText
    private lateinit var etModel: EditText
    private lateinit var etTask: EditText
    // private lateinit var btnStep: Button  // REMOVED
    private lateinit var btnAutoLoop: Button

    // ==================== Core Components ====================
    private lateinit var agentCore: AgentCore
    private var actionExecutor: ActionExecutor? = null

    // ==================== Loop Control ====================
    private val isLooping = AtomicBoolean(false)

    companion object {
        private const val MAX_STEPS = 15
        private const val MAX_RETRIES = 3  // 网络错误最大重试次数
    }

    // ==================== Lifecycle ====================

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        initializeComponents()
        bindViews()
        loadSettings()
        setupButtons()
        checkAndRequestPermission()
        checkADBKeyboard()
    }

    override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
        if (grantResult == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            statusText.text = "Status: Ready"
        } else {
            statusText.text = "Status: Permission Denied"
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        Shizuku.removeRequestPermissionResultListener(this)

        // 异步解绑 Shizuku 服务，避免阻塞 Activity 销毁
        lifecycleScope.launch(Dispatchers.IO) {
            ShizukuManager.unbind()
        }
    }

    // ==================== Initialization ====================

    private fun initializeComponents() {
        SettingsManager.init(this)
        agentCore = AgentCore(this)
    }

    private fun bindViews() {
        statusText = findViewById(R.id.tv_status)
        imageView = findViewById(R.id.iv_preview)
        etApiKey = findViewById(R.id.et_api_key)
        etBaseUrl = findViewById(R.id.et_base_url)
        etModel = findViewById(R.id.et_model)
        etTask = findViewById(R.id.et_task)
        // btnStep = findViewById(R.id.btn_step) // REMOVED
        btnAutoLoop = findViewById(R.id.btn_auto_loop)
    }

    private fun loadSettings() {
        etApiKey.setText(SettingsManager.apiKey)
        etBaseUrl.setText(SettingsManager.baseUrl)
        etModel.setText(SettingsManager.model)
    }

    private fun setupButtons() {
        // Clear buttons
        setupClearButton(R.id.btn_clear_api_key, etApiKey)
        setupClearButton(R.id.btn_clear_base_url, etBaseUrl)
        setupClearButton(R.id.btn_clear_model, etModel)
        setupClearButton(R.id.btn_clear_task, etTask)

        // Save settings button
        findViewById<Button>(R.id.btn_save_settings).setOnClickListener {
            saveSettings()
        }

        // Step button listener REMOVED

        // Auto loop button
        btnAutoLoop.isEnabled = true
        btnAutoLoop.setOnClickListener {
            toggleAutoLoop()
        }
    }

    private fun setupClearButton(btnId: Int, targetEditText: EditText) {
        findViewById<Button>(btnId).setOnClickListener {
            targetEditText.setText("")
        }
    }

    // ==================== Settings Management ====================

    private fun saveSettings() {
        val apiKey = etApiKey.text.toString().trim()
        val baseUrl = etBaseUrl.text.toString().trim()
        val model = etModel.text.toString().trim()

        SettingsManager.apiKey = apiKey
        SettingsManager.baseUrl = baseUrl
        SettingsManager.model = model

        ApiClient.init(baseUrl, apiKey)
        Toast.makeText(this, "Settings Saved", Toast.LENGTH_SHORT).show()
    }

    // ==================== Permission Management ====================

    private fun checkAndRequestPermission() {
        if (ShizukuManager.checkPermission()) {
            statusText.text = "Status: Ready (Shizuku Granted)"
        } else {
            statusText.text = "Status: Requesting Shizuku..."
            ShizukuManager.requestPermission(this)
        }
    }
    
    // ==================== IME Management ====================

    /**
     * 检查 ADB Keyboard 是否已安装
     */
    private fun checkADBKeyboard() {
        lifecycleScope.launch {
            try {
                delay(500) // 等待 Shizuku 权限就绪

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
     * 显示 ADB Keyboard 安装引导对话框
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
                dialog.dismiss()
            }
            .show()
    }

    /**
     * 还原输入法（异步执行，避免阻塞主线程）
     * 在任务完成、错误、停止时调用
     */
    private suspend fun restoreIMEIfNeeded() {
        withContext(Dispatchers.IO) {
            try {
                actionExecutor?.restoreIME()
                Log.d("MainActivity", "IME restoration attempted")
            } catch (e: Exception) {
                Log.e("MainActivity", "Failed to restore IME", e)
            }
        }
    }
    
    // ==================== Action Callbacks ====================

    /**
     * Take_over 回调 - 显示对话框暂停等待用户操作
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
     * Interact 回调 - 显示选项让用户选择
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
     * Note 回调 - 记录页面信息
     */
    private fun handleNote(note: String) {
        agentCore.addNote(note)
        Log.d("MainActivity", "Note recorded: $note")
    }

    /**
     * 敏感操作确认回调
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
    
    // ==================== Loop Control ====================

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
        // btnStep.isEnabled = true // REMOVED

        // 异步还原输入法，避免阻塞主线程
        lifecycleScope.launch(Dispatchers.IO) {
            restoreIMEIfNeeded()
        }
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
        // btnStep.isEnabled = false // REMOVED
        
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
                            // Take_over 会暂停并等待用户操作完成（suspend 函数）
                            handleTakeOver(message)
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

    // runOneStep REMOVED
}
