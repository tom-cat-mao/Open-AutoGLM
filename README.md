# TaskWizard (Open-AutoGLM Android 客户端)

<p align="center">
  <img src="resources/Icon.png" alt="TaskWizard Icon" width="128" height="128">
</p>

> Open-AutoGLM 的 Android 原生客户端 - AI 驱动的手机自动化框架

[![Android](https://img.shields.io/badge/Android-8.0%2B-green)](https://developer.android.com/)
[![Kotlin](https://img.shields.io/badge/Kotlin-2.0.0-blue)](https://kotlinlang.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

[English Documentation](README_en.md) | 中文文档

## 项目简介

**TaskWizard** 是一款将 [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) 移植到 Android 设备的原生应用。它使用 AI 视觉模型来理解屏幕内容，并通过自然语言命令自动在手机上执行任务。

### 核心特性

- **AI 智能自动化**：使用自然语言控制手机
- **视觉理解**：AI 分析截图以理解上下文
- **悬浮窗实时显示**：精美的悬浮窗界面显示任务进度
- **历史记录管理**：查看和继续历史任务
- **新建对话**：一键清空开始新对话
- **Shizuku 系统集成**：获得系统级权限实现高级自动化
- **100+ 应用支持**：支持微信、淘宝、美团等主流应用
- **隐私优先**：所有处理均在本地或使用您自己的 API

### 最新功能 ✨

- ✅ **人工接管**：AI 可在需要时请求人工介入，悬浮窗显示倒计时，完成后点击继续
- ✅ **内置输入法**：内置 TaskWizard 键盘，无需额外安装 ADB Keyboard
- ✅ **历史记录完整显示**：点击历史记录可查看所有消息（思考、操作、系统消息）
- ✅ **新建对话按钮**：快速开始新对话，无需手动清空
- ✅ **性能优化**：优化历史记录加载和消息渲染性能

## 项目起源

这是 [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) Python 原版项目的 Android 原生移植版本。原版运行在 PC 上通过 ADB 控制手机，而 TaskWizard 完全运行在您的 Android 设备上。

**与原版的主要区别：**

| 特性 | Open-AutoGLM (Python) | TaskWizard (Android) |
|------|----------------------|---------------------|
| 运行平台 | PC + ADB 连接 | Android 原生应用 |
| 架构 | Python + ADB/HDC | Kotlin + Shizuku |
| 屏幕截图 | ADB shell 命令 | 系统截图 API |
| 输入方式 | ADB Keyboard | Shizuku 输入注入 |
| 模型调用 | 远程 API | 远程 API（可配置） |
| 用户界面 | 终端/Python 脚本 | Jetpack Compose 原生 UI |

## 工作原理

```
用户输入指令
    ↓
AI 模型分析（截图 + 提示词）
    ↓
生成操作（点击、滑动、输入等）
    ↓
Shizuku 执行（系统级操作）
    ↓
截图验证结果
    ↓
循环直到任务完成
```

## 系统要求

- **Android**：8.0 (API 26) 或更高版本
- **Shizuku**：必须安装 ([下载地址](https://github.com/RikkaApps/Shizuku/releases))
- **输入法**：TaskWizard 内置键盘（推荐），ADB Keyboard 可选用于向后兼容
- **API 访问**：AutoGLM 兼容的模型 API（见[模型选项](#模型选项)）

## 安装方法

### 方法一：从源码构建

```bash
# 克隆仓库
git clone https://github.com/yourusername/Open-AutoGLM.git
cd Open-AutoGLM

# 构建 Debug APK
./gradlew assembleDebug

# 安装到设备
adb install app/build/outputs/apk/debug/app-debug.apk
```

### 方法二：下载发布版 APK

从 [发布页面](https://github.com/yourusername/Open-AutoGLM/releases) 下载最新的 APK 文件。

## 设置指南

### 1. 安装 Shizuku

1. 下载 [Shizuku APK](https://github.com/RikkaApps/Shizuku/releases)
2. 安装并打开 Shizuku
3. 启动 Shizuku 服务（按照应用内说明操作）
4. 在提示时授予 TaskWizard Shizuku 权限

### 2. 配置输入法

TaskWizard 提供两种文本输入方式：

#### 选项 1：内置键盘（推荐）

TaskWizard 内置了输入法功能，无需额外安装，应用会自动引导您启用。

#### 选项 2：ADB Keyboard（可选）

如果您更喜欢使用外部 ADB Keyboard：

1. 下载 [ADB Keyboard APK](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)
2. 安装到您的设备
3. 在 设置 → 语言和输入法 → 当前键盘 中启用 ADB Keyboard

### 3. 配置 API 设置

打开 TaskWizard 并进入设置页面：

| 设置项 | 说明 | 示例 |
|--------|------|------|
| **API Key** | 您的模型 API 密钥 | `sk-xxxxx` |
| **Base URL** | 模型 API 地址 | `https://open.bigmodel.cn/api/paas/v4` |
| **Model Name** | 使用的模型名称 | `autoglm-phone` 或 `autoglm-phone-9b` |

### 4. 授予权限

- **悬浮窗权限**：显示任务状态悬浮窗需要
- **Shizuku 权限**：执行系统操作需要
- **通知权限**：前台服务需要

## 键盘管理

TaskWizard 支持两种输入法，并会在需要时自动切换到兼容的输入法：

- **内置键盘**：TaskWizard 原生输入法（推荐）
- **ADB Keyboard**：外部输入法（向后兼容）

应用状态栏会显示当前键盘状态：
- 内置键盘：显示"内置键盘"（绿色）
- ADB Keyboard：显示"ADB Keyboard"（绿色）
- 未启用：显示"键盘未启用"（红色）

点击状态栏键盘图标可以查看设置选项和切换引导。

## 功能使用

### 历史记录管理

1. 点击顶部状态栏的「历史」按钮
2. 查看所有历史任务记录
3. 点击任意历史记录可查看完整对话内容
4. 查看任务状态、步数、模型等信息
5. 支持搜索和筛选功能

### 继续历史对话

1. 在历史记录页面，点击想要继续的任务
2. 系统自动加载历史消息到聊天界面
3. 可以继续与 AI 对话执行任务
4. 所有消息类型（思考、操作、系统消息）都会显示

### 新建对话

1. 点击顶部状态栏的「➕」按钮
2. 立即清空当前对话，开始新任务
3. 无需手动清除输入框

## 模型选项

### 选项 1：智谱 BigModel（推荐）

```kotlin
Base URL: https://open.bigmodel.cn/api/paas/v4
Model: autoglm-phone
API Key: 从 https://open.bigmodel.cn/ 获取
```

### 选项 2：ModelScope（魔搭社区）

```kotlin
Base URL: https://api-inference.modelscope.cn/v1
Model: ZhipuAI/AutoGLM-Phone-9B
API Key: 从 https://modelscope.cn/ 获取
```

### 选项 3：自托管模型

使用 vLLM 或 SGLang 部署自己的模型：

```bash
# vLLM 示例
vllm serve zai-org/AutoGLM-Phone-9B \
  --max-model-len 8192 \
  --limit-mm-per-prompt '{"image": 10}' \
  --guided-decoding-backend lm-format-enforcer
```

然后将 Base URL 设置为 `http://your-server-ip:8000/v1`。

## 支持的应用

TaskWizard 支持 100+ Android 应用，涵盖多个分类：

| 分类 | 应用 |
|------|------|
| **社交通讯** | 微信、QQ、微博、WhatsApp、Telegram、X/Twitter |
| **电商购物** | 淘宝、京东、拼多多、Temu |
| **美食外卖** | 美团、饿了么、肯德基 |
| **出行旅游** | 携程、12306、去哪儿、滴滴出行 |
| **视频娱乐** | 哔哩哔哩、抖音、快手、腾讯视频、爱奇艺 |
| **音乐音频** | 网易云音乐、QQ音乐、喜马拉雅 |
| **地图导航** | 高德地图、百度地图 |
| **生活服务** | 小红书、豆瓣、知乎 |

完整列表请查看 [AppMap.kt](app/src/main/java/com/taskwizard/android/config/AppMap.kt)。

## 使用方法

### 基本任务执行

1. 打开 TaskWizard
2. 用自然语言输入您的指令：
   - "打开微信，给文件传输助手发送消息：你好世界"
   - "打开美团搜索附近的火锅店"
   - "去淘宝搜索无线耳机"
3. 点击 START 按钮
4. 观看悬浮窗显示的实时进度

### 支持的操作

| 操作 | 说明 | 示例 |
|------|------|------|
| `Launch` | 打开应用 | `do(action="Launch", app="微信")` |
| `Tap` | 点击屏幕 | `do(action="Tap", element=[500, 500])` |
| `Type` | 输入文本 | `do(action="Type", text="你好")` |
| `Swipe` | 滑动屏幕 | `do(action="Swipe", start=[200,800], end=[200,200])` |
| `Back` | 返回上一页 | `do(action="Back")` |
| `Home` | 返回桌面 | `do(action="Home")` |
| `Wait` | 等待加载 | `do(action="Wait", duration="2 seconds")` |
| `Take_over` | 请求人工帮助 | `do(action="Take_over", message="请手动登录")` |

### 任务状态

任务执行期间，悬浮窗会显示：
- **思考中**：AI 的推理过程
- **正在执行**：当前正在执行的操作
- **步数**：已执行的步数
- **进度**：可视化进度指示器

## 项目架构

```
TaskWizard/
├── ui/                     # Jetpack Compose UI
│   ├── screens/           # 主界面、设置界面、历史界面
│   ├── components/        # 可复用 UI 组件
│   ├── overlay/           # 悬浮窗 UI
│   └── viewmodel/         # 状态管理
├── core/                   # 核心自动化逻辑
│   ├── AgentCore.kt       # AI 智能体编排
│   ├── ActionExecutor.kt  # Shizuku 操作执行
│   └── ResponseParser.kt  # 模型响应解析
├── api/                    # 网络层
│   └── AutoGLMService.kt  # Retrofit API 客户端
├── manager/                # 系统集成
│   └── ShizukuManager.kt  # Shizuku 连接与 IPC
├── data/                   # 数据层
│   ├── history/           # 历史记录数据库
│   └── AppState.kt        # 应用状态管理
├── config/                 # 配置
│   ├── AppMap.kt          # 应用包名映射
│   ├── SystemPrompt.kt    # AI 系统提示词
│   └── TimingConfig.kt    # 时间常量配置
├── service/                # Android 服务
│   └── OverlayService.kt  # 前台悬浮窗服务
└── utils/                  # 工具类
    ├── SettingsManager.kt  # 持久化
    └── TaskScope.kt       # 协程作用域
```

## 开发

### 构建要求

- JDK 17 或更高版本
- Android SDK 34
- Kotlin 2.0.0
- Gradle 8.0+

### 构建命令

```bash
# Debug 构建
./gradlew assembleDebug

# Release 构建（需要签名配置）
./gradlew assembleRelease

# 运行测试
./gradlew test

# 运行 Lint
./gradlew lint
```

### 代码签名

Release 构建需要配置密钥库。详细说明请查看 [RELEASE_SETUP.md](RELEASE_SETUP.md)。

### 性能测试

项目包含完整的性能测试套件：

```bash
# 运行单元性能测试
./gradlew testDebugUnitTest --tests "com.taskwizard.android.PerformanceTest"

# 运行 UI 性能基准测试
./gradlew connectedAndroidTest
```

## 常见问题

### Shizuku 未连接

1. 确认 Shizuku 服务正在运行
2. 打开 Shizuku 应用检查服务状态
3. 如需要，重启 Shizuku 服务

### 输入法问题

**Q: 内置键盘无法使用？**

1. 打开设置 → 语言和输入法
2. 找到并启用"TaskWizard Keyboard"
3. 在弹出的警告对话框中点击"确定"

**Q: ADB Keyboard 不工作？**

1. 确认在设置中已启用 ADB Keyboard
2. 检查 ADB Keyboard 是否设为当前输入法
3. 如需要，重新启用 ADB Keyboard

**Q: 如何切换输入法？**

1. 点击应用状态栏的键盘状态图标
2. 选择您偏好的输入法
3. 按照引导完成设置

### API 连接失败

1. 检查网络连接
2. 验证 API Base URL 是否正确
3. 确认 API Key 有效
4. 检查模型服务是否正在运行（自托管）

### 悬浮窗不显示

1. 在 设置 → 应用 → TaskWizard 中授予悬浮窗权限
2. 检查是否已为 TaskWizard 关闭电池优化
3. 确保已授予通知权限

### 历史记录不显示完整

1. 确保使用最新版本
2. 历史记录会保存所有消息类型（思考、操作、系统消息）
3. 如果看不到操作消息，可能需要重新执行任务以保存

## 贡献

欢迎贡献！请：

1. Fork 本仓库
2. 创建特性分支
3. 进行您的更改
4. 提交 Pull Request

## 许可证

本项目采用 Apache License 2.0 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。

## 致谢

- [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) - 原始 Python 框架
- [Shizuku](https://github.com/RikkaApps/Shizuku) - 系统权限框架
- [Jetpack Compose](https://developer.android.com/jetpack/compose) - 现代 UI 工具包

## 免责声明

本项目仅供研究和学习使用。使用本应用时，请遵守所有适用的法律和服务条款。

## 相关链接

- [原版 Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM)
- [Shizuku 文档](https://shizuku.rikka.app/)
- [问题反馈](https://github.com/yourusername/Open-AutoGLM/issues)

---

用 ❤️ 制作，by TaskWizard 团队
