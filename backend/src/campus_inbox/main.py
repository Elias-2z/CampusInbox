"""Local single-user API. Model output is never a database command."""

import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import update
from sqlmodel import Session, select

from campus_inbox import cancellations, deadlines, merging, services, splitting
from campus_inbox.database import DEFAULT_DB, initialize, make_engine
from campus_inbox.models import ModelSettings, SplitDraft, Suggestion, Task, now
from campus_inbox.providers import OllamaProvider, ProviderError
from campus_inbox.schemas import (
    AcceptSuggestion,
    CancellationChangeRead,
    CancellationPreview,
    CancellationPreviewRequest,
    DeadlineChangeRead,
    DeadlinePreview,
    DeadlinePreviewRequest,
    ImportMessage,
    InboxItem,
    LocalModelConfig,
    MergeChangeRead,
    MergePreview,
    MergePreviewRequest,
    SplitAccept,
    SplitDecision,
    SplitEdit,
    TaskDetail,
    TaskFields,
    TaskRead,
    TaskUpdate,
)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


def get_write_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        # Serialize writes before reading the objects they will mutate.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield session


DB = Annotated[Session, Depends(get_session)]
WriteDB = Annotated[Session, Depends(get_write_session)]


def create_app(db_path: Path | None = None, provider_factory=OllamaProvider) -> FastAPI:
    engine = make_engine(db_path or Path(os.environ.get("CAMPUSINBOX_DB_PATH", str(DEFAULT_DB))))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize(engine)
        yield
        engine.dispose()

    application = FastAPI(title="CampusInbox API", version="0.1.0", lifespan=lifespan)
    application.state.engine = engine

    @application.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "campus-inbox"}

    @application.get("/api/inbox", response_model=list[InboxItem], tags=["inbox"])
    def list_inbox(
        session: DB, status: Literal["pending", "accepted", "ignored", "all"] = "pending"
    ):
        query = select(Suggestion)
        if status == "pending":
            query = query.where(Suggestion.status.in_(["pending", "processing"]))
        elif status != "all":
            query = query.where(Suggestion.status == status)
        rows = session.exec(query.order_by(Suggestion.created_at.desc()))
        return [services.inbox_item(session, row) for row in rows]

    @application.post("/api/inbox", response_model=InboxItem, tags=["inbox"])
    def import_text(body: ImportMessage, session: WriteDB):
        return services.inbox_item(
            session, services.import_message(session, body, provider_factory)
        )

    @application.post(
        "/api/inbox/{suggestion_id}/analyze", response_model=InboxItem, tags=["inbox"]
    )
    def reanalyze(suggestion_id: str, session: WriteDB):
        suggestion = services.get_or_404(session, Suggestion, suggestion_id)
        return services.inbox_item(session, services.retry(session, suggestion, provider_factory))

    @application.post(
        "/api/suggestions/{suggestion_id}/accept", response_model=TaskRead, tags=["inbox"]
    )
    def accept(suggestion_id: str, body: AcceptSuggestion, session: WriteDB):
        return services.accept(
            session, services.get_or_404(session, Suggestion, suggestion_id), body
        )

    @application.post(
        "/api/suggestions/{suggestion_id}/deadline-preview",
        response_model=DeadlinePreview,
        tags=["deadline changes"],
    )
    def deadline_preview(suggestion_id: str, body: DeadlinePreviewRequest, session: WriteDB):
        return deadlines.preview(session, suggestion_id, body)

    @application.post(
        "/api/deadline-proposals/{proposal_id}/confirm",
        response_model=DeadlineChangeRead,
        tags=["deadline changes"],
    )
    def confirm_deadline(proposal_id: str, session: WriteDB):
        return deadlines.confirm(session, proposal_id)

    @application.post(
        "/api/deadline-changes/{suggestion_id}/undo",
        response_model=DeadlineChangeRead,
        tags=["deadline changes"],
    )
    def undo_deadline(suggestion_id: str, session: WriteDB):
        return deadlines.undo(session, suggestion_id)

    @application.post(
        "/api/suggestions/{suggestion_id}/cancellation-preview",
        response_model=CancellationPreview,
        tags=["cancellations"],
    )
    def cancellation_preview(
        suggestion_id: str, body: CancellationPreviewRequest, session: WriteDB
    ):
        return cancellations.preview(session, suggestion_id, body)

    @application.post(
        "/api/cancellation-proposals/{proposal_id}/confirm",
        response_model=CancellationChangeRead,
        tags=["cancellations"],
    )
    def confirm_cancellation(proposal_id: str, session: WriteDB):
        return cancellations.confirm(session, proposal_id)

    @application.post(
        "/api/cancellation-changes/{suggestion_id}/undo",
        response_model=CancellationChangeRead,
        tags=["cancellations"],
    )
    def undo_cancellation(suggestion_id: str, session: WriteDB):
        return cancellations.undo(session, suggestion_id)

    @application.post(
        "/api/suggestions/{suggestion_id}/split", response_model=InboxItem, tags=["split tasks"]
    )
    def split_suggestion(suggestion_id: str, session: WriteDB):
        return splitting.generate(session, suggestion_id)

    @application.patch(
        "/api/split-drafts/{draft_id}", response_model=InboxItem, tags=["split tasks"]
    )
    def save_split(draft_id: str, body: SplitEdit, session: WriteDB):
        return splitting.save(session, draft_id, body)

    @application.post(
        "/api/split-drafts/{draft_id}/accept", response_model=TaskRead, tags=["split tasks"]
    )
    def accept_split(draft_id: str, body: SplitAccept, session: WriteDB):
        return splitting.accept(session, draft_id, body)

    @application.post(
        "/api/suggestions/{suggestion_id}/merge-preview",
        response_model=MergePreview,
        tags=["merge notices"],
    )
    def merge_preview(suggestion_id: str, body: MergePreviewRequest, session: WriteDB):
        return merging.preview(session, suggestion_id, body)

    @application.post(
        "/api/split-drafts/{draft_id}/merge-preview",
        response_model=MergePreview,
        tags=["merge notices"],
    )
    def split_merge_preview(draft_id: str, body: MergePreviewRequest, session: WriteDB):
        draft = services.get_or_404(session, SplitDraft, draft_id)
        return merging.preview(session, draft.suggestion_id, body, draft.id)

    @application.post(
        "/api/merge-proposals/{proposal_id}/confirm",
        response_model=MergeChangeRead,
        tags=["merge notices"],
    )
    def merge_confirm(proposal_id: str, session: WriteDB):
        return merging.confirm(session, proposal_id)

    @application.post(
        "/api/merge-changes/{change_id}/undo",
        response_model=MergeChangeRead,
        tags=["merge notices"],
    )
    def merge_undo(change_id: str, session: WriteDB):
        return merging.undo(session, change_id)

    @application.post(
        "/api/split-drafts/{draft_id}/{operation}", response_model=InboxItem, tags=["split tasks"]
    )
    def decide_split(
        draft_id: str, operation: Literal["ignore", "reopen"], body: SplitDecision, session: WriteDB
    ):
        return splitting.decide(session, draft_id, body.expected_updated_at, operation == "reopen")

    @application.post(
        "/api/suggestions/{suggestion_id}/{operation}", response_model=InboxItem, tags=["inbox"]
    )
    def change_suggestion(
        suggestion_id: str, operation: Literal["ignore", "reopen"], session: WriteDB
    ):
        suggestion = services.get_or_404(session, Suggestion, suggestion_id)
        if operation == "ignore" and splitting.drafts(session, suggestion_id):
            raise HTTPException(409, "已生成拆分草稿，请逐项处理或跳过剩余草稿。")
        if operation == "reopen" and splitting.drafts(session, suggestion_id):
            raise HTTPException(409, "拆分通知请使用各条目中的恢复待审核操作。")
        expected, target = (
            ("pending", "ignored") if operation == "ignore" else ("ignored", "pending")
        )
        changed = session.execute(
            update(Suggestion)
            .where(
                Suggestion.id == suggestion.id,
                Suggestion.status == expected,
            )
            .values(status=target, updated_at=now())
        ).rowcount
        if changed != 1:
            session.rollback()
            raise HTTPException(409, "建议状态已经变化，请刷新。")
        session.commit()
        session.refresh(suggestion)
        return services.inbox_item(session, suggestion)

    @application.get("/api/tasks", response_model=list[TaskRead], tags=["tasks"])
    def list_tasks(session: DB):
        return session.exec(select(Task).order_by(Task.created_at.desc())).all()

    @application.post("/api/tasks", response_model=TaskRead, tags=["tasks"])
    def create_task(body: TaskFields, session: WriteDB):
        return services.manual_create(session, body)

    @application.get("/api/tasks/{task_id}", response_model=TaskDetail, tags=["tasks"])
    def task_detail(task_id: str, session: DB):
        return services.detail(session, services.get_or_404(session, Task, task_id))

    @application.patch("/api/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
    def edit_task(task_id: str, body: TaskUpdate, session: WriteDB):
        return services.update_task(session, services.get_or_404(session, Task, task_id), body)

    @application.delete("/api/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
    def archive_task(task_id: str, session: WriteDB, expected_version: Annotated[int, Query(ge=1)]):
        task = services.get_or_404(session, Task, task_id)
        data = TaskRead.model_validate(task).model_dump(include=set(TaskFields.model_fields))
        return services.update_task(
            session,
            task,
            TaskUpdate(
                **data,
                status="archived",
                expected_version=expected_version,
            ),
        )

    @application.get("/api/settings/model", response_model=LocalModelConfig, tags=["settings"])
    def model_config(session: DB):
        return services.config(session)

    @application.put("/api/settings/model", response_model=LocalModelConfig, tags=["settings"])
    def save_model_config(body: LocalModelConfig, session: WriteDB):
        if body.enabled and not body.model:
            raise HTTPException(422, "启用兜底前请先选择或填写模型名。")
        settings = session.get(ModelSettings, 1) or ModelSettings()
        for key, value in body.model_dump().items():
            setattr(settings, key, value)
        session.add(settings)
        session.commit()
        return body

    @application.post("/api/settings/model/probe", tags=["settings"])
    def probe_model(body: LocalModelConfig):
        try:
            return {
                "reachable": True,
                "models": provider_factory(body).list_models(),
                "error": None,
            }
        except ProviderError as exc:
            return {"reachable": False, "models": [], "error": str(exc)}

    return application


app = create_app()
