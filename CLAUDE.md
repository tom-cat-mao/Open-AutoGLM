# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TaskWizard** (com.taskwizard.android) is an Android automation application that uses AI models to perform automated tasks on Android devices through screen understanding and action execution. The app uses Shizuku for system-level privileges and ADB Keyboard for text input.

## Architecture

### MVVM with Clean Architecture

- **Presentation Layer**: Jetpack Compose UI + ViewModels (`ui/`, `ui/viewmodel/`)
- **Domain Layer**: Business logic (`core/`, `data/`)
- **Infrastructure Layer**: External integrations (`api/`, `manager/`)

### Key Components

- **TaskWizardApplication**: Application class managing `TaskScope` (application-level coroutine scope) and broadcast receivers
- **MainActivity**: Compose entry point with Navigation Compose, integrates with `MainViewModel`
- **MainViewModel**: Central state management using StateFlow, handles API config, task execution, system status checks, theme, and overlay service control
- **OverlayService**: Foreground service displaying floating Compose overlay for task status
- **TaskScope**: Coroutine scope with SupervisorJob for long-running tasks independent of Activity lifecycle
- **AgentCore**: AI logic implementation, manages conversation history and action generation
- **ActionExecutor**: Executes UI actions via Shizuku with retry logic
- **IAutoGLMService**: AIDL interface for IPC with Shizuku (defined in `app/src/main/aidl/com/taskwizard/android/IAutoGLMService.aidl`)

### Important Patterns

- **Single Source of Truth**: All UI state in `AppState` data class with immutable collections (`ImmutableList`)
- **Overlay Animation Flow**: App shrinks to overlay when task starts; MainActivity moves to background (not finished); task continues with status updates
- **TaskScope Lifecycle**: Application-level coroutine scope using `SupervisorJob`, survives Activity recreation for long-running tasks; manages cleanup callbacks and task stopping notifications
- **IME Management**: ADB Keyboard is automatically switched via Shizuku before text input and restored on error/task completion
- **Coordinate System**: `ActionExecutor` uses screenshot dimensions (not physical screen) for coordinate conversion; must call `updateScreenSize()` after each capture
- **Error Handling**: Maximum step limits, consecutive failure detection, automatic IME restoration
- **State Management**: StateFlow for reactive updates, immutable data classes
- **History Persistence**: Room database (`data/history/`) stores task history for continuation; supports full message/action tracking

## Build and Test Commands

```bash
# Build debug APK
./gradlew assembleDebug

# Build release APK (requires signing config or uses debug signing locally)
./gradlew assembleRelease

# Clean build
./gradlew clean

# Run lint
./gradlew lint

# Run all unit tests
./gradlew test
./gradlew testDebugUnitTest

# Run tests for specific module
./gradlew :app:testDebugUnitTest

# Run a single test class
./gradlew testDebugUnitTest --tests "com.taskwizard.android.MainViewModelTest"

# Run a single test method
./gradlew testDebugUnitTest --tests "com.taskwizard.android.MainViewModelTest.testApiConfigValidation"

# Compile Kotlin code (syntax check without full build)
./gradlew compileDebugKotlin
```

## Build Configuration

- **Android Gradle Plugin**: 8.1.0
- **Kotlin**: 2.0.0
- **Compile SDK**: 34
- **Min SDK**: 26 (Android 8.0+)
- **JVM Args**: `-Xmx2048m -XX:MaxMetaspaceSize=512m` (optimized for CI/CD)
- **Compose**: Enabled with compiler metrics/reports

Note: The JVM args in `gradle.properties` are specifically tuned for GitHub Actions to prevent OOM during CI builds.

## Signing Configuration

Release builds use environment variables for signing:
- `KEYSTORE_FILE`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`

For local release builds without these set, the app falls back to debug signing.

## CI/CD

GitHub Actions automatically builds signed APKs and creates releases when tags are pushed:
- Push tag `v1.0.0` format to trigger release workflow
- See `RELEASE_SETUP.md` for keystore setup and GitHub Secrets configuration
- PR builds automatically create debug APK artifacts

## Testing Framework

- JUnit 4 + Kotlin Test
- Coroutines Test
- Robolectric (Android unit tests)
- Mockito + MockK (mocking)
- AndroidX Test (Core, JUnit, Arch Core Testing)

## System Requirements for Running the App

- Android 8.0+ (API 26)
- Shizuku app for system privileges
- ADB Keyboard for text input
- Internet connection for API calls
