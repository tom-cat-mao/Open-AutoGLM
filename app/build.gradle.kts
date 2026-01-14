plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.0"
    id("com.google.devtools.ksp") version "2.0.0-1.0.24"
}

android {
    namespace = "com.taskwizard.android"
    compileSdk = 34
    buildToolsVersion = "34.0.0"

    defaultConfig {
        applicationId = "com.taskwizard.android"
        minSdk = 26 // Android 8.0+
        targetSdk = 34

        // Read version from env var (GitHub Actions), fallback to hardcoded default
        val versionFromEnv = System.getenv("VERSION_NAME")
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = versionFromEnv ?: "1.0.0"
    }

    // ==================== 签名配置 ====================
    signingConfigs {
        create("release") {
            // 从环境变量读取签名信息（GitHub Actions 会设置）
            val keystoreFile = System.getenv("KEYSTORE_FILE")
            val keystorePassword = System.getenv("KEYSTORE_PASSWORD")
            val keyAlias = System.getenv("KEY_ALIAS")
            val keyPassword = System.getenv("KEY_PASSWORD")

            if (keystoreFile != null && keystorePassword != null &&
                keyAlias != null && keyPassword != null) {
                storeFile = file(keystoreFile)
                storePassword = keystorePassword
                this.keyAlias = keyAlias
                this.keyPassword = keyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // 只有在环境变量存在时才使用 release 签名配置
            // 否则使用 debug 签名（用于本地测试构建）
            val keystoreFile = System.getenv("KEYSTORE_FILE")
            if (keystoreFile != null) {
                signingConfig = signingConfigs.getByName("release")
            } else {
                // 本地构建使用 debug 签名
                signingConfig = signingConfigs.getByName("debug")
            }
        }
        debug {
            // Debug 版本使用默认签名
        }
    }
    // ==================== 签名配置结束 ====================

    // ==================== Lint 配置（减少内存占用）====================
    lint {
        // 禁用 Release 构建时的 Lint 检查（减少内存占用）
        checkReleaseBuilds = false
        abortOnError = false
        // 只检查严重错误
        checkOnly += setOf("NewApi", "InlinedApi")
    }
    // ==================== Lint 配置结束 ====================

    buildFeatures {
        aidl = true
        viewBinding = true
        buildConfig = true
        compose = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.8"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = "11"
        freeCompilerArgs += listOf(
            "-opt-in=androidx.compose.material3.ExperimentalMaterial3Api",
            "-opt-in=androidx.compose.foundation.ExperimentalFoundationApi"
        )
    }

    // ==================== Compose Compiler Metrics（性能优化）====================
    // 启用 Compose 编译器报告，用于分析重组性能和稳定性
    // 报告将生成在 app/build/compose-metrics 和 app/build/compose-reports 目录
    composeCompiler {
        metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
        reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    }
    // ==================== Compose Compiler Metrics 结束 ====================
}

dependencies {
    // Jetpack Compose BOM (统一版本管理)
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    // Compose Activity
    implementation("androidx.activity:activity-compose:1.8.2")

    // ViewModel + Lifecycle
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-service:2.7.0")  // 悬浮窗服务需要

    // Navigation Compose
    implementation("androidx.navigation:navigation-compose:2.7.6")

    // Shizuku API
    implementation("dev.rikka.shizuku:api:13.1.5")
    // Shizuku Provider
    implementation("dev.rikka.shizuku:provider:13.1.5")

    // Kotlin Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Kotlinx Immutable Collections (性能优化：稳定化 Compose State)
    implementation("org.jetbrains.kotlinx:kotlinx-collections-immutable:0.3.7")

    // Core
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")

    // Networking (updated OkHttp to 4.12.0)
    implementation("com.squareup.retrofit2:retrofit:2.9.0")
    implementation("com.squareup.retrofit2:converter-gson:2.9.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // JSON
    implementation("com.google.code.gson:gson:2.10.1")

    // ==================== Room Database (History Feature) ====================
    val roomVersion = "2.6.1"
    implementation("androidx.room:room-runtime:$roomVersion")
    implementation("androidx.room:room-ktx:$roomVersion")
    ksp("androidx.room:room-compiler:$roomVersion")

    // ==================== Security (Encrypted Storage) ====================
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // ==================== 测试依赖 ====================

    // JUnit 4
    testImplementation("junit:junit:4.13.2")

    // Kotlin Test
    testImplementation("org.jetbrains.kotlin:kotlin-test:2.0.0")

    // Coroutines Test
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")

    // Robolectric (Android单元测试)
    testImplementation("org.robolectric:robolectric:4.11.1")

    // Mockito
    testImplementation("org.mockito:mockito-core:5.8.0")
    testImplementation("org.mockito:mockito-inline:5.2.0")

    // MockK (Kotlin mocking library)
    testImplementation("io.mockk:mockk:1.13.8")
    testImplementation("io.mockk:mockk-android:1.13.8")

    // MockWebServer for network testing
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")

    // AndroidX Test
    testImplementation("androidx.test:core:1.5.0")
    testImplementation("androidx.test.ext:junit:1.1.5")
    testImplementation("androidx.arch.core:core-testing:2.2.0")

    // Compose UI Test
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")

    // Navigation Test
    androidTestImplementation("androidx.navigation:navigation-testing:2.7.6")

    // Room Testing (for history feature tests)
    testImplementation("androidx.room:room-testing:2.6.1")
}
