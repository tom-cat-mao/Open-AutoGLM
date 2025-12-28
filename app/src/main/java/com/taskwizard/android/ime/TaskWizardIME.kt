package com.taskwizard.android.ime

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.inputmethodservice.InputMethodService
import android.os.Build
import android.util.Base64
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.ExtractedTextRequest

/**
 * TaskWizard 内置输入法服务
 *
 * 实现与 ADB Keyboard 兼容的广播接收机制，
 * 通过 InputConnection 将文本注入到当前输入框。
 *
 * 支持的广播 Actions:
 * - ADB_INPUT_TEXT: 普通文本输入
 * - ADB_INPUT_B64: Base64 编码文本输入（主要使用）
 * - ADB_INPUT_CHARS: 字符数组输入
 * - ADB_INPUT_CODE: 按键码输入
 * - ADB_EDITOR_CODE: 编辑器动作
 * - ADB_CLEAR_TEXT: 清除文本
 */
class TaskWizardIME : InputMethodService() {

    companion object {
        private const val TAG = "TaskWizardIME"

        // 广播 Actions - 与 ADB Keyboard 保持兼容
        const val ACTION_INPUT_TEXT = "ADB_INPUT_TEXT"
        const val ACTION_INPUT_B64 = "ADB_INPUT_B64"
        const val ACTION_INPUT_CHARS = "ADB_INPUT_CHARS"
        const val ACTION_INPUT_CODE = "ADB_INPUT_CODE"
        const val ACTION_EDITOR_CODE = "ADB_EDITOR_CODE"
        const val ACTION_CLEAR_TEXT = "ADB_CLEAR_TEXT"
    }

    private var broadcastReceiver: AdbBroadcastReceiver? = null

    override fun onCreate() {
        super.onCreate()
        Log.d(TAG, "TaskWizardIME created")
        registerBroadcastReceiver()
    }

    override fun onCreateInputView(): View? {
        // 返回 null，因为这是一个隐藏的输入法，不需要显示键盘界面
        Log.d(TAG, "onCreateInputView called")
        return null
    }

    override fun onDestroy() {
        super.onDestroy()
        unregisterBroadcastReceiver()
        Log.d(TAG, "TaskWizardIME destroyed")
    }

    /**
     * 注册广播接收器
     */
    private fun registerBroadcastReceiver() {
        if (broadcastReceiver != null) {
            Log.d(TAG, "Broadcast receiver already registered")
            return
        }

        broadcastReceiver = AdbBroadcastReceiver()
        val filter = IntentFilter().apply {
            addAction(ACTION_INPUT_TEXT)
            addAction(ACTION_INPUT_B64)
            addAction(ACTION_INPUT_CHARS)
            addAction(ACTION_INPUT_CODE)
            addAction(ACTION_EDITOR_CODE)
            addAction(ACTION_CLEAR_TEXT)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(broadcastReceiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(broadcastReceiver, filter)
        }
        Log.d(TAG, "Broadcast receiver registered")
    }

    /**
     * 注销广播接收器
     */
    private fun unregisterBroadcastReceiver() {
        broadcastReceiver?.let {
            try {
                unregisterReceiver(it)
                Log.d(TAG, "Broadcast receiver unregistered")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to unregister receiver", e)
            }
        }
        broadcastReceiver = null
    }

    /**
     * 内部广播接收器
     */
    private inner class AdbBroadcastReceiver : BroadcastReceiver() {

        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent == null) return

            val ic = currentInputConnection
            if (ic == null) {
                Log.w(TAG, "No input connection available")
                return
            }

            when (intent.action) {
                ACTION_INPUT_TEXT -> handleInputText(intent)
                ACTION_INPUT_B64 -> handleInputBase64(intent)
                ACTION_INPUT_CHARS -> handleInputChars(intent)
                ACTION_INPUT_CODE -> handleInputCode(intent)
                ACTION_EDITOR_CODE -> handleEditorCode(intent)
                ACTION_CLEAR_TEXT -> handleClearText()
            }
        }

        /**
         * 处理普通文本输入
         */
        private fun handleInputText(intent: Intent) {
            val text = intent.getStringExtra("msg") ?: return
            val ic = currentInputConnection ?: return

            ic.commitText(text, 1)
            Log.d(TAG, "Input text: ${text.take(20)}${if (text.length > 20) "..." else ""}")
        }

        /**
         * 处理 Base64 编码文本输入
         */
        private fun handleInputBase64(intent: Intent) {
            val base64 = intent.getStringExtra("msg") ?: return
            val ic = currentInputConnection ?: return

            try {
                val decoded = String(Base64.decode(base64, Base64.DEFAULT), Charsets.UTF_8)
                ic.commitText(decoded, 1)
                Log.d(TAG, "Input B64: ${decoded.take(20)}${if (decoded.length > 20) "..." else ""}")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to decode base64", e)
            }
        }

        /**
         * 处理字符数组输入
         */
        private fun handleInputChars(intent: Intent) {
            val chars = intent.getIntArrayExtra("chars") ?: return
            val ic = currentInputConnection ?: return

            val text = String(chars, 0, chars.size)
            ic.commitText(text, 1)
            Log.d(TAG, "Input chars: ${text.take(20)}${if (text.length > 20) "..." else ""}")
        }

        /**
         * 处理按键码输入
         */
        private fun handleInputCode(intent: Intent) {
            val code = intent.getIntExtra("code", -1)
            if (code == -1) return

            val ic = currentInputConnection ?: return

            // 发送按键按下和抬起事件
            ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
            ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
            Log.d(TAG, "Input keycode: $code")
        }

        /**
         * 处理编辑器动作
         */
        private fun handleEditorCode(intent: Intent) {
            val code = intent.getIntExtra("code", -1)
            if (code == -1) return

            val ic = currentInputConnection ?: return

            ic.performEditorAction(code)
            Log.d(TAG, "Editor action: $code")
        }

        /**
         * 处理清除文本
         */
        private fun handleClearText() {
            val ic = currentInputConnection ?: return

            try {
                val extracted = ic.getExtractedText(ExtractedTextRequest(), 0)
                extracted?.text?.length?.let { length ->
                    if (length > 0) {
                        // 获取光标前后的文本长度
                        val beforeCursor = ic.getTextBeforeCursor(length, 0)?.length ?: 0
                        val afterCursor = ic.getTextAfterCursor(length, 0)?.length ?: 0
                        ic.deleteSurroundingText(beforeCursor, afterCursor)
                        Log.d(TAG, "Cleared text: before=$beforeCursor, after=$afterCursor")
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to clear text", e)
            }
        }
    }
}
