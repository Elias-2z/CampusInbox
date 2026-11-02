"""Full-template verification; uncertainty always retains model assistance."""
import re
from .core import normalize,extract_deadline,parse_timestamp,parse_message,TASK_PATTERN


def verified_template(text,candidates,published_at):
    if len(candidates)!=1:return False
    c=candidates[0]
    if c.get('operation')!='create' or c.get('warnings') or c.get('requirements') or not c.get('matchable'):return False
    fields={}
    labels={'课程名称':'course','作业名称':'title','任务名称':'title','截止时间':'deadline','结束时间':'deadline','提交方式':'submission_method'}
    for line in text.strip().splitlines():
        match=re.fullmatch(r'\s*([^:：]+)\s*[:：]\s*(\S[^\r\n]*?)\s*',line)
        if not match or match[1].strip() not in labels:return False
        key=labels[match[1].strip()]
        if key in fields:return False
        fields[key]=match[2]
    if not {'course','title','deadline'}<=fields.keys():return False
    if any(normalize(fields[key])!=normalize(c.get(key,'')) for key in ('course','title','submission_method') if key in fields):return False
    if 'submission_method' not in fields and c.get('submission_method'):return False
    if any(re.search(r'[。；;，,]|如果|若|除外|延期|补交|附件|详见',value) for key,value in fields.items() if key!='deadline'):return False
    if re.search(r'[。；;，,]|以及|并|和|与|除外|取消|延期|调整|改为',fields['title']):return False
    if len(re.findall(TASK_PATTERN,fields['title']))!=1:return False
    if not re.fullmatch(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s+\d{1,2}[:：]\d{2})?',fields['deadline']):return False
    due,precision,_,warnings=extract_deadline(fields['deadline'],parse_timestamp(published_at))
    parsed=parse_message(text,published_at,c.get('course',''),c.get('scope',''))
    if len(parsed)!=1 or parsed[0]['kind']!=c.get('kind'):return False
    return bool(due and due==c.get('deadline') and precision==c.get('date_precision') and not warnings)


def rules_sufficient(text,candidates,published_at):
    """Skip paid review only for one coherent, source-grounded simple task."""
    if len(candidates)!=1:return False
    c=candidates[0]
    if c.get('operation')!='create' or not c.get('matchable') or not c.get('title'):return False
    if re.search(r'如果|若|除外|补交|取消|改为|延期|详见|附件|另附|分别|以及',text):return False
    if len(re.findall(TASK_PATTERN,c['title']))!=1:return False
    if len(re.findall(r'(?:截止时间|截止日期|结束时间)\s*[:：]',text))>1:return False
    if re.search(r'明天|后天|下周|本周|周[一二三四五六日天]',text):return False
    from .chinese_dates import normalize as normalize_dates
    dated,_=normalize_dates(text)
    dates=re.findall(r'(?:\d{4}\s*[年/-]\s*)?\d{1,2}\s*[月/-]\s*\d{1,2}(?:[日号])?(?:\s*\d{1,2}[:：点时]\d{1,2})?',dated)
    if len(set(dates))>1:return False
    unannounced=bool(re.search(r'(?:截止(?:时间|日期)?|提交时间)(?:尚未公布|未公布|未定|待定|另行通知|另行公布)',re.sub(r'[\s：:]','',text)))
    date_cue=bool(re.search(r'\d{1,4}\s*[年月日号/:-]|[一二三四五六七八九十]+\s*[月日号]|明天|后天|下周|本周|周[一二三四五六日天]|截止|截至|最晚',text))
    warnings=c.get('warnings',[])
    if any(not ((unannounced or not date_cue) and w in ('未识别到明确截止日期，请核对或留空。','截止时间未明确，不推测补填。')) for w in warnings):return False
    if re.search(r'投递到|发送至|发送到|发到|发给|上传至|交至|交到|提交给|提交方式',text) and not c.get('submission_method'):return False
    if re.search(r'《[^》]+》',text) and not c.get('course'):return False
    if c.get('deadline'):
        evidence=c.get('deadline_evidence') or c.get('field_evidence',{}).get('deadline','')
        if not evidence or evidence not in text:return False
    elif not unannounced:
        if date_cue:return False
        if not c.get('requirements'):return False
    # Recheck supplied candidates so a platform/rule disagreement still gets review.
    parsed=parse_message(text,published_at,c.get('course',''),c.get('scope',''))
    if len(parsed)!=1:return False
    reference=parsed[0]
    if any(c.get(k)!=reference.get(k) for k in ('title','kind','deadline','submission_method')):return False
    collected=normalize(' '.join(c.get('requirements',[])))
    if any(normalize(r) not in collected for r in reference.get('requirements',[])):return False
    # A date alone cannot prove the rest of the source was understood.
    # Remove only extracted values and a small set of form boilerplate;
    # unaccounted prose or a second date still needs model review.
    residual=text
    values=[c.get(k,'') for k in ('title','course','deadline_evidence','submission_method')]+list(c.get('requirements',[]))
    for value in sorted(filter(None,values),key=len,reverse=True):residual=residual.replace(value,'')
    residual=re.sub(r'(?:截止(?:时间|日期)?|提交时间)(?:\s*[：:]?\s*)(?:尚未公布|未公布|未定|待定|另行通知|另行公布)','',residual)
    residual=re.sub(r'课程名称|作业名称|任务名称|提交方式|截止时间|截止日期|结束时间|截止|截至|最晚|之前|前|请|需要|需|务必|提交|上传|上交','',residual)
    residual=re.sub(r'[\s《》：:。；;，,、！!（）()\[\]【】]+','',residual)
    if residual:return False
    return bool(c.get('deadline') or c.get('requirements') or unannounced)
