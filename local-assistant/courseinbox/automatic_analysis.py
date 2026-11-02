"""Rules first; request paid Chaoxing text review only when needed."""
import time
from .core import now_iso


def analyze(message,analyzer):
    candidates=message['candidates'];external=message['external']
    status=dict(status='rules_only',reason='未配置模型，使用规则解析',model='',request_count=0,at=now_iso(),elapsed_ms=0)
    trace=[];mode='rules'
    if analyzer and analyzer.provider:
        status['model']=getattr(analyzer.provider,'model','')
        from .checkin_evidence import is_checkin
        protected_platform=external.get('kind') in ('task_center','checkin_activity') or is_checkin(message)
        if external.get('kind') not in ('chat','course_notice','task_center','checkin_activity'):
            status.update(status='model_skipped',reason='结构化任务或签到活动按平台证据处理')
        else:
            status.update(reason='规则优先，仅对无法覆盖的任务信息调用模型补漏',review_policy='on_demand')
            if protected_platform:status.update(reason='模型补充原文可核实的缺失字段；保留平台已有值及签到核验',review_policy='platform_evidence_first')
            known=bool(external.get('time_known'))
            anchor=message['captured_at'] if known else '2000-01-01T00:00:00+08:00'
            started=time.monotonic()
            result=analyzer.analyze(message['text'],anchor,message['course'],message['scope'],rule_candidates=candidates,publication_known=known,force_model=bool(external.get('extraction_warning')))
            trace=list(result.get('trace',[]));calls=result.get('model_calls',[])
            failed=any(t.get('result',{}).get('fallback')=='rules' for t in trace)
            status.update(status='model_failed' if failed else 'model_success' if calls else 'model_skipped',request_count=len(calls),requests=calls,limited=result.get('limited',False),elapsed_ms=round((time.monotonic()-started)*1000))
            if failed:status['error']='模型调用失败，已保留规则结果'
            if not calls and not failed:status['reason']=next((t['result']['reason'] for t in trace if t.get('tool')=='analysis_route'),'本次由确定性规则处理，未发起模型请求')
            mode=result['mode']
            if protected_platform and candidates:
                from .task_identity import name_key
                protected=[]
                for original in candidates:
                    reviewed=next((c for c in result['candidates'] if name_key(c)==name_key(original)),None)
                    item=dict(original)
                    if reviewed:
                        requirements=list(dict.fromkeys(list(original.get('requirements',[]))+[r for r in reviewed.get('requirements',[]) if r in message['text']]))
                        item['requirements']=requirements[:30]
                        item['warnings']=list(dict.fromkeys(list(original.get('warnings',[]))+list(reviewed.get('warnings',[]))))
                        item['model_review']=reviewed.get('model_review',[])
                        # Fill absent task-center fields from validated source quotes;
                        # never replace an explicit platform value or a sign-in end time.
                        if external.get('kind')=='task_center':
                            evidence=reviewed.get('deadline_evidence','')
                            from .chinese_dates import has_explicit_year
                            if not original.get('deadline') and reviewed.get('deadline') and evidence and evidence in message['text'] and (known or has_explicit_year(evidence)):
                                for field in ('deadline','date_precision','deadline_evidence'):item[field]=reviewed.get(field)
                            evidence=reviewed.get('field_evidence',{}).get('submission_method','')
                            if not original.get('submission_method') and reviewed.get('submission_method') and evidence and evidence in message['text']:
                                item['submission_method']=reviewed['submission_method']
                                item['field_evidence']=dict(original.get('field_evidence',{}),submission_method=evidence)
                        if len(requirements)>30:item['warnings'].append('任务要求较长，仅显示前30项，请核对完整原文。')
                    protected.append(item)
                candidates=protected
            else:candidates=result['candidates']
    trace.append(dict(tool='analysis_status',result=status))
    return dict(candidates=candidates,mode=mode,trace=trace)
