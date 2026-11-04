"""Persistence only. All timestamps use timezone-aware UTC."""

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    text: str
    source: str = "手动粘贴"
    sent_at: datetime
    timezone: str = "Asia/Shanghai"
    received_at: datetime = Field(default_factory=now)
    dedupe_key: str = Field(unique=True, index=True)


class Suggestion(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    message_id: str = Field(foreign_key="message.id", unique=True, index=True)
    status: str = "processing"
    action: str = "create"
    method: str = "rule"
    fields: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    warnings: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Attachment(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    filename: str
    size: int
    status: str = "parsing"
    text: str = ""
    error: str = ""
    batch_id: str = ""
    message_id: str | None = Field(default=None, foreign_key="message.id", index=True)
    created_at: datetime = Field(default_factory=now)


class LocalOCRSource(SQLModel, table=True):
    attachment_id: str = Field(foreign_key="attachment.id", primary_key=True)
    cloud_consent: bool = False


class LocalAttachmentExtraction(SQLModel, table=True):
    """Durable local extraction request for a collected original."""

    attachment_id: str = Field(foreign_key="attachment.id", primary_key=True)
    state: str = Field(default="pending", index=True)


class Task(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    title: str
    course: str = ""
    kind: str = "other"
    description: str = ""
    due_at: datetime | None = None
    due_date: date | None = None
    status: str = Field(default="todo", index=True)
    submission_platform: str = ""
    submission_method: str = ""
    file_requirements: str = ""
    location: str = ""
    remarks: str = ""
    version: int = 1
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class TaskMaterials(SQLModel, table=True):
    task_id: str = Field(foreign_key="task.id", primary_key=True)
    items: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class TaskEvent(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    message_id: str = Field(foreign_key="message.id")
    operation: str
    before: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    after: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    actor: str = "user"
    created_at: datetime = Field(default_factory=now)


class ModelSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""
    timeout_seconds: int = 45


class DeadlineProposal(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    task_version: int
    before_due_at: datetime | None = None
    proposed_due_at: datetime
    suggestion_updated_at: datetime
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)


class DeadlineChange(SQLModel, table=True):
    suggestion_id: str = Field(foreign_key="suggestion.id", primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    proposal_id: str = Field(foreign_key="deadlineproposal.id", unique=True)
    event_id: str = Field(foreign_key="taskevent.id", unique=True)
    applied_version: int
    undo_event_id: str | None = Field(default=None, foreign_key="taskevent.id", unique=True)


class DateDeadlineProposal(SQLModel, table=True):
    """Deadline review that preserves civil dates without inventing a time."""

    id: str = Field(default_factory=uid, primary_key=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    task_version: int
    before_due_at: datetime | None = None
    before_due_date: date | None = None
    proposed_due_at: datetime | None = None
    proposed_due_date: date | None = None
    suggestion_updated_at: datetime
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)


class DateDeadlineChange(SQLModel, table=True):
    suggestion_id: str = Field(foreign_key="suggestion.id", primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    proposal_id: str = Field(foreign_key="datedeadlineproposal.id", unique=True)
    event_id: str = Field(foreign_key="taskevent.id", unique=True)
    applied_version: int
    undo_event_id: str | None = Field(default=None, foreign_key="taskevent.id", unique=True)


class CancellationProposal(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    task_version: int
    before_status: str
    suggestion_updated_at: datetime
    created_at: datetime = Field(default_factory=now)


class CancellationChange(SQLModel, table=True):
    suggestion_id: str = Field(foreign_key="suggestion.id", primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    proposal_id: str = Field(foreign_key="cancellationproposal.id", unique=True)
    event_id: str = Field(foreign_key="taskevent.id", unique=True)
    applied_version: int
    undo_event_id: str | None = Field(default=None, foreign_key="taskevent.id", unique=True)


class SplitDraft(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    position: int
    quote: str
    status: str = "pending"
    can_create: bool = True
    fields: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    warnings: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    task_id: str | None = Field(default=None, foreign_key="task.id", unique=True)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class MergeProposal(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    source_key: str = Field(index=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    split_draft_id: str | None = Field(default=None, foreign_key="splitdraft.id")
    source_updated_at: datetime
    task_id: str = Field(foreign_key="task.id", index=True)
    task_version: int
    before_fields: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    after_fields: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)


class MergeChange(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    source_key: str = Field(unique=True, index=True)
    suggestion_id: str = Field(foreign_key="suggestion.id", index=True)
    split_draft_id: str | None = Field(default=None, foreign_key="splitdraft.id", unique=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    proposal_id: str = Field(foreign_key="mergeproposal.id", unique=True)
    event_id: str = Field(foreign_key="taskevent.id", unique=True)
    applied_version: int
    undo_event_id: str | None = Field(default=None, foreign_key="taskevent.id", unique=True)


class ReminderSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    enabled: bool = True
    lead_minutes: int = 1440
    overdue_enabled: bool = True


class ReminderState(SQLModel, table=True):
    id: str = Field(primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    due_at: datetime
    phase: str
    is_read: bool = False
    read_at: datetime | None = None
    snoozed_until: datetime | None = None
    version: int = 0
    updated_at: datetime = Field(default_factory=now)


class DateReminderState(SQLModel, table=True):
    id: str = Field(primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    due_date: date
    phase: str
    is_read: bool = False
    read_at: datetime | None = None
    snoozed_until: datetime | None = None
    version: int = 0
    updated_at: datetime = Field(default_factory=now)
