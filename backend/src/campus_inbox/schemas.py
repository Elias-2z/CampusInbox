"""Validated API contracts, separate from database tables."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

TaskStatus = Literal[
    "todo", "in_progress", "done", "submitted", "completed", "cancelled", "archived"
]
TaskKind = Literal["homework", "exam", "event", "other"]


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


Timestamp = Annotated[datetime, AfterValidator(aware)]


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ImportMessage(BaseModel):
    text: str = Field(min_length=2, max_length=20000)
    source: str = Field(default="手动粘贴", min_length=1, max_length=100)
    sent_at: datetime | None
    timezone: str = "Asia/Shanghai"
    attachment_id: str | None = None

    @field_validator("text", "source")
    @classmethod
    def strip_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @field_validator("sent_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("发送时间必须包含时区")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("无效时区") from exc
        return value


class TaskFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    course: str = Field(default="", max_length=100)
    kind: TaskKind = "other"
    description: str = Field(default="", max_length=20000)
    due_at: datetime | None = None
    due_date: date | None = None
    submission_platform: str = Field(default="", max_length=100)
    submission_method: str = Field(default="", max_length=300)
    file_requirements: str = Field(default="", max_length=2000)
    location: str = Field(default="", max_length=300)
    remarks: str = Field(default="", max_length=2000)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("任务名称不能为空")
        return value.strip()

    @field_validator("due_at")
    @classmethod
    def due_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("截止时间必须包含时区")
        return value

    @field_validator("due_date", mode="before")
    @classmethod
    def due_date_is_date(cls, value):
        if (
            isinstance(value, datetime)
            or (value is not None and not isinstance(value, (str, date)))
            or (isinstance(value, str) and len(value) != 10)
        ):
            raise ValueError("仅日期截止须使用 YYYY-MM-DD，不包含时间")
        return value

    @model_validator(mode="after")
    def one_deadline(self):
        if self.due_at is not None and self.due_date is not None:
            raise ValueError("截止日期与截止时间只能填写一项")
        return self


class AcceptSuggestion(TaskFields):
    allow_duplicate: bool = False


class TaskRead(ReadModel):
    id: str
    suggestion_id: str
    message_id: str
    title: str
    course: str
    kind: TaskKind
    description: str
    due_at: Timestamp | None
    due_date: date | None = None
    status: TaskStatus
    submission_platform: str
    submission_method: str
    file_requirements: str
    location: str
    remarks: str
    version: int
    created_at: Timestamp
    updated_at: Timestamp


class TaskListItem(TaskRead):
    recognition_method: str = ""
    checkin_closed: bool


class TaskUpdate(TaskFields):
    expected_version: int = Field(ge=1)
    status: TaskStatus = "todo"


class AttachmentInfo(ReadModel):
    id: str
    filename: str
    size: int
    status: Literal["parsing", "ready", "failed"]


class AttachmentRead(AttachmentInfo):
    text: str
    error: str


class MessageRead(ReadModel):
    id: str
    text: str
    source: str
    sent_at: Timestamp | None
    timezone: str
    received_at: Timestamp
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    source_context: dict | None = None


class EventRead(ReadModel):
    id: str
    message_id: str
    operation: str
    before: dict
    after: dict
    actor: str
    created_at: Timestamp


class DeadlinePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    due_at: datetime | None = None
    due_date: date | None = None

    @field_validator("due_at")
    @classmethod
    def aware_due(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("截止时间必须包含时区")
        return value

    @field_validator("due_date", mode="before")
    @classmethod
    def date_only(cls, value):
        return TaskFields.due_date_is_date(value)

    @model_validator(mode="after")
    def one_deadline(self):
        if self.due_at is not None and self.due_date is not None:
            raise ValueError("截止日期与截止时间只能填写一项")
        return self


class DeadlinePreview(BaseModel):
    id: str
    suggestion_id: str
    task: TaskRead
    proposed_due_at: Timestamp | None
    proposed_due_date: date | None = None
    warnings: list[str]
    evidence: dict
    created_at: Timestamp


class DeadlineChangeRead(BaseModel):
    suggestion_id: str
    task: TaskRead
    event_id: str
    undo_event_id: str | None
    applied_version: int
    can_undo: bool
    reverted: bool


class CancellationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1)


class CancellationPreview(BaseModel):
    id: str
    suggestion_id: str
    task: TaskRead
    proposed_status: Literal["cancelled"] = "cancelled"
    created_at: Timestamp


class CancellationChangeRead(DeadlineChangeRead):
    pass


MergeField = Literal[
    "title",
    "course",
    "kind",
    "description",
    "due_at",
    "due_date",
    "submission_platform",
    "submission_method",
    "file_requirements",
    "location",
    "remarks",
]


class MergePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1)
    fields: list[MergeField] = Field(default_factory=list, max_length=11)


class MergePreview(BaseModel):
    id: str
    task: TaskRead
    before_fields: dict
    after_fields: dict
    warnings: list[str]
    created_at: Timestamp


class MergeChangeRead(BaseModel):
    id: str
    suggestion_id: str
    split_draft_id: str | None
    task: TaskRead
    event_id: str
    undo_event_id: str | None
    applied_version: int
    reverted: bool
    can_undo: bool


class MaterialItem(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    done: bool = False

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("材料名称不能为空")
        return value


class MaterialsUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    items: list[MaterialItem] = Field(max_length=100)


class TaskDetail(BaseModel):
    legacy: dict | None = None
    task: TaskRead
    platform_observation: dict | None = None
    checkin_closed: bool = False
    materials: list[MaterialItem] = Field(default_factory=list)
    source: MessageRead
    sources: list[MessageRead]
    deadline_changes: list[DeadlineChangeRead]
    cancellation_changes: list[CancellationChangeRead]
    merge_changes: list[MergeChangeRead]
    events: list[EventRead]
    evidence: dict
    method: str


class PlatformStatusUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=100)


class SplitDraftRead(ReadModel):
    merge_change: MergeChangeRead | None = None
    id: str
    position: int
    quote: str
    status: Literal["pending", "accepted", "ignored"]
    can_create: bool
    fields: dict
    evidence: dict
    warnings: list[str]
    matched_tasks: list[TaskRead]
    task_id: str | None
    updated_at: Timestamp


class SplitAccept(AcceptSuggestion):
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def aware_revision(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("草稿修订时间必须包含时区")
        return value


class SplitEdit(TaskFields):
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def aware_revision(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("草稿修订时间必须包含时区")
        return value


class SplitDecision(BaseModel):
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def aware_revision(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("草稿修订时间必须包含时区")
        return value


class InboxItem(BaseModel):
    id: str
    status: Literal["processing", "pending", "accepted", "ignored", "archived"]
    action: Literal["create", "review", "ignore"]
    method: str
    fields: dict
    evidence: dict
    warnings: list[str]
    message: MessageRead
    activity_schedule: list[str] = Field(default_factory=list)
    matched_tasks: list[TaskRead]
    task_id: str | None = None
    updated_at: Timestamp
    merge_change: MergeChangeRead | None = None
    cancellation_supported: bool
    cancellation_change: CancellationChangeRead | None = None
    split_supported: bool
    split_items: list[SplitDraftRead]
    deadline_supported: bool
    deadline_change: DeadlineChangeRead | None = None


class LocalModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = Field(default="", max_length=200, pattern=r"^[\w./:\-]*$")
    timeout_seconds: int = Field(default=45, ge=3, le=120)

    @field_validator("base_url")
    @classmethod
    def local_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("第一版仅支持本机 Ollama，请使用 localhost 或回环 IP 地址")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("无效端口") from exc
        return value.rstrip("/")


class ReminderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    lead_minutes: int = Field(default=1440, ge=5, le=10080)
    overdue_enabled: bool = True


class ReminderRead(BaseModel):
    id: str
    task: TaskRead
    phase: Literal["upcoming", "overdue"]
    status: Literal["unread", "read", "snoozed"]
    due_at: Timestamp | None
    due_date: date | None = None
    read_at: Timestamp | None
    snoozed_until: Timestamp | None
    version: int


class ReminderFeed(BaseModel):
    config: ReminderConfig
    items: list[ReminderRead]
    evaluated_at: Timestamp


class ReminderAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    snooze_minutes: Literal[15, 30, 60, 120] = 30


class CalendarFeed(BaseModel):
    start: date
    end: date
    timezone: str
    tasks: list[TaskRead]
    undated: list[TaskRead]
