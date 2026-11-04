"""Local PushPlus settings and simulated delivery."""

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlmodel import Session

from campus_inbox import wechat_push
from campus_inbox.main import create_app

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(os.name != "nt", reason="requires DPAPI")]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_push_requires_opt_in_and_does_not_repeat_uncertain_delivery(tmp_path, monkeypatch):
    current = datetime(2026, 11, 4, 9, tzinfo=UTC)
    app = create_app(
        tmp_path / "main.db",
        reminder_clock=lambda: current,
        qq_background=False,
        chaoxing_background=False,
    )
    push = app.state.wechat_push
    monkeypatch.setattr(push, "start", lambda: None)
    deliveries = []
    outcome = "accepted"

    def deliver(token, content):
        deliveries.append((token, content))
        return outcome

    monkeypatch.setattr(wechat_push, "deliver", deliver)
    token = "synthetic_pushplus_token"
    path = "/api/connectors/wechat/push"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            assert not (await client.get(path)).json()["enabled"]
            assert (await client.post(path + "/test")).status_code == 409
            assert (
                await client.get(path, headers={"Origin": "https://outside.example"})
            ).status_code == 403

            async def create_task(title):
                response = await client.post(
                    "/api/tasks",
                    json={"title": title, "due_at": (current + timedelta(hours=1)).isoformat()},
                )
                assert response.status_code == 200

            await create_task("旧任务")
            saved = await client.put(path, json={"enabled": True, "token": token})
            assert saved.status_code == 200 and token not in saved.text
            with Session(app.state.engine) as session:
                credential = session.get(wechat_push.WechatPushSettings, 1).token
                assert token.encode() not in credential
            push.dispatch()
            assert deliveries == []
            await create_task("新的真实标题不得发送")
            push.dispatch()
            push.dispatch()
            assert deliveries == [(token, "有新的截止提醒，请打开电脑上的 CampusInbox 查看。")]
            assert (await client.get(path)).json()["result"] == "accepted"
            await create_task("可能未送达")
            outcome = "unknown"
            push.dispatch()
            assert not (await client.get(path)).json()["enabled"]
            push.dispatch()
            assert len(deliveries) == 2
            assert (await client.put(path, json={"enabled": True})).status_code == 200
            with Session(app.state.engine) as session:
                settings = session.get(wechat_push.WechatPushSettings, 1)
                settings.result = "sending"
                settings.sending_at = current - timedelta(minutes=3)
                session.commit()
            push.dispatch()
            restored = (await client.get(path)).json()
            assert restored["result"] == "unknown" and not restored["enabled"]
            assert len(deliveries) == 2
            response = await client.put(path, json={"enabled": False, "clear_token": True})
            assert response.status_code == 200 and not response.json()["configured"]


async def test_review_results_require_opt_in_and_confirmation(tmp_path, monkeypatch):
    app = create_app(tmp_path / "main.db", qq_background=False, chaoxing_background=False)
    push = app.state.wechat_push
    intake = app.state.chaoxing.intake
    monkeypatch.setattr(push, "start", lambda: None)
    messages = []

    def deliver(_token, content):
        messages.append(content)
        return "accepted"

    monkeypatch.setattr(wechat_push, "deliver", deliver)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:

            async def receive(key, text):
                event_id, _ = intake.receive(
                    "account",
                    {
                        "kind": "course_notice",
                        "message_id": key,
                        "captured_at": datetime.now(UTC).isoformat(),
                        "text": text,
                    },
                )
                assert intake.process(event_id) == "done"
                with Session(app.state.engine) as session:
                    return session.get(wechat_push.ChaoxingEvent, event_id).suggestion_id

            async def accept(suggestion_id, title):
                response = await client.post(
                    f"/api/suggestions/{suggestion_id}/accept",
                    json={"title": title, "course": "数据库原理", "due_date": "2099-11-30"},
                )
                assert response.status_code == 200, response.text
                return response.json()

            path = "/api/connectors/wechat/push"
            await accept(await receive("old", "请提交旧报告。"), "旧报告")
            configured = await client.put(
                path,
                json={
                    "enabled": True,
                    "token": "synthetic_pushplus_token",
                    "include_review_results": True,
                },
            )
            assert configured.json()["include_review_results"]
            item = await receive("new", "数据库原理：请提交实验四报告。")
            push.dispatch()
            assert messages == []
            task = await accept(item, "实验四报告")
            push.dispatch()
            push.dispatch()
            assert len(messages) == 1
            for kind, text in (
                ("deadline", "数据库原理：实验四报告延期至2099年12月3日。"),
                ("cancellation", "数据库原理：实验四报告已取消。"),
            ):
                change = await receive(kind, text)
                preview = await client.post(
                    f"/api/suggestions/{change}/{kind}-preview", json={"task_id": task["id"]}
                )
                assert preview.status_code == 200, preview.text
                if kind == "deadline":
                    assert preview.json()["proposed_due_date"] == "2099-12-03"
                    assert preview.json()["evidence"]["method"] == "rule"
                confirmed = await client.post(
                    f"/api/{kind}-proposals/{preview.json()['id']}/confirm"
                )
                assert confirmed.status_code == 200, confirmed.text
                push.dispatch()
            push.dispatch()
            assert messages == ["学习通任务审核结果已更新，请打开电脑上的 CampusInbox 查看。"] * 3


async def test_course_notice_push_only_sends_new_opted_in_notices_once(tmp_path, monkeypatch):
    current = datetime.now(UTC) - timedelta(minutes=3)
    app = create_app(
        tmp_path / "main.db",
        reminder_clock=lambda: current,
        qq_background=False,
        chaoxing_background=False,
    )
    push = app.state.wechat_push
    intake = app.state.chaoxing.intake
    monkeypatch.setattr(push, "start", lambda: None)
    deliveries = []

    def deliver(_token, content):
        deliveries.append(content)
        return "accepted"

    monkeypatch.setattr(wechat_push, "deliver", deliver)

    def notice(message_id, stamp, text="请于2099年12月1日前提交报告。", **extra):
        event_id, _ = intake.receive(
            "account",
            {
                "kind": "course_notice",
                "message_id": message_id,
                "captured_at": stamp.isoformat() if stamp else None,
                "text": text,
                **extra,
            },
        )
        assert intake.process(event_id, sent_at=stamp or current) == "done"

    old = current - timedelta(minutes=1)
    fresh = current + timedelta(minutes=1)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            path = "/api/connectors/wechat/push"
            assert (
                await client.put(path, json={"enabled": True, "token": "synthetic_pushplus_token"})
            ).status_code == 200
            notice("before-opt-in", fresh)
            push.dispatch()
            assert deliveries == []
            current = fresh + timedelta(seconds=1)
            assert (await client.put(path, json={"enabled": True, "include_notices": True})).json()[
                "include_notices"
            ]
            notice("old", old)
            notice("unknown", None)
            push.dispatch()
            assert deliveries == []
            fresh = current + timedelta(seconds=1)
            notice("new", fresh)
            push.dispatch()
            assert deliveries == []
            current = fresh
            notice("course-only-chat", fresh, kind="chat")
            push.dispatch()
            notice("new", fresh, "请于2099年12月2日前提交报告。")
            push.dispatch()
            assert deliveries == ["学习通有新课程通知，请打开电脑上的 CampusInbox 查看。"]
            assert (
                await client.put(path, json={"enabled": True, "include_notices": False})
            ).status_code == 200
            notice("after-off", fresh)
            push.dispatch()
            assert len(deliveries) == 1
            notice("old-chat", fresh, kind="chat")
            current += timedelta(seconds=1)
            configured = await client.put(
                path, json={"enabled": True, "include_expanded_notices": True}
            )
            assert configured.json()["include_expanded_notices"]
            push.dispatch()
            assert len(deliveries) == 1
            notice("chat", current, kind="chat")
            notice("change", current, "实验报告延期至2099年12月3日。")
            task = {
                "kind": "task_center",
                "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
                "published_at": current.isoformat(),
                "field_evidence": {"published_at": "发布时间"},
            }
            notice("work-1", current, **task)
            push.dispatch()
            assert deliveries[-1] == (
                "学习通有新聊天消息；学习通有新作业或考试；学习通有任务变更待核对，"
                "请打开电脑上的 CampusInbox 查看。"
            )
            notice("work-2", current, "请于2099年12月2日前提交报告。", **task)
            push.dispatch()
            assert len(deliveries) == 2
