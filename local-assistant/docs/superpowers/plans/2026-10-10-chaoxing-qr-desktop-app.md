# CourseInbox 学习通二维码登录桌面软件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个可双击运行的 Windows 桌面程序，让用户在原生窗口内扫描学习通官方二维码，登录后持续监测聊天和课程通知。

**Architecture:** 使用 .NET Framework WinForms 制作轻量桌面外壳，通过现有回环 HTTP API 控制 CourseInbox 服务和 `DesktopSync`，不复制学习通网页解析、Cookie 或数据库逻辑。桌面程序只负责服务启动/复用、状态呈现、二维码显示、用户操作和托盘生命周期；现有 Python/Node worker 继续承担登录及同步。

**Tech Stack:** C# WinForms、.NET Framework 系统编译器、Python 标准库 HTTP/SQLite、Node.js + Playwright + Microsoft Edge、PowerShell 构建与测试脚本。

**Spec:** `docs/superpowers/specs/2026-10-10-chaoxing-qr-desktop-app-design.md`

## Global Constraints

- 首期仅支持 Windows 10/11 x64，程序不要求管理员权限。
- 登录仅使用学习通官方二维码或官方可见登录窗口；不得增加账号密码输入框。
- Cookie 只在既有 worker 与 Python 进程管道中传递，不进入桌面 HTTP 状态、日志或明文文件。
- 桌面外壳不直接写 SQLite，不解析学习通 DOM，不复制现有同步业务逻辑。
- `start.cmd`、`start-lan.cmd` 和现有 Web 界面行为保持兼容。
- 退出账号清除本机加密登录会话，但保留消息、任务、草稿和去重记录。
- 不将模拟 DOM 或自动化测试描述成真实学习通账号验收。
- 当前共享目录已有用户改动；只修改本计划列出的文件，不执行重置或清理，不创建提交。

## File Structure

- `desktop-app/Contracts.cs`：状态 DTO、API 接口、服务宿主接口和桌面视图接口。
- `desktop-app/DesktopApi.cs`：回环 HTTP、令牌获取、JSON 状态/计数、二维码和控制动作。
- `desktop-app/Presentation.cs`：纯状态到中文界面模型的映射，不依赖 WinForms。
- `desktop-app/ServiceHost.cs`：检测、启动、复用和停止本程序拥有的 Python 服务进程。
- `desktop-app/AppController.cs`：轮询、登录/暂停/恢复/退出动作以及错误归一化。
- `desktop-app/MainForm.cs`：WinForms 布局、二维码图片、按钮、计时器与系统托盘。
- `desktop-app/Program.cs`：参数、单实例、依赖装配和主入口。
- `desktop-app/AssemblyInfo.cs`：产品名称、版本和 WinForms 程序元数据。
- `desktop-app/tests/DesktopAppTests.cs`：不依赖真实账号的 C# 单元/契约测试入口。
- `desktop-app/build.ps1`：调用系统 C# 编译器生成桌面 EXE 和测试 EXE。
- `desktop-app/run-tests.ps1`：构建并运行 C# 测试。
- `start-desktop.cmd`：普通用户双击入口；缺少或过期时构建，再启动 EXE。
- `tests/test_desktop_app.py`：Python 侧静态/构建契约以及现有 API 兼容性测试。
- `README.md`、`desktop-sync/README.md`：面向普通用户和开发者的桌面软件说明。
- `docs/verification/chaoxing-qr-desktop-app.md`：自动化结果与真实账号验收边界。

## Review Focus

- 8765 端口被非 CourseInbox 程序占用时，必须报冲突且绝不结束该进程；Task 2 的 foreign-service 测试固定此行为。
- 同一用户重复启动桌面软件时，第二个实例不得启动第二套服务；Task 3 的 single-instance 测试固定此行为。
- 状态轮询响应慢、暂时失败或返回未知状态时，UI 不得冻结或误报已退出；Task 3 的 refresh/error 测试固定此行为。
- 二维码响应被替换、过期或不是 PNG 时，旧二维码必须清除且给出可重试提示；Task 1/3 的 QR validation 测试固定此行为。
- 桌面软件复用用户已经启动的服务后退出时，不得停止该服务；Task 2 的 reused-service ownership 测试固定此行为。

---

### Task 1: 本机 API 客户端与状态呈现

**Files:**
- Create: `desktop-app/Contracts.cs`
- Create: `desktop-app/DesktopApi.cs`
- Create: `desktop-app/Presentation.cs`
- Create: `desktop-app/tests/DesktopAppTests.cs`
- Create: `desktop-app/build.ps1`
- Create: `desktop-app/run-tests.ps1`

**Interfaces:**
- Consumes: 现有 `/api/bootstrap`、`/api/chaoxing/desktop/*`、`/api/settings` JSON/PNG 接口。
- Produces: `IDesktopApi`；`DesktopStatus`；`DesktopCounts`；`DesktopViewState`; `DesktopPresenter.From(DesktopStatus, DesktopCounts) -> DesktopViewState`。

- [ ] **Step 1: 写状态映射和 API 契约的失败测试**

  在 `DesktopAppTests.cs` 添加：`MapsIdleToLogin`、`MapsWaitingScanToQr`、`MapsSyncingToMonitoring`、`MapsExpiredToRelogin`、`MapsUnknownToActionableError`、`PreservesIndependentSourceStates`、`ParsesCountsWithoutMissingFieldFailures`。断言中文标题、可见按钮、是否请求二维码以及缺失计数默认为零。

- [ ] **Step 2: 运行 C# 测试并确认因类型尚不存在而失败**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Expected: 编译失败，指出 `DesktopStatus`、`DesktopViewState` 或 `DesktopPresenter` 尚不存在。

- [ ] **Step 3: 实现状态 DTO 与纯呈现映射**

  在 `Contracts.cs` 定义 DTO/接口，在 `Presentation.cs` 实现 `DesktopPresenter.From`。固定状态文案为：尚未登录、正在连接、等待扫码、正在监测、登录已失效、同步已暂停、网络波动正在重试、部分消息待重试、连接需要处理。

- [ ] **Step 4: 实现回环 API 客户端**

  `DesktopApi(Uri baseUri)` 先从 `/api/bootstrap` 取得主令牌，后续请求使用 `Authorization: Bearer <token>`；提供 `GetStatus()`、`GetCounts()`、`GetQrPng()`、`Login(bool visible)`、`Resume()`、`Pause()`、`Logout()`。`GetQrPng()` 只接受以 PNG 签名开头且不超过 2 MiB 的响应。

- [ ] **Step 5: 添加传输替身测试并覆盖二维码校验**

  给 `DesktopApi` 注入 `IHttpTransport`，用内存替身断言：令牌只从回环 bootstrap 获取、每个受保护请求带 Bearer 令牌、POST body 为 JSON、非 PNG/空/超限二维码抛出可理解异常且不返回旧数据。

- [ ] **Step 6: 运行桌面客户端单元测试**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Expected: 所有 Task 1 C# tests PASS。

### Task 2: 服务启动、复用与所有权

**Files:**
- Create: `desktop-app/ServiceHost.cs`
- Modify: `desktop-app/Contracts.cs`
- Modify: `desktop-app/tests/DesktopAppTests.cs`
- Modify: `desktop-app/build.ps1`

**Interfaces:**
- Consumes: 项目根目录、端口、`server.py --host 127.0.0.1 --port <port>` 和 `/api/health`。
- Produces: `IServiceHost.EnsureRunning() -> ServiceStartResult`、`IServiceHost.StopOwned()`、`ServiceStartResult.Owned`、`ServiceStartResult.BaseUri`。

- [ ] **Step 1: 写服务生命周期失败测试**

  添加 `ReusesHealthyCourseInbox`、`StartsServiceWhenPortIsFree`、`RejectsForeignServiceOnPort`、`DoesNotStopReusedService`、`StopsOnlyOwnedProcessTree`、`ReportsMissingPython`、`ReportsStartupTimeout`。进程和健康检查通过 `IProcessLauncher`、`IHealthProbe` 替身测试，不启动真实服务。

- [ ] **Step 2: 运行测试并确认生命周期类型缺失**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Expected: 编译失败，指出 `ServiceHost` 或所有权接口尚不存在。

- [ ] **Step 3: 实现健康识别与 Python 解析**

  `ServiceHost` 仅在 `/api/health` 同时返回 `status=ok`、`app=CourseInbox` 时复用端口。Python 搜索顺序为项目 `.venv\Scripts\python.exe`、PATH 中 `py.exe -3`、PATH 中 `python.exe`；缺失时返回面向用户的错误，不修改系统 PATH。

- [ ] **Step 4: 实现受控服务进程**

  以隐藏、非管理员进程启动 `server.py`，最多等待 15 秒；记录 `Owned=true` 和 PID。`StopOwned()` 只处理该实例保存的 PID/进程树，复用服务时为空操作。

- [ ] **Step 5: 运行生命周期测试**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Expected: Task 1-2 C# tests PASS，foreign/reused 场景均未调用 kill 替身。

### Task 3: 原生窗口、控制器与系统托盘

**Files:**
- Create: `desktop-app/AppController.cs`
- Create: `desktop-app/MainForm.cs`
- Create: `desktop-app/Program.cs`
- Create: `desktop-app/AssemblyInfo.cs`
- Modify: `desktop-app/Contracts.cs`
- Modify: `desktop-app/tests/DesktopAppTests.cs`
- Modify: `desktop-app/build.ps1`

**Interfaces:**
- Consumes: Task 1 `IDesktopApi`/`DesktopPresenter` 和 Task 2 `IServiceHost`。
- Produces: `AppController.Refresh()` 与用户动作方法；`MainForm` 原生 UI；`Program.Main(string[] args)`；命名互斥量 `Local\CourseInboxLearningAssistant`。

- [ ] **Step 1: 写控制器失败测试**

  添加 `RefreshDoesNotBlockViewThread`、`TransientRefreshErrorKeepsSessionState`、`LoginRequestsFreshQr`、`VisibleLoginUsesOfficialWindow`、`PauseResumeLogoutCallExpectedEndpoint`、`LogoutClearsQr`、`UnknownStateShowsRetry`。使用 fake API/view，禁止测试访问真实学习通。

- [ ] **Step 2: 写单实例和窗口关闭行为失败测试**

  添加 `SecondInstanceIsRejected`、`WindowCloseMinimizesToTray`、`TrayExitStopsOwnedService`、`TrayExitLeavesReusedServiceRunning`。将 mutex 和 tray/exit 决策放在可替换接口后测试。

- [ ] **Step 3: 运行测试并确认控制器/窗口接口缺失**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Expected: 编译失败，指出 `AppController`、`IAppView` 或单实例接口尚不存在。

- [ ] **Step 4: 实现非阻塞控制器**

  `AppController` 使用后台 `Task` 串行化刷新，避免重叠轮询；成功刷新同时读取 status/counts，只有 `waiting_scan` 才下载二维码。暂时错误保留上次登录/会话呈现并增加错误提示，明确 `expired` 才显示重新扫码。

- [ ] **Step 5: 实现 WinForms 主窗口**

  建立登录状态区、300×300 二维码区、来源计数、最近同步时间、错误提示和按钮栏。控件提供稳定 `Name`/`AccessibleName`；不显示密码输入；“打开消息与任务”打开本地 `/#inbox`；额外验证调用 `Login(true)`。

- [ ] **Step 6: 实现托盘与单实例**

  X 按钮隐藏窗口并显示一次托盘提示；托盘双击/菜单恢复窗口；只有“退出软件”执行 controller disposal 和 `StopOwned()`。第二实例显示“软件已在运行”并退出，不启动服务。

- [ ] **Step 7: 运行控制器和生命周期测试**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Expected: Task 1-3 全部 C# tests PASS。

### Task 4: 构建产物、启动入口和项目契约

**Files:**
- Modify: `desktop-app/build.ps1`
- Create: `start-desktop.cmd`
- Create: `tests/test_desktop_app.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Task 1-3 C# 文件和 Windows Framework64/Framework `csc.exe`。
- Produces: `artifacts/desktop/CourseInbox-Learning-Assistant.exe`；双击入口 `start-desktop.cmd`。

- [ ] **Step 1: 写 Python 构建/静态契约失败测试**

  `tests/test_desktop_app.py` 断言：所有源文件存在；启动入口不打开 `#sync`；C# 源码不包含账号/密码字段；构建输出路径固定；EXE 缺失或源文件较新时启动入口调用构建脚本。

- [ ] **Step 2: 运行测试并确认启动入口/产物缺失**

  Run: `.venv\Scripts\python.exe -B -m unittest tests.test_desktop_app -v`

  Expected: FAIL，指出 `start-desktop.cmd` 或 EXE 尚不存在。

- [ ] **Step 3: 完成 WinExe 构建脚本**

  `build.ps1` 检查 `csc.exe` 与项目源文件，引用 `System.dll`、`System.Core.dll`、`System.Drawing.dll`、`System.Windows.Forms.dll`、`System.Web.Extensions.dll`，使用 `/target:winexe` 输出正式 EXE；测试目标用 `/target:exe` 和指定测试入口生成后立即运行。

- [ ] **Step 4: 实现普通用户启动入口**

  `start-desktop.cmd` 只负责定位项目目录：EXE 不存在或任一 `desktop-app/*.cs`/构建脚本更新时先构建，成功后启动 EXE；构建失败时保留窗口并显示说明。它不得打开普通浏览器监测页。

- [ ] **Step 5: 构建并运行桌面测试**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\build.ps1 -RunTests`

  Expected: 生成 `artifacts\desktop\CourseInbox-Learning-Assistant.exe`，C# tests PASS。

- [ ] **Step 6: 运行 Python 桌面契约测试**

  Run: `.venv\Scripts\python.exe -B -m unittest tests.test_desktop_app -v`

  Expected: PASS。

### Task 5: 文档、隔离验收与完整回归

**Files:**
- Modify: `README.md`
- Modify: `desktop-sync/README.md`
- Create: `docs/verification/chaoxing-qr-desktop-app.md`
- Modify: `scripts/run-tests.ps1`

**Interfaces:**
- Consumes: 正式 EXE、现有隔离同步 fixture 和项目完整测试入口。
- Produces: 普通用户说明、开发构建说明、可复现验证记录和明确的真实账号验收清单。

- [ ] **Step 1: 把桌面单元测试接入完整测试入口**

  在 `scripts/run-tests.ps1` 的 Windows 路径加入 `desktop-app\run-tests.ps1`，任一编译或测试失败时返回非零退出码。

- [ ] **Step 2: 更新用户与开发文档**

  README 首选入口改为 `start-desktop.cmd`/正式 EXE，说明二维码登录、托盘、暂停/恢复、退出账号和依赖；保留 `start.cmd` 为开发/完整 Web 入口。`desktop-sync/README.md` 增加桌面外壳与 worker 职责边界。

- [ ] **Step 3: 执行针对性测试**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1`

  Run: `.venv\Scripts\python.exe -B -m unittest tests.test_desktop_app tests.test_desktop_sync tests.test_server -v`

  Expected: 全部 PASS。

- [ ] **Step 4: 执行完整回归**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1 -Browser`

  Expected: Python、Node、桌面 C# 和浏览器检查全部返回 0；不因桌面外壳改变现有 Web/同步行为。

- [ ] **Step 5: 执行本机软件冒烟验收**

  启动正式 EXE，确认原生窗口标题、未出现浏览器地址栏、状态/计数可读、关闭进入托盘、托盘重新打开、明确退出后进程行为符合所有权规则。不得在正式数据上点击“退出账号”。

- [ ] **Step 6: 写验证记录**

  在 `docs/verification/chaoxing-qr-desktop-app.md` 记录精确命令、测试数量、EXE 路径/哈希、冒烟结果；把真实扫码、新消息到达、会话失效后的重新扫码列为需要用户完成的人工验收，不声称已完成。
