# Android 0.3.9：通知使用权已开启但接收未连接

2026-10-12。用户截图显示学习通通知图标、手机收件箱有 15 条消息、待确认任务 0 项，以及“后台通知接收未连接 / CourseInbox 提醒已允许”。用户确认 Android 通知使用权已开启。截图没有显示学习通通知正文，不能据此断言这条通知包含可提取的任务。

## 定位与修改

当前 0.3.8 完整手机应用包含通知服务与消息提醒，但在监听断开时没有请求重新绑定，返回应用也只重试已采集通知的保存。连接回调没有扫描已有系统通知，因此连接之前发布、之后一直留在通知栏的通知可能漏收。

0.3.9 区分通知访问授权、实际监听连接和发送提醒权限。监听断开、返回主界面及前台每 15 秒刷新时，已授权且未连接的服务请求重新绑定；请求间隔至少 10 秒。只有收到连接回调才标记已连接，随后自动扫描通知栏中仍保留的白名单通知。

自动扫描沿用原始包名、标题、系统正文、通知键和发布时间。新记录成功保存后可以提醒；现有 `LocalInbox.ingest` 去重和 `MobileStore.notification` 的成功保存判断继续限制重复提醒。手动“补收当前通知栏”保持静默补收。系统未提供正文的摘要仍保存为消息，只有符合任务规则的内容才生成待确认草稿。网页读取、电脑配对、通知白名单和既有手机存档格式保留。

实现依据：[Android NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService) 要求断连期间只请求 `requestRebind`，在 `onListenerConnected` 后才读取当前通知。[NotificationManager](https://developer.android.com/reference/android/app/NotificationManager#isNotificationListenerAccessGranted(android.content.ComponentName)) 的授权查询从 API 27 可用，API 26 沿用系统已启用监听组件设置，并匹配本应用的具体服务组件。

## 本机验证

- 修复前 `android/run-notification-tests.ps1` 因“connection must scan notifications posted before it was bound”失败；修复后 Android 边界替身执行真实通知服务代码，17 项断言通过，覆盖重连、节流、权限撤销、系统拒绝、连接前禁止读取、连接后补扫、扫描异常、销毁及 API 26 授权兼容。替身不证明 Android 系统实际执行绑定。
- `android/run-tests.ps1`：8 个原有 JVM 测试类的 120 项断言及新增 17 项断言通过；原有测试包含消息去重、原文存档、任务草稿和通知保存重试。
- `scripts/run-tests.ps1`：122 项 Python 测试通过；桌面应用测试通过。APK 下载回归先因新路由返回 404 失败，加入下载白名单后通过，仍拒绝非 APK 文件和越界路径。
- `scripts/browser-check.cjs`：27 个 Edge 流程通过，`consoleErrors` 为空，覆盖新版下载入口、无任务摘要的消息展示及收件箱刷新。
- `scripts/build-android.ps1`：离线 `assembleDebug` 成功，v2 签名通过，沿用证书 SHA-256 `9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`。
- `aapt dump badging`：包名 `cn.courseinbox.companion`，versionCode 17，versionName 0.3.9，minSdk 26，targetSdk 35，启动页面为 `MainActivity`。
- APK：`artifacts/mobile/courseinbox-learning-0.3.9-debug.apk`，94,159 字节，SHA-256 `369C382C64A904EA5F791E88E280F9B4FA74A583F95845680998FC5BEE08C0E3`。

## 实机验收

当前没有可用的 adb 或连接的 Android 测试设备，未验证用户手机上的系统绑定、真实学习通正文、横幅或声音。需要保留旧应用覆盖安装，保留学习通通知栏内容；若接收仍未连接，在系统通知使用权中关闭再开启 CourseInbox，返回首页确认“后台通知接收已连接”，然后核对消息原文、真实新消息提醒和重复通知去重。原文只有“您有一条新消息 / 请点击查看”时，应保存消息但不凭摘要创建任务。
