# 学习通二维码登录桌面软件验证记录

验证日期：2026-10-10（Asia/Shanghai）

## 交付物

- 普通用户入口：`start-desktop.cmd`
- 原生 Windows 程序：`artifacts/desktop/CourseInbox-Learning-Assistant.exe`
- EXE 大小：40,960 bytes
- SHA-256：`83B08299BB8E44E0FF07507FD2053458468ABBA37D81363CC0361783A057A298`
- PE：x64 (`0x8664`)，Windows GUI subsystem (`2`)
- 原生窗口截图：`artifacts/desktop/courseinbox-learning-assistant.png`

桌面程序使用 .NET Framework WinForms。它启动或安全复用本机 CourseInbox 服务，在原生窗口显示学习通官方二维码、连接状态、来源状态和分类计数。桌面外壳不提供账号密码输入框，不解析学习通网页，不接触 Cookie，也不直接写 SQLite。

## 自动化验证

### 桌面 C# 测试

命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1
```

结果：33/33 PASS。覆盖状态文案、来源独立状态、计数缺省、回环限制、主令牌、JSON 控制动作、PNG/2 MiB 二维码边界、旧二维码清除、服务启动与复用、外部端口冲突、进程所有权、启动超时、非阻塞刷新、窗口显示后的首次刷新、暂时错误、登录/暂停/恢复/退出、单实例、托盘退出、系统关机和窗口无密码输入框。

### 针对性 Python 回归

命令：

```powershell
.venv\Scripts\python.exe -B -m unittest tests.test_desktop_app tests.test_desktop_sync tests.test_server -v
```

结果：24/24 PASS。正式 EXE 能由双击入口重新构建，PE subsystem 为 Windows GUI；现有扫码、会话、接口授权和服务启动测试继续通过。

### 完整项目回归

命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1 -Browser
```

结果：退出码 0。

- Python：115 tests，全部通过。
- 桌面 C#：33 tests，全部通过。
- Node/Edge：聊天解析、作业/考试结构化读取、通知饥饿保护、主浏览器 27 个 journey、课程通知 12 个 journey、桌面后台 16 个 journey 全部通过。
- 桌面后台隔离流程使用模拟官方 DOM 和模拟扫码确认，不是真实账号；日志明确记录 `isolated sanitized fixtures; simulated QR confirmation; no real account`。
- 主浏览器旧草稿恢复流程输出一次预期的 HTTP 409，最终结果 `passed=true` 且无控制台错误。

## 原生窗口冒烟

在正式 EXE 上完成以下只读冒烟，不点击“退出账号”，不删除正式数据：

- 窗口标题：`CourseInbox 学习通助手`。
- 本机 `/api/health`：`ok`。
- 原生窗口能读到当前状态 `部分消息待重试`。
- 分类计数：已保存 21 条、聊天 15、课程通知 6、作业/考试 0。
- 可访问性树包含“扫码登录并持续监测消息”“打开消息与任务”“打开官方登录窗口”。
- 当前已保存会话处于监测/局部重试状态时，“打开官方登录窗口”不会显示，避免误触重新登录。
- 发送等同于用户点击标题栏 X 的 `SC_CLOSE` 后，应用进程仍存活、主窗口隐藏且 `/api/health=ok`，符合最小化到系统托盘设计；进程管理器/系统关闭则允许正常退出。
- 冒烟结束后精确清理测试启动的桌面程序与其自有服务；最终桌面进程 0、8765 监听器 0。

## 尚需真实账号人工验收

以下项目没有用自动化或模拟结果冒充完成：

1. 在当前版本原生窗口中对一个已退出状态的真实学习通账号重新扫描官方二维码并在手机确认。
2. 真实账号收到一条新的聊天消息或课程通知后，桌面计数增加且本地收件箱保存原文。
3. 重启桌面软件后复用本机加密会话。
4. 主动使学习通会话失效后，软件明确提示重新扫码。

现有正式数据证明桌面窗口可以读取已保存会话的同步状态，但不能替代以上真实扫码和新消息到达验收。
