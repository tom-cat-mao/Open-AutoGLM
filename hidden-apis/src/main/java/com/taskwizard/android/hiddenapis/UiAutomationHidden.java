package com.taskwizard.android.hiddenapis;

import android.app.UiAutomation;
import android.os.Looper;
import android.view.InputEvent;

import dev.rikka.tools.refine.RefineAs;

/**
 * UiAutomation 隐藏 API 存根类
 * 使用 Rikka Refine 在运行时映射到真实的 UiAutomation 类
 */
@RefineAs(UiAutomation.class)
public class UiAutomationHidden {

    /**
     * 构造函数 - 需要 Looper 和 UiAutomationConnection
     */
    public UiAutomationHidden(Looper looper, UiAutomationConnection connection) {
        throw new RuntimeException("Stub!");
    }

    /**
     * 连接到 UiAutomation 服务
     * @param flags 连接标志
     */
    public void connect(int flags) {
        throw new RuntimeException("Stub!");
    }

    /**
     * 断开连接
     */
    public void disconnect() {
        throw new RuntimeException("Stub!");
    }
}
