# 安卓学习通接入准备验证（2026-10-03）

本次完成 APK 构建、签名和电脑接口验证。未连接实体手机，不把安装、通知权限或学习通真实消息接收标为已验收。

## 安装包

- 路径：`artifacts/mobile/courseinbox-companion-0.1.0-debug.apk`
- 大小：17105 字节
- SHA-256：`5c27a44d4642672bcd468bdd27089ee2680ab503d71ff67a3037fb59e8b18c4d`
- 应用包名：`cn.courseinbox.companion`
- 版本：`0.1.0`，versionCode 1
- minSdk 26，targetSdk/compileSdk 35
- `apksigner verify --verbose --print-certs` 成功，使用 APK Signature Scheme v2；Android Debug 证书。
- 编译后的 AndroidManifest.xml 包含 INTERNET、通知监听服务、BIND_NOTIFICATION_LISTENER_SERVICE 和 NotificationListenerService intent action。
- 调试 APK 不包含电脑令牌；令牌由用户在手机界面配对填写。

## 构建和接口证据

采用本机项目内 JDK 17.0.20.1+1、Gradle 8.11.1、Android SDK Platform 35 revision 2 / Build Tools 35.0.0、AGP 8.9.2。下载档案与官方提供的校验值匹配。命令 `assembleDebug` 返回 `BUILD SUCCESSFUL`，31 个任务执行成功。

构建日志：`artifacts/android-build/assemble-debug.log`；签名日志：`artifacts/android-build/apk-signature.log`；元数据日志：`artifacts/android-build/apk-metadata.log`。平台工具未安装且 SDK license 未接受的提示没有阻止 APK 编译；本次没有使用 adb 安装或控制手机。

运行 `scripts/run-tests.ps1`：42 项测试通过。数据库为隔离的临时数据库，包括手机通知接口的鉴权、学习通包名白名单、重复事件去重、原通知时间要求及草稿确认。

正式服务已以 `0.0.0.0:8765` 运行。电脑本机使用 `http://10.121.29.219:8765` 验证健康接口、首页和带令牌状态接口均成功，白名单包含 `com.chaoxing.mobile`。这只能证明电脑端通过自己的局域网地址可访问；手机网络连通和防火墙仍需从手机验证。当前消息和任务仍为原有示例，没有把测试消息写入正式库。

## 真机待验收

1. 安卓手机安装 APK，配置同一可互通网络下的服务地址、令牌和白名单。
2. 用户在系统设置授予通知访问，伴侣显示监听已连接。
3. 用示例事件验证上传，再验证一条学习通真正发布到系统通知栏的通知。
4. 核对电脑消息原文、来源和原通知时间；有明确任务时核对草稿并确认。
5. 测试锁屏、电脑暂时离线后的补传，记录摘要截断、系统后台限制和重复消息等实际情况。

当前实现无可靠周期后台重试；仅凭编译和模拟事件不承诺完整实时同步。
