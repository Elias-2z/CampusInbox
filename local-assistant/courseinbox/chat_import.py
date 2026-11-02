"""User-pasted WeChat/QQ messages; no account login or chat-history access."""
import re
import hashlib
from .core import clean_string,parse_timestamp,parse_message,ValidationError,dump,now_iso

PLATFORMS={'wechat':'微信','qq':'QQ'}


def candidates(text,published_at,course,scope):
    parsed=parse_message(text,published_at or '2000-01-01T00:00:00+08:00',course,scope)
    if not published_at:
        for candidate in parsed:
            from .chinese_dates import has_explicit_year
            if not has_explicit_year(candidate.get('deadline_evidence','')):
                candidate.update(deadline=None,date_precision='unknown',deadline_evidence='')
                candidate['warnings']=[w for w in candidate.get('warnings',[]) if not w.startswith(('截止日期早于','原文未写年份'))]+['发布时间未知，不推算相对日期或年份。']
    return parsed


def ingest(inbox,body,analyzer=None):
    if not isinstance(body,dict) or set(body)-{'platform','text','conversation','sender','published_at','course','mode'}:raise ValidationError('聊天导入字段不支持。')
    platform=body.get('platform')
    if not isinstance(platform,str) or platform not in PLATFORMS:raise ValidationError('请选择微信或QQ来源。')
    text=clean_string(body.get('text',''),'消息正文',30000,True)
    group=clean_string(body.get('conversation',''),'群名或会话',100,True)
    sender=clean_string(body.get('sender',''),'发件人',100)
    course=clean_string(body.get('course',''),'课程',100)
    published=body.get('published_at')
    if published is not None and published!='':published=parse_timestamp(published).replace(microsecond=0).isoformat()
    else:published=None
    mode=body.get('mode','rules')
    if mode not in ('rules','agent'):raise ValidationError('提取模式无效。')
    metadata=dict(platform=platform,conversation=group,sender=sender,published_at=published)
    source=PLATFORMS[platform]+' · 文本导入'
    fingerprint=hashlib.sha256(dump([source,'chat-import',text,course,metadata]).encode()).hexdigest()
    with inbox.intake_lock(fingerprint):
        with inbox.connection() as db:
            seen=db.execute('SELECT 1 FROM messages WHERE fingerprint=? UNION SELECT 1 FROM cleared_messages WHERE fingerprint=?',(fingerprint,fingerprint)).fetchone()
        if seen:return inbox.ingest(text,source=source,course=course,scope=group,candidates=[],_chat_import=metadata)
        # A neutral anchor is never evidence for a publication date or missing year.
        analysis=analyzer.analyze(text,published or '2000-01-01T00:00:00+08:00',course,group,publication_known=bool(published)) if analyzer is not None else None
        if analysis:
            from .intake_policy import automatic_candidate
            context=dict(text=text,captured_at=published or '2000-01-01T00:00:00+08:00',scope=group,chat_import=metadata)
            analysis['candidates']=[automatic_candidate(c,context) for c in analysis['candidates']]
            calls=analysis.get('model_calls',[])
            failed=any(t.get('result',{}).get('fallback')=='rules' for t in analysis['trace'])
            analysis['trace'].append(dict(tool='analysis_status',result=dict(status='model_failed' if failed else 'model_success' if calls else 'model_skipped' if getattr(analyzer,'provider',None) else 'rules_only',request_count=len(calls),requests=calls,model=getattr(getattr(analyzer,'provider',None),'model',''),at=now_iso(),reason=next((t['result']['reason'] for t in analysis['trace'] if t.get('tool')=='analysis_route'),'规则先提取，按需调用模型补漏，原文证据校验'))))
        result=inbox.ingest(text,source=PLATFORMS[platform]+' · 文本导入',captured_at=published,course=course,scope=group,
            candidates=analysis['candidates'] if analysis else candidates(text,published,course,group),
            mode=analysis['mode'] if analysis else 'rules',trace=analysis['trace'] if analysis else [],
            _chat_import=dict(platform=platform,conversation=group,sender=sender,published_at=published))
        return result
