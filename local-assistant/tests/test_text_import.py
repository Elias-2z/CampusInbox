import tempfile
import unittest
from pathlib import Path
from courseinbox.core import Inbox
from courseinbox.agent import Agent
from tests.test_agent import FakeProvider,reply,call


class TextImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.box=Inbox(Path(self.tmp.name)/'db.sqlite3')

    def receive(self,body,agent=None):
        from courseinbox.text_import import ingest
        return ingest(self.box,body,agent)

    def test_text_alone_fills_course_kind_deadline_method_and_requirements(self):
        text='《软件工程》实验一：截止2026年11月3日20:30，提交方式：学习通上传。需使用Python编写程序，附运行截图。'
        r=self.receive(dict(text=text));c=r['proposals'][0]['candidate']
        self.assertEqual(c['course'],'软件工程');self.assertEqual(c['kind'],'experiment')
        self.assertEqual(c['deadline'],'2026-11-03T20:30:00+08:00');self.assertEqual(c['submission_method'],'学习通上传')
        self.assertTrue(any('Python' in x for x in c['requirements']));self.assertFalse(self.box.tasks())

    def test_publication_and_scope_can_be_read_from_text(self):
        r=self.receive(dict(text='发布时间：2026年10月29日18:00\n班级：计科一班\n《软件工程》实验一：明天20:00前提交报告。'))
        c=r['proposals'][0]['candidate'];self.assertEqual(c['deadline'],'2026-10-30T20:00:00+08:00');self.assertEqual(c['scope'],'计科一班')

    def test_unknown_publication_still_allows_model_but_not_relative_date_guess(self):
        text='请复习本章内容，明天课堂讨论。'
        p=FakeProvider([reply([call('propose_task',dict(title='复习本章内容',course='',kind='other',deadline=None,deadline_evidence='',evidence=text,requirements=['复习本章内容']))]),reply(content='完成')])
        r=self.receive(dict(text=text),Agent(self.box,p))
        self.assertTrue(p.seen);self.assertFalse(p.seen[0][0][1]['content'].find('publication_known')<0)
        self.assertIsNone(r['proposals'][0]['candidate']['deadline'])
        self.assertIsNone(self.box.message_display(r['message']['id'])['timing']['published_at'])

    def test_garbage_reviewed_without_task_and_replay_avoids_second_api_call(self):
        p=FakeProvider([reply(content='没有任务')]);agent=Agent(self.box,p)
        r=self.receive(dict(text='今天天气不错'),agent);self.assertEqual(r['proposals'],[])
        again=self.receive(dict(text='今天天气不错'),agent);self.assertTrue(again['duplicate']);self.assertEqual(len(p.seen),1)

    def test_missing_year_does_not_leak_placeholder_experiment_date(self):
        r=self.receive(dict(text='《软件工程》课后实验：十一月三号到实验室进行实验。'))
        c=r['proposals'][0]['candidate'];self.assertIsNone(c['deadline']);self.assertIsNone(c.get('activity_date'))

    def test_full_chinese_year_is_usable_without_publication_time(self):
        r=self.receive(dict(text='《软件工程》实验一：截止二〇二六年十一月三日晚上八点半。'))
        self.assertEqual(r['proposals'][0]['candidate']['deadline'],'2026-11-03T20:30:00+08:00')

    def test_deadline_before_delivering_materials_to_mailbox(self):
        r=self.receive(dict(text='《软件工程》实验一：请于2026年11月3日晚上八点半前将PDF实验报告投递到课程邮箱。'))
        self.assertEqual(r['proposals'][0]['candidate']['deadline'],'2026-11-03T20:30:00+08:00')

    def test_negative_delivery_instruction_does_not_create_a_deadline(self):
        r=self.receive(dict(text='《软件工程》实验一：2026年11月3日前将报告不要发送到邮箱，具体提交时间另行通知。'))
        self.assertIsNone(r['proposals'][0]['candidate']['deadline'])


    def test_simultaneous_duplicate_intake_pays_for_one_review(self):
        import threading,time
        from concurrent.futures import ThreadPoolExecutor
        class Counting:
            model='audit-mock'
            def __init__(self):self.calls=0;self.lock=threading.Lock()
            def chat(self,*args):
                with self.lock:self.calls+=1
                time.sleep(.1)
                return reply(content='没有学习任务')
        provider=Counting();agent=Agent(self.box,provider)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.receive(dict(text='普通测试消息'),agent),range(2)))
        self.assertEqual(provider.calls,1)
        self.assertEqual(sum(r['duplicate'] for r in results),1)
        self.assertEqual(len(self.box.messages()),1)

if __name__=='__main__':unittest.main()
