import tempfile
import unittest
from pathlib import Path
from courseinbox.core import Inbox
from courseinbox.agent import Agent,AgentError
from courseinbox.chaoxing import ingest_web_message
from tests.test_agent import FakeProvider,reply,call


class ChaoxingModelFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.box=Inbox(Path(self.tmp.name)/'inbox.sqlite3')

    def job(self,text='这是一条垃圾信息哈哈哈',**extra):
        return dict(dict(kind='course_notice',message_id='fallback-notice',title='《软件工程》实验一' if '实验一' in text else '普通通知',text=text,course='软件工程',conversation='课程群',captured_at='2026-10-29T18:04:00+08:00',url='https://notice.chaoxing.com/pc/notice/$CACG$fallback/detail',attachments=[]),**extra)

    def status(self,result):
        return next(x['result'] for x in result['message']['trace'] if x['tool']=='analysis_status')

    def test_empty_rules_are_reviewed_and_no_task_response_is_recorded(self):
        provider=FakeProvider([reply(content='普通信息，没有学习任务。')]);provider.model='test-model'
        result=ingest_web_message(self.box,self.job(),analyzer=Agent(self.box,provider))
        self.assertEqual(len(provider.seen),1);self.assertEqual(result['proposals'],[])
        status=self.status(result);self.assertEqual(status['status'],'model_success');self.assertEqual(status['model'],'test-model');self.assertEqual(status['request_count'],1)
        self.assertEqual(self.box.message(result['message']['id'])['trace'],result['message']['trace'])
        again=ingest_web_message(self.box,self.job(),analyzer=Agent(self.box,provider))
        self.assertTrue(again['duplicate']);self.assertEqual(len(provider.seen),1)

    def test_same_platform_version_with_new_capture_id_skips_model(self):
        provider=FakeProvider([reply(content='已核对'),reply(content='不应再次请求')])
        analyzer=Agent(self.box,provider)
        job=dict(kind='task_center',message_id='capture-first',title='实验报告',text='完成实验报告',course='课程一',conversation='课程一班',
                 task_type='assignment',deadline=None,description='请对照课堂示例完成，细节见附件。',platform_status='未提交',
                 captured_at='2026-10-29T18:04:00+08:00',url='https://mooc1.chaoxing.com/mooc2/work/detail?courseId=1&classId=2&workId=43',
                 field_evidence={'title':'实验报告'},detail_state='read')
        first=ingest_web_message(self.box,job,analyzer=analyzer)
        self.assertEqual(len(provider.seen),1)
        second=ingest_web_message(self.box,dict(job,message_id='capture-second',captured_at='2026-10-29T18:05:00+08:00'),analyzer=analyzer)
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['message']['id'],first['message']['id'])
        self.assertEqual(len(provider.seen),1)

    def test_cleared_platform_version_skips_model_but_real_changes_do_not(self):
        provider=FakeProvider([reply(content='已核对'),reply(content='真实要求变化需重新核对'),reply(content='不应重复请求')])
        analyzer=Agent(self.box,provider)
        job=dict(kind='task_center',message_id='clear-first',title='实验报告',text='完成实验报告',course='课程一',conversation='课程一班',
                 task_type='assignment',deadline=None,description='请对照课堂示例完成，细节见附件。',platform_status='未提交',
                 captured_at='2026-10-29T18:04:00+08:00',url='https://mooc1.chaoxing.com/mooc2/work/detail?courseId=1&classId=2&workId=44',
                 field_evidence={'title':'实验报告'},detail_state='read')
        first=ingest_web_message(self.box,job,analyzer=analyzer)
        changed=ingest_web_message(self.box,dict(job,message_id='clear-changed',description='请提交新的实验报告，细节见附件。'),analyzer=analyzer)
        self.assertEqual(len(provider.seen),2)
        for proposal in self.box.proposals():self.box.dismiss(proposal['id'])
        self.box.clear_messages([first['message']['id'],changed['message']['id']])
        replay=ingest_web_message(self.box,dict(job,message_id='clear-replayed'),analyzer=analyzer)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(len(provider.seen),2)

    def test_missing_submission_is_filled_but_only_confirmed_by_human(self):
        text='实验一：请将报告投递到课程邮箱。'
        provider=FakeProvider([reply([call('propose_task',dict(title='实验一',course='软件工程',kind='experiment',deadline=None,deadline_evidence='',requirements=[],submission_method='邮件提交',submission_evidence='投递到课程邮箱',evidence=text))]),reply(content='已提出草稿')])
        result=ingest_web_message(self.box,self.job(text),analyzer=Agent(self.box,provider))
        self.assertEqual(self.status(result)['status'],'model_success')
        self.assertEqual(result['proposals'][0]['candidate']['submission_method'],'邮件提交')
        self.assertEqual(self.box.tasks(),[])

    def test_model_finds_a_task_missing_from_rules_without_inventing_deadline(self):
        text='请复习本章内容。'
        provider=FakeProvider([reply([call('propose_task',dict(title='复习本章内容',course='软件工程',kind='other',deadline=None,deadline_evidence='',requirements=['复习本章内容'],submission_method='',submission_evidence='',evidence=text))]),reply(content='已提出草稿')])
        result=ingest_web_message(self.box,self.job(text),analyzer=Agent(self.box,provider))
        self.assertEqual(len(result['proposals']),1)
        self.assertEqual(result['proposals'][0]['candidate']['title'],'复习本章内容')
        self.assertIsNone(result['proposals'][0]['candidate']['deadline'])
        self.assertEqual(self.status(result)['request_count'],1)

    def test_unfounded_model_deadline_does_not_replace_unknown(self):
        text='实验一：提交报告，截止时间另行通知。'
        provider=FakeProvider([reply([call('propose_task',dict(title='实验一',course='软件工程',deadline='2026-10-30',deadline_evidence='截止时间另行通知',evidence=text))]),reply(content='已结束')])
        result=ingest_web_message(self.box,self.job(text),analyzer=Agent(self.box,provider))
        self.assertIsNone(result['proposals'][0]['candidate']['deadline'])

    def test_provider_failure_preserves_rule_draft_and_original(self):
        class Broken:
            model='test-model'
            def chat(self,*args):raise AgentError('模型连接失败。')
        result=ingest_web_message(self.box,self.job('实验一：请将报告投递到课程邮箱。'),analyzer=Agent(self.box,Broken()))
        self.assertEqual(self.status(result)['status'],'model_failed')
        self.assertEqual(self.status(result)['request_count'],1)
        self.assertEqual(len(result['proposals']),1);self.assertIn('投递到课程邮箱',result['message']['text'])

    def test_complete_task_skips_paid_review_and_unconfigured_is_explicit(self):
        provider=FakeProvider([reply(content='规则字段一致。')])
        result=ingest_web_message(self.box,self.job('实验一：截止2026年11月2日20:00，提交方式：学习通上传。'),analyzer=Agent(self.box,provider))
        self.assertEqual(len(provider.seen),0);self.assertEqual(self.status(result)['status'],'model_skipped')
        plain=ingest_web_message(self.box,self.job(message_id='plain'))
        self.assertEqual(self.status(plain)['status'],'rules_only')

    def test_unknown_publication_is_reviewed_without_using_receipt_clock(self):
        provider=FakeProvider([reply(content='发布时间未知，不能推算明天日期。')])
        job=self.job('实验一明天交报告');job.pop('captured_at')
        result=ingest_web_message(self.box,job,analyzer=Agent(self.box,provider))
        self.assertEqual(len(provider.seen),1);self.assertEqual(self.status(result)['status'],'model_success')
        import json
        source=json.loads(provider.seen[0][0][1]['content'])['source_message']
        self.assertFalse(source['publication_known']);self.assertEqual(source['captured_at'],'2000-01-01T00:00:00+08:00')
        self.assertTrue(all(not p['candidate']['deadline'] for p in result['proposals']))

    def test_structured_platform_fields_and_checkin_are_not_model_overwritten(self):
        from courseinbox.automatic_analysis import analyze
        for kind,text in [('task_center','作业一'),('chat','位置签到')]:
            with self.subTest(kind=kind):
                provider=FakeProvider([reply(content='已复核，原文没有截止时间')])
                r=analyze(dict(candidates=[],text=text,captured_at='2026-10-29T18:04:00+08:00',course='软件工程',scope='课程群',external=dict(platform='chaoxing-web',kind=kind,time_known=True)),Agent(self.box,provider))
                self.assertEqual(len(provider.seen),1);self.assertEqual(r['trace'][-1]['result']['status'],'model_success');self.assertEqual(r['candidates'],[])

    def test_structured_description_review_only_enriches_grounded_requirements(self):
        from courseinbox.automatic_analysis import analyze
        text='实验一：截止2026年11月3日20:00，请使用Python实现排序。'
        provider=FakeProvider([reply([call('propose_task',dict(title='实验一',course='软件工程',kind='experiment',deadline='2026-11-03T20:00:00+08:00',deadline_evidence='2026年11月3日20:00',requirements=['使用Python实现排序'],evidence=text))]),reply(content='已复核')])
        baseline=dict(title='实验一',course='软件工程',scope='课程群',kind='assignment',deadline='2026-11-02T20:00:00+08:00',date_precision='minute',operation='create',requirements=[],warnings=[],evidence=text,matchable=True,field_evidence={'deadline':'平台截止2026-11-02 20:00'})
        r=analyze(dict(candidates=[baseline],text=text,captured_at='2026-10-29T18:04:00+08:00',course='软件工程',scope='课程群',external=dict(platform='chaoxing-web',kind='task_center',time_known=True,detail_state='read',description=text)),Agent(self.box,provider))
        self.assertEqual(len(provider.seen),1);self.assertEqual(len(r['candidates']),1)
        c=r['candidates'][0];self.assertEqual(c['deadline'],baseline['deadline']);self.assertEqual(c['kind'],'assignment')
        self.assertEqual(c['field_evidence'],baseline['field_evidence']);self.assertIn('使用Python实现排序',c['requirements'])

    def test_desktop_worker_uses_model_without_blocking_status(self):
        import threading
        from courseinbox.desktop_sync import DesktopSync
        entered=threading.Event();release=threading.Event();read=threading.Event()
        class Slow:
            model='slow-test'
            def chat(self,*args):
                entered.set();release.wait(5);return reply(content='无任务')
        sync=DesktopSync(self.box,Path(__file__).resolve().parents[1],autostart=False,analyzer=Agent(self.box,Slow()))
        self.addCleanup(sync.close);sync.enabled=True;sync.pending=[self.job()]
        worker=threading.Thread(target=sync.retry_pending);worker.start()
        try:
            self.assertTrue(entered.wait(2))
            reader=threading.Thread(target=lambda:(sync.status(),read.set()));reader.start()
            self.assertTrue(read.wait(1),'model request blocked sync status')
        finally:release.set();worker.join(6)
        self.assertFalse(worker.is_alive());self.assertEqual(sync.pending,[])
        self.assertEqual(self.box.messages()[0]['mode'],'agent')

    def test_ollama_keeps_model_warm_with_bounded_cpu_threads(self):
        import json
        from unittest.mock import patch,MagicMock
        from courseinbox.agent import HttpProvider
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"message":{"role":"assistant","content":"ok"}}'
        with patch('urllib.request.urlopen',return_value=response) as request:
            HttpProvider('ollama','http://127.0.0.1:11434','qwen3:4b-instruct').chat([{'role':'user','content':'test'}],[])
        payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['keep_alive'],'5m')
        self.assertEqual(payload['options']['num_thread'],2)
        self.assertFalse(payload['think'])

    def test_ollama_stage_metrics_are_recorded_without_response_text(self):
        import json
        from unittest.mock import patch,MagicMock
        from courseinbox.agent import HttpProvider
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(dict(
            message=dict(role='assistant',content='仅供测试'),
            total_duration=29000000000,load_duration=5000000000,
            prompt_eval_duration=4000000000,eval_duration=20000000000,
            prompt_eval_count=400,eval_count=200)).encode()
        with patch('urllib.request.urlopen',return_value=response):
            result=Agent(self.box,HttpProvider('ollama','http://127.0.0.1:11434','test-model')).ask('性能测试')
        receipt=result['model_calls'][0]
        self.assertEqual(receipt['load_ms'],5000)
        self.assertEqual(receipt['prompt_eval_ms'],4000)
        self.assertEqual(receipt['generation_ms'],20000)
        self.assertEqual(receipt['input_tokens'],400)
        self.assertEqual(receipt['output_tokens'],200)
        self.assertEqual(receipt['tokens_per_second'],10)
        self.assertNotIn('仅供测试',json.dumps(receipt,ensure_ascii=False))

    def test_invalid_ollama_metrics_do_not_break_an_otherwise_valid_reply(self):
        import json
        from unittest.mock import patch,MagicMock
        from courseinbox.agent import HttpProvider
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(dict(
            message=dict(role='assistant',content='ok'),load_duration='invalid',
            total_duration=float('nan'),eval_duration=-1,eval_count=True)).encode()
        with patch('urllib.request.urlopen',return_value=response):
            reply=HttpProvider('ollama','http://127.0.0.1:11434','test-model').chat([],[])
        self.assertEqual(reply,dict(role='assistant',content='ok'))

    def test_deepseek_request_explicitly_disables_thinking(self):
        import json
        from unittest.mock import patch,MagicMock
        from courseinbox.agent import HttpProvider
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'
        with patch('urllib.request.urlopen',return_value=response) as request:
            HttpProvider('openai','https://api.deepseek.com','deepseek-flash','test-secret').chat([{'role':'user','content':'test'}],[])
        payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload.get('thinking'),{'type':'disabled'})



    def test_broken_notice_rule_still_calls_model_and_saves_source(self):
        from unittest.mock import patch
        provider=FakeProvider([reply(content='没有任务')])
        with patch('courseinbox.chaoxing.parse_notice_candidates',side_effect=RuntimeError('broken rule')):
            r=ingest_web_message(self.box,self.job(),Agent(self.box,provider))
        self.assertEqual(len(provider.seen),1)
        self.assertTrue(r['message']['id'])
        self.assertFalse(self.box.tasks())

    def test_structured_missing_deadline_can_use_grounded_model_result(self):
        from courseinbox.automatic_analysis import analyze
        text='实验一：截止2026年11月3日20:00，请使用Python实现排序。'
        provider=FakeProvider([reply([call('propose_task',dict(title='实验一',course='软件工程',kind='assignment',deadline='2026-11-03T20:00:00+08:00',deadline_evidence='2026年11月3日20:00',requirements=[],evidence=text))]),reply(content='已复核')])
        baseline=dict(title='实验一',course='软件工程',scope='课程群',kind='assignment',deadline=None,date_precision='unknown',operation='create',requirements=[],warnings=[],evidence=text,matchable=True,field_evidence={})
        r=analyze(dict(candidates=[baseline],text=text,captured_at='2026-10-29T18:04:00+08:00',course='软件工程',scope='课程群',external=dict(platform='chaoxing-web',kind='task_center',time_known=True,detail_state='read',description=text)),Agent(self.box,provider))
        self.assertEqual(r['candidates'][0]['deadline'],'2026-11-03T20:00:00+08:00')
        self.assertIsNone(baseline['deadline'])

if __name__=='__main__':unittest.main()
