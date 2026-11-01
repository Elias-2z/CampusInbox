# Mobile Autodiscovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 手机继续连接校园 Wi-Fi，首次配对后自动寻找同一台电脑，减少 IP 改变后的手动配置。

**Architecture:** 电脑以 DNS-SD 公布持久化身份和当前私有 IPv4。手机先验证缓存地址，失效后使用 Android NSD 寻找服务；只有通过访问令牌生成的 HMAC 证明、且身份匹配，才允许携带令牌上传原有通知队列。

**Tech Stack:** Python 3.10+、可选 python-zeroconf、Android Java / NsdManager、现有 Python unittest 和 Gradle / JDK 17。

**Spec:** D:/Doc/course-inbox/docs/superpowers/specs/2026-10-04-mobile-autodiscovery-design.md（用户于 2026-10-04 确认）。

## Global Constraints

- 校园 DHCP 和手机 Wi-Fi 不修改；不启用电脑热点，不设置校园静态数字 IP。
- 只公布 RFC 1918 IPv4，服务类型为 `_courseinbox._tcp.local.`，地址检查间隔 2 秒。
- 身份保存在 `data/mobile-device.json`：128 位随机 device_id，名称 `CourseInbox-<short ID>`；发现记录只含版本、身份、名称和端点。
- 证明使用 HMAC-SHA256；UTF-8 消息为 `courseinbox-pair-v1`、device_id、challenge、address、十进制 port 五行，以 LF 分隔且无尾部换行。
- challenge 为 32 随机字节的 64 位小写十六进制字符串；proof 同为小写十六进制。证明请求不发送令牌，不跟随重定向。
- 新依赖放入项目环境，缺少发现依赖不能阻断手动连接。
- 通知队列上限 200；仅 HTTP 200/201 删除事件；保留原键、时间、去重和通知授权机制。
- 原包名 `cn.courseinbox.companion`、原签名保留；版本升至 versionCode 2 / versionName 0.2.0；Android 8+，compile/target SDK 35。
- 测试使用临时数据库，不向正式收件箱插入验收数据，不显示真实令牌。
- 校园网的手机发现及 IP 变化后恢复必须通过真实手机验证，不能以同机测试代替。

## Review Focus

- 旧地址被其他设备占用：证明验证失败时不发送令牌或移除事件（任务 2、3）。
- 多台电脑同时被发现：错误身份和错误令牌的候选不覆盖已配对电脑，继续尝试候选（任务 3）。
- DHCP 变化或多网卡：Host 白名单同步更新，只发布实际 RFC 1918 地址，排除 198.18.*（任务 2）。
- 网络屏蔽多播或发现服务不可用：限时结束、释放监听、保留队列和手动入口（任务 2、3）。
- 覆盖安装及配置改变：保留队列，令牌变化或换电脑要求明确重新配对，避免后台旧结果覆盖新配置（任务 4、5）。

## File Map

- `courseinbox/discovery.py`：持久身份、证明、地址快照、可选服务广播与停止。
- `courseinbox/http.py` / `server.py`：证明端点、动态 Host 校验、设置状态和生命周期。
- `requirements-mobile.txt` / `scripts/setup-mobile.ps1` / `.gitignore`：本地可选环境安装及排除 `.venv/`。
- `scripts/configure-mobile-firewall.ps1`：可预览、严格限定本项目进程与 WLAN 的防火墙规则。
- Android `PairingProof.java`：无 Android 依赖的协议校验；`ComputerDiscovery.java`：限时发现和端点认证。
- Android `NotificationQueue.java` / `MainActivity.java` / manifest / build.gradle：发送前恢复连接、配对界面、权限和版本。
- `web/app.js` / 两端 README：固定名称、当前地址、发现状态和升级步骤。
- `tests/test_discovery.py` / `tests/test_api.py` / Android 协议验收源文件 / `docs/verification/mobile-autodiscovery.md`：自动测试及分层验收记录。

### Task 1: 固定身份和可跨语言验证的证明

**Files:** Create `courseinbox/discovery.py`, `tests/test_discovery.py`; Create `android/app/src/main/java/cn/courseinbox/companion/PairingProof.java`, `android/protocol-tests/PairingProofTest.java`。

**Interfaces:** `load_identity(directory: Path) -> dict` 返回 device_id/name；`make_proof(identity: dict, token: str, challenge: str, address: str, port: int) -> dict` 返回协议字段。Java `PairingProof.verify(String token, String expectedId, String challenge, String address, int port, String deviceId, String proof) -> boolean`；expectedId 为空只允许首次配对。

- [x] 先写测试：同目录多次 load_identity 的 ID/name 不变；ID 是 32 位小写十六进制；畸形身份文件显式报错而非悄悄换身份；证明响应不含令牌。
- [x] 用固定测试令牌、ID、nonce、地址和端口，独立计算黄金摘要；Java 验证正确向量为 true，修改令牌、身份、nonce、地址、端口及摘要均为 false。
- [x] 运行 Python 单测与 Java 协议测试，记录缺失实现导致的失败。
- [x] 实现上述函数及类；原子创建身份文件，严查字段格式，比较摘要使用恒定时间比较；不引入 JSON / Android 依赖到纯 Java 校验类。
- [x] 重跑 `python -m unittest discover -s tests -p test_discovery.py -v` 和 JDK `javac` / `java` 协议测试，全部通过；仅提交本任务文件。

### Task 2: 动态地址、服务广播及受限证明端点

**Files:** Modify `courseinbox/discovery.py`, `courseinbox/http.py`, `server.py`; Test `tests/test_discovery.py`, `tests/test_api.py`。

**Interfaces:** `DiscoveryService(directory: Path, token: str, host: str, port: int)` 提供 `start()`, `close()`, `refresh_addresses()`, `allowed_hosts() -> set`, `status() -> dict`, `pairing(challenge: str, address: str) -> dict`。`create_server` 保留现有参数及返回值；服务器关闭必须关闭发现服务。地址刷新与状态在服务内负责。

- [x] 先写 API 测试：合法证明请求无需 Authorization；缺失、重复、超长、非小写十六进制 challenge 返回 400；学生数据仍需令牌，恶意 Host / Origin 仍返回 403。
- [x] 用可替换地址探测和广播适配器测试：地址从 10.1.2.3 变为 10.1.2.4 后，新 Host 获准且旧 Host 移除；排除 198.18.*、公网和 IPv6；缺少 zeroconf 时手动 API 可用；close 后线程结束且服务注销。
- [x] 运行上述单测，确认新测试失败再实现。
- [x] 加入 `GET /api/mobile/pairing?challenge=...`；proof address 必须与已验证 Host 目标 IPv4 相符，不接受客户端任意提供的 address；配对端点不写入数据库。
- [x] LAN 模式启动 `_courseinbox._tcp.local.` 广播，每 2 秒刷新地址并更新接口及记录；loopback 模式不广播。`/api/settings` 添加 `mobile_discovery`，含身份、名称、可用状态和明确原因，不含密钥。
- [x] 使用同一个地址服务快照校验 Host，保持现有 bootstrap 限制；生命周期接入 server_close，关闭注册、定时线程和套接字。
- [x] 完整运行 Python 测试通过；仅提交本任务改动。

### Task 3: Android 限时寻找与发送前认证

**Files:** Create `android/app/src/main/java/cn/courseinbox/companion/ComputerDiscovery.java`; Modify `NotificationQueue.java`; Test 协议测试及独立解析流程验收。

**Interfaces:** `ComputerDiscovery.resolve(Context context, boolean allowPairing) -> String`，在后台执行，返回已认证端点；失败抛出带明确中文原因的异常。`resolve` 使用 SharedPreferences 的 token、server、paired_device_id、paired_name；只有显式配对 allowPairing=true 可首次保存身份。读取配置后，在保存结果前再次确认配置未改变。

- [x] 先写解析流程测试：缓存地址证明通过直接返回；缓存失效后找到同一 ID 恢复；其他 ID / 无效 HMAC 不选中；第一个候选失败继续尝试后续候选；取消或超时后关闭监听且不修改队列。
- [x] 先跑失败测试；采用小型可替换发现/验证适配器测试流程，不在 JVM 单测伪造 Android 整个平台。
- [x] 实现 NsdManager 浏览 `_courseinbox._tcp.` 和候选解析；只尝试 RFC 1918 IPv4，发现总预算 15 秒，单候选连接/读取各不超过 3 秒，避免无界候选导致总超时失效；所有完成路径停止发现、释放等待者。
- [x] 每次证明请求生成 SecureRandom nonce，禁用重定向，限制响应体，调用任务 1 校验后才保存地址；首次配对和已配对恢复共用流程，后台线程不阻塞主界面。
- [x] 队列已配对时在发送批次前 resolve(false)；仍未配对时保留原手动连接流程。认证或发现失败保留事件并显示具体原因；保留原 200/201 才出队和并发追加处理。
- [x] 运行协议和解析测试通过，构建编译检查；仅提交本任务改动。

### Task 4: 配对界面与安装升级兼容

**Files:** Modify `MainActivity.java`, `AndroidManifest.xml`, `android/app/build.gradle`, `web/app.js`, `README.md`, `android/README.md`。

**Interfaces:** 手机显式“自动寻找并配对电脑”调用 resolve(true)，成功后显示身份名和当前地址；“重新配对”需确认，清除配对身份但不清空事件；网页从 settings.mobile_discovery 显示名称与发现状态。

- [x] 检查并记录升级约束：旧 server/token/scope/packages/queue 偏好键保留；新字段独立；修改令牌后提示重新配对，不自动换电脑；已修改地址需认证后保存为已配对端点。
- [x] 实现按钮后台任务、配对状态、重新配对确认和实际地址同步，保留原配置、授权、样例、补传、清空入口；旧后台结果不能覆盖用户的新配置。
- [x] 新增必要 `ACCESS_NETWORK_STATE` 权限；不新增学习通账号权限、位置采集或热点控制。更新版本 0.2.0 / 2，保持包名、minSDK 和原签名环境。
- [x] 更新电脑手机连接页和两端说明：首次配对、电脑启动窗口保持打开、发现受校园网络影响、手动恢复入口；不将实现状态写成手机实测成功。
- [x] `node --check web/app.js` 通过，Gradle 编译成功；核对升级前后偏好键和所有事件出队路径；仅提交本任务明确改动，保留已有未提交文档内容。

### Task 5: 项目依赖、DHCP 兼容防火墙和 APK 交付

**Files:** Create `requirements-mobile.txt`, `scripts/setup-mobile.ps1`, `scripts/configure-mobile-firewall.ps1`; Modify `.gitignore`; Output `artifacts/mobile/courseinbox-companion-0.2.0-debug.apk`。

**Interfaces:** setup-mobile 在 `.venv` 创建可选环境并安装测试验证过的 python-zeroconf 版本；原 scripts/find-python.ps1 已优先选择 .venv。防火墙脚本默认预览，显式 Apply 才执行，支持用户允许的 Windows UAC 管理员操作。

- [x] 写安装脚本，添加 `.venv/` 忽略；先核对当前官方库接口兼容性并固定成功验证的版本，安装仅写项目环境，不更改全局 Python。
- [x] 防火墙预览：只操作 Group=CourseInbox 的既有 TCP 8765 规则和新 UDP 5353 规则；使用实际监听进程的程序路径、WLAN 接口、LocalSubnet 远端、Public/Private，TCP 本地地址仅 RFC 1918 范围，UDP 兼顾 mDNS 多播目的地址但不开放其他端口。
- [x] 对既有规则先检查所有者组、端口与用途；不匹配时停止修改。保存变更前后结果，仅修改上述规则，不改变整机防火墙策略。
- [x] 核验项目环境真实运行程序路径，再应用限定规则；停止原 CourseInbox 实例后以 LAN 模式启动，确认当前局域网健康检查及未授权请求保护正常。
- [x] 使用 artifacts/android-build/build-environment.json 中 JDK/SDK/Gradle/cache/profile，设置同一 user.home 保留原 debug keystore；运行 assembleDebug，把结果复制到交付路径。
- [x] aapt / apksigner 验证包名、版本、权限和证书 SHA256 与原 APK 相同；记录新 APK SHA256。构建失败时保留原 APK 和现有安装，不交付半成品。

### Task 6: 综合验收与真实手机交接

**Files:** Create `docs/verification/mobile-autodiscovery.md`; Modify 两端 README 中实际验收状态。

**Interfaces:** 输出 APK 下载路径、首次配对步骤及实测边界；未完成的手机验收明确标为待用户操作。

- [x] 执行全部 Python 测试、Java 协议和解析测试、网页语法检查、git diff --check，记录真实结果。
- [x] 用临时数据目录独立启动 LAN 实例；同机浏览发现记录并验证挑战证明、关闭注销；不得将该结果记为手机已成功发现。
- [x] 检查正式实例健康、发现设置、数据库任务/消息数量，避免测试写入正式数据；只记录数量，不输出消息内容或令牌。
- [ ] 打开手机连接页并展示 APK 文件及简明升级操作；覆盖安装一次，保留原令牌配置后配对，发送样例确认队列清零、电脑收到。
- [ ] 用户在手机上配对、发送及电脑 IP 变化后恢复三项实测分别记录；用户暂未反馈时交付软件并明确校园网自动恢复仍待验证，不无期限等待、不自行改变 DHCP 模拟实网。
- [x] 自行复核完整变更，重点检查 proof 验证先于 Bearer 上传、错误身份拒绝、线程释放、配置竞态、相同 APK 签名；无需创造新任务或 PR。

## 执行方式与计划核对

建议由当前会话直接实施（Native）：六项任务存在共享协议和配置接口，直接顺序完成最便于保持一致。实施中已发现独立审查工具，并完成一轮只读检查和修复；测试边界另记于验收文档。

计划已核对设计的身份、协议、发现、Host 更新、队列、权限、防火墙、交付和手机验收要求。用户已确认并授权直接实施；任务 1–5 已实施并验证，任务 6 的软件验证已完成，真实手机操作待用户进行。
实施进度与工程裁定保存在 .superpowers/sdd/2026-10-04-mobile-autodiscovery/progress.md；独立检查工具已发现并安排复核。


独立检查修正：发现期间短时获取 Wi-Fi 多播锁并在所有退出路径释放（CHANGE_WIFI_MULTICAST_STATE 是正常权限）；单候选解析最多3秒，总发现预算15秒；手动地址的未保存编辑不会被后台刷新覆盖。Android 13 T Extensions 7 / Android 14+ 可取消解析；更旧系统没有公共取消接口，会忽略迟到结果并在系统忙时限时重试其他候选，具体系统行为仍需真机确认。
