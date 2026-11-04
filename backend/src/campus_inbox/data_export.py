"""Read-only exports of business records; configuration and credentials stay local."""

import json
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session, select

from campus_inbox.connectors.chaoxing import ChaoxingEvent
from campus_inbox.connectors.chaoxing_api import loopback
from campus_inbox.connectors.platform_status import PlatformObservation
from campus_inbox.connectors.qq import QQEvent
from campus_inbox.legacy_records import LegacyRecord
from campus_inbox.models import (
    Attachment,
    CancellationChange,
    CancellationProposal,
    DateDeadlineChange,
    DateDeadlineProposal,
    DateReminderState,
    DeadlineChange,
    DeadlineProposal,
    MergeChange,
    MergeProposal,
    Message,
    ReminderSettings,
    ReminderState,
    SplitDraft,
    Suggestion,
    Task,
    TaskEvent,
    TaskMaterials,
)

# Explicit allowlists prevent a future credential column from silently entering exports.
RECORDS = {
    "legacy_records": (LegacyRecord, "id namespace kind legacy_id content_hash target_id payload"),
    "messages": (Message, "id text source sent_at timezone received_at dedupe_key"),
    "suggestions": (
        Suggestion,
        "id message_id status action method fields evidence warnings created_at updated_at",
    ),
    "tasks": (
        Task,
        "id suggestion_id message_id title course kind description due_at due_date status "
        "submission_platform submission_method file_requirements location remarks version "
        "created_at updated_at",
    ),
    "task_events": (TaskEvent, "id task_id message_id operation before after actor created_at"),
    "task_materials": (TaskMaterials, "task_id items"),
    "attachments": (Attachment, "id filename size status text message_id created_at"),
    "split_drafts": (
        SplitDraft,
        "id suggestion_id position quote status can_create fields evidence warnings task_id "
        "created_at updated_at",
    ),
    "deadline_proposals": (
        DeadlineProposal,
        "id suggestion_id task_id task_version before_due_at proposed_due_at "
        "suggestion_updated_at evidence created_at",
    ),
    "deadline_changes": (
        DeadlineChange,
        "suggestion_id task_id proposal_id event_id applied_version undo_event_id",
    ),
    "date_deadline_proposals": (
        DateDeadlineProposal,
        "id suggestion_id task_id task_version before_due_at before_due_date "
        "proposed_due_at proposed_due_date suggestion_updated_at evidence created_at",
    ),
    "date_deadline_changes": (
        DateDeadlineChange,
        "suggestion_id task_id proposal_id event_id applied_version undo_event_id",
    ),
    "cancellation_proposals": (
        CancellationProposal,
        "id suggestion_id task_id task_version before_status suggestion_updated_at created_at",
    ),
    "cancellation_changes": (
        CancellationChange,
        "suggestion_id task_id proposal_id event_id applied_version undo_event_id",
    ),
    "merge_proposals": (
        MergeProposal,
        "id source_key suggestion_id split_draft_id source_updated_at task_id task_version "
        "before_fields after_fields evidence created_at",
    ),
    "merge_changes": (
        MergeChange,
        "id source_key suggestion_id split_draft_id task_id proposal_id event_id "
        "applied_version undo_event_id",
    ),
    "reminder_settings": (ReminderSettings, "id enabled lead_minutes overdue_enabled"),
    "reminder_states": (
        ReminderState,
        "id task_id due_at phase is_read read_at snoozed_until version updated_at",
    ),
    "date_reminder_states": (
        DateReminderState,
        "id task_id due_date phase is_read read_at snoozed_until version updated_at",
    ),
    "qq_events": (
        QQEvent,
        "id identity payload state blocked_revision suggestion_id received_at",
    ),
    "chaoxing_events": (
        ChaoxingEvent,
        "id identity payload state suggestion_id received_at",
    ),
    "platform_observations": (PlatformObservation, "identity id status checked_at"),
}
QQ_PAYLOAD_FIELDS = {
    "account_id",
    "group_id",
    "sender_id",
    "sender_name",
    "message_id",
    "media_types",
    "sent_at",
    "text",
    "_course",
    "_group_name",
    "_course_mode",
}
CHAOXING_FIELDS = {
    "kind",
    "message_id",
    "text",
    "title",
    "description",
    "course",
    "conversation",
    "sender",
    "display_time",
    "captured_at",
    "published_at",
    "direction",
    "task_type",
    "deadline",
    "submission_method",
    "platform_status",
    "teacher",
    "activity_id",
    "course_id",
    "class_id",
    "detail_state",
    "media_pending_count",
}
CHAOXING_EVIDENCE_FIELDS = {
    "title",
    "course",
    "description",
    "deadline",
    "due_at",
    "published_at",
    "submission_method",
    "platform_status",
    "location",
    "remarks",
}
ATTACHMENT_FIELDS = {"name", "filename", "size", "status", "text"}
SCOPE = {
    "included": "任务、通知原文、建议、变更历史与草稿、提醒、附件提取文字和 QQ/学习通队列记录。",
    "excluded": (
        "不包含附件原文件、模型及连接设置、凭据、远程解析批次号、错误详情和学习通链接及未知字段。"
    ),
    "restore": "目前没有 JSON 导入恢复功能；此文件不能替代数据库、附件目录及所需私有配置的备份。",
    "privacy": "任务、原文和提取文字可能包含个人信息或用户自行输入的敏感内容，请谨慎保管。",
}


def chaoxing_payload(payload):
    # An external job can contain file bytes or credential-bearing URLs.
    scalar = (str, int, float, bool, type(None))
    result = {
        key: value
        for key, value in payload.items()
        if key in CHAOXING_FIELDS and isinstance(value, scalar)
    }
    evidence = payload.get("field_evidence")
    if isinstance(evidence, dict):
        result["field_evidence"] = {
            key: value
            for key, value in evidence.items()
            if key in CHAOXING_EVIDENCE_FIELDS and isinstance(value, scalar)
        }
    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        result["attachments"] = [
            {
                key: value
                for key, value in attachment.items()
                if key in ATTACHMENT_FIELDS and isinstance(value, scalar)
            }
            for attachment in attachments
            if isinstance(attachment, dict)
        ]
    return result


def snapshot(engine):
    # sqlite3's default transaction mode does not begin on SELECT. Explicitly start
    # a read transaction so related tables reflect one consistent point in time.
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN")
        exported_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        records = {}
        for name, (model, fields) in RECORDS.items():
            rows = session.exec(select(model).order_by(*model.__table__.primary_key.columns)).all()
            values = []
            for row in rows:
                item = row.model_dump(include=set(fields.split()))
                for key, value in item.items():
                    if isinstance(value, datetime):
                        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
                        item[key] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
                    elif isinstance(value, date):
                        item[key] = value.isoformat()
                if name == "qq_events":
                    item["payload"] = {
                        key: value
                        for key, value in item["payload"].items()
                        if key in QQ_PAYLOAD_FIELDS
                        and (
                            isinstance(value, str)
                            or (
                                key == "media_types"
                                and isinstance(value, list)
                                and all(isinstance(part, str) for part in value)
                            )
                        )
                    }
                elif name == "chaoxing_events":
                    item["payload"] = chaoxing_payload(item["payload"])
                values.append(item)
            records[name] = values
    return {
        "format": "campusinbox-data-export",
        "version": 1,
        "exported_at": exported_at,
        "scope": SCOPE,
        "records": records,
    }


def readable(data):
    records = data["records"]
    lines = [
        "CampusInbox 数据概览",
        f"导出时间：{data['exported_at']}",
        "这是方便阅读的摘要；更多业务记录请查看 JSON 导出。",
        *SCOPE.values(),
        "",
        f"【任务】共 {len(records['tasks'])} 项",
    ]
    statuses = {
        "todo": "待开始",
        "in_progress": "进行中",
        "done": "已做完，未提交",
        "submitted": "已提交",
        "completed": "已完成",
        "cancelled": "已取消",
        "archived": "已归档",
    }
    labels = (
        ("description", "说明"),
        ("submission_platform", "提交平台"),
        ("submission_method", "提交方式"),
        ("file_requirements", "文件要求"),
        ("location", "地点"),
        ("remarks", "备注"),
    )
    material_by_task = {item["task_id"]: item["items"] for item in records["task_materials"]}
    for task in records["tasks"]:
        deadline = (
            task["due_date"] + "（仅日期）" if task["due_date"] else task["due_at"] or "未设置"
        )
        lines.extend(
            [
                "",
                f"{task['title']}（{task['id']}）",
                f"课程：{task['course'] or '未填写'}",
                f"状态：{statuses.get(task['status'], task['status'])}",
                f"截止：{deadline}",
            ]
        )
        lines.extend(f"{label}：{task[key]}" for key, label in labels if task[key])
        for item in material_by_task.get(task["id"], []):
            lines.append(f"材料：{'已准备' if item['done'] else '未准备'} · {item['text']}")
    lines.extend(["", f"【识别建议】共 {len(records['suggestions'])} 项"])
    for suggestion in records["suggestions"]:
        title = suggestion["fields"].get("title") or "未命名"
        lines.append(f"{title}（{suggestion['status']}；通知 {suggestion['message_id']}）")
    lines.extend(["", f"【任务历史】共 {len(records['task_events'])} 条"])
    for event in records["task_events"]:
        lines.append(
            f"任务 {event['task_id']} · {event['created_at']} · {event['operation']} · "
            f"修改前 {json.dumps(event['before'], ensure_ascii=False)} · "
            f"修改后 {json.dumps(event['after'], ensure_ascii=False)}"
        )
    lines.extend(["", f"【通知原文】共 {len(records['messages'])} 条"])
    for message in records["messages"]:
        lines.extend(
            [
                "",
                f"--- 通知 {message['id']} ---",
                f"来源：{message['source']}",
                f"发送时间：{message['sent_at']}（UTC）",
                message["text"],
            ]
        )
    lines.extend(["", f"【附件提取文字】共 {len(records['attachments'])} 条"])
    for attachment in records["attachments"]:
        lines.extend(
            [
                "",
                f"--- {attachment['filename']}（{attachment['status']}）---",
                attachment["text"] or "（无提取文字）",
            ]
        )
    lines.extend(["", f"【QQ 队列记录】共 {len(records['qq_events'])} 条"])
    for event in records["qq_events"]:
        lines.extend(
            [
                "",
                f"--- {event['state']} · {event['received_at']} ---",
                event["payload"].get("text", "（无正文）"),
            ]
        )
    lines.extend(["", f"【学习通队列记录】共 {len(records['chaoxing_events'])} 条"])
    for event in records["chaoxing_events"]:
        payload = event["payload"]
        lines.extend(
            [
                "",
                f"--- {event['state']} · {event['received_at']} ---",
                str(payload.get("title") or "（无标题）"),
                str(payload.get("text") or "（无正文）"),
            ]
        )
        for attachment in payload.get("attachments", []):
            lines.extend(
                [
                    f"附件：{attachment.get('name') or attachment.get('filename') or '未命名'}",
                    str(attachment.get("text") or "（无提取文字）"),
                ]
            )
    return "\n".join(lines) + "\n"


def router(engine):
    api = APIRouter(tags=["data export"], dependencies=[Depends(loopback)])

    @api.get("/api/export.json")
    def export_json():
        content = json.dumps(snapshot(engine), ensure_ascii=False, indent=2) + "\n"
        return Response(
            content,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="campusinbox-data.json"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api.get("/api/export.txt")
    def export_text():
        return Response(
            readable(snapshot(engine)),
            media_type="text/plain",
            headers={
                "Content-Disposition": 'attachment; filename="campusinbox-report.txt"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return api
