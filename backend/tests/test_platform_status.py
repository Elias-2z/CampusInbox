"""Platform status must not be inferred from names or stale observations."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from campus_inbox.connectors.platform_status import (
    PlatformObservation,
    identity,
    observe,
    suggested,
    suggested_for_task,
)
from campus_inbox.database import initialize, make_engine


def job(**updates):
    return {
        "kind": "task_center",
        "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
        "platform_status": "未提交",
        "captured_at": datetime.now(UTC).isoformat(),
        **updates,
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/mooc2/work/view?courseid=1&classid=2&workid=3",
        "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3&workid=4",
        "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&workid=3",
        "https://user@mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
    ],
)
def test_identity_rejects_ambiguous_or_foreign_urls(url):
    assert not identity("account", job(url=url))


def test_identity_separates_accounts_and_never_infers_submitted_from_finished():
    assert identity("one", job()) != identity("two", job())
    assert suggested("已完成") is None
    assert suggested("待批阅") == "submitted"
    assert suggested("已交卷") == "submitted"
    assert suggested("已阅卷") == "submitted"
    assert suggested("已退回重做") == "todo"
    assert suggested("未交卷") is None
    assert suggested("待重交") == "todo"
    assert suggested_for_task("未提交", "done") is None
    assert suggested_for_task("未提交", "submitted") == "todo"
    assert suggested_for_task("待重交", "done") == "todo"


def test_old_or_future_observations_cannot_replace_fresh_status(tmp_path):
    engine = make_engine(tmp_path / "status.db")
    initialize(engine)
    stamp = datetime.now(UTC)
    try:
        with Session(engine) as session:
            observe(
                session, "account", job(platform_status="待批阅", captured_at=stamp.isoformat())
            )
            session.commit()
            first = session.get(PlatformObservation, identity("account", job()))
            original_id = first.id
            observe(
                session,
                "account",
                job(platform_status="未提交", captured_at=(stamp - timedelta(days=1)).isoformat()),
            )
            observe(
                session,
                "account",
                job(platform_status="未提交", captured_at=(stamp + timedelta(days=1)).isoformat()),
            )
            session.commit()
            assert session.get(PlatformObservation, first.identity).id == original_id
            assert session.get(PlatformObservation, first.identity).status == "待批阅"
    finally:
        engine.dispose()


def test_new_empty_status_invalidates_prior_submitted_observation(tmp_path):
    engine = make_engine(tmp_path / "status.db")
    initialize(engine)
    stamp = datetime.now(UTC)
    try:
        with Session(engine) as session:
            observe(
                session,
                "account",
                job(
                    platform_status="已提交", captured_at=(stamp - timedelta(seconds=1)).isoformat()
                ),
            )
            session.commit()
            row = session.get(PlatformObservation, identity("account", job()))
            old_id = row.id
            observe(session, "account", job(platform_status="", captured_at=stamp.isoformat()))
            session.commit()
            session.refresh(row)
            assert row.id != old_id and row.status == ""
            assert suggested(row.status) is None
    finally:
        engine.dispose()
