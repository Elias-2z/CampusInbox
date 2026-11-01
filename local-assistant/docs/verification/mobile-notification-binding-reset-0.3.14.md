# Android 0.3.14 通知监听绑定重建

用户在 iQOO 13（系统版本 16.1.19.11）再次确认：每次覆盖更新后系统“通知使用权”开关仍开启，但 CourseInbox 显示“接收未连接”；手动关闭再开启授权后恢复。之前 0.3.12 的单次重传成功不能证明每次升级后自动绑定成功。截图显示应用已向系统请求连接 5 次，仍没有收到 `onListenerConnected()`；这定位了应用与 Android 通知管理器之间的绑定边界，但不能仅凭截图证明 OEM 的内部原因。

Android [通知监听接口说明](https://developer.android.com/reference/android/service/notification/NotificationListenerService.html)指出 `requestRebind()` 是在解绑后请求重新绑定；API 34 起提供本应用监听组件的静态 `requestUnbind(ComponentName)`。0.3.12 只重复调用 `requestRebind()`，没有重建旧绑定。0.3.14 在升级或开机后先照常请求重绑；若仍已授权但没有连接回调，Android 14 及以上限次执行一次系统监听解绑与重绑。应用保持打开时由前台计时约 15 秒后尝试；后台安排最早 15 秒后的不精确闹钟，实际触发可能被系统延后（见 [AlarmManager 文档](https://developer.android.com/reference/android/app/AlarmManager.html)）。低版本保留原重试流程；任何版本都不修改系统授权开关。重建失败仍继续普通重试，在“我的 → 手机提醒”显示重建尝试次数。此机制只是针对陈旧绑定的受控恢复路径，不能保证系统一定执行绑定。

2026-10-15 验证：Android 通知边界 shim 测试 28 项通过，覆盖已授权时解绑再重绑、无回调不得视为已连接、每次升级仅一次、低版本不调用 API 34、授权撤销及系统拒绝解绑。独立升级恢复测试确认后续闹钟及限次标记在解绑前持久安排；闹钟安排失败测试确认不会解绑，仍走普通重绑。全套 Android 协议测试通过。项目 Python 132 项测试、桌面应用测试、浏览器完整流程与新版 APK 下载入口检查通过。`assembleDebug` 成功，APK 包名 `cn.courseinbox.companion`、versionCode 22、versionName 0.3.14、minSdk 26、targetSdk 35；v2 签名证书 SHA-256 与旧版相同：`9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`。APK 文件 SHA-256：`EAA09F5957B0235172C2713306298863E8F2E2738F54A51C95DB36EF04C97EA6`。

仍待 iQOO 13 覆盖安装后无需切换系统开关的真实验收。构建和模拟测试只能证明应用调用顺序，不证明 OEM 已完成连接。

2026-10-15 晚间后续反馈：用户确认截图来自 iQOO 13 上覆盖安装后的 0.3.14，起初出现“通知接收仍未连接”，随后确认“已连接成功，但速度较慢”，估计等待约 1～3 分钟。本次证明最终能够连接；没有手机系统日志，尚不能定位延迟发生在哪一层，也不能推断后续每次升级均会成功。0.3.14 的 25 秒提示早于本次实际恢复时间，后续 [0.3.15](mobile-notification-waiting-0.3.15.md) 调整等待提示并加入手机端诊断，没有另行更改系统绑定策略。
