"""Prevent unsafe associations and retain evidence-based learning-platform deadlines."""

from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from campus_inbox import cancellations, deadlines, services
from campus_inbox.connectors.chaoxing import ChaoxingEvent
from campus_inbox.database import initialize, make_engine
from campus_inbox.extraction import Extraction, extract_rules, merge_model
from campus_inbox.models import Message, Suggestion
from campus_inbox.schemas import CancellationPreviewRequest, DeadlinePreviewRequest, TaskFields


@pytest.fixture
def session(tmp_path):
    engine = make_engine(tmp_path / "safety.db")
    initialize(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("operation", ["deadline", "cancel"])
@pytest.mark.parametrize("stage", ["preview", "confirm"])
def test_cross_course_change_rejected_before_any_task_write(session, operation, stage):
    target = services.manual_create(
        session, TaskFields(title="实验四报告", course="数据库原理", due_at="2026-12-08T12:00:00Z")
    )
    text = "数据库原理：实验四报告" + (
        "延期到2026年12月10日20:00。" if operation == "deadline" else "取消，不用再提交。"
    )
    stamp = datetime.fromisoformat("2026-11-06T01:00:00Z")
    parsed = extract_rules(text, stamp, "Asia/Shanghai", scoped_deadline=True)
    message = Message(text=text, sent_at=stamp, dedupe_key="chaoxing:isolated")
    session.add(message)
    session.flush()
    source = Suggestion(
        message_id=message.id,
        status="pending",
        action="review",
        fields=parsed.fields,
        evidence=parsed.evidence,
    )
    session.add(source)
    event = ChaoxingEvent(
        identity="isolated",
        account_id="audit",
        payload={"course": "数据库原理", "kind": "course_notice"},
    )
    session.add(event)
    session.commit()
    module = deadlines if operation == "deadline" else cancellations
    request = (DeadlinePreviewRequest if operation == "deadline" else CancellationPreviewRequest)(
        task_id=target.id
    )
    proposal = module.preview(session, source.id, request) if stage == "confirm" else None
    # Represents an old saved preview whose course restriction was never checked.
    source.fields = {**source.fields, "course": "线性代数"}
    event.payload = {**event.payload, "course": "线性代数"}
    session.add(event)
    session.add(source)
    session.commit()
    before = services.snapshot(target)
    with pytest.raises(HTTPException) as error:
        if proposal:
            module.confirm(session, proposal.id)
        else:
            module.preview(session, source.id, request)
    assert error.value.status_code == 422
    session.refresh(target)
    assert services.snapshot(target) == before
    assert source.status == "pending"


@pytest.mark.parametrize(
    "incoming,existing,expected",
    [
        ("第二次作业", "第一次作业", False),
        ("第2次作业", "第二次作业", True),
        ("作业2", "作业1", False),
        ("实验二报告", "实验一报告", False),
        ("第二章习题", "第三章习题", False),
        ("第二次实验报告", "第三次实验报告", False),
        ("第二次作业补充要求", "第二次作业", True),
    ],
)
def test_task_ordinals_separate_distinct_assignments(session, incoming, existing, expected):
    target = services.manual_create(session, TaskFields(title=existing, course="数据库"))
    result = services.matching_tasks(session, {"title": incoming, "course": "数据库"})
    assert [t.id for t in result] == ([target.id] if expected else [])


@pytest.mark.parametrize(
    "incoming,existing,expected",
    [
        ("homework", "exam", False),
        ("exam", "event", False),
        ("homework", "homework", True),
        ("other", "exam", True),
    ],
)
def test_known_task_kinds_must_agree(session, incoming, existing, expected):
    target = services.manual_create(
        session, TaskFields(title="期末考试", course="数据库", kind=existing)
    )
    matches = services.matching_tasks(
        session, {"title": "期末考试", "course": "数据库", "kind": incoming}
    )
    assert [t.id for t in matches] == ([target.id] if expected else [])


@pytest.mark.parametrize(
    "text",
    [
        "请完成实验报告，最晚时间是明天晚上八点。",
        "请完成实验报告，最迟提交时间为明天晚上八点。",
        "请整理实验报告，明晚八点收齐。",
    ],
)
def test_learning_natural_deadline_keeps_original_message_anchor(text):
    result = extract_rules(
        text, datetime.fromisoformat("2026-11-06T01:00:00Z"), "Asia/Shanghai", scoped_deadline=True
    )
    assert result.fields["due_at"] == "2026-11-07T12:00:00+00:00"


@pytest.mark.parametrize(
    "text,quote,expected",
    [
        ("请于明天晚上八点前提交实验报告。", "明天晚上八点", "2026-11-07T12:00:00+00:00"),
        ("请完成实验报告，开始时间是明天晚上八点。", "明天晚上八点", None),
        ("明天晚上八点上课，请完成实验报告。", "明天晚上八点", None),
        ("请提交实验报告，不是明天晚上八点截止。", "明天晚上八点", None),
        ("请提交实验报告，时间待定。", "明天晚上八点", None),
        ("实验报告明天或后天晚上八点截止。", "后天晚上八点", None),
    ],
)
def test_learning_model_deadline_requires_task_context_and_verbatim_evidence(text, quote, expected):
    result = merge_model(
        Extraction(fields={"due_at": None, "due_date": None}),
        {"due_text": quote},
        text,
        datetime.fromisoformat("2026-11-06T01:00:00Z"),
        "Asia/Shanghai",
        scoped_deadline=True,
    )
    assert result.fields["due_at"] == expected
