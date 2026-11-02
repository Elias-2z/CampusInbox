# 手机自动寻找已配对电脑验收

日期：2026-10-04。交付版本：Android 0.2.0 / versionCode 2；源码分支 codex/mobile-autodiscovery。

## 已验证

- 原项目 42 项测试通过；新增身份持久化、跨语言证明、动态 Host、缺少依赖回退、生命周期和限定 APK 下载测试后，全部 **53 项 Python 测试**通过。
- JVM `PairingProofTest` 8 项断言通过：黄金向量一致，错误令牌、身份、nonce、地址、端口和摘要不能通过。
- JVM `DiscoveryRegressionTest` 8 项断言通过：地址草稿保留、候选独立超时、成功/超时均取消请求。
- JVM `ConnectionResolverTest` 7 项断言通过：缓存成功无需发现；缓存失败后跳过错误身份与无效证明，找到同一电脑；失败/超时结束时关闭候选源。
- 直接调用实际编译的 `ComputerDiscovery.privateIpv4` 的 JVM 检查 11 项断言通过，拒绝代理测试网段、公网、IPv6、越界数字和带前导零的地址。
- Android Gradle `assembleDebug` 成功。使用原构建环境的 user.home / debug keystore；包名 cn.courseinbox.companion，Android 8+，target/compile SDK 35；INTERNET、ACCESS_NETWORK_STATE 和 CHANGE_WIFI_MULTICAST_STATE 正常权限，保留原通知监听授权。
- APK v2 签名验证通过，证书与旧版一致：`9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`。这支持覆盖升级；手机上是否实际保留配置/队列仍待验证。
- 可选依赖 zeroconf 0.151.5 和 ifaddr 0.2.0 已安装在本项目 `.venv`，未向全局 Python 安装。
- 已经由 Windows UAC 应用并核验两条 Group=CourseInbox 规则：TCP 8765、UDP 5353，只限实际服务 Python、WLAN、RemoteAddress=LocalSubnet、Public/Private；TCP 不再绑定旧单个 IP。UDP 本地地址包含私有范围及 mDNS 224.0.0.251。未修改校园 DHCP 或整机防火墙策略。
- `scripts/verify-mobile-discovery.py` 使用临时数据库、随机临时身份与假令牌实测同机 mDNS 找到服务、解析 `10.121.87.2`、验证 HMAC、确认 TXT 无令牌并观察服务关闭注销。
- 正式服务已通过 start.ps1 以 LAN 模式重启。健康接口正常，固定名 **CourseInbox-042b5fe7**，发现 available=true；真实局域网地址上的配对证明核验通过，未授权 `/api/state` 仍返回 401。
- 新 APK 的实际 HTTP 下载内容 SHA256 与本地文件一致。Web 手机连接页已在内置浏览器检查，显示固定名、当前地址、发现状态和安装入口；没有显示访问令牌。
- 正式数据库仍为 **4 个任务、6 条消息**，与实施前相同；验收未向正式收件箱写入测试事件。

## 安装包与证据

文件：`artifacts/mobile/courseinbox-companion-0.2.0-debug.apk`，51462 字节。
SHA256：`876fc5d633fd494e88009c3a351648ba87e142bf840db89fbba7e7fd75568549`。

局域网下载入口：`http://10.121.87.2:8765/downloads/courseinbox-companion-0.2.0-debug.apk`。当前 IP 仍由校园 DHCP 分配，可变化；固定名称不变，配对后通过发现恢复。

构建日志：`artifacts/android-build/autodiscovery-build.log`；签名：`autodiscovery-signature.log`。网络证据：`artifacts/network/mobile-mdns-integration.log`、`mobile-autodiscovery-live.json`、`mobile-autodiscovery-firewall.json`。这些本地证据与 APK 不随 Git 提交。

## 尚需真实手机验证

同机发现不证明校园网络允许手机发现。尚未操作真实手机的新版安装、系统 NSD、覆盖升级配置/队列保留，也尚未实测电脑 IP 改变后的恢复。因此不把“校园网以后永远不用手动改地址”标为已验证。

1. 手机继续连接校园 Wi-Fi，覆盖安装 0.2.0，保留原应用和配置。
2. 填写电脑配对名称 `CourseInbox-042b5fe7`，保留原访问令牌和学习通白名单，点击“自动寻找并配对电脑”。
3. 显示“已配对”后发送示例事件，核对待传数量归零及电脑收件箱收到。
4. 之后遇到电脑 IP 变化时打开伴侣或点击补传，核对固定身份不变、当前地址自动更新且消息送达。
5. 仍需另行核对学习通实际发布的新系统通知，样例事件不替代真实通知验收。

失败时保留队列。校园网屏蔽多播、电脑离线或令牌不匹配会显示原因；可查看电脑页面当前地址后手动更新，并继续验证原电脑身份。基础版不保证系统休眠时定时寻找。可信局域网 HTTP 保留既有明文传输边界，远程仍需 HTTPS。

## 独立复核

独立只读检查发现 0 个 Critical、3 个 Important。已修正：

- 在限时发现期间获取并释放 Wi-Fi 多播锁，补齐正常权限；参照 [Android NsdManager 官方说明](https://developer.android.com/reference/android/net/nsd/NsdManager#section-wifi-multicast-lock)。
- 单候选解析最多3秒，解析失败继续候选，现代 Android 取消请求；已配对时优先准确的固定服务名称。旧版 Android 没有公共取消解析接口，会忽略迟到结果、在系统忙时限时重试；该系统限制需真机验证，不承诺任意无响应恶意公告下都能恢复。
- 用 EndpointDraft 区分未保存编辑和自动更新；用户编辑新地址后转到其他输入框不会被旧地址覆盖。

新增 DiscoveryRegressionTest 8 项断言通过，总 JVM 断言为34项；修复后重新构建 APK，签名及真实 HTTP 下载摘要再次核验。独立检查不替代手机验收。

独立复核 e6acc77：原三个 Important 均已处理，Critical/Important/Minor 均为0；复核为只读，手机实测仍待完成。
