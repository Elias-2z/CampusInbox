"""Read-only local-model questions about a bounded task snapshot."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from campus_inbox import services
from campus_inbox.connectors.chaoxing_api import loopback
from campus_inbox.models import Task, TaskMaterials
from campus_inbox.providers import ProviderError, TaskAnswer
from campus_inbox.schemas import TaskRead

MAX_TASKS = 30
BEIJING = ZoneInfo("Asia/Shanghai")


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    course: str = Field(default="", max_length=100)


def router(engine, provider_factory):
    api = APIRouter(prefix="/api/assistant", dependencies=[Depends(loopback)])

    @api.post("/ask")
    def ask(body: Question):
        question = body.question.strip()
        if not question:
            raise HTTPException(422, "请输入想查询的任务。")
        with Session(engine) as session:
            settings = services.config(session)
            if not settings.enabled or not settings.model:
                raise HTTPException(409, "请先在设置中启用并选择本地模型。")
            query = select(Task).where(Task.status != "archived")
            if body.course:
                query = query.where(Task.course == body.course)
            rows = session.exec(
                query.order_by(Task.updated_at.desc(), Task.id).limit(MAX_TASKS + 1)
            )
            tasks = [TaskRead.model_validate(task) for task in rows]
            materials = (
                {
                    row.task_id: row.items
                    for row in session.exec(
                        select(TaskMaterials).where(
                            TaskMaterials.task_id.in_([task.id for task in tasks])
                        )
                    )
                }
                if tasks
                else {}
            )
        limited = len(tasks) > MAX_TASKS
        tasks = tasks[:MAX_TASKS]
        if not tasks:
            return {"answer": "当前范围内没有任务。", "tasks": [], "limited": False}
        facts = []
        size = 0
        for index, task in enumerate(tasks, start=1):
            fact = {
                "number": index,
                "title": task.title,
                "course": task.course,
                "status": task.status,
                "deadline": task.due_at.astimezone(BEIJING).isoformat()
                if task.due_at
                else task.due_date.isoformat()
                if task.due_date
                else None,
                "details": task.description[:160],
                "submission": task.submission_method[:100],
                "platform": task.submission_platform,
                "location": task.location[:100],
                "requirements": task.file_requirements[:120],
                "materials": materials.get(task.id, [])[:8],
            }
            size += len(json.dumps(fact, ensure_ascii=False))
            if size > 6400:
                limited = True
                break
            facts.append(fact)
        try:
            answer = TaskAnswer.model_validate(
                provider_factory(settings).answer_tasks(
                    question, facts, datetime.now(BEIJING).date().isoformat()
                )
            )
            refs = list(dict.fromkeys(answer.references))
            if any(not 1 <= number <= len(facts) for number in refs):
                raise ProviderError("回答引用了查询范围之外的任务，请缩小范围或改用规则查询。")
        except (ProviderError, AttributeError, ValueError):
            raise HTTPException(502, "本地模型未返回可用回答，请检查模型或改用规则查询。") from None
        return {"answer": answer.answer, "tasks": [tasks[n - 1] for n in refs], "limited": limited}

    return api
