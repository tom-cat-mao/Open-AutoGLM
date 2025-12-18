#!/bin/bash

# 设置Android SDK路径（通过Homebrew安装的位置）
export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"

# 检查 SDK 目录
if [ ! -d "$ANDROID_HOME" ]; then
    echo "ERROR: ANDROID_HOME not found at $ANDROID_HOME"
    echo "Trying to find SDK in other locations..."
    
    # 尝试其他可能的位置
    if [ -d "$HOME/Library/Android/sdk" ]; then
        export ANDROID_HOME="$HOME/Library/Android/sdk"
        echo "Found SDK at: $ANDROID_HOME"
    else
        echo "ERROR: Cannot find Android SDK"
        exit 1
    fi
fi

echo "Using ANDROID_HOME: $ANDROID_HOME"

# 检查 build-tools 是否存在
if [ ! -d "$ANDROID_HOME/build-tools" ]; then
    echo "ERROR: build-tools directory not found in $ANDROID_HOME"
    exit 1
fi

echo "Found build-tools: $(ls $ANDROID_HOME/build-tools/)"

# 检查 platforms 是否存在
if [ ! -d "$ANDROID_HOME/platforms" ]; then
    echo "ERROR: platforms directory not found in $ANDROID_HOME"
    exit 1
fi

echo "Found platforms: $(ls $ANDROID_HOME/platforms/)"

# 清理并构建
echo ""
echo "========================================" 
echo "Starting Build Process"
echo "========================================"
chmod +x gradlew
./gradlew clean assembleDebug --stacktrace
