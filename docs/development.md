# 开发环境

## 工具与边界

- Python 3.11，uv 管理后端，锁文件为 `backend/uv.lock`。
- Node.js 24 LTS（`.nvmrc`），npm 管理前端，锁文件为 `frontend/package-lock.json`。
- PyCharm 打开仓库根目录，解释器选择 `backend/.venv/bin/python`。
- Windows 解释器路径为 `backend/.venv/Scripts/python.exe`。
- PyCharm 的 `Dev (frontend + backend)` 运行配置可同时启动两端；`Backend` 可单独启动后端。Windows 请按上面路径调整解释器。
- Git 在仓库根目录初始化，分支为 `main`。依赖缓存、IDE 私有配置、数据库、密钥和个人消息不提交。
- 本次仅搭建开发骨架；任务识别、存储、模型调用、平台同步及六大业务页面尚未实现。

## 第一次安装

先安装 Git、uv 和 Node.js 24 LTS，在仓库根目录执行：

```bash
uv python install 3.11
uv run --python 3.11 scripts/setup.py
```

脚本执行 `uv sync --locked` 与 `npm ci`，不会升级锁文件。可选依赖 fsevents 的安装脚本已显式禁用；本次已验证 Vite 启动正常。
macOS 如果使用 Homebrew 的 `node@24`，脚本会优先使用其可执行文件，无需覆盖系统现有 Node。

## 启动

```bash
uv run --python 3.11 scripts/dev.py
```

前端：`http://127.0.0.1:5173`；后端：`http://127.0.0.1:8000`；接口文档：`http://127.0.0.1:8000/docs`。
前端 `/api` 请求通过 Vite 代理到后端。两个服务只监听本机。Ctrl+C 停止启动的服务。

也可以在两个终端分别运行：

```bash
cd backend
uv run uvicorn campus_inbox.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm run dev
```

## 检查

```bash
uv run --python 3.11 scripts/check.py
```

包括后端 Ruff、格式检查、pytest，以及前端 lint、格式检查、TypeScript 检查和生产构建。
GitHub Actions 执行相同的主要检查；只有推送至 GitHub 后才会在远端运行。

添加依赖请在对应目录使用 `uv add 包名` / `uv add --dev 包名` 或 `npm install 包名` / `npm install -D 包名`，并提交清单与锁文件。

## Ollama

本机已安装 Ollama CLI；尚未启动常驻服务、下载模型或设置开机自启。Ollama 是独立服务，不在 Python `.venv` 中运行，模型权重不放进 Git。
当前骨架不依赖 Ollama 即可启动；首次做识别功能时再验证具体的 Qwen 4B 型号、下载模型并配置服务。
尚未实现模型配置读取或连接状态检测，不要将 API 健康检查视为模型健康检查。

## GitHub

当前仅配置本地仓库和 CI 文件，不创建远程仓库、不推送。
本地提交身份使用连接账号的 GitHub noreply 邮箱，只影响此仓库。
GitHub CLI 与 Codex GitHub 连接的授权独立；本机 CLI 尚未登录，按当前要求无需登录。
后续需要上传时执行 `gh auth login`，再关联或创建经确认的远程仓库。

开源许可证尚未选择，因此目前不声明已完成开源发布。正式公开前需确定 LICENSE。
禁止提交 `.env`、密钥、Cookie、数据库及真实消息；`.gitignore` 不能替代提交前检查。

## 常见问题

- PyCharm 报导入错误：确认解释器指向后端 `.venv`，先执行 setup；必要时将 `backend/src` 标记为 Sources Root。
- 端口占用：先关闭原有服务；前端启用了 strictPort，避免悄悄切换端口。
- Node 版本不匹配：切换到 Node 24 LTS 后重新运行 setup。
- 不需要手动激活 `.venv`：`uv run` 会使用项目环境。

## 验证范围

本次在 macOS Apple Silicon 上验证。Windows / Linux 尚未在独立机器上实测；CI 工作流已配置，尚未在 GitHub 远端执行。
