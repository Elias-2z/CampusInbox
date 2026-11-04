"""Human-readable overview; the JSON export remains the complete record."""

STATUS = {'pending': '待开始', 'in_progress': '进行中', 'done': '已做完', 'submitted': '已提交', 'cancelled': '已取消'}


def render_report(tasks, messages, proposals, exported_at):
    lines = ['CampusInbox 数据概览', f'导出时间：{exported_at}', '此报告便于阅读，完整记录请使用 JSON 导出。', '', f'【任务】共 {len(tasks)} 项']
    for index, task in enumerate(tasks, 1):
        lines.extend(['', f'{index}. {task.get("title") or "未命名任务"}',
                      f'课程：{task.get("course") or "未填写"}',
                      f'状态：{STATUS.get(task.get("status"), task.get("status") or "未知")}',
                      f'截止：{task.get("deadline") or "未设置"}'])
        for item in task.get('checklist') or []:
            lines.append(f'  [{"√" if item.get("done") else " "}] {item.get("text", "")}')
    lines.extend(['', f'【待核对建议】共 {len(proposals)} 项'])
    for index, proposal in enumerate(proposals, 1):
        candidate = proposal.get('candidate') or {}
        lines.append(f'{index}. {candidate.get("title") or "未命名"}（{candidate.get("course") or "课程未填写"}）')
    lines.extend(['', f'【通知原文】共 {len(messages)} 条'])
    for index, message in enumerate(messages, 1):
        lines.extend(['', f'--- 通知 {index} ---', f'来源：{message.get("source") or "未知"}',
                      f'时间：{message.get("captured_at") or "未知"}', message.get('text') or '（无正文）'])
    return '\n'.join(lines) + '\n'
