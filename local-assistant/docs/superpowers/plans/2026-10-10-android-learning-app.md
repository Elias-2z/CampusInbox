# Android 学习通助手实施记录

规格：`docs/superpowers/specs/2026-10-10-android-learning-app-design.md`。

本轮用户的手机端交付修正沿用此前“继续实现”的授权，直接实施，不重复要求设计或计划批准。

- [x] 规则与本机存储：验证日期、重复消息、草稿确认、重启保存和通知重试；原文存档原子替换，写失败保留原存档。新版手机行为 50 个断言通过。
- [x] 官方登录与网页读取：HTTPS 官方域名限制；evaluateJavascript 读取 JSON，不暴露原生 Java bridge；隔离 DOM 测试包含延迟切换群名、空通知页与非官方来源。
- [x] 原生手机 UI：首页、消息、任务、日历、账号与登录；旧电脑配对保留；消息分页、滚动位置保留、失败重试提示及逾期显示已实现。原生页面运行仍待真机确认。
- [x] 构建、验证、交付：0.3.0 / versionCode 8，APK 输出到 artifacts/mobile；106 个 JVM 断言、115 项 Python 测试、桌面与浏览器回归通过。v2 签名与 0.2.5 一致；下载路由与文档更新。

本轮查出默认调试签名漂移，已显式使用原 profile/.android/debug.keystore，构建脚本导出前验证旧版证书指纹。未触碰正式学习通登录会话；没有真机/模拟器，实际扫码、权限、后台到达和覆盖安装仍未验收。详情见 docs/verification/android-learning-app.md。

实现约定：使用已有 Android 8+/SDK35/Java8 工程；不更改正式桌面会话，不提交或推送；独立运行假设可由用户随时修正。
