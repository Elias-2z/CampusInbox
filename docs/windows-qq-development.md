# Windows QQ 开发与 AI Chat 交接指南

更新时间：2026-11-02。目标：让 Windows 用户在同一套 CampusInbox 中连接本人 QQ、选择群／成员、接收通知并审核任务，复用现有核心与 OneBot 协议。

## 先确认拿到了正确版本

本文对应包含 `backend/src/campus_inbox/connectors/qq.py`、`onebot.py`、`courses.py` 的版本。开始前先检查这些文件存在，且 `docs/qq-core.md`、`docs/qq-mac.md` 可以打开；缺少时先向项目负责人同步最新 QQ 改动，不要在旧版本里重新写一套核心。本文不表示代码已自动推送到远端。

本仓库有两套程序：

- **本次开发目标**：根目录 `backend/`（FastAPI，8000）、`frontend/`（Vue，5173），数据在 `backend/data/`。
- `local-assistant/`：同学此前提交的独立 Windows 程序，默认 8877，具有另一套 API、任务状态和数据库。可参考其 Windows 启动、DPAPI 等实现，但不能把它能登录／接收消息当成原应用已经接通。

最终验收必须发生在根目录主应用的收件箱与任务详情中。不要新增第三套任务库，不要替换 Mac 已通过验收的实现。

## 当前可以复用什么

| 位置 | 已有能力 |
| --- | --- |
| `backend/src/campus_inbox/connectors/qq.py` | 适配器协议、账号／群／成员范围、配置修订、持久队列、去重、重试、后台检查、接入审核 |
| `backend/src/campus_inbox/connectors/onebot.py` | 本机 HTTP API、在线／账号检查、群与成员目录、事件标准化、HMAC 签名回调、响应限制和错误脱敏 |
| `backend/src/campus_inbox/connectors/courses.py` | 群名年份／学期／班级清理，保留课程等级 |
| `backend/src/campus_inbox/services.py` | 原文保存、规则／模型识别、字段来源、任务建议及来源显示 |
| `backend/src/campus_inbox/providers.py` | 本地 Ollama；通知与群名／发件人／时间上下文分别传入 |
| `frontend/src/components/QQConnections.vue` | 账号、群与成员选择、课程别名、同步状态和失败重试 |
| `frontend/src/components/OneBotBridge.vue` | 桥接地址、密钥输入、保存与连接检查；当前文案仍偏 Mac |
| `backend/tests/test_qq_core.py`、`test_onebot.py`、`test_course_names.py` | 核心、传输及课程名回归 |

Mac 已真实验证扫码、目录、指定成员文本通知入箱、进程重启后的会话与范围恢复；该结果不能代替 Windows 实机验收。本地完整检查基线为 **288 项后端测试**及前端日期／lint／格式／类型／生产构建通过，后续以实际运行结果为准。

## Windows 最小交付范围

按以下顺序完成，优先交付可运行链路：

1. **Windows 安全凭据存储**，并按操作系统选择凭据后端。
2. **Windows 本机消息桥安装与连接说明**，扫码和账号验证由本人操作。
3. **现有设置页的平台文案与配置适配**，保留同一组 API。
4. **启动／停止与恢复**：先验证手动一键启动和持久登录；再按用户选择提供后台／登录启动。
5. **真实账号回归与 Windows CI**，提供可复现记录和已知限制。

本阶段不扩展微信、学习通、完整聊天历史、媒体下载、跨设备远程桥接或桌面重设计。首个交付可使用现有网页配合 Windows 启动器，不要求先开发完整托盘外壳。

## 1. 凭据存储是当前主要缺口

`OneBotAdapter(directory, secrets=store, transport=None)` 已支持注入存储对象，存储最小接口为：

```python
class CredentialStore:
    def get(self, key: str) -> str:
        """找不到时返回空字符串；其他失败需安全处理。"""

    def set(self, key: str, value: str) -> None:
        """成功才返回；失败不能把密钥写入异常、日志或响应。"""
```

目前使用 `Keychain`：Mac 调用 `/usr/bin/security`，非 Mac 读取返回空、写入拒绝。因此只启动现有代码，Windows 仍无法完成桥接密钥保存；这是必须补齐的真实工作。

建议增加 `credentials.py` 或同等小模块：Mac 继续使用现有钥匙串，Windows 优先用 **Windows Credential Manager**；也可以采用当前用户 DPAPI 加密后的文件存储。若引入依赖，要更新清单、锁文件和安装流程。可以借鉴 `local-assistant/courseinbox/session_crypto.py` 的 DPAPI 思路，但需独立测试、处理缓冲区和错误，不能因此引入整个独立程序。

要求：

- 存储 `access`（OneBot HTTP API Token）和 `events`（上报签名密钥），两者用途不同。
- 凭据按用户与数据目录隔离，不能无意复用其他配置实例。
- 普通 `qq-onebot.json` 只保存地址、启用状态，API 只返回密钥是否已保存。
- 使用 DPAPI 时只落加密内容；损坏、解密失败或换 Windows 用户时给出重新配置提示，不改用明文。
- 空密码输入保持旧值；配置校验错误也不能回显输入。不要通过测试日志、命令行打印或截图提交凭据。
- 默认存储后端应由平台工厂选择；在 `create_app()` / 适配器构造处接入，保持测试替身可注入。

注意：NapCat 自身可能必须在本地配置中保存 OneBot 密钥，这不等于 CampusInbox 可以把密钥写进自己的普通 JSON；桥接程序配置应限制文件权限并忽略提交。

## 2. OneBot 连接方式：复用 HTTP 服务与签名上报

推荐先选择可在目标 Windows / QQ 版本运行的 NapCat 或其他兼容 OneBot v11 的消息桥，核对项目官方发布和文档后安装。不要把第三方桥接描述为腾讯官方 API，不承诺永久在线。登录、扫码、额外验证与需要管理员权限的操作由用户完成。

```text
Windows QQ / 消息桥
  ├─ OneBot HTTP 服务 → CampusInbox 查询账号、群、成员
  └─ HTTP POST 上报 → 签名校验 → 持久队列 → 原收件箱
```

桥接 API 示例地址为 `http://127.0.0.1:3000`。当前只接受字面回环 IP（包括 `::1`）与明确端口，不支持 LAN、远程主机、URL 密码、查询参数或重定向；不要通过关闭这些检查来接通。

现有适配器调用：

| OneBot Action | 用途 |
| --- | --- |
| `get_status` | 必须确认 online / good；端口可连不等于 QQ 在线 |
| `get_login_info` | 验证实际账号，避免切号后误用旧检测范围 |
| `get_group_list` | 获取群 ID 和名称 |
| `get_group_member_list` | 获取成员 ID、群名片／昵称 |

API 使用 `Authorization: Bearer <access>`；返回格式需含 `status: ok`、整数 `retcode: 0` 和 `data`。

HTTP 事件上报地址从设置页读取，默认：

```text
http://127.0.0.1:8000/api/connectors/qq/onebot/events
```

对**原始请求体字节**计算 `HMAC-SHA1(events_secret, body)`，请求头为 `X-Signature: sha1=<hex>`。NapCat 当前 HTTP Client 配置中的 `token` 字段承担这个签名密钥用途；它不是 WebUI 密码。端口、字段名称应按所用版本实际核对。

事件回调仅接受本机连接、拒绝浏览器 Origin，最大 256 KB。正确落盘、重复或按范围过滤返回 204；坏签名 401，坏格式 422，未能保存 503，过大 413。不要对实际失败强行返回成功，也不要伪造在线状态。

当前 HTTP 适配器 `poll()` 返回空批次，事件走 HTTP 回调；不要虚构 OneBot 的“获取全部新消息”接口。桥接器是否重投／补拉需实测，没有的能力明确说明。

## 3. 保持现有核心契约

核心协议见 `QQAdapter`：`status()`、`groups()`、`members(group_id)`、`poll(cursor)`、`disconnect()`。现有 OneBot 适配器另有 `close()`，用于正常关机释放资源而不清掉启用意图；用户主动断开才暂停并停用。

- 时间必须来自真实 QQ 事件并含时区；缺失时不能用接收时间推算“明天”。
- 原文、群名、发件人、时间分别保留；不把元数据拼进原文后保存。
- QQ 身份与显示名称分开：按 ID 过滤；改昵称不重复入队，编号仍可在来源详情查看。
- 每群可限定成员；空成员列表表示全群。自己发送的消息会过滤，测试要由另一个允许成员发出。
- 群课程映射支持自动清理、手动别名及显式留空；原文明示课程优先。不要删除 A、Ⅲ 等课程本身的等级。
- 事件进入审核建议，不直接执行 CREATE / UPDATE / CANCEL；保留预览、确认、版本冲突和历史。
- 只支持本机单进程后端；多 worker 之前需要数据库队列抢占，不能简单增加 uvicorn workers。
- 图片／文件／语音未完成读取时保留缺口，不能声明已经识别媒体内容。

管理 API 路径沿用 `docs/qq-core.md` 和 `docs/qq-mac.md`。主要是 `/api/connectors/qq`、`/settings`、`/groups`、`/groups/{id}/members`、`/sync`、`/retry`、`/disconnect`、`/events`、`/course-suggestion`，以及 `/api/connectors/qq/onebot`、`/probe`、`/events`。

不要混淆“收到事件”“送入收件箱”和“正式任务已创建”。当前 `done` 计数表示事件已交给收件箱，不表示用户已确认任务。

## 4. Windows 启动与后台

先在 Windows 根项目跑通原应用：

```powershell
uv python install 3.11
uv run --python 3.11 scripts/setup.py
uv run --python 3.11 scripts/dev.py
```

Ollama 是独立服务，按需启动；本地模式保持禁用云端回退。Windows 默认 Python 环境在 `backend/.venv/Scripts/`。

建议新增清晰的 Windows 启动／停止脚本。只绑定本机，检测端口占用并核对服务身份，不杀未知进程。QQ 登录状态由消息桥持久化，CampusInbox 保存范围和处理进度；不要复制 Mac 的会话文件、钥匙串或个人数据库。

后台／登录启动可评估用户级计划任务或启动项，但先让用户选择开启；提供停止、禁用与卸载路径。不要要求管理员权限来解决普通开发问题，也不要默认关闭系统安全保护。关闭浏览器后要继续接收；关机、休眠、会话过期与后端退出的限制要真实显示。

修改 `OneBotBridge.vue` 中的“Mac 消息桥连接”“钥匙串”等文案，建议由后端暴露平台与凭据后端能力再展示。不要只改文案而让底层仍调用 Mac `security` 命令。

## 5. 必须完成的验收

自动化使用独立临时数据库和模拟凭据存储，禁止真实消息或 Token 进仓库。

```powershell
uv run --python 3.11 scripts/check.py
cd backend
uv run pytest tests/test_qq_core.py tests/test_onebot.py tests/test_course_names.py -q
```

新增 Windows 凭据测试：保存／读取、重启后可解密、损坏处理、不同用户或作用域隔离、不落明文、不回显。现有测试内存替身不能代替实机凭据验收。

真实验收至少包含：

1. 新用户启动并配置桥接，自己扫码；账号、群、成员列表可用。
2. 选择一个测试群和指定成员；该成员发送明确通知，原文与时间正确进入原收件箱；审核前没有正式任务。
3. 同一事件重复投递不重复创建；非白名单群、非选定成员和本人消息被过滤。
4. 确认创建后发送延期／取消通知，预览并确认到原任务，来源与历史保留。
5. 关闭网页仍可接收；重启后端、桥接器后范围和记录保留，会话有效时恢复；失效时提示验证。
6. 连接中断、错误 Token、错误签名、端口占用、消息桥失败，界面不显示假成功。
7. 记录断网期间是否丢消息，不把进程恢复当成完整历史补拉；整机重启与长时间运行另行记录。

CI 应覆盖根项目的 Windows 测试，可以新增独立工作流或扩展原 CI；`local-assistant.yml` 通过不能代替根 FastAPI 的 Windows 验证。保留已有 Ubuntu 检查，OS 专属实机验收与 Mock 测试分别记录。

## 6. 给朋友 AI Chat 的提示词（可直接复制）

```text
请在 CampusInbox 仓库中实现 Windows QQ 接入适配，先阅读 docs/windows-qq-development.md、docs/qq-core.md、docs/qq-mac.md，以及适用的 AGENTS.md。

请先检查 backend/src/campus_inbox/connectors/qq.py、onebot.py、courses.py 是否存在。不存在说明当前分支缺少最新核心，请报告需要同步，不能据旧代码重新造核心。

目标是根目录 FastAPI + Vue 主应用（8000 / 5173），不是 local-assistant 独立程序（8877）。复用现有 QQCore、OneBot HTTP API／签名回调、群和成员筛选、课程映射、持久队列、去重、本地模型及收件箱审核。

优先实现 Windows 安全凭据后端，按平台选择并保持 Mac 钥匙串回归可用；接入实际可运行的 Windows QQ 消息桥，适配设置页文案，提供简单启动／停止和恢复流程。可参考 local-assistant 的 Windows 工具，但不引入第二套任务库或重写审核业务。

保持 API 兼容、回环地址与鉴权限制。扫码和账号验证由用户完成；不把密码、Cookie、Token、真实群消息或数据库提交到 Git。不要默认改安全设置、结束未知进程或创建未经用户选择的登录启动项。

先阅读现有代码、核对分支和 git status，再小步实现。执行根目录完整检查，新增 Windows 凭据与故障回归，并在 Windows 用真实测试账号验证“选群/成员 → 收消息 → 识别 → 收件箱审核 → 创建/延期/取消 → 来源历史”。模拟测试与真实验收分别报告，不宣称永久登录、全量离线补拉或未验证的媒体能力。

交付代码、Windows 运行说明、测试记录与已知限制。保留 Mac 实现，不修改与本任务无关的学习通或微信代码。不自动合并或推送；准备好可审查的提交后向我报告。
```

朋友完成后提交小范围 PR，写清改动、Windows 测试环境、真实验证与剩余缺口；合并前再次核对 Mac 现有回归。
