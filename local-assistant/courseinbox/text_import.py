"""Text-first task intake. Optional context is never an invented publication clock."""
import hashlib
import re
import time
from .core import clean_string,parse_timestamp,parse_message,ValidationError,now_iso,dump
from .agent import Agent
from .intake_policy import automatic_candidate


def publication_from_text(text):
    from .chinese_dates import normalize
    normalized,_=normalize(text)
    m=re.search(r'(?:^|\n)\s*(?:消息)?发布时间\s*[:：]\s*(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})(?:日|号)?(?:[T \t]*(\d{1,2})\s*[:：点时]\s*(\d{1,2}))?',normalized)
    if not m:return None
    value=f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}'
    validated=parse_timestamp(value+f'T{int(m[4] or 0):02d}:{int(m[5] or 0):02d}:00+08:00')
    return validated.isoformat() if m[4] else value


def ingest(inbox,body,analyzer=None):
    if not isinstance(body,dict) or set(body)-{'text','source','course','scope','published_at','mode'}:raise ValidationError('文本导入字段不支持。')
    text=clean_string(body.get('text',''),'消息正文',30000,True)
    source=clean_string(body.get('source','文本导入'),'来源',120,True)
    course=clean_string(body.get('course',''),'课程',100)
    scope=clean_string(body.get('scope',''),'班级',100)
    if not scope:
        m=re.search(r'(?:^|\n)\s*(?:班级|分组|课程群)\s*[:：]\s*([^\n，,。；;]{1,100})',text)
        scope=m[1].strip() if m else ''
    published=body.get('published_at')
    published=parse_timestamp(published).isoformat() if published else publication_from_text(text)
    anchor=(published+'T00:00:00+08:00' if len(published or '')==10 else published) or '2000-01-01T00:00:00+08:00'
    mode=body.get('mode','default')
    if mode not in ('default','rules','agent'):raise ValidationError('提取模式无效。')
    analyzer=Agent(inbox) if mode=='rules' else analyzer or Agent(inbox)
    if mode=='agent' and not analyzer.provider:raise ValidationError('服务端尚未配置模型，请选择自动或规则模式。')
    metadata=dict(platform='text',conversation=scope,sender='',published_at=published)
    fingerprint=hashlib.sha256(dump([source,'chat-import',text,course,metadata]).encode()).hexdigest()
    with inbox.intake_lock(fingerprint):
        with inbox.connection() as db:
            seen=db.execute('SELECT 1 FROM messages WHERE fingerprint=? UNION SELECT 1 FROM cleared_messages WHERE fingerprint=?',(fingerprint,fingerprint)).fetchone()
        if seen:return inbox.ingest(text,source=source,course=course,scope=scope,_chat_import=metadata,candidates=[])
        context=dict(text=text,captured_at=anchor,scope=scope,chat_import=metadata)
        try:baseline=[automatic_candidate(c,context) for c in parse_message(text,anchor,course,scope)]
        except Exception:
            if not analyzer.provider:raise
            baseline=None
        started=time.monotonic()
        result=analyzer.analyze(text,anchor,course,scope,rule_candidates=baseline,publication_known=bool(published),force_model=baseline is None)
        candidates=[automatic_candidate(c,context) for c in result['candidates']]
        calls=result.get('model_calls',[]);trace=list(result.get('trace',[]))
        failed=any(t.get('result',{}).get('fallback')=='rules' for t in trace)
        status=dict(status='model_failed' if failed else 'model_success' if calls else 'model_skipped' if analyzer.provider else 'rules_only',model=getattr(analyzer.provider,'model',''),request_count=len(calls),requests=calls,at=now_iso(),elapsed_ms=round((time.monotonic()-started)*1000),reason=next((t['result']['reason'] for t in trace if t.get('tool')=='analysis_route'),'按需调用模型补漏；原文缺失字段保留待核对'))
        trace.append(dict(tool='analysis_status',result=status))
        return inbox.ingest(text,source=source,course=course,scope=scope,captured_at=published if published and len(published)>10 else None,candidates=candidates,mode=result['mode'],trace=trace,_chat_import=metadata)
