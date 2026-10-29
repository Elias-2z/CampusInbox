# CampusInbox backend

Python 3.11，使用 uv 管理依赖和虚拟环境。

```bash
uv sync --locked
uv run uvicorn campus_inbox.main:app --reload --host 127.0.0.1 --port 8000
```

当前仅提供 `/api/health` 和 FastAPI 自动接口文档 `/docs`，尚未实现任务业务。

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
