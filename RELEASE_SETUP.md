# GitHub Actions 自动发布设置指南

## 📋 概述

本项目已配置 GitHub Actions 自动编译和发布 APK。推送 tag 时会自动：
- 编译签名 APK
- 生成分类 Changelog
- 创建 GitHub Release
- 上传 APK 和 SHA256 校验和

---

## 🔐 首次设置（一次性）

### 1. 生成签名密钥

```bash
# 在项目根目录执行
keytool -genkey -v -keystore release.keystore \
  -alias autoglm-release \
  -keyalg RSA -keysize 2048 -validity 10000

# 按提示输入信息：
# - 密钥库密码（KEYSTORE_PASSWORD）
# - 密钥密码（KEY_PASSWORD）
# - 姓名、组织等信息
```

### 2. 转换为 Base64

```bash
# macOS/Linux
base64 -i release.keystore -o release.keystore.base64

# 查看 Base64 内容
cat release.keystore.base64
```

### 3. 配置 GitHub Secrets

进入 GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret

添加以下 4 个 Secrets：

| Secret 名称 | 值 | 说明 |
|------------|-----|------|
| `KEYSTORE_BASE64` | `<base64 文件内容>` | 完整的 Base64 编码内容 |
| `KEYSTORE_PASSWORD` | `你的密钥库密码` | 生成时输入的密钥库密码 |
| `KEY_ALIAS` | `autoglm-release` | 密钥别名（与生成时一致） |
| `KEY_PASSWORD` | `你的密钥密码` | 生成时输入的密钥密码 |

### 4. 备份密钥文件

```bash
# 将 release.keystore 备份到安全位置
# ⚠️ 永远不要提交到 Git！
# ⚠️ 丢失后无法恢复，所有后续版本都需要重新签名！
```

---

## 🚀 日常发布流程

### 1. 提交代码

```bash
git add .
git commit -m "feat: 添加新功能"
git push origin main
```

### 2. 创建并推送 Tag

```bash
# 创建 tag（版本号格式：v主版本.次版本.修订号）
git tag v1.0.0

# 推送 tag 到 GitHub（触发自动发布）
git push origin v1.0.0
```

### 3. 等待自动构建

- GitHub Actions 会自动执行（约 2-3 分钟）
- 访问 `Actions` 标签页查看进度
- 构建完成后会自动创建 Release

### 4. 检查 Release

访问 `Releases` 页面，确认：
- ✅ APK 文件已上传
- ✅ SHA256 校验和正确
- ✅ Changelog 已生成
- ✅ 系统要求说明完整

---

## 📝 提交信息规范（用于 Changelog 分类）

为了生成清晰的 Changelog，请遵循以下提交信息格式：

| 前缀 | 用途 | 示例 |
|-----|------|------|
| `feat:` | 新功能 | `feat: 添加自动循环功能` |
| `fix:` | Bug 修复 | `fix: 修复 STOP 按钮卡顿` |
| `refactor:` | 代码重构 | `refactor: 优化 MainActivity 结构` |
| `perf:` | 性能优化 | `perf: 减少线程切换次数` |
| `docs:` | 文档更新 | `docs: 更新 README` |
| `chore:` | 其他改动 | `chore: 更新依赖版本` |

**示例：**
```bash
git commit -m "feat: 添加敏感操作确认对话框"
git commit -m "fix: 修复 Shizuku 服务解绑阻塞问题"
git commit -m "refactor: 重构代码结构提升可读性"
git commit -m "perf: 优化历史记录加载性能"
```

---

## 🔍 Release 格式预览

发布后的 Release 页面会显示：

```markdown
## 🚀 TaskWizard v1.0.0

### 📦 下载

**APK 文件：** TaskWizard-v1.0.0.apk

**SHA256 校验和：**
```
abc123def456...
```

---

### 📝 更新日志

#### ✨ 新功能
- feat: 添加自动循环功能 (a1b2c3d)
- feat: 支持敏感操作确认 (d4e5f6g)

#### 🐛 Bug 修复
- fix: 修复 STOP 按钮卡顿 (j1k2l3m)

#### 🎨 代码优化
- refactor: 重构 MainActivity (y7z8a9b)

#### ⚡ 性能优化
- perf: 优化历史记录加载 (m4n5o6p)

---

### ⚙️ 系统要求

- Android 8.0 (API 26) 或更高版本
- 需要安装 [Shizuku](https://github.com/RikkaApps/Shizuku)
- 需要安装 [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard)（用于文本输入）

### 📖 使用说明

详细使用说明请查看项目文档
```

---

## 🛠️ 故障排查

### 问题 1：构建失败 - "Keystore not found"

**原因：** GitHub Secrets 未正确配置

**解决：**
1. 检查 4 个 Secrets 是否都已添加
2. 确认 `KEYSTORE_BASE64` 内容完整（无换行符）
3. 确认密码和别名正确

### 问题 2：APK 未签名

**原因：** 签名配置未生效

**解决：**
1. 检查 `app/build.gradle.kts` 中的签名配置
2. 确认环境变量正确设置
3. 查看 Actions 日志中的错误信息

### 问题 3：Changelog 为空

**原因：** 提交信息不符合规范

**解决：**
1. 使用规范的提交信息前缀（feat:, fix: 等）
2. 确保两个 tag 之间有提交记录

### 问题 4：Release 创建失败

**原因：** 权限不足

**解决：**
1. 检查 workflow 文件中的 `permissions: contents: write`
2. 确认 `GITHUB_TOKEN` 有足够权限

---

## 📚 相关文件

- `.github/workflows/release.yml` - Release 自动发布配置
- `.github/workflows/build-pr.yml` - PR 自动构建配置
- `app/build.gradle.kts` - 签名配置
- `.gitignore` - 密钥文件排除规则

---

## ⚠️ 安全注意事项

1. ✅ **永远不要**将 `release.keystore` 提交到 Git
2. ✅ **永远不要**在代码中硬编码密码
3. ✅ **定期备份** keystore 文件到安全位置
4. ✅ **妥善保管**所有密码和密钥
5. ✅ 如果密钥泄露，立即生成新密钥并更新 Secrets

---

## 📞 需要帮助？

如果遇到问题：
1. 查看 GitHub Actions 日志
2. 检查本文档的故障排查部分
3. 在 Issues 中提问
