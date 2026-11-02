# CampusInbox · Windows 本地采集扩展

本模块从独立本地项目导入，来源提交 `dde6146`。与仓库根目录的 FastAPI 应用独立运行：API、数据库和任务状态不自动互通。接入方式与验证范围见[整合说明](../docs/local-assistant.md)。

把课程通知整理成清晰的学习任务，在一个地方查看截止时间、核对材料和记录提交进度。

当前版本使用 Vue 3 界面，连接本项目的 Python 服务与 SQLite 数据库。通知识别后先生成草稿，由你核对后加入任务清单；基础文本处理无需模型 API Key。

## 界面预览

![CampusInbox 当前首页：待办清单、截止提醒和提交进度](docs/images/overview.png)

截图使用独立测试数据，不包含个人课程或账号信息。[查看手机宽度界面](docs/images/mobile.png)。

## 快速开始（Windows）

### 1. 准备环境与代码

- 安装 **Python 3.10+**，建议使用 Python 3.12；安装时勾选 **Add python.exe to PATH**，安装后重新打开启动窗口。基础后端只使用 Python 标准库，无需安装 pip 依赖或模型。首次从源码使用还需安装 Node.js 24.12+ 或 26，以构建网页界面。
- 在仓库页面先选择 **codex/local-assistant-integration** 分支，再点击绿色 **代码 → 下载 ZIP**，解压到普通文件夹；不要在压缩包中直接运行。进入解压目录中的 **local-assistant/**；此目录内应能看到 `server.py`、`start.cmd`、`frontend/` 和 `web/`。下文“项目根目录”均指 `local-assistant/`。
- 已安装 Git 的开发者也可以运行：

```powershell
git clone --branch codex/local-assistant-integration https://github.com/zjx20040128-lgtm/CampusInbox-1.git
cd CampusInbox-1/local-assistant
```

### 2. 构建并启动网页

首次在 `local-assistant/` 打开终端，执行以下命令。仓库只提交源码，不包含生成的网页文件；更新前端后也需重新构建。

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
```

双击根目录的 **启动融合版.cmd** 或 **start.cmd**，保持打开的启动窗口运行。浏览器会打开 [http://127.0.0.1:8877](http://127.0.0.1:8877)，没有自动打开时手动访问即可。

首次运行会自动创建 `data/`，无需手动建数据库、填写令牌或创建 `.env`。从本机地址访问会自动连接；初始任务列表为空。前一步生成的 `web/ui` 由 Python 服务直接提供；构建完成后的日常启动不需要运行 Node.js。

停止服务：在启动窗口按 `Ctrl+C`。下次双击启动脚本，继续使用已有数据。

### 3. 完成第一次任务操作

**直接记一条任务：** 进入 **我的任务 → 新建任务 → 手动填写**，填写任务名称与课程，截止时间可选“未明确”“仅日期”或具体时间，点击创建。任务会直接进入清单，不需要识别服务。

**从通知生成任务：** 在 **新建任务 → 文本粘贴** 中输入下面的测试通知，核对消息实际发送时间，点击 **识别并前往收件箱审核**：

```text
《软件工程》第一次作业：提交实验报告和源代码。截止2030年11月8日18:00。
```

在收件箱核对课程、要求和截止时间，确认草稿后，任务才进入“我的任务”。测试日期请按实际使用时间调整为未来日期。

点击任务名称查看详情和材料清单。任务左侧圆圈第一次点击记为“已做完”；在原平台实际提交后，再点击并确认记为“已提交”。已提交任务可通过任务页的状态筛选查看。

## 按需开启的功能

### 图片与文档解析

文本粘贴、手动填写以及 TXT / Markdown 文件导入不需要 MinerU。新版图片和 PDF / Word / PPT 解析需要 MinerU Token：

1. 将根目录 `.env.example` 复制为 `.env`，注意不要保存成 `.env.txt`。
2. 在 `.env` 中添加 `MINERU_API_KEY=你的Token`。
3. 重新打开“新建任务”窗口，选择图片或文件，勾选同意上传后开始解析；提取结果仍需审核。

支持格式、大小限制、Token 获取和解析恢复见[新建任务与附件解析](docs/task-import.md)。

### 学习通登录与后台同步

此功能另外需要 **Windows、Microsoft Edge、Node.js 和 Playwright**。安装 Node.js 后，在项目根目录打开终端，执行一次：

```powershell
npm install --prefix desktop-sync
```

然后双击 **start-desktop.cmd**。启动器会使用 Windows 的 .NET Framework 编译器构建桌面程序；若提示编译器缺失，需要启用或安装 .NET Framework 4.x 开发工具。

在桌面窗口使用本人学习通账号扫码并完成验证。网页连接入口位于 **设置 → 数据源连接 → 学习通同步**，手机连接也从此处进入。同步生成的任务仍需收件箱审核；保持电脑、服务及后台采集运行。详细说明见[学习通后台同步](desktop-sync/README.md)。

更新后，先停止旧服务；桌面程序还需从系统托盘退出，再重新启动。只关闭网页或桌面窗口不一定会停止后台程序。

## 启动遇到问题

- **提示找不到 Python**：确认已安装 Python 3.10+ 且加入 PATH。也可在项目根目录执行下面的环境检查；如使用虚拟环境，可通过 `CI_PYTHON` 指定其 `python.exe`。
- **网页打不开**：保持启动窗口打开，查看是否有错误；确认访问 `8877`，不要使用旧文档中的 `8765`。刷新网页不能代替启动服务。
- **8877 被其他程序占用**：不要关闭不认识的程序，在根目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Port 8890`，访问 `http://127.0.0.1:8890`。开发代理仍默认指向 `8877`。
- **启动后显示旧页面**：确认下载的是 `codex/local-assistant-integration` 分支，并从刚解压的目录启动；停止之前的服务后重开，再用 `Ctrl+F5` 刷新。
- **识别后没有任务**：先到收件箱核对草稿；只有确认后才进入正式清单。逾期任务请切换到“已逾期”或“全部任务”查看。

环境检查命令（在项目根目录执行）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -CheckOnly
```

## 主要功能

- **今天**：优先查看未逾期的待办，汇总近期截止、提交进度和待核对消息。
- **我的任务**：按课程、关键词和状态筛选，维护材料清单、截止日期与任务进度。
- **收件箱**：核对识别结果、延期与冲突，查看任务对应的通知原文。
- **日历**：按周或月查看截止安排，导出 `.ics` 日历。
- **提醒**：显示提醒窗口内尚未到期的任务，支持已读和稍后提醒。
- **设置**：配置数据源连接、查看模型状态、导出数据与备份说明。

首页待办和“尚未提交”筛选排除已逾期、已提交及已取消任务。提交进度按“已提交 ÷（已提交＋未逾期的待完成任务）”计算。

“已做完”和“已提交”分别记录；本地标记提交不代表平台回执，软件不会代交作业。只有日期的截止保持为日期，不补造具体时刻。网页通知需要授权，页面关闭后不运行；桌面与手机提醒的使用范围见对应文档。

## 数据与配置

任务、通知、附件和本机配置保存在 `data/`。完整备份前先停止服务，再复制整个目录；恢复时停止服务，将备份放回原位置。

- `.env` 保存可选模型与解析服务配置，参考 `.env.example`。
- 基础文本流程在本地处理；启用外部模型或 MinerU 时，相关文字或文件会发送至对应服务。
- `.env`、`data/`、访问令牌和登录会话不应提交到仓库。
- 网页 JSON 导出不包含附件二进制，不能代替完整目录备份。

## Docker 网页模式（可选）

Docker 镜像复制本机的网页产物，**必须先构建 Vue 界面**。在 `local-assistant/` 执行：

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
docker compose up --build -d
```

访问 `http://127.0.0.1:8765`（此端口仅用于本模块现有 Docker 配置）。容器只提供网页服务，不提供 Windows 桌面采集或系统 OCR。连接页需要访问令牌时，在自己的终端运行 `docker compose exec courseinbox cat /app/data/access-token.txt` 并填入；令牌不要公开。停止容器使用 `docker compose down`，不要添加 `-v`，以保留数据卷。本次在 Windows 验证源码启动，未运行 Docker 镜像验收。

## 开发与验证

前端开发需要 Node.js 24.12+ 或 26。

```powershell
cd frontend
npm ci
npm run dev
```

开发服务器默认使用 `http://127.0.0.1:5175`，API 代理至运行中的 Python 服务 `8877`。修改前端后，构建到 `web/ui`：

```powershell
npm test
npm run build
```

完整回归从项目根目录执行；浏览器检查需要 Playwright 和 Edge，使用独立临时数据库：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-integrated.ps1 -Browser
```

主要源码：`frontend/` 为 Vue 界面，`courseinbox/` 为业务与 API，`web/` 包含静态资源与内嵌业务工作区，`desktop-app/` 和 `desktop-sync/` 提供 Windows 桌面与采集功能，`android/` 为可选手机客户端。

## 进一步阅读

- [界面融合与运行说明](docs/ui-integration.md)
- [新建任务与附件解析](docs/task-import.md)
- [学习通后台同步与依赖](desktop-sync/README.md)
- [Android 客户端](android/README.md)
- [浏览器采集组件](browser-companion/README.md)
- [QQ Connector](docs/qq-connector.md)
- [API 文档](docs/API.md)
- [验收记录](docs/verification/acceptance.md)
- [历史版本与旧版说明](docs/project-history.md)

新版界面与统一新建任务交互参考小组项目 [Elias-2z/CampusInbox](https://github.com/Elias-2z/CampusInbox)，具体来源提交与适配范围见[融合说明](docs/ui-integration.md#来源)。
