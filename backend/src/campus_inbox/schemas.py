"""Validated API contracts, separate from database tables."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

TaskStatus = Literal["todo", "in_progress", "completed", "cancelled", "archived"]
TaskKind = Literal["homework", "exam", "event", "other"]


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


Timestamp = Annotated[datetime, AfterValidator(aware)]


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ImportMessage(BaseModel):
    text: str = Field(min_length=2, max_length=20000)
    source: str = Field(default="手动粘贴", min_length=1, max_length=100)
    sent_at: datetime
    timezone: str = "Asia/Shanghai"

    @field_validator("text", "source")
    @classmethod
    def strip_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @field_validator("sent_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
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
    status: TaskStatus
    submission_platform: str
    submission_method: str
    file_requirements: str
    location: str
    remarks: str
    version: int
    created_at: Timestamp
    updated_at: Timestamp


class TaskUpdate(TaskFields):
    expected_version: int = Field(ge=1)
    status: TaskStatus = "todo"


class MessageRead(ReadModel):
    id: str
    text: str
    source: str
    sent_at: Timestamp
    timezone: str
    received_at: Timestamp


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

    @field_validator("due_at")
    @classmethod
    def aware_due(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("截止时间必须包含时区")
        return value


class DeadlinePreview(BaseModel):
    id: str
    suggestion_id: str
    task: TaskRead
    proposed_due_at: Timestamp
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


class TaskDetail(BaseModel):
    task: TaskRead
    source: MessageRead
    sources: list[MessageRead]
    deadline_changes: list[DeadlineChangeRead]
    cancellation_changes: list[CancellationChangeRead]
    events: list[EventRead]
    evidence: dict
    method: str


class SplitDraftRead(ReadModel):
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
    status: Literal["processing", "pending", "accepted", "ignored"]
    action: Literal["create", "review", "ignore"]
    method: str
    fields: dict
    evidence: dict
    warnings: list[str]
    message: MessageRead
    matched_tasks: list[TaskRead]
    task_id: str | None = None
    updated_at: Timestamp
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
