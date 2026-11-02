# Windows 本地采集扩展接入说明

## 接入范围

`local-assistant/` 导入成员本地版本，来源为 `zjx20040128-lgtm/CampusInbox-1` 的 `dde6146`。本分支基于小组 `main` 的 `1d02252`，提交历史可直接比较与审核。

本模块包含 Vue 界面、标准库 Python API、Windows 桌面程序、学习通后台采集、可选 Android 客户端，以及对应的测试与说明。源代码保留原目录关系，使文件解析、桌面启动、静态资源及测试的相对路径继续有效。

## 两套程序的边界

- 仓库根目录的 `frontend/` 与 `backend/` 继续使用原启动流程，网页默认端口为 `5173`，API 为 `8000`。
- 扩展从 `local-assistant/` 启动，网页和 API 默认端口均为 `8877`，手机提醒为 `8878`，QQ Connector 为 `8879`。
- 扩展数据在 `local-assistant/data/`，配置在 `local-assistant/.env`；与根目录后端的数据和 `backend/.env` 分开。
- 两套 API 的任务字段、状态和数据库结构不同。本次提供独立运行的扩展模块，不进行数据库迁移，也不在两套程序之间同步任务或登录状态。
- 现有 FastAPI 应用尚未接入此扩展的学习通与手机 Connector；使用这些功能需要打开扩展程序。

## 从源码启动扩展

Windows 安装 Python 3.10+（建议 3.12）以及 Node.js 24.12+ 或 26，然后在仓库根目录执行：

```powershell
cd local-assistant
npm --prefix frontend ci
npm --prefix frontend run build
.\start.cmd
```

访问 `http://127.0.0.1:8877`。首次自动创建空数据库；可先用“我的任务 → 新建任务 → 手动填写”验证，再尝试文本导入与收件箱审核。

初次构建后，日常使用双击 `local-assistant/start.cmd` 即可。学习通同步还需 Edge 和 Playwright，安装与扫码步骤见[模块 README](../local-assistant/README.md)。

## 提交与数据

生成的 `web/ui/`、依赖、安装包、数据库、访问令牌与账号会话不提交。界面修改后重新运行构建，构建结果只保存在本机。文档截图使用合成通知。

## 验证

在 `local-assistant/` 目录中执行：

```powershell
npm --prefix frontend test
npm --prefix frontend run build
uv run --no-project --python 3.12 --with-requirements requirements-web-push.txt python -B -m unittest discover -s tests -q
```

桌面系统集成测试需要 Windows。浏览器检查通过独立临时数据库执行，未接入真实校园账号；通过测试不代表校园网络、平台页面变化或实体手机权限已验收。

完整 Python 回归包含可选 Web Push 用例，因此需要加载 `requirements-web-push.txt` 中的依赖（上述命令需安装 uv）。基础模式运行仍不要求安装推送依赖；Windows 专属测试需在 Windows 执行。
