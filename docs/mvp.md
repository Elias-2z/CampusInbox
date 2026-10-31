# 第一阶段：可审核的任务创建闭环

## 范围与验收

沿用已确认的紫色 / 青柠 / 便签黄 / emoji 设计。完成文本导入、规则优先识别、可选本地 Ollama 兜底、Inbox 审核、任务 CRUD、原文与操作记录。初版所有任务必须确认，不启用自动入库。

验收：粘贴一条带明确日期时间的课程通知 → 查看原文、提取字段及依据 → 修改并确认 → 正式任务出现 → 刷新或重启后保留 → 完成任务并可撤销此次状态变更。重复确认不得重复创建。日期不明确不能编造；模型失效不得丢失原文。

创建闭环之外，现已增加截止时间变更审核：明确的延期 / 提前通知可以匹配并由用户确认更新已有任务（见 deadline-review.md）。取消通知也支持匹配、预览、确认与撤销（见 cancellation-review.md）。连续编号的多任务通知支持逐项审核创建（见 multi-task-review.md）；普通新通知 / 拆分条目可关联已有任务并明确勾选字段补充（见 notice-merging.md）；未编号多任务及已创建任务归并仍待后续。站内截止提醒已可用（见 reminders.md）。周 / 月截止日历与 .ics 手动导出已可用（见 calendar.md）。OCR、系统提醒推送、日历自动同步、平台 Connector、云端 API、数据备份恢复留到后续阶段。首页仅显示真实本地数据，不放演示任务。

## 最小数据模型

- Message：原始文本、来源、实际发送时间、时区、接收时间和去重键。
- Suggestion：关联 Message，处理 / 待确认 / 已确认 / 已忽略状态，动作、结构化草稿、字段依据、识别方式和警告。
- Task：关联建议与原文，标题、课程、类型、内容、截止时间、提交信息、状态、乐观锁版本、创建 / 更新时间。
- TaskEvent：用户确认或编辑的前后快照，操作时间和来源。撤销采用新操作，保留旧记录。
- DeadlineProposal / DeadlineChange：固定预览与确认 / 撤销记录，原任务保留最初创建来源。
- CancellationProposal / CancellationChange：取消预览、原状态与确认 / 撤销记录。
- SplitDraft：同一原文的多个独立草稿，逐项保存 / 确认 / 跳过，关联正式任务。
- MergeProposal / MergeChange：普通通知 / 拆分条目的关联预览、选择字段与确认 / 撤销记录。
- ReminderSettings / ReminderState：站内提醒窗口、开关与每次截止阶段的已读 / 稍后状态。
- ModelSettings：本地 Ollama 地址、模型名、是否启用、超时。初版无云端配置，不保存 API Key。

数据库为 backend/data/campus-inbox.db（忽略提交）。数据库时间采用 UTC，API 返回带 UTC 标识的时间；相对日期按原消息的时间和时区解析。基表沿用 schema v1 字段；审核表增量创建。多任务阶段通过特定迁移将 Task.suggestion_id 唯一索引改为普通索引，先备份、不重建表；不支持任意旧字段结构迁移。

## 主要 API

- GET /api/health
- GET /api/inbox；POST /api/inbox；POST /api/inbox/{id}/analyze
- POST /api/suggestions/{id}/accept；POST /api/suggestions/{id}/ignore；POST /api/suggestions/{id}/reopen
- GET /api/tasks；POST /api/tasks；GET /api/tasks/{id}；PATCH /api/tasks/{id}；DELETE /api/tasks/{id}（归档）
- POST /api/suggestions/{id}/deadline-preview；POST /api/deadline-proposals/{id}/confirm；POST /api/deadline-changes/{id}/undo
- POST /api/suggestions/{id}/cancellation-preview；POST /api/cancellation-proposals/{id}/confirm；POST /api/cancellation-changes/{id}/undo
- POST /api/suggestions/{id}/split；PATCH /api/split-drafts/{id}；POST /api/split-drafts/{id}/accept 或 ignore / reopen
- POST /api/suggestions/{id}/merge-preview 或 /api/split-drafts/{id}/merge-preview；POST /api/merge-proposals/{id}/confirm；POST /api/merge-changes/{id}/undo
- GET /api/reminders；GET / PUT /api/settings/reminders；POST /api/reminders/{id}/read 或 snooze / restore
- GET /api/calendar；GET /api/calendar/export.ics（同一范围 / 时区 / 筛选，只读）
- GET /api/settings/model；PUT /api/settings/model；POST /api/settings/model/probe

确认、创建与事件记录使用事务；通过建议 / 草稿状态与事务防止重复确认，同一多任务建议可关联多项任务。任务修改携带 expected_version，过期版本返回 409，避免静默覆盖。相同原文、来源和消息日期去重；不同日期的“明天”等通知不能被跨日误去重。

## 代码边界

backend/src/campus_inbox/{models,schemas,extraction,providers,services,deadlines,cancellations,splitting,merging,reminders,calendar,main}.py；前端 API 类型与请求在 src/api.ts，业务状态在 Pinia store，视图和共享组件独立。模型只返回待校验字段，不执行数据库操作。重要字段必须能在原文中找到依据；可靠规则值不被模型覆盖。
