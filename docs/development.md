# 开发环境与运行

## 基础工具

Python 3.11 + uv，Node.js 24 LTS + npm，SQLite。前后端解耦，开发时 Vite 将 `/api` 代理到本机 FastAPI。

PyCharm 打开仓库根目录，解释器选择 `backend/.venv/bin/python`；Windows 为 `backend/.venv/Scripts/python.exe`。共享运行配置 Dev 同时启动两端，Backend 只启动 API。macOS 使用 Homebrew node@24 时，辅助脚本自动优先使用它，不替换全局 Node。

## 安装、运行和检查

在仓库根目录执行：

```bash
uv python install 3.11
uv run --python 3.11 scripts/setup.py
uv run --python 3.11 scripts/dev.py
```

前端 `http://127.0.0.1:5173`，API `http://127.0.0.1:8000`，文档 `/docs`。开发脚本会在服务异常退出时关闭另一端，Ctrl+C 同时清理两端进程。

分开启动：

```bash
cd backend
uv run uvicorn campus_inbox.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm run dev
```

完整检查（仓库根目录）：

```bash
uv run --python 3.11 scripts/check.py
```

安装脚本使用锁文件，不升级依赖。新增依赖在 backend 使用 `uv add` / `uv add --dev`；在 frontend 使用 `npm install` / `npm install -D`，提交清单与锁文件。可选 fsevents 安装脚本显式禁用，已验证 Vite 可正常运行。

## 数据库

默认路径与启动位置无关，为 `backend/data/campus-inbox.db`；可通过 `CAMPUSINBOX_DB_PATH` 指定测试数据路径。原消息和识别处理中占位记录在推理前提交。服务重启时将中断的识别恢复为待审核，允许重新识别。

原有 schema v1 保持不变，通过 create_all 增量增加 DeadlineProposal / DeadlineChange 与 CancellationProposal / CancellationChange 独立表；升级前已使用 SQLite backup API 创建本地一致性备份，并验证旧记录与原文保留。当前仍没有通用的旧字段迁移工具，后续更改已有表结构前需先实现迁移，不得删除真实数据库解决升级问题。

所有时间以带时区的 UTC 传输和保存，界面显示浏览器本地时间。消息携带原发送时间及命名时区用于相对日期解析。未明确时刻不默认成 23:59；用户可以手动明确截止时间。

SQLite 开启外键与 WAL。写操作在读取待修改对象前获取写事务；模型调用前释放写事务。任务修改用 expected_version 防止并发覆盖。接受建议的状态修改、任务和创建事件在同一事务中完成。

## 截止时间审核与隔离测试

截止时间更新使用持久化预览，确认时校验任务版本和建议修订；更新、事件与通知关联在同一事务内完成。详情返回多条来源，撤销记录为新事件。撤销后建议仍为已处理；旧确认不能再次应用，需要重做时请人工编辑任务。

取消审核使用相同的任务版本和建议修订校验，确认只改变任务状态；撤销恢复原状态，来源和历史均保留。详见 cancellation-review.md。

浏览器验收可以独立运行另一份 API 与 Vite：API 用 CAMPUSINBOX_DB_PATH 指定测试数据库并选择空闲端口；Vite 用 CAMPUSINBOX_API_TARGET 指向测试 API，再指定另一端口。该变量只在开发代理中使用，不注入浏览器。默认仍为 8000 / 5173。不得把测试通知写入用户的正式数据库。

## 模型

业务通过 LLMProvider 接口调用 Ollama，使用 JSON Schema 结构化输出。模型只填补有原文依据的缺失字段；不覆盖规则已确定值。时间由日期模块计算，不接收模型虚构的日期。

本地服务仅接受 localhost、127.0.0.1 或 ::1，不跟随重定向、不继承 HTTP 代理。排除云模型并用模型元数据确认本地权重。建议 Ollama 服务使用 OLLAMA_NO_CLOUD=1 禁用云端功能。当前没有真实 4B / 8B 模型效果指标，测试中的模拟响应不能替代模型评测。

官方接口参考：[Ollama API](https://docs.ollama.com/api)、[Ollama 云功能设置](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features)、[SQLModel Session](https://sqlmodel.tiangolo.com/tutorial/fastapi/session-with-dependency/)。

## 常见问题

- 导入后发现字段不完整：原文仍在收件箱，手动填写后确认即可；也可配置模型后重新识别。
- 误把通知发送时间设为今天：相对日期依此解释。保留原通知，可忽略旧建议，再修改发送时间导入；同一天同内容会去重，必要时在审核表单手动修正时间。
- 提示任务版本变化：刷新后核对新内容再编辑；旧操作不会静默覆盖新值。
- 模型不可连接：检查 Ollama 是否启动，设置页“测试连接 / 读取模型”；规则审核仍可正常使用。
- PyCharm 导入报错：先 setup，再检查解释器，必要时标记 backend/src 为 Sources Root。
- 页面连接不到服务：查看 Backend 是否启动、8000 是否占用；Vite 5173 启用了 strictPort。

## GitHub 与平台验证

本地分支和提交使用 GitHub noreply 身份，不改全局 Git 身份。已配置 CI 与 Dependabot，但未建立远程仓库，远端 CI 尚未运行。GitHub CLI 与 Codex 连接独立授权，本机 CLI 尚未登录，上传时再处理。

已在 macOS Apple Silicon 验证；Windows / Linux 独立机器安装、正式开源许可证与安装包仍待后续验收。
