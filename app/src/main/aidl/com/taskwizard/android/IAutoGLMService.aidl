package com.taskwizard.android;

interface IAutoGLMService {
    /**
     * 销毁服务（释放资源）
     */
    void destroy();

    /**
     * 执行 Shell 命令
     * @param command 例如 "input tap 500 500"
     * @return 命令的标准输出 (stdout)
     */
    String executeShellCommand(String command);

    /**
     * 获取屏幕截图（通过文件系统）
     * 避免 Binder 传输大数据导致 DeadObjectException
     * @return 截图文件的绝对路径，失败时返回 "ERROR: 错误信息"
     */
    String takeScreenshotToFile();
    
    /**
     * 注入 Base64 文本 (通过 ADB Keyboard 广播)
     * @param base64Text Base64编码的文本
     */
    void injectInputBase64(String base64Text);
    
    /**
     * 获取当前前台应用的包名
     * @return 包名，例如 "com.tencent.mm"，失败返回空字符串
     */
    String getCurrentPackage();
    
    /**
     * Phase 3: 获取当前启用的输入法
     * @return 输入法 ID，例如 "com.android.adbkeyboard/.AdbIME"，失败返回空字符串
     */
    String getCurrentIME();
    
    /**
     * Phase 3: 设置输入法
     * @param imeId 输入法 ID，例如 "com.android.adbkeyboard/.AdbIME"
     * @return 是否成功
     */
    boolean setIME(String imeId);
    
    /**
     * Phase 3: 检查 ADB Keyboard 是否已安装
     * @return 是否已安装
     */
    boolean isADBKeyboardInstalled();

    /**
     * 检查指定 IME 是否已启用（在启用列表中）
     * @param imeId 输入法 ID
     * @return 是否已启用
     */
    boolean isIMEEnabled(String imeId);

    // ==================== UiAutomation API ====================

    /**
     * 初始化 UiAutomation 连接
     * @return 是否成功
     */
    boolean initUiAutomation();

    /**
     * 通过 UiAutomation 注入点击事件
     * @param x X 坐标
     * @param y Y 坐标
     * @return 是否成功
     */
    boolean injectTap(int x, int y);

    /**
     * 通过 UiAutomation 注入滑动事件
     * @param x1 起始 X
     * @param y1 起始 Y
     * @param x2 结束 X
     * @param y2 结束 Y
     * @param duration 持续时间（毫秒）
     * @return 是否成功
     */
    boolean injectSwipe(int x1, int y1, int x2, int y2, long duration);

    /**
     * 执行全局操作（如返回、主页等）
     * @param action 操作类型（参考 AccessibilityService.GLOBAL_ACTION_*）
     * @return 是否成功
     */
    boolean performGlobalAction(int action);

    /**
     * 检查 UiAutomation 是否可用
     * @return 是否可用
     */
    boolean isUiAutomationAvailable();
}
