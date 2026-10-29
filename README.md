# CampusInbox

让分散的校园信息，自动变成可执行任务。

面向大学生的校园智能任务 Agent，计划以规则优先、LLM 兜底的方式，将通知整理成可审核、可追溯的任务。

**当前状态：开发环境骨架。** 已配置 Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router、FastAPI、uv 和自动检查。当前 API 仅包含健康检查；尚未实现通知识别、任务管理、数据库业务、模型调用或第三方平台同步。

## 快速开始

准备 Git、uv 和 Node.js 24 LTS，在仓库根目录执行：

```bash
uv python install 3.11
uv run --python 3.11 scripts/setup.py
uv run --python 3.11 scripts/dev.py
```

访问前端 `http://127.0.0.1:5173` 和接口文档 `http://127.0.0.1:8000/docs`。
使用 Ctrl+C 关闭服务。

## 项目结构

```text
frontend/    Vue 网页端
backend/     Python API、依赖与后端测试
docs/        项目与环境文档
tests/       后续跨模块测试
benchmarks/  后续识别效果评测
scripts/     安装、启动与检查脚本
.github/     CI、依赖更新与协作模板
```

运行检查：

```bash
uv run --python 3.11 scripts/check.py
```

详细配置、PyCharm 运行方式和限制见 [开发文档](docs/development.md)。
项目尚未发布；开源许可证待确定。
