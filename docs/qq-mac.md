# Mac QQ / OneBot 连接适配器

2026-11-02。本阶段实现 CampusInbox 与本机 OneBot HTTP 消息桥的连接，不包含自动安装、修改 QQ 或代替用户扫码。单靠 CampusInbox 或 OneBot 协议本身不能登录 QQ。

## 已完成

- 本机 HTTP API：验证 `get_status` 的 online / good、`get_login_info` 的账号，然后读取群列表及成员列表。
- 仅允许带端口的字面回环 IP（`127.0.0.1` / `::1`），拒绝远端地址、URL 凭据、查询参数、路径及重定向；不继承系统 HTTP 代理。
- 在 macOS 钥匙串保存 OneBot Access Token 与独立事件签名密钥。普通 JSON 配置只保存地址和启用状态；API 和验证错误不回显密钥。使用系统 `security` 命令访问钥匙串，不修改 QQ 的凭据或配置。
- HTTP POST 群消息事件签名校验、256 KB 上限、本机来源校验、拒绝浏览器 Origin。只有事件持久保存或按范围过滤后才返回 204；写入失败返回 503。
- 文本段与 CQ 字符串标准化；保留真实发送时间和消息身份。不访问图片或文件 URL；混合媒体消息在审核建议中提示缺口，纯媒体本阶段跳过。
- 复用主应用 QQ 核心的账号／群／成员筛选、去重、持久队列与后台识别，最终进入现有收件箱。
- 后端正常退出保留桥接启用配置。用户主动断开则暂停检测并停用本适配器，不登出或结束外部 QQ 进程。

这套 HTTP 协议层也供 Windows 复用；2026-11-04 已增加 Windows Credential Manager，macOS 继续使用原钥匙串。Windows 操作与验收边界见 [Windows QQ](qq-windows.md)。适配器不读取另一套 `local-assistant/` 的数据库。

## Mac 前置条件

NapCat 项目提供 [macOS 安装器](https://github.com/NapNeko/NapCat-Mac-Installer)，其 README 声明支持 macOS 12+ 的 Intel / Apple Silicon。该安装器会备份并修改 `/Applications/QQ.app` 的启动入口；文档中的 NapCat 启动命令包含 `--no-sandbox`。这涉及现有 QQ 程序与沙箱保护；本机已在用户明确授权并授予 App 管理权限后执行，不能对其他用户默认执行。

安装器还说明同时运行原版 QQ 与 NapCat 时两者共享部分 QQ 数据，并要求使用不同账号。请在决定采用该方案前核对当前版本说明，先用测试账号／测试群联调。不要把第三方消息桥描述为腾讯官方接口或承诺账号永久在线。

本机已安装 NapCat 4.18.42，下载包 SHA-256 与 GitHub 发布资产 digest 一致；备份 QQ 基础包及当前热更新包的启动配置后切换入口，完成本人手机扫码和真实账号联调。Docker 路径未安装、未验收。

## 消息桥中的配置

1. 用户自行完成 NapCat 安装及 QQ 登录／验证，启用 OneBot **HTTP 服务端**，仅绑定本机，例如 `127.0.0.1:3000`。
2. 设置至少 24 位随机 ASCII Access Token。它与 NapCat WebUI 登录密码是不同用途的凭据。
3. 启用 OneBot **HTTP 事件上报**，指向 CampusInbox 设置页显示的事件地址。正常默认后端为：

   ```text
   http://127.0.0.1:8000/api/connectors/qq/onebot/events
   ```

4. 为事件上报设置独立、至少 24 位随机 ASCII Secret，桥接器必须按 OneBot v11 对原始请求体计算 `X-Signature: sha1=<HMAC-SHA1>`。仅能提供其他鉴权方式的桥接器需要先适配，不接受关闭鉴权来兼容。
5. 配置消息桥对非 2xx 投递进行重试。CampusInbox 不会替桥接器补抓历史，桥接器没有保留／重发的离线消息仍可能缺失。

参考：[OneBot HTTP API](https://github.com/botuniverse/onebot-11/blob/master/communication/http.md)、[事件上报与签名](https://github.com/botuniverse/onebot-11/blob/master/communication/http-post.md)、[群消息事件](https://github.com/botuniverse/onebot-11/blob/master/event/message.md)。不同 NapCat 版本的界面标签可能不同，应核对实际提供的协议能力。

## CampusInbox 中的配置

1. 打开设置 → 数据源连接 → QQ → **Mac 消息桥连接**。
2. 填入本机 HTTP 地址、Access Token、事件签名 Secret，勾选启用，保存。已有密钥留空保持原值；清空输入框不删除钥匙串记录。
3. 点击“检查已保存的连接”，确认显示真实 QQ 账号。此检查不等于群事件已经投递成功。
4. 回到检测范围，选择账号和课程群，读取成员后可限定老师／班长。保存并启用检测。
5. 在允许的测试群由被允许的其他成员发送一条明确的通知。确认最近接收时间变化，事件进入收件箱，审核前正式任务数不增加。
6. 再发送延期通知，核对匹配、预览、确认与来源历史；不要只以“账号在线”作为验收结果。

保存桥接配置会重新读取检测范围，先保存已有范围编辑再调整桥接连接。适配器连接检查有约 10 秒缓存；手动检查绕过缓存。后端持续运行时检查连接并处理队列，关闭网页不影响后端；电脑休眠、关机或外部 QQ 退出时不能实时检测。

暂停检测只停止后续接收范围；已开始识别的操作可能先完成。断开连接不清空消息、任务和已保存群选择。若需退出 QQ，请在外部桥接程序操作。

## API 与验证

- `GET /api/connectors/qq/onebot`：公开配置、凭据是否已保存、最后检查结果与回调地址；无密钥值。
- `PUT /api/connectors/qq/onebot`：保存启用状态、本机地址及可选新密钥。
- `POST /api/connectors/qq/onebot/probe`：立即检查桥接与账号。
- `POST /api/connectors/qq/onebot/events`：带签名的本机事件回调；不用于浏览器测试表单。

```bash
cd backend
uv run pytest tests/test_onebot.py tests/test_qq_core.py -q
```

采用 MockTransport 和临时数据库验证请求鉴权、API 响应、目录、标准化、签名、重复投递、来源与审核、错误脱敏、凭据不落 JSON、重启配置恢复及禁用。测试中的凭据存储是独立内存替身，不触碰真实钥匙串；不能替代 Mac 钥匙串权限、真实 QQ 扫码和群事件实机验收。

后续实机验收：Mac 安装与当前 QQ 版本兼容性、钥匙串授权、扫码登录、允许群与指定成员的新消息、重复与断网投递、账号变化、正常重启恢复、会话失效提示。长期运行与离线历史补拉仍需单独观察。

本阶段本地完整检查：后端 269 项测试通过（其中 18 项 OneBot 适配器、12 项 QQ 核心），前端 3 组日期测试、lint、格式、类型检查与生产构建通过。

## 本机真实联调（2026-11-02）

- 用户完成 App 管理权限与 QQ 手机扫码；真实钥匙串凭据写入、账号在线、群列表与成员目录读取均成功。管理页和 HTTP 接口只监听回环地址。
- 用户自行选择一个群并限定一个其他成员，该成员的一条文本通知经签名回调进入原收件箱待审核。正式任务未自动创建。恢复 Ollama qwen3:4b 后重新识别补充了标题；课程与具体时刻不明确，继续留空。
- 后端、QQ / NapCat、Ollama 已交由用户级 macOS LaunchAgent 管理，设为用户登录系统后启动、异常退出重试。重启三个进程后 QQ 自动恢复在线，不需再次扫码；检测范围与既有消息记录保留，通知未重复创建。
- 本次没有执行整机重启／注销验收；长期运行、休眠、离线完整补拉和未来 QQ 版本兼容性仍待验证，会话失效可能需要重新扫码。
- NapCat 原生 packet / 媒体附加模块出现签名加载错误，没有绕过其签名校验。本次只验收文本通知与目录，图片、文件、语音等不据此宣称可用。

后台标签为 `io.campusinbox.backend`、`io.campusinbox.qq`、`io.campusinbox.ollama`。本机交付目录提供启动／停止和带校验的 QQ 原入口恢复工具；恢复脚本已准备但未执行。停止工具同时禁用自动加载，启动工具可重新启用。

CampusInbox 普通连接配置不含密钥；NapCat 自身所需 OneBot 密钥保存在它自己的本地配置，已设为仅用户读写。QQ 会话、真实通知、本机配置、日志及上述设备管理工具不提交 Git。
