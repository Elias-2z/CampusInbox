"""Provenance for migrated records; application tables remain authoritative."""

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, select


class LegacyRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    namespace: str = Field(index=True)
    kind: str
    legacy_id: str
    content_hash: str
    target_id: str
    payload: dict = Field(sa_column=Column(JSON, nullable=False))


def context(session: Session, message_id: str):
    record = session.exec(
        select(LegacyRecord).where(
            LegacyRecord.kind == "messages", LegacyRecord.target_id == message_id
        )
    ).first()
    if record is None:
        return None
    from campus_inbox.legacy_migration import known_time

    data = record.payload
    return {
        "platform": "legacy",
        "namespace": record.namespace,
        "legacy_id": record.legacy_id,
        "time_known": known_time(data),
        "course": data.get("course", ""),
        "group_name": data.get("scope", ""),
    }


def task_record(session: Session, task_id: str):
    record = session.exec(
        select(LegacyRecord).where(LegacyRecord.kind == "tasks", LegacyRecord.target_id == task_id)
    ).first()
    return (
        {"namespace": record.namespace, "legacy_id": record.legacy_id, "record": record.payload}
        if record
        else None
    )
