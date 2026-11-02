# Android 0.3.10：避免重复切换通知使用权

日期：2026-10-12。

## 原因与修正

0.3.9 在授权仍有效但通知监听服务尚未连接时，请求异步重连后立即打开 Android 通知使用权设置。`requestRebind` 还未完成时 `isConnected()` 自然仍为 false，用户因此被过早引导到系统设置，并容易误以为每次都要关闭再开启授权。

0.3.10 将“通知使用权是否获准”和“监听服务是否已连接”分开处理：

- 只有系统授权确实关闭时，才直接打开通知使用权设置。
- 授权已开启但监听未连接时，请求重连并等待最多 12 秒的连接回调。
- 等待后仍未连接，提示用户可重试或手动打开设置；不再自动要求反复切换授权。
- Android 13 及以上的 `POST_NOTIFICATIONS` 仍作为 CourseInbox 自身弹出提醒的单独权限处理。

## 构建产物

- APK：`artifacts/mobile/courseinbox-learning-0.3.10-debug.apk`
- 包名：`cn.courseinbox.companion`
- versionCode：18；versionName：0.3.10
- SHA-256：`323D693384D4CEC5ADBE8F93007961B417CAB2DF4400A08327FEE6E46B74C56B`
- 大小：95,575 bytes
- Gradle `assembleDebug`：成功
- APK v2 签名验证：成功；证书沿用旧版 SHA-256 `9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`
- `aapt dump badging`：核对 versionCode、versionName、minSdk 26、targetSdk 35

## 实机边界

本机没有连接 Android 设备，未验证具体手机系统上的监听重连行为。请覆盖安装，不要卸载旧应用；若新版本仍显示“接收未连接”，先查看通知使用权开关是否仍处于开启状态，再把手机型号、Android 版本和应用内状态用于设备级排查。只有系统授权实际关闭时才需要重新授权。
