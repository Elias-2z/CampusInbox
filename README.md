# CampusInbox

让分散的校园信息，自动变成可执行任务。

面向大学生、本地运行的校园任务工具。当前已实现第一阶段核心闭环：**粘贴通知 → 规则优先识别 / 可选本地模型兜底 → 收件箱审核 → 创建任务 → 查看来源与历史。**

## 已可使用

- ☀️ 首页：真实待办、完成进度、未来七天的截止任务和待处理建议。
- 📮 收件箱：保存原文与实际发送时间，查看识别依据，编辑建议后确认；支持忽略、恢复和重新识别。
- 🗂️ 任务：手动创建、编辑、完成、取消、归档与恢复，按课程 / 状态筛选、搜索，查看原文和操作记录。
- ⚙️ 本地模型设置：可配置 Ollama、读取已安装本地模型并测试连接。默认关闭兜底，无模型也能使用规则与人工审核。
- SQLite 本地持久化；重复导入去重、重复确认防重，版本检查防止编辑覆盖。完成与归档后的即时撤销会记录为新操作。

**初版采用保守模式：所有新任务均须用户确认。** 延期、取消、多任务通知仅标记待核对，不自动创建新任务或更新已有任务；请查看原文并手动编辑相关任务。

尚未实现 OCR、完整日历、提醒推送、微信 / QQ / 学习通自动同步、云端模型、自动任务合并、备份恢复和安装包。未连接任何第三方校园账号。

## 快速开始

准备 Git、uv 和 Node.js 24 LTS，在仓库根目录执行：

```bash
uv python install 3.11
uv run --python 3.11 scripts/setup.py
uv run --python 3.11 scripts/dev.py
```

打开 `http://127.0.0.1:5173`；API 文档在 `http://127.0.0.1:8000/docs`。Ctrl+C 停止两端服务。

首次没有演示任务。点击“导入第一条通知”，可粘贴真实通知或选择“试填一条示例通知”，核对原消息发送时间，整理并审核后创建任务。刷新或重启后仍保留。

PyCharm 可直接运行 **Dev (frontend + backend)**；解释器选择 `backend/.venv/bin/python`。Windows 对应 `backend/.venv/Scripts/python.exe`。

## 本地模型（可选）

先安装并启动 Ollama，再安装符合本机资源的本地模型。建议启动服务时禁用 Ollama 云端功能：

```bash
OLLAMA_NO_CLOUD=1 ollama serve
```

Windows PowerShell：`$env:OLLAMA_NO_CLOUD="1"; ollama serve`。如果已通过 Ollama 应用运行服务，请先确认该服务的本地模式配置，避免重复启动。

进入“设置”测试连接、选择本机已安装的模型、启用兜底并保存。模型名按实际安装列表填写；本项目不会自动下载权重。Provider 会排除云模型并检查本地模型元数据；不认识的模型保留规则结果供人工审核。

通知不明确时才调用模型；结构化输出仍要经过来源校验，不会直接写入任务。服务未启动、超时或输出无效时保留原文与规则字段。实际模型效果和速度取决于所用模型、机器和通知内容。

## 数据与隐私

默认数据库为 `backend/data/campus-inbox.db`，并使用 SQLite WAL 文件。数据库、模型权重、个人消息和凭据均不提交 Git。关闭服务后再迁移整个 data 目录，避免遗漏 WAL 数据；当前没有图形备份功能。

仅供本机单用户使用，服务绑定 `127.0.0.1`。没有登录鉴权，不应将端口暴露给局域网或公网。当前接口只允许回环地址的 Ollama，不支持云端 API Key。

测试可通过 `CAMPUSINBOX_DB_PATH` 指向独立数据库，不污染实际使用数据。原始通知不可直接删除；任务“删除”采用可恢复归档。

## 开发与验证

```bash
uv run --python 3.11 scripts/check.py
```

包含后端 Ruff / pytest 和前端 lint / 格式 / TypeScript / 生产构建。测试使用临时 SQLite 和模型模拟响应，不会将消息发给真实模型。真实模型效果评测尚未开展。

- [第一阶段范围、模型与 API](docs/mvp.md)
- [开发与故障排查](docs/development.md)
- [本阶段验证记录](docs/verification.md)

```text
frontend/    Vue 3 + TypeScript + Vite 网页端
backend/     FastAPI + SQLModel API、SQLite 与后端测试
scripts/     安装、启动与检查
benchmarks/  后续模型效果评测
.github/     CI、Dependabot、Issue / PR 模板
```

当前仅保存在本地 Git；未创建远程仓库或上传 GitHub。正式公开前仍需选择开源 LICENSE，并在一台未参与开发的电脑上完成安装验收。
