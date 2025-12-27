# GitHub Actions 配置检查清单

## ✅ 当前配置分析

### 1. Release Workflow (`release.yml`)

#### ✅ 优点
- JDK 17 配置正确
- 签名配置完善（使用环境变量）
- 自动生成分类 Changelog
- 计算 SHA256 校验和
- 自动创建 GitHub Release
- 清理敏感文件
- Gradle 缓存已配置

#### ✅ 包名配置
- **项目名称**: TaskWizard
- **包名**: `com.taskwizard.android`
- 状态: ✅ 已正确配置

#### ⚠️ 签名密钥配置要求
需要在 GitHub Secrets 中配置以下变量：
- `KEYSTORE_BASE64`: Base64 编码的签名密钥文件
- `KEYSTORE_PASSWORD`: 密钥库密码
- `KEY_ALIAS`: 密钥别名 (`autoglm-release`)
- `KEY_PASSWORD`: 密钥密码

### 2. PR Workflow (`build-pr.yml`)

#### ✅ 优点
- 自动构建 PR
- 运行测试
- 上传 APK 作为 Artifact
- 自动添加 PR 评论
- 使用最新版本 `actions/upload-artifact@v4`

#### ⚠️ 注意事项
- 测试失败时使用 `continue-on-error: true`（允许失败继续）

---

## 🔧 GitHub Secrets 配置

在 GitHub 仓库设置中添加以下 Secrets：

### 1. 生成签名密钥

```bash
keytool -genkey -v -keystore release.keystore \
  -alias autoglm-release \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

### 2. 转换为 Base64

```bash
# macOS
base64 -i release.keystore -o release.keystore.base64

# Linux
base64 release.keystore -w 0 > release.keystore.base64
```

### 3. 在 GitHub 添加 Secrets

- Settings → Secrets and variables → Actions → New repository secret
- 添加以下 4 个 secrets：
  - `KEYSTORE_BASE64`: 粘贴 base64 文件内容
  - `KEYSTORE_PASSWORD`: 密钥库密码
  - `KEY_ALIAS`: `autoglm-release`
  - `KEY_PASSWORD`: 密钥密码

---

## ✅ 配置验证清单

在推送到 GitHub 之前，请确认：

- [x] 包名已正确配置为 `com.taskwizard.android`
- [ ] 已生成签名密钥
- [ ] 已在 GitHub Secrets 中配置所有必需的变量
- [ ] 已测试本地构建 Debug APK
- [ ] 已更新文档中的项目名称引用
- [ ] 已测试 `./gradlew assembleDebug` 本地构建成功

---

## 🚀 首次发布流程

### 1. 生成签名密钥

```bash
keytool -genkey -v -keystore release.keystore \
  -alias autoglm-release \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

### 2. 配置 GitHub Secrets

按照上面的说明添加 4 个 secrets

### 3. 本地测试构建

```bash
# 设置环境变量（本地测试）
export KEYSTORE_FILE=./release.keystore
export KEYSTORE_PASSWORD=your_password
export KEY_ALIAS=autoglm-release
export KEY_PASSWORD=your_password

# 构建
./gradlew assembleRelease
```

### 4. 推送代码并创建 Tag

```bash
git add .
git commit -m "chore: prepare for v1.0.0 release"
git push origin main

# 创建 tag 触发 release workflow
git tag v1.0.0
git push origin v1.0.0
```

---

## 📊 预期构建时间

- **PR 构建**: 约 3-5 分钟
- **Release 构建**: 约 5-8 分钟（包含签名和 Release 创建）

---

## 🔒 安全建议

1. **永远不要提交签名密钥到 Git**
   - 已在 `.gitignore` 中添加 `*.keystore`

2. **定期更新 Secrets**
   - 建议每年更新一次签名密钥密码

3. **限制 Secrets 访问权限**
   - 只在必要的 workflow 中使用 secrets

---

## 📋 当前项目配置

### 项目信息

| 配置项 | 值 |
|--------|-----|
| 项目名称 | TaskWizard |
| 包名 | `com.taskwizard.android` |
| 最小 SDK | 26 (Android 8.0) |
| 目标 SDK | 34 |
| 编译 SDK | 34 |
| JDK 版本 | 17 |
| Gradle 插件 | 8.1.0 |
| Kotlin 版本 | 2.0.0 |

### 构建配置

```kotlin
// app/build.gradle.kts
android {
    namespace = "com.taskwizard.android"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.taskwizard.android"
        minSdk = 26
        targetSdk = 34
    }

    signingConfigs {
        create("release") {
            // 从环境变量读取签名信息
            val keystoreFile = System.getenv("KEYSTORE_FILE")
            val keystorePassword = System.getenv("KEYSTORE_PASSWORD")
            val keyAlias = System.getenv("KEY_ALIAS")
            val keyPassword = System.getenv("KEY_PASSWORD")

            if (keystoreFile != null && keystorePassword != null &&
                keyAlias != null && keyPassword != null) {
                storeFile = file(keystoreFile)
                storePassword = keystorePassword
                this.keyAlias = keyAlias
                keyPassword = keyPassword
            }
        }
    }
}
```

---

## 📚 相关文档

- [Android 应用签名](https://developer.android.com/studio/publish/app-signing)
- [GitHub Actions 文档](https://docs.github.com/en/actions)
- [Gradle Build Cache](https://docs.gradle.org/current/userguide/build_cache.html)
- [项目 README](README.md)
- [发布设置指南](RELEASE_SETUP.md)
- [使用指南](GITHUB_ACTIONS_GUIDE.md)
