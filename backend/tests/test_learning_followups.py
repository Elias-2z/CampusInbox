"""Learning-platform precision, source bindings and explicit ignored-message reuse."""

import base64
import io
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from campus_inbox import attachments, merging, services
from campus_inbox.connectors import platform_status
from campus_inbox.connectors.chaoxing import ChaoxingEvent, ChaoxingIntake
from campus_inbox.connectors.chaoxing_runtime import ChaoxingRuntime
from campus_inbox.database import initialize, make_engine
from campus_inbox.models import Attachment, LocalAttachmentExtraction, Suggestion, Task
from campus_inbox.providers import OllamaProvider
from campus_inbox.schemas import AcceptSuggestion, ImportMessage, MergePreviewRequest, TaskFields


@pytest.fixture
def intake(tmp_path):
    engine = make_engine(tmp_path / "followups.db")
    initialize(engine)
    yield ChaoxingIntake(engine, OllamaProvider)
    engine.dispose()


def job(**changes):
    stamp = datetime.now(UTC) - timedelta(seconds=2)
    return {
        "kind": "task_center",
        "message_id": "work-one",
        "title": "第一次作业",
        "text": "数据库：请提交第一次作业。",
        "course": "数据库",
        "task_type": "assignment",
        "published_at": "2026-11-06T01:00:00Z",
        "captured_at": stamp.isoformat(),
        "platform_status": "未提交",
        "direction": "received",
        "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
        "deadline": "2099-12-10T20:00:00+08:00",
        "field_evidence": {"title": "第一次作业", "deadline": "截止时间：2099-12-10 20:00"},
        **changes,
    }


def receive(intake, payload):
    event_id, _ = intake.receive("account", payload)
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        return session.get(ChaoxingEvent, event_id).suggestion_id


def linked(intake):
    payload = job()
    source_id = receive(intake, payload)
    with Session(intake.engine) as session:
        target = services.manual_create(
            session, TaskFields(title="第一次作业", course="数据库", kind="homework")
        )
        proposal = merging.preview(
            session, source_id, MergePreviewRequest(task_id=target.id, fields=[])
        )
        change = merging.confirm(session, proposal.id)
        return target.id, source_id, change.id, payload


def test_platform_clock_refines_same_day_text_date(intake):
    source_id = receive(intake, job(text="数据库：2099年12月10日提交第一次作业。"))
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        assert source.fields["due_at"] == "2099-12-10T12:00:00+00:00"
        assert source.fields["due_date"] is None
        assert source.evidence["due_at"]["method"] == "connector"


def test_merged_platform_source_exposes_status_and_undo_removes_binding(intake):
    task_id, _, change_id, _ = linked(intake)
    with Session(intake.engine) as session:
        target = session.get(Task, task_id)
        assert platform_status.for_task(session, target).status == "未提交"
        merging.undo(session, change_id)
        assert platform_status.for_task(session, target) is None


@pytest.mark.parametrize("content_changed", [False, True])
def test_status_only_update_of_merged_source_does_not_duplicate_inbox(intake, content_changed):
    task_id, source_id, _, payload = linked(intake)
    update = {**payload, "platform_status": "待重交", "captured_at": datetime.now(UTC).isoformat()}
    if content_changed:
        update["text"] += "文件命名为学号姓名。"
    _, created = intake.receive("account", update)
    assert created is content_changed
    with Session(intake.engine) as session:
        assert len(session.exec(select(ChaoxingEvent)).all()) == (2 if content_changed else 1)
        if not content_changed:
            assert platform_status.for_task(session, session.get(Task, task_id)).status == "待重交"
            assert session.get(Suggestion, source_id).status == "accepted"


def test_two_tasks_bound_to_one_platform_identity_are_not_auto_synchronized(intake):
    task_id, source_id, _, _ = linked(intake)
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        duplicate = Task(
            title="第一次作业",
            course="数据库",
            kind="homework",
            suggestion_id=source.id,
            message_id=source.message_id,
        )
        session.add(duplicate)
        session.commit()
        assert platform_status.for_task(session, session.get(Task, task_id)) is None
        assert platform_status.for_task(session, duplicate) is None


@pytest.mark.parametrize("automatic", [False, True])
def test_only_explicit_manual_reimport_restores_ignored_notice(intake, automatic):
    body = ImportMessage(
        text="数据库：2099年12月10日20:00前提交第一次作业。",
        sent_at=datetime(2026, 11, 6, tzinfo=UTC),
    )
    key = "chaoxing:automatic-repeat" if automatic else None
    with Session(intake.engine) as session:
        source = services.import_message(session, body, OllamaProvider, dedupe_key=key)
        source.status = "ignored"
        session.add(source)
        session.commit()
        repeated = services.import_message(session, body, OllamaProvider, dedupe_key=key)
        assert repeated.id == source.id
        assert repeated.status == ("ignored" if automatic else "pending")
        assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize("ignored", [False, True])
def test_completed_attachment_refreshes_only_pending_suggestion(
    intake, tmp_path, monkeypatch, ignored
):
    runtime = ChaoxingRuntime(intake.engine, OllamaProvider, tmp_path)
    runtime.enabled = True
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)
    monkeypatch.setattr(
        attachments,
        "extract_local_ocr",
        lambda _: ("数据库：2099年12月10日20:00前提交实验报告。", ""),
    )
    source_id = receive(
        intake,
        job(
            kind="course_notice",
            title="课程通知",
            text="请查看附件说明。",
            attachments=[
                {
                    "name": "要求.pdf",
                    "url": "https://example.test/requirements.pdf",
                    "content_base64": base64.b64encode(b"synthetic-pdf").decode(),
                }
            ],
        ),
    )
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        assert not source.fields.get("due_at")
        if ignored:
            source.status = "ignored"
            session.add(source)
            session.commit()
    runtime.process_local_attachments()
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        assert source.fields.get("due_at") == (None if ignored else "2099-12-10T12:00:00+00:00")
        assert source.status == ("ignored" if ignored else "pending")
        assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize("stage", ["preview", "confirm"])
def test_distinct_platform_ids_cannot_merge_even_with_same_course_and_title(intake, stage):
    source_id = receive(intake, job())
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        target = services.accept(session, source, AcceptSuggestion(**source.fields))
        target_id = target.id
    payload = job(message_id="another-work", text="数据库：请提交第一次作业。请注意格式。")
    if stage == "preview":
        payload["url"] = payload["url"].replace("workid=3", "workid=4")
    second_id = receive(intake, payload)
    with Session(intake.engine) as session:
        second = session.get(Suggestion, second_id)
        target = session.get(Task, target_id)
        before = services.snapshot(target)
        if stage == "confirm":
            proposal = merging.preview(
                session, second_id, MergePreviewRequest(task_id=target_id, fields=[])
            )
            origin = session.exec(
                select(ChaoxingEvent).where(ChaoxingEvent.suggestion_id == second_id)
            ).one()
            origin.payload = {
                **origin.payload,
                "url": origin.payload["url"].replace("workid=3", "workid=4"),
            }
            session.add(origin)
            session.commit()
        assert services.inbox_item(session, second).matched_tasks == []
        with pytest.raises(HTTPException) as error:
            if stage == "confirm":
                merging.confirm(session, proposal.id)
            else:
                merging.preview(
                    session, second_id, MergePreviewRequest(task_id=target_id, fields=[])
                )
        assert error.value.status_code == 422
        session.refresh(target)
        assert services.snapshot(target) == before
        # These are genuinely different platform tasks, so creating the second is allowed.
        created = services.accept(session, second, AcceptSuggestion(**second.fields))
        assert created.id != target_id


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_word_text_enters_draft_only_when_the_whole_document_is_readable(
    intake, tmp_path, monkeypatch, partial, legacy
):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        body = "数据库：2099年12月10日20:00前提交实验报告。"
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + body
            + "</w:t>"
            + ("<w:drawing/>" if partial else "")
            + "</w:r></w:p></w:body></w:document>",
        )
    source_id = receive(
        intake,
        job(
            kind="course_notice",
            title="课程通知",
            text="请查看附件说明。",
            attachments=[
                {
                    "name": "要求.docx",
                    "url": "https://example.test/file.docx",
                    "content_base64": base64.b64encode(stream.getvalue()).decode(),
                }
            ],
        ),
    )
    runtime = ChaoxingRuntime(intake.engine, OllamaProvider, tmp_path)
    runtime.enabled = True
    if legacy:
        original = attachments.extract_local_docx
        monkeypatch.setattr(
            attachments,
            "extract_local_docx",
            lambda _: (
                "数据库：2099年12月10日20:00前提交实验报告。",
                "仅提取正文文字，图片等内容请对照原文件核对。",
            ),
        )
        runtime.process_local_attachments()
        monkeypatch.setattr(attachments, "extract_local_docx", original)
    runtime.process_local_attachments()
    with Session(intake.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        assert bool(attachment.error) is partial
        expected = None if partial else "2099-12-10T12:00:00+00:00"
        assert session.get(Suggestion, source_id).fields.get("due_at") == expected
        reanalyzed = services.retry(session, session.get(Suggestion, source_id), OllamaProvider)
        assert reanalyzed.fields.get("due_at") == expected
        assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize("ignored_during_failure", [False, True])
def test_refresh_failure_is_durable_and_retries_without_repeating_ocr(
    intake, tmp_path, monkeypatch, ignored_during_failure
):
    runtime = ChaoxingRuntime(intake.engine, OllamaProvider, tmp_path)
    runtime.enabled = True
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)
    calls = []

    def extract(_):
        calls.append(1)
        return "数据库：2099年12月10日20:00前提交实验报告。", ""

    monkeypatch.setattr(attachments, "extract_local_ocr", extract)
    source_id = receive(
        intake,
        job(
            kind="course_notice",
            title="课程通知",
            text="请查看附件说明。",
            attachments=[
                {
                    "name": "要求.pdf",
                    "url": "https://example.test/requirements.pdf",
                    "content_base64": base64.b64encode(b"synthetic-pdf").decode(),
                }
            ],
        ),
    )
    original = services.refresh_attachment_suggestion

    def fail(*_):
        raise RuntimeError("simulated refresh failure")

    monkeypatch.setattr(services, "refresh_attachment_suggestion", fail)
    with pytest.raises(RuntimeError):
        runtime.process_local_attachments()
    with Session(intake.engine) as session:
        assert session.exec(select(LocalAttachmentExtraction)).one().state == "awaiting_refresh"
        if ignored_during_failure:
            source = session.get(Suggestion, source_id)
            source.status = "ignored"
            session.add(source)
            session.commit()
    monkeypatch.setattr(services, "refresh_attachment_suggestion", original)
    restarted = ChaoxingRuntime(intake.engine, OllamaProvider, tmp_path)
    restarted.enabled = True
    restarted.process_local_attachments()
    with Session(intake.engine) as session:
        assert session.exec(select(LocalAttachmentExtraction)).one().state == "complete"
        source = session.get(Suggestion, source_id)
        assert source.fields.get("due_at") == (
            None if ignored_during_failure else "2099-12-10T12:00:00+00:00"
        )
        assert source.status == ("ignored" if ignored_during_failure else "pending")
        assert len(calls) == 1
