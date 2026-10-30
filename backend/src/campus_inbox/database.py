"""Local storage setup and one session per request."""

from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from campus_inbox.models import ModelSettings, Suggestion, now

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
    with Session(engine) as session:
        if session.get(ModelSettings, 1) is None:
            session.add(ModelSettings())
        # No background jobs survive a restart. Retain the message and allow retry.
        for suggestion in session.exec(select(Suggestion).where(Suggestion.status == "processing")):
            suggestion.status = "pending"
            suggestion.warnings = [*suggestion.warnings, "上次识别被中断，可重新识别或手动填写。"]
            suggestion.updated_at = now()
            session.add(suggestion)
        session.commit()
