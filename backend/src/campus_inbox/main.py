"""Local single-user API. Model output is never a database command."""

import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import update
from sqlmodel import Session, select

from campus_inbox import (
    attachments,
    calendar,
    cancellations,
    data_export,
    deadlines,
    legacy_migration,
    material_suggestions,
    merging,
    phone_reminders,
    reminders,
    services,
    splitting,
    wechat_push,
)
from campus_inbox.connectors import platform_status
from campus_inbox.connectors.browser_bridge import router as browser_bridge_router
from campus_inbox.connectors.chaoxing_api import router as chaoxing_router
from campus_inbox.connectors.chaoxing_runtime import ChaoxingRuntime
from campus_inbox.connectors.onebot import OneBotAdapter
from campus_inbox.connectors.onebot import router as onebot_router
from campus_inbox.connectors.qq import QQCore
from campus_inbox.connectors.qq import router as qq_router
from campus_inbox.database import DEFAULT_DB, initialize, make_engine
from campus_inbox.inference import InferenceSettings, ModelManager
from campus_inbox.inference import router as inference_router
from campus_inbox.mineru import MinerUProvider
from campus_inbox.models import (
    ModelSettings,
    ReminderSettings,
    SplitDraft,
    Suggestion,
    Task,
    TaskEvent,
    TaskMaterials,
    now,
)
from campus_inbox.providers import OllamaProvider, ProviderError
from campus_inbox.schemas import (
    AcceptSuggestion,
    CalendarFeed,
    CancellationChangeRead,
    CancellationPreview,
    CancellationPreviewRequest,
    DeadlineChangeRead,
    DeadlinePreview,
    DeadlinePreviewRequest,
    ImportMessage,
    InboxItem,
    LocalModelConfig,
    MaterialsUpdate,
    MergeChangeRead,
    MergePreview,
    MergePreviewRequest,
    PlatformStatusUpdate,
    ReminderAction,
    ReminderConfig,
    ReminderFeed,
    ReminderRead,
    SplitAccept,
    SplitDecision,
    SplitEdit,
    TaskDetail,
    TaskFields,
    TaskListItem,
    TaskRead,
    TaskRecognitionPreview,
    TaskRecognitionRequest,
    TaskUpdate,
)
from campus_inbox.task_assistant import router as task_assistant_router


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


def create_app(
    db_path: Path | None = None,
    provider_factory=OllamaProvider,
    reminder_clock=now,
    mineru_factory=MinerUProvider,
    qq_adapter=None,
    qq_background=True,
    chaoxing_background=True,
    model_secrets=None,
    api_transport=None,
) -> FastAPI:
    engine = make_engine(db_path or Path(os.environ.get("CAMPUSINBOX_DB_PATH", str(DEFAULT_DB))))
    manager = ModelManager(engine, provider_factory, model_secrets, api_transport)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize(engine)
        if qq_background:
            qq_core.start()
        if chaoxing_background:
            chaoxing.resume_if_enabled()
        push_status = push.status()
        if push_status["enabled"] or push_status["result"] == "sending":
            push.start()
        try:
            yield
        finally:
            qq_core.close()
            chaoxing.shutdown()
            phone.stop()
            push.stop()
            engine.dispose()

    qq_core = QQCore(
        engine, provider_factory, qq_adapter or OneBotAdapter(Path(engine.url.database).parent)
    )
    chaoxing = ChaoxingRuntime(engine, provider_factory, Path(engine.url.database).parent)
    phone = phone_reminders.PhoneReminders(engine)
    push = wechat_push.WechatPush(engine, reminder_clock)
    application = FastAPI(title="CampusInbox API", version="0.1.0", lifespan=lifespan)
    application.state.engine = engine
    application.state.qq_core = qq_core
    application.state.chaoxing = chaoxing
    application.state.phone_reminders = phone
    application.state.wechat_push = push
    application.include_router(inference_router(manager))
    application.include_router(qq_router(qq_core))
    application.include_router(onebot_router(qq_core))
    application.include_router(attachments.router(engine, mineru_factory))
    application.include_router(data_export.router(engine))
    application.include_router(legacy_migration.router(engine))
    application.include_router(chaoxing_router(chaoxing))
    application.include_router(browser_bridge_router(chaoxing))
    application.include_router(task_assistant_router(engine, provider_factory))
    application.include_router(phone_reminders.control_router(phone))
    application.include_router(wechat_push.router(push))

    @application.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "campus-inbox"}

    @application.get("/api/inbox", response_model=list[InboxItem], tags=["inbox"])
    def list_inbox(
        session: WriteDB,
        status: Literal["pending", "accepted", "ignored", "archived", "all"] = "pending",
    ):
        from campus_inbox.connectors.chaoxing import archive_expired

        archive_expired(session)
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
            session, services.import_message(session, body, manager.provider, use_active_model=True)
        )

    @application.post(
        "/api/inbox/{suggestion_id}/analyze", response_model=InboxItem, tags=["inbox"]
    )
    def reanalyze(suggestion_id: str, session: WriteDB):
        suggestion = services.get_or_404(session, Suggestion, suggestion_id)
        return services.inbox_item(
            session, services.retry(session, suggestion, manager.provider, use_active_model=True)
        )

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
        suggestion_id: str,
        operation: Literal["ignore", "reopen", "restore-archive"],
        session: WriteDB,
    ):
        suggestion = services.get_or_404(session, Suggestion, suggestion_id)
        if operation == "reopen":
            drafts = splitting.drafts(session, suggestion_id)
            if drafts and not any(draft.status == "pending" for draft in drafts):
                raise HTTPException(409, "拆分通知请使用各条目中的恢复待审核操作。")
        if (operation, suggestion.status) in {
            ("restore-archive", "archived"),
            ("reopen", "ignored"),
        }:
            from campus_inbox.connectors.chaoxing import require_restorable

            require_restorable(session, suggestion)
        expected, target = {
            "ignore": ("pending", "ignored"),
            "reopen": ("ignored", "pending"),
            "restore-archive": ("archived", "pending"),
        }[operation]
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

    @application.get("/api/tasks", response_model=list[TaskListItem], tags=["tasks"])
    def list_tasks(session: DB):
        return services.list_tasks(session, reminder_clock())

    @application.post("/api/tasks", response_model=TaskRead, tags=["tasks"])
    def create_task(body: TaskFields, session: WriteDB):
        return services.manual_create(session, body)

    @application.get("/api/tasks/{task_id}", response_model=TaskDetail, tags=["tasks"])
    def task_detail(task_id: str, session: DB):
        return services.detail(session, services.get_or_404(session, Task, task_id))

    @application.post(
        "/api/tasks/{task_id}/recognize", response_model=TaskRecognitionPreview, tags=["tasks"]
    )
    def recognize_task(task_id: str, body: TaskRecognitionRequest, session: DB):
        return services.recognize_task(
            session, services.get_or_404(session, Task, task_id), body, manager.provider
        )

    @application.get("/api/tasks/{task_id}/material-suggestions", tags=["tasks"])
    def suggest_materials(task_id: str, session: DB):
        task = services.get_or_404(session, Task, task_id)
        return {"items": material_suggestions.candidates(session, task)}

    @application.patch("/api/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
    def edit_task(task_id: str, body: TaskUpdate, session: WriteDB):
        return services.update_task(session, services.get_or_404(session, Task, task_id), body)

    @application.post(
        "/api/tasks/{task_id}/platform-status", response_model=TaskRead, tags=["tasks"]
    )
    def adopt_platform_status(task_id: str, body: PlatformStatusUpdate, session: WriteDB):
        from datetime import timedelta

        task = services.get_or_404(session, Task, task_id)
        observation = platform_status.for_task(session, task)
        if (
            task.version != body.expected_version
            or not observation
            or observation.id != body.observation_id
        ):
            raise HTTPException(409, "任务或平台状态已变化，请刷新后重新核对。")
        target = platform_status.suggested_for_task(observation.status, task.status)
        age = now() - platform_status.aware(observation.checked_at)
        if age > timedelta(hours=24) or age < timedelta(seconds=-2):
            raise HTTPException(409, "平台状态已过期，请先重新同步。")
        if not target or task.status in {"cancelled", "archived"}:
            raise HTTPException(422, "此平台状态不能直接采用，请手动核对。")
        if task.status == target:
            return task
        before = services.snapshot(task)
        task.status, task.version, task.updated_at = target, task.version + 1, now()
        session.add(task)
        session.add(
            TaskEvent(
                task_id=task.id,
                message_id=task.message_id,
                operation="platform_status",
                before=before,
                after={
                    **services.snapshot(task),
                    "platform_observation": platform_status.public(observation)
                    | {"checked_at": platform_status.aware(observation.checked_at).isoformat()},
                },
            )
        )
        session.commit()
        session.refresh(task)
        return task

    @application.put("/api/tasks/{task_id}/materials", response_model=TaskRead, tags=["tasks"])
    def update_materials(task_id: str, body: MaterialsUpdate, session: WriteDB):
        task = services.get_or_404(session, Task, task_id)
        if task.version != body.expected_version:
            raise HTTPException(409, "任务已被其他操作修改，请刷新后重试。")
        materials = session.get(TaskMaterials, task_id) or TaskMaterials(task_id=task_id)
        before = list(materials.items)
        after = [item.model_dump() for item in body.items]
        if before == after:
            return task
        previous_version = task.version
        task.version += 1
        task.updated_at = now()
        materials.items = after
        session.add(materials)
        session.add(task)
        session.add(
            TaskEvent(
                task_id=task.id,
                message_id=task.message_id,
                operation="materials",
                before={"materials": before, "version": previous_version},
                after={"materials": after, "version": task.version},
            )
        )
        session.commit()
        session.refresh(task)
        return task

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

    @application.get("/api/calendar", response_model=CalendarFeed, tags=["calendar"])
    def calendar_feed(
        session: DB,
        start: date,
        end: date,
        timezone: str = "Asia/Shanghai",
        status: calendar.CalendarScope = "active",
        course: Annotated[str, Query(max_length=100)] = "",
    ):
        return calendar.feed(session, start, end, timezone, status, course, reminder_clock())

    @application.get("/api/calendar/export.ics", tags=["calendar"])
    def calendar_export(
        session: DB,
        start: date,
        end: date,
        timezone: str = "Asia/Shanghai",
        status: calendar.CalendarScope = "active",
        course: Annotated[str, Query(max_length=100)] = "",
    ):
        result = calendar.feed(session, start, end, timezone, status, course, reminder_clock())
        return Response(
            calendar.export(result),
            media_type="text/calendar",
            headers={
                "Content-Disposition": f'attachment; filename="{calendar.filename(start, end)}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.get("/api/reminders", response_model=ReminderFeed, tags=["reminders"])
    def reminder_feed(session: DB):
        return reminders.feed(session, reminder_clock())

    @application.get("/api/settings/reminders", response_model=ReminderConfig, tags=["reminders"])
    def reminder_config(session: DB):
        return reminders.config(session)

    @application.put("/api/settings/reminders", response_model=ReminderConfig, tags=["reminders"])
    def save_reminders(body: ReminderConfig, session: WriteDB):
        settings = session.get(ReminderSettings, 1) or ReminderSettings()
        for key, value in body.model_dump().items():
            setattr(settings, key, value)
        session.add(settings)
        session.commit()
        return body

    @application.post(
        "/api/reminders/{reminder_id}/{operation}", response_model=ReminderRead, tags=["reminders"]
    )
    def reminder_action(
        reminder_id: str,
        operation: Literal["read", "snooze", "restore"],
        body: ReminderAction,
        session: WriteDB,
    ):
        return reminders.act(session, reminder_id, operation, body, reminder_clock())

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
        choice = session.get(InferenceSettings, 1) or InferenceSettings()
        choice.revision += 1
        session.add(choice)
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
