# CampusInbox backend

Python 3.11，uv 管理依赖，FastAPI + SQLModel + SQLite。模块与接口见根目录 docs/mvp.md。

```bash
uv sync --locked
uv run uvicorn campus_inbox.main:app --reload --host 127.0.0.1 --port 8000
```

默认数据库为 data/campus-inbox.db；使用 CAMPUSINBOX_DB_PATH 可指定测试路径。本地单用户，模型默认关闭；无模型也可以完成规则提取与人工审核闭环。

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

pytest 使用临时数据库和模拟模型响应，不读取个人数据库、不调用真实模型。
