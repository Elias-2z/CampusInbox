"""Local storage setup and one session per request."""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from campus_inbox.models import Attachment, ModelSettings, ReminderSettings, Suggestion, now

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = BACKEND_ROOT / "data" / "campus-inbox.db"


def make_engine(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 15}
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def initialize(engine) -> None:
    SQLModel.metadata.create_all(engine)
    migrate_task_source_index(engine)
    with Session(engine) as session:
        if session.get(ModelSettings, 1) is None:
            session.add(ModelSettings())
        if session.get(ReminderSettings, 1) is None:
            session.add(ReminderSettings())
        # No background jobs survive a restart. Retain the message and allow retry.
        for suggestion in session.exec(select(Suggestion).where(Suggestion.status == "processing")):
            suggestion.status = "pending"
            suggestion.warnings = [*suggestion.warnings, "上次识别被中断，可重新识别或手动填写。"]
            suggestion.updated_at = now()
            session.add(suggestion)
        for attachment in session.exec(
            select(Attachment).where(Attachment.status == "parsing", Attachment.batch_id == "")
        ):
            attachment.status = "failed"
            attachment.error = "上次上传被中断，原文件已保留，可以重新解析。"
            session.add(attachment)
        session.commit()


def migrate_task_source_index(engine) -> None:
    """v2: remove only the known one-task-per-notice unique index, without rebuilding data."""
    with engine.connect() as connection:
        indexes = connection.exec_driver_sql("PRAGMA index_list(task)").fetchall()
        legacy = next(
            (row for row in indexes if row[1] == "ix_task_suggestion_id" and row[2]), None
        )
        if legacy is None:
            return
        columns = connection.exec_driver_sql("PRAGMA index_info(ix_task_suggestion_id)").fetchall()
        if [row[2] for row in columns] != ["suggestion_id"] or legacy[3] != "c":
            raise RuntimeError(
                "Unrecognized task source index; migration stopped without data changes"
            )
    path = Path(engine.url.database)
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f"before-split-index-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    os.chmod(backup, 0o600)
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.exec_driver_sql("DROP INDEX IF EXISTS ix_task_suggestion_id")
        connection.exec_driver_sql("CREATE INDEX ix_task_suggestion_id ON task (suggestion_id)")
