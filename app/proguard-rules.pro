# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.

# Keep data classes for Gson serialization
-keep class com.taskwizard.android.data.** { *; }

# Keep Shizuku AIDL interfaces
-keep class com.taskwizard.android.service.IAutoGLMService** { *; }

# Keep Room entities
-keep class * extends androidx.room.RoomDatabase
-keep @androidx.room.Entity class *

# Uncomment if you want to enable ProGuard in the future
# -dontwarn **
