package com.example.autoglm.manager

import android.content.ComponentName
import android.content.Context
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.IBinder
import android.util.Log
import com.example.autoglm.BuildConfig
import com.example.autoglm.IAutoGLMService
import com.example.autoglm.service.AutoGLMUserService
import kotlinx.coroutines.suspendCancellableCoroutine
import rikka.shizuku.Shizuku
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

object ShizukuManager {
    private const val TAG = "ShizukuManager"
    
    // 缓存的服务代理对象
    var service: IAutoGLMService? = null
        private set

    /**
     * 检查是否有 Shizuku 权限
     */
    fun checkPermission(): Boolean {
        if (Shizuku.isPreV11()) return false // 不支持 Android 11 以下的老版本 Shizuku
        return try {
            Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
        } catch (e: Exception) {
            false
        }
    }

    /**
     * 请求权限
     */
    fun requestPermission(listener: Shizuku.OnRequestPermissionResultListener) {
        Shizuku.addRequestPermissionResultListener(listener)
        Shizuku.requestPermission(0)
    }

    /**
     * 绑定 UserService (带服务存活检测和自动重连)
     */
    suspend fun bindService(context: Context): IAutoGLMService = suspendCancellableCoroutine { cont ->
        // 检查现有服务是否存活
        if (service != null) {
            try {
                // 测试调用一个轻量级命令来检查Binder是否存活
                if (service!!.asBinder().isBinderAlive) {
                    service!!.executeShellCommand("echo alive")
                    Log.d(TAG, "Existing service is alive, reusing")
                    cont.resume(service!!)
                    return@suspendCancellableCoroutine
                }
            } catch (e: android.os.DeadObjectException) {
                Log.w(TAG, "Service died (DeadObjectException), rebinding...")
                service = null
            } catch (e: Exception) {
                Log.w(TAG, "Service test failed, rebinding: ${e.message}")
                service = null
            }
        }

        // 需要重新绑定服务
        Log.d(TAG, "Binding new UserService...")
        val args = Shizuku.UserServiceArgs(
            ComponentName(context.packageName, AutoGLMUserService::class.java.name)
        )
            .daemon(false) // 不需要守护进程模式
            .processNameSuffix("service") // 进程名后缀
            .debuggable(BuildConfig.DEBUG)
            .version(BuildConfig.VERSION_CODE) // 使用版本号，升级时自动重新绑定

        Shizuku.bindUserService(args, object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
                Log.d(TAG, "UserService connected")
                if (binder != null && binder.pingBinder()) {
                    val proxy = IAutoGLMService.Stub.asInterface(binder)
                    service = proxy
                    cont.resume(proxy)
                } else {
                    cont.resumeWithException(IllegalStateException("Binder is null or dead"))
                }
            }

            override fun onServiceDisconnected(name: ComponentName?) {
                Log.w(TAG, "UserService disconnected unexpectedly")
                service = null
            }
        })
    }
    
    fun unbind() {
        // Shizuku UserService 的解绑比较特殊，通常依赖于 removeConnection 或 destroy
        try {
            service?.destroy()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to destroy service", e)
        }
        service = null
        try {
            Shizuku.unbindUserService(Shizuku.UserServiceArgs(
                ComponentName("com.example.autoglm", AutoGLMUserService::class.java.name)
            ), null, true)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to unbind service", e)
        }
    }
}
