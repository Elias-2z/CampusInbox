# Android 0.3.20 安装包体积

2026-10-16。用户询问能否减少程序大小。本轮仅调整 CPU 架构打包范围，保留现有业务代码和离线中文 OCR。这里记录 APK 下载文件大小，MB 按 1000000 字节计算；未测量实体手机安装后的总占用。

## 来源与结果

原 0.3.19 APK 为 47,217,999 字节（47.2 MB）。ZIP 中四份 OCR 本地库占 41,033,660 字节，分别用于 ARM64、32 位 ARM、x86、x86_64；OCR 模型资产约 1.95 MB，DEX 代码约 3.45 MB。

- 默认 ARM 手机版：[courseinbox-learning-0.3.20-debug.apk](../../artifacts/mobile/courseinbox-learning-0.3.20-debug.apk)，23,914,592 字节（23.9 MB），减少 49.35%。包含 `armeabi-v7a`、`arm64-v8a`，供 32/64 位 ARM 手机使用。
- ARM64 精简版：[courseinbox-learning-0.3.20-arm64-debug.apk](../../artifacts/mobile/courseinbox-learning-0.3.20-arm64-debug.apk)，17,121,519 字节（17.1 MB），减少 63.74%。仅包含 `arm64-v8a`，需要设备支持该架构。

均沿用包名 `cn.courseinbox.companion`、versionCode 28、versionName 0.3.20、minSdk 26、targetSdk 35，以及证书 SHA-256 `9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`。匹配架构的设备可保留应用数据覆盖升级。

## 实现范围

`build.gradle` 的 `ciArchitecture` 参数控制 ABI 集合，默认 `arm`；`scripts/build-android.ps1 -Architecture arm64` 导出 ARM64 专用文件名。参数规范为小写并限制取值，导出前检查实际架构集合及原签名，防止文件名与内容不符。

保留 `-Architecture universal` 构建参数，包含四种架构，用于 x86/x86_64 等开发环境；本轮没有导出该安装包。下载路由为三个明确文件名建立白名单，网页默认链接 ARM 手机版，另提供 ARM64 链接。

本轮没有启用 R8、资源裁剪或更改本地库压缩方式。离线中文识图、网页读取、通知、任务与消息清理的实现保持不变。

## 验证

1. 两次离线 `assembleDebug` 均成功；构建脚本分别核对实际 `native-code` 为两种 ARM 架构和仅 ARM64，签名 v2 验证通过。
2. 两包的全部 DEX、全部 assets（包含 OCR 模型和网页脚本）与 0.3.19 逐字节相同。两包保留的每个本地库也与 0.3.19 对应文件逐字节相同。这证明本轮没有删去功能代码或模型，不等于真机运行验收。
3. `android/run-tests.ps1` 最终完整通过，覆盖历史入库 49 项、排序清理 25 项、手机收件箱 57 项、图片导入 24 项、读取流程 31 项及通知连接恢复等既有回归。
4. Python 132 项、桌面应用 33 项通过。Edge 27 个界面流程通过，consoleErrors 为空；确认默认 ARM 和 ARM64 两个下载入口及手机宽度布局。
5. 隔离 HTTP 服务分别实际下载两个签名产物，均为 HTTP 200，MIME 为 Android 安装包，下载内容逐字节等于本地 APK。该服务已关闭，不代表用户常用端口的服务已重启。
6. `git diff --check` 通过。没有修改实际手机存档，也没有连接实体手机测试安装或离线识图。

文件 SHA-256：

- ARM：`127C528E35BD718969DB76A6641C5CAA6D39F06975EC1097334B715E8A2C0004`。
- ARM64：`218870948AB26D9620B2B71B1A820CB5B9EC71BFFBD6BA0E6BCA299E4BC0AA73`。

## 使用边界

保留旧应用，选择匹配架构的 APK 直接覆盖安装。默认 ARM 包不包含 x86/x86_64 本地库；ARM64 包也不包含 32 位 ARM 本地库。仅声明 Android API 范围不能证明所有品牌机型已验收，原生 OCR、覆盖升级和后台通知仍需在对应实体手机核对。
