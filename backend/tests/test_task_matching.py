import pytest
from sqlmodel import Session

from campus_inbox import services
from campus_inbox.database import initialize, make_engine
from campus_inbox.schemas import TaskFields


@pytest.mark.parametrize(
    "incoming,existing,expected",
    [
        ("数据库", "数据库原理", False),
        ("英语", "大学英语", False),
        ("线性代数", "数据库", False),
        ("数据库", "", False),
        ("", "数据库", False),
        ("数据库", "数据库", True),
        (" 数据库 ", "数据库", True),
        ("", "", True),
    ],
)
def test_same_title_requires_same_course(tmp_path, incoming, existing, expected):
    engine = make_engine(tmp_path / "matching.db")
    initialize(engine)
    with Session(engine) as session:
        task = services.manual_create(session, TaskFields(title="第一次作业", course=existing))
        matches = services.matching_tasks(session, {"title": "第一次作业", "course": incoming})
        assert [t.id for t in matches] == ([task.id] if expected else [])
    engine.dispose()
