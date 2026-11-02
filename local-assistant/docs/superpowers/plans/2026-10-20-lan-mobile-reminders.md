> 2026-10-20用户明确修正：手机浏览器直接连接服务器，无需安装程序。主要交付为同一Wi-Fi受限服务器与手机网页；已构建的Android接收端改为可选，不作为使用前提。浏览器只承诺前台网页提醒，锁屏系统推送仍需HTTPS/Web Push，当前未实现。

# 同一 Wi-Fi 电脑到 Android 提醒

用户已明确方向：电脑后台采集学习通，手机接收提醒；第一阶段只要求同一 Wi-Fi。现有本机主服务和手机通知上传保持兼容。

## 接口及边界

主服务仅本机、主访问令牌：

- GET `/api/mobile-reminders/status` -> `{enabled,listening,port,addresses:["http://私有IPv4:端口"],server_id,error,devices:[{id,name,paired_at,last_seen,online}],android_version:"0.3.21"}`。
- POST `/api/mobile-reminders/settings` `{enabled:bool}` -> 同上。开启独立的受限局域网监听器，默认端口8766；主服务和管理接口仍仅本机。
- POST `/api/mobile-reminders/pairing` `{}` -> `{code:"六位数字",expires_at:ISO}`，5分钟、单次、限次尝试。
- POST `/api/mobile-reminders/revoke` `{id:"设备编号"}` -> `{ok:true}`。撤销立即使旧设备令牌失效。

受限局域网监听器只提供：

- GET `/v1/health` -> `{app:"CourseInbox Reminders",version:1,server_id}`。
- POST `/v1/pair` `{code,name}` -> `{token,device_id,server_id}`。
- GET `/v1/alerts`，Bearer设备令牌 -> `{enabled:true,server_id,events:[{id,kind,title,body,url}],poll_after:15}`。
- POST `/v1/ack` `{ids:[id]}`，同设备令牌 -> `{ok:true}`。只能确认已经向该设备发出的提醒。

设备令牌只允许这组受限接口，服务端只存其哈希。手机不接收电脑主令牌或学习通凭据，不根据外部URL打开任意页面。设备回执与桌面回执分离，断线/重启保留，首次配对已有内容建立基线，新接收的消息及新任务/任务变更/截止事件按稳定ID去重。暂停与撤销不能被正在飞行的请求绕过。

## 实施与验证

1. 后端：新增 `courseinbox/mobile_reminders.py`，编写隔离SQLite/API测试，验证双设备ACK互不影响、基线、受限路由、失效码、撤销、断线重放、主接口本机边界。
2. Android：新增电脑提醒配对页面和后台连接服务；共享字段使用上述协议。Android8+，发送通知权限独立于通知读取权限。用户主动启动 connectedDevice 前台服务，15秒轮询，失败退避；保存成功通知及待ACK回执，稳定通知tag、禁止重定向、地址限私有IPv4/可信本机HTTP。手机显示提醒历史，点通知查看本地提醒，停止后不再接收。版本0.3.21/code29，保留原签名，构建ARM/ARM64安装包及JVM/通知测试。
3. 页面：手机连接页增加服务开关、地址、配对码/有效期、设备在线状态、撤销和新版安装包入口。保留原手机通知上传流程，方向明确。浏览器测试使用隔离服务/模拟配对，不宣称真机验收。
4. 整合：正式APK下载白名单、文档、Windows防火墙8766最小规则辅助脚本；完整Python/桌面/浏览器/Android回归及独立代码审查。真实同Wi-Fi、OEM后台、系统通知显示仍待手机验收。

Android前台服务类型依据官方文档：https://developer.android.com/develop/background-work/services/fgs/service-types 。此服务用于维持与电脑外部设备的网络连接；不使用有时长上限的dataSync类型，也不承诺绕过省电/强制停止。

## 浏览器修正

受限监听器固定提供 `/`、`/phone.js` 和 `/phone.css`，与主管理网页隔离。手机仅输入六位码和名称，同源调用v1接口；配对信息和最近100条提醒保存在此浏览器，显示后回执。前台15秒轮询，返回后补收；稳定事件ID、独立设备令牌、跨标签控制代次及storage事件保护停止/断开/重配，渲染仅textContent。网页提供可选提示音，不将HTTP页面宣称为系统后台通知。电脑面板主流程改为地址/复制/配对码，无APK步骤。
