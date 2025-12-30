plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.taskwizard.android.hiddenapis"
    compileSdk = 34

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = "11"
    }
}

dependencies {
    // Rikka Refine - Access hidden APIs
    implementation("dev.rikka.tools.refine:runtime:4.4.0")
    compileOnly("dev.rikka.tools.refine:annotation:4.4.0")
}
