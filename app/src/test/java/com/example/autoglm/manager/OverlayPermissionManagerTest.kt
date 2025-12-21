package com.example.autoglm.manager

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import io.mockk.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * OverlayPermissionManager单元测试
 *
 * 测试悬浮窗权限管理的所有功能
 * 使用Robolectric进行Android框架测试
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.TIRAMISU]) // Android 13
class OverlayPermissionManagerTest {

    private lateinit var mockContext: Context
    private lateinit var mockActivity: Activity

    @Before
    fun setup() {
        mockContext = mockk(relaxed = true)
        mockActivity = mockk(relaxed = true)

        // Mock packageName
        every { mockContext.packageName } returns "com.example.autoglm"
        every { mockActivity.packageName } returns "com.example.autoglm"
    }

    @After
    fun tearDown() {
        unmockkAll()
    }

    /**
     * 测试：有权限时返回true
     */
    @Test
    fun `checkPermission with permission returns true`() {
        // Mock Settings.canDrawOverlays返回true
        mockkStatic(Settings::class)
        every { Settings.canDrawOverlays(any()) } returns true

        val result = OverlayPermissionManager.checkPermission(mockContext)

        assertTrue("应该返回true当有权限时", result)
        verify { Settings.canDrawOverlays(mockContext) }
    }

    /**
     * 测试：无权限时返回false
     */
    @Test
    fun `checkPermission without permission returns false`() {
        mockkStatic(Settings::class)
        every { Settings.canDrawOverlays(any()) } returns false

        val result = OverlayPermissionManager.checkPermission(mockContext)

        assertFalse("应该返回false当无权限时", result)
        verify { Settings.canDrawOverlays(mockContext) }
    }

    /**
     * 测试：Settings.canDrawOverlays抛出异常时返回false
     */
    @Test
    fun `checkPermission with exception returns false`() {
        mockkStatic(Settings::class)
        every { Settings.canDrawOverlays(any()) } throws SecurityException("Test exception")

        val result = OverlayPermissionManager.checkPermission(mockContext)

        assertFalse("应该返回false当发生异常时", result)
    }

    /**
     * 测试：请求权限启动正确的Intent
     */
    @Test
    fun `requestPermission launches system settings`() {
        val capturedIntent = slot<Intent>()
        every { mockActivity.startActivity(capture(capturedIntent)) } just Runs

        OverlayPermissionManager.requestPermission(mockActivity)

        verify { mockActivity.startActivity(any()) }

        val intent = capturedIntent.captured
        assertEquals(
            "Intent action应该是ACTION_MANAGE_OVERLAY_PERMISSION",
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            intent.action
        )
        assertEquals(
            "Intent data应该包含包名",
            "package:com.example.autoglm",
            intent.data.toString()
        )
    }

    /**
     * 测试：请求权限失败时尝试打开通用设置
     */
    @Test
    fun `requestPermission falls back to general settings on failure`() {
        val capturedIntents = mutableListOf<Intent>()
        every { mockActivity.startActivity(capture(capturedIntents)) } throws SecurityException("Test") andThenJust Runs

        OverlayPermissionManager.requestPermission(mockActivity)

        verify(exactly = 2) { mockActivity.startActivity(any()) }
        assertEquals(
            "第二个Intent应该是通用设置",
            Settings.ACTION_SETTINGS,
            capturedIntents[1].action
        )
    }

    /**
     * 测试：shouldShowRationale在无权限时返回true
     */
    @Test
    fun `shouldShowRationale returns true when permission not granted`() {
        mockkStatic(Settings::class)
        every { Settings.canDrawOverlays(any()) } returns false

        val result = OverlayPermissionManager.shouldShowRationale(mockContext)

        assertTrue("应该返回true当无权限时", result)
    }

    /**
     * 测试：shouldShowRationale在有权限时返回false
     */
    @Test
    fun `shouldShowRationale returns false when permission granted`() {
        mockkStatic(Settings::class)
        every { Settings.canDrawOverlays(any()) } returns true

        val result = OverlayPermissionManager.shouldShowRationale(mockContext)

        assertFalse("应该返回false当有权限时", result)
    }

    /**
     * 测试：getPermissionIntent返回正确的Intent
     */
    @Test
    fun `getPermissionIntent returns correct intent`() {
        val intent = OverlayPermissionManager.getPermissionIntent(mockContext)

        assertEquals(
            "Intent action应该是ACTION_MANAGE_OVERLAY_PERMISSION",
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            intent.action
        )
        assertEquals(
            "Intent data应该包含包名",
            "package:com.example.autoglm",
            intent.data.toString()
        )
    }

    /**
     * 测试：Android 6.0以下版本默认有权限
     */
    @Test
    @Config(sdk = [Build.VERSION_CODES.LOLLIPOP]) // Android 5.0
    fun `checkPermission returns true on Android below M`() {
        val result = OverlayPermissionManager.checkPermission(mockContext)

        assertTrue("Android 6.0以下应该默认返回true", result)
    }
}
