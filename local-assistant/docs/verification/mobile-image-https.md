# 官方课程封面 HTTPS 兼容修复 · 0.3.5

日期：2026-10-11。用户确认修复学习通页面图片加载问题，不放开不安全图片加载或证书校验，不清除登录和本机存档。

## 原因证据与实现范围

独立、只读官网检查观察到 9 张课程封面的 DOM `src` 是 HTTP；电脑 Edge 将实际请求自动升级为 HTTPS，同一图片 HTTPS 请求返回 200/image-jpg。App 的 WebView 保持 `MIXED_CONTENT_NEVER_ALLOW`。这是已验证的协议兼容隐患，但没有手机请求日志，不能断言它是用户手机所有图片失败的唯一原因。

- 新增 `mobile-images.js`，只在官方 HTTPS 顶层页面、默认 HTTPS 端口且无页面凭证时工作。
- 只将精确主机 `p.ananas.chaoxing.com`、默认 HTTP 端口的 `<img src>` 升级到 HTTPS，保留路径、查询和片段。不改未验证主机、非默认端口、userinfo、HTTPS/data/blob/相对地址。
- 保留原图片节点、alt 和事件；观察后续插入及 src 更新，重复注入复用同一适配器，已升级地址不重复写入。不会改视频、iframe 或其他资源的 src。
- 主框架完成加载且通过原 view/当前 URL/官方 HTTPS 检查后注入，与可选手机布局独立。刷新会在新文档重新注入。没有新增 native bridge、图片代理、凭证读取或表单提交。
- HTTPS 请求失败不降级 HTTP，也不伪造图片加载成功。保留 `MIXED_CONTENT_NEVER_ALLOW`、SSL 错误取消和原包签名；不改 Cookie、本机数据库、任务或消息采集范围。

## 已验证

- 先观察到旧版 HTTP `src` 不变的断言失败，再实现升级并通过；下载入口先观察到 404，再扩充精确 APK 白名单至 200，旧版下载仍保留。
- 复核中补充非图片 src、空 userinfo、前导控制字符等边界，各自先复现失败再修正。原生注入只覆盖主框架，因此新增跨域 iframe fixture：父图改 HTTPS，子图保留原 HTTP，明确覆盖边界而非冒称支持子文档。
- `scripts/test-mobile-images.cjs` 在 Edge 320/390/480px 视口验证图片实际加载、原节点/表单/点击事件/原通知文本保留、动态插入及 src 变化、路径与参数中的 @ 保留、未验证 URL 不动、重复注入无属性回环、HTTPS 404 后无降级。
- 最终脚本真实官网只读回归：390px、与 App 相同 UA、独立浏览器上下文，9 HTTP 封面变为 9 HTTPS，9 张均 `complete && naturalWidth > 0` 且有正尺寸。没有输出个人资料、图片完整地址、凭证或消息正文，也没有保存会话或执行官网提交操作。这是电脑浏览器结果，不是 Android 真机结果。
- 最终离线 APK 构建成功，v2 签名通过，证书保持 `9320aa55a9957826a0bd4df0263f72e03730d35f9bcaf9ad794d562406979177`。最终 8 个 Android JVM 测试类、120 个断言通过。
- 安装包内 mobile-images.js、mobile-page-ui.js、mobile-reader.js、mobile-login-ui.js、parser.js、notices.js 与最终源码一致。
- 所有修正后的最终 `scripts/run-tests.ps1 -Browser` 返回 0：115 项 Python/API、33 项桌面测试及全部 Node/Edge 回归通过；网页 27、通知 12、桌面隔离模拟 16 个流程 passed，无 consoleErrors。HTTP 409 是预期的过期草稿冲突检查。此前用于复现边界的失败均已被最终通过的回归覆盖。

## 产物与未验证边界

[courseinbox-learning-0.3.5-debug.apk](../../artifacts/mobile/courseinbox-learning-0.3.5-debug.apk)，包名 `cn.courseinbox.companion`，versionCode 13 / versionName 0.3.5，minSdk 26 / targetSdk 35，89462 字节。

SHA-256：`88642AB73BFA632E1076008773A8EEDABCA4752B995151103137264F290315BB`。

修复只覆盖顶层文档 `<img src>`。官方原版门户的课程页位于跨域 iframe 内，外层脚本不能修改其图片；手机布局中需点击“展开课程页”，进入顶层课程页。CSS 背景、srcset/picture/source、跨域子文档、其他未验证图片主机不在本轮修复范围。

Android WebView 真机、校园网、VPN/DNS 可达性、HTTPS 服务临时异常和手机旧缓存没有实测。更新后需在实际手机进入课程页检查图片；若仍失败，应按手机当前页面和网络继续诊断，不能放开 SSL 来掩盖问题。

请直接覆盖安装，不要卸载旧版或清除数据。缺少 SDK platform-tools 的构建警告不影响本次 APK 生成，但本机没有通过 adb 安装验收。保留旧版本 APK；本轮没有重启正式电脑服务或修改账号配置。只读检查当前 8765 服务的新版 APK 下载仍返回 404，旧进程需重启后才能加载新版下载白名单；本地安装包文件已经生成并完成签名及内容验证。
