package com.example.autoglm

import android.graphics.BitmapFactory
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
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
import rikka.shizuku.Shizuku
import java.util.concurrent.atomic.AtomicBoolean

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
    private val agentCore = AgentCore()
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
        // Executor will be initialized when needed or we can do it here if service is somehow available
    }

    override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
        if (grantResult == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            statusText.text = "Status: Ready"
        } else {
            statusText.text = "Status: Permission Denied"
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
                
                if (actionExecutor == null) {
                    val metrics = resources.displayMetrics
                    actionExecutor = ActionExecutor(this@MainActivity, service, metrics.widthPixels, metrics.heightPixels)
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
                    
                    // Update Preview
                    withContext(Dispatchers.Main) {
                         val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                         imageView.setImageBitmap(bitmap)
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
                         actionExecutor?.execute(action)
                         
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
                if (actionExecutor == null) {
                    val metrics = resources.displayMetrics
                    actionExecutor = ActionExecutor(this@MainActivity, service, metrics.widthPixels, metrics.heightPixels)
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
