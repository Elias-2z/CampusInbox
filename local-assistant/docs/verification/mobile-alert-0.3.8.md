# Android 0.3.8 学习通系统通知提醒

2026-10-11。学习通向 Android 发布单条系统通知后，手机通知访问服务保存其原始标题与系统提供的文字。仅在新消息成功写入手机存档后，CourseInbox 再显示一条可点击的本机提醒；点击定位到该消息原文。相同原文的重复通知与手动“补收当前通知栏”不会补弹提醒。其他已加入白名单的应用通知继续按原逻辑保存，但不触发这条学习通提醒。电脑服务和配对不参与此流程。

Android 13 及以上需要允许 CourseInbox 发送通知，所有版本都需要在系统设置中允许 CourseInbox 访问通知。首页和账号页的“开启 / 检查手机提醒”引导授权。提醒内容在锁屏上设为私密；是否响铃或弹出横幅仍受系统通知频道与用户设置控制。学习通若只提供“您有一条新消息／请点击查看”之类的摘要，CourseInbox 也只能显示并保存该摘要。

## 本机验证

- `scripts/build-android.ps1`：`assembleDebug` 成功，APK v2 签名通过，沿用既有签名证书 SHA-256 `9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`。
- `aapt dump badging`：包名 `cn.courseinbox.companion`，versionCode 16，versionName 0.3.8，minSdk 26，targetSdk 35；`aapt dump permissions` 显示 `POST_NOTIFICATIONS`。
- APK：`artifacts/mobile/courseinbox-learning-0.3.8-debug.apk`，SHA-256 `F96D06A784E99CB8770D1C826B7BE2B1D3270D0573CB97CA108002F0BF8750F3`。
- `android/run-tests.ps1`：8 个 JVM 测试类通过，含手机消息去重、存档、通知文字和通知重试队列。
- `scripts/browser-check.cjs`：27 个 Edge 流程通过，consoleErrors 为 0；`tests/test_api.py`：12 项通过，新 APK 下载路由由临时服务验证。
- `scripts/run-tests.ps1` 的 117 项 Python 测试中有 2 项桌面 EXE 产物测试未通过：正在运行的 `CourseInbox-Learning-Assistant.exe` 占用测试试图删除或重建的文件。其余测试通过，不能据此声称整套脚本通过。

## 待实机验证

本机没有连接 Android 实机或模拟器。需在手机覆盖安装 0.3.8，确认通知访问与 CourseInbox 发送通知权限，再等待一条真实学习通系统通知，核对提醒出现、点击打开对应原文，以及学习通只发布摘要时的内容。编译和 JVM 测试不证明真机通知到达。

当前运行的电脑服务尚未重启，仍按旧版下载白名单返回新版 APK 路由 404；本地 APK 文件可直接安装。服务重启命令被自动审批拦截，返回 `blocked by policy`，因此未中断现有后台同步服务。
