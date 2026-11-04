# Windows QQ 通知接入

2026-11-04。适用于根目录 FastAPI + Vue 主应用，不使用 `local-assistant/` 的数据库或接收端口。

## 当前实现与边界

Windows 使用当前登录用户的 Credential Manager 存储 OneBot Access Token 和事件签名 Secret；按数据目录隔离，与模型 API 的凭据空间分离。普通 `qq-onebot.json` 只保存本机地址和启用状态。macOS 沿用原有钥匙串服务名，旧凭据无须迁移。凭据读取／保存失败会报错，不回退到明文。

QQCore 负责群／成员过滤、持久队列、去重、课程上下文和收件箱审核。收到通知不等于创建正式任务。媒体不自动读取，离线消息需要桥接器保留和重投。

## 1. 准备并运行主应用

先安装 Git、uv 和 `.nvmrc` 指定的 Node 版本，在仓库根目录运行：

```powershell
uv python install 3.11
uv run --python 3.11 scripts/setup.py
powershell -NoProfile -File scripts/windows.ps1 start
```

默认网页 `http://127.0.0.1:5173`、API `http://127.0.0.1:8000/docs`。端口被占用会停止启动并报错，不结束原进程。可指定空闲端口：

```powershell
powershell -NoProfile -File scripts/windows.ps1 start -ApiPort 8002 -WebPort 5174
powershell -NoProfile -File scripts/windows.ps1 status
powershell -NoProfile -File scripts/windows.ps1 stop
```

启动器隐藏运行主应用，关闭网页不停止后端；日志和进程记录位于忽略提交的 `work/windows-main/`。后台模式不启用源码热重载，更新代码后先 stop 再 start。启动成功前会请求网页代理的健康检查和 QQ 配置接口，避免把旧后端误认为当前版本。停止前核验保存的 PID、启动时间、可执行文件及脚本路径，只结束该启动器的进程树。`status` 显示监督进程是否存在，不代表 QQ 在线。

本脚本不创建登录启动项，不安装或结束 QQ／NapCat／Ollama。停止时尚未完成的入队事件会在再次启动后恢复；识别中的通知保留原有幂等恢复语义。前台开发可用 `uv run --python 3.11 scripts/dev.py --api-port 8002 --web-port 5174`，Ctrl+C 停止两端。

## 2. 安装并登录 Windows 消息桥

按照 [NapCat 官方 Windows Shell 安装文档](https://napneko.github.io/guide/boot/Shell) 选择适合机器的包。官方提供 Shell 手动安装和 Windows 一键安装两种方式；启动文件随安装方式不同，请按所下载版本的说明操作。由用户完成手机扫码和账号验证。第三方消息桥不是腾讯官方接口。

首次安装建议使用测试账号和测试群。先手动启动和验证恢复，不默认添加系统启动项或修改系统安全设置。QQ 会话由消息桥维护，不能从 Mac 复制会话文件。后续 QQ／NapCat 升级可能需要重新验证兼容性。

## 3. 配置 OneBot 双向连接

在消息桥启用以下两个连接（参见 [NapCat 官方连接说明](https://napneko.github.io/NapCatQQ-Desktop/docs/connections/)）：

1. **HTTP 服务端**：只监听 `127.0.0.1`，例如端口 `3000`。设置至少 24 位随机 ASCII Access Token，CampusInbox 用它查询在线账号、群和成员。
2. **HTTP 事件上报**：地址使用 CampusInbox QQ 页面实际显示的回调地址；API 端口为 8002 时是 `http://127.0.0.1:8002/api/connectors/qq/onebot/events`。设置另一个至少 24 位随机 ASCII Secret，按 OneBot v11 对原始请求体计算 `X-Signature: sha1=<HMAC-SHA1>`。此密钥不是 WebUI 密码，也不是 HTTP 服务端 Token。

消息桥需对非 2xx 响应保留并重投。无法提供签名上报的桥接器不能通过关闭校验来接入。本应用不会主动补抓桥接器未保留的历史。NapCat 自身的配置也可能包含凭据，应限制访问权限，不提交到仓库。

打开主应用“设置 → 数据源连接 → QQ → 高级连接配置”，填写地址及两项密钥，启用并保存。页面应显示“Windows 凭据管理器”；以后密钥留空保持旧值。点击检查连接，确认真实账号在线。读取凭据失败时可重新填写两项密钥修复，不要删除任务数据库。

再选择账号、群和允许成员，保存并启用检测。空成员列表表示全群；自己发送的消息会过滤，联调应让另一个允许成员发送。群／成员目录读取成功只代表 HTTP 查询可用，不能代替事件上报验收。

## 4. 故障与恢复

- 配置接口 404：核对启动目录和后端版本，使用启动器健康检查。代码更新后重启后端。
- 无法保存密钥／读取凭据：使用同一 Windows 用户运行，检查系统凭据权限；重新填写两项密钥。切换 Windows 用户不会共享凭据。
- QQ 不在线：在外部消息桥完成扫码或验证，再检查连接，不以端口可连判断账号在线。
- 有账号／群但没有通知：核对 HTTP 上报地址、签名 Secret、群／成员范围和发件人；检查最近接收时间、队列和收件箱。
- 401 为签名失败，422 为事件格式不正确，503 为停用或尚未持久保存；桥接器应重投可重试失败。过滤范围外事件或重复事件返回 204。
- 关闭网页可继续接收；休眠、关机、后端或桥接器退出期间不能实时接收。重启保留范围、事件和启用意图，但 QQ 会话失效仍需重新扫码。

## 5. 验证记录

本次在 Windows、Python 3.11 执行自动化验证。原生凭据测试使用临时目录的合成凭据，验证真实 CredWrite／CredRead、跨解释器读取、目录隔离、格式损坏拒绝、重新配置和清理测试凭据；不访问用户已有 QQ 凭据。模拟测试验证 Mac 原钥匙串目标保留、签名回调、过滤、队列、收件箱和错误脱敏。CI 已扩展主应用后端到 Ubuntu／Windows／macOS，远程运行结果需以实际 CI 为准。

启动器已在本机验证启动、代理连通、停止、状态检查、再次启动及占用端口时安全拒绝。完整检查包括 318 项后端测试，以及前端 lint、格式、3 组日期测试、类型检查与构建；最终结果以本次执行输出为准。命令为：

```powershell
uv run --python 3.11 scripts/check.py
```

**真实 QQ 联调待用户完成扫码和选择测试群后验收**：指定成员新通知入箱、审核前无正式任务、重复投递与范围过滤、确认创建及后续延期／取消的来源历史、关闭网页接收、重启恢复、断网丢失范围。尚未声称已完成真实群采集、长时间稳定性、整机重启或媒体识别。
