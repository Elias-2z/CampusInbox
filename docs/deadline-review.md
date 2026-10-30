# 截止时间变更审核

## 这一阶段

延期 / 提前 / 截止时间调整通知 → 候选任务 → 用户选择目标与新时间 → 持久化预览 → 明确确认 → 仅更新截止时间 → 可撤销。

不处理自动合并、取消任务或多任务通知。没有明确的新时刻时留空，由用户核对填写。匹配结果只供建议，不自动应用；多候选不默认替用户选择。任务需处于待办或进行中。

## 数据与事务

在原有 schema 上新增 DeadlineProposal（建议、目标任务、任务版本、旧时间、新时间、建议修订时间、字段依据）与 DeadlineChange（唯一建议关联、预览、确认事件、应用版本、撤销事件）。不修改或删除旧表，已有数据保留。

确认时同时检查建议状态和修订、目标任务版本、任务状态，在一个 SQLite 写事务中更新 due_at、增加版本、记录 TaskEvent 并关联来源。唯一建议应用记录防止重复执行。预览本身不修改任务；用户改变目标或时间后必须重新预览。

撤销只恢复此次操作之前的截止时间，增加版本并记录新事件。若之后有任何任务编辑，旧撤销请求返回 409，不覆盖后续内容。同一确认 / 撤销的重复请求不重复执行。撤销后该通知仍为已处理，历史与来源不删除。

## 接口

- POST /api/suggestions/{id}/deadline-preview：task_id、可选 due_at，返回预览 ID、当前目标任务、新时间和依据。
- POST /api/deadline-proposals/{id}/confirm：确认固定的预览，不能在此临时替换目标或时间。
- POST /api/deadline-changes/{suggestion_id}/undo：撤销这条建议的已确认变更。
- InboxItem 增加 deadline_supported、deadline_change、updated_at；TaskDetail 增加 sources、deadline_changes；事件增加 message_id。

兼容已有待核对通知，通过原文判断是否可进入截止时间流程；不批量重跑用户的模型识别。UI 可手动补充时间或主动重新识别。
