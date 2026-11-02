import unittest
from courseinbox.core import parse_message,parse_timestamp,extract_deadline
from courseinbox.intake_policy import automatic_candidate

AT='2026-10-29T18:40:00+08:00'


class ChineseDateTests(unittest.TestCase):
    def guard(self,text):
        c=parse_message(text,AT,'测试课程')[0]
        return automatic_candidate(c,dict(text=text,captured_at=AT,external=dict(platform='chaoxing-web',kind='course_notice',time_known=True,captured_at=AT)))

    def test_chinese_deadline_in_requirements_preserves_exact_evidence(self):
        for text,want in [('实验一\n任务要求：十一月 三号前提交报告。','2026-11-03'),('实验一：截止二〇二六年十一月三日晚上八点半。','2026-11-03T20:30:00+08:00'),('实验一：十一月三号收作业！','2026-11-03')]:
            with self.subTest(text=text):
                c=self.guard(text);self.assertEqual(c['deadline'],want);self.assertIn(c['deadline_evidence'],text)

    def test_experiment_arrangement_is_identified_without_inventing_submission_deadline(self):
        text='课后实验通知\n十一月 三号到实验室进行实验，请自备一头母猪！'
        c=self.guard(text)
        self.assertIsNone(c['deadline']);self.assertEqual(c.get('activity_date'),'2026-11-03')
        self.assertTrue(any('实验日期' in w and '2026-11-03' in w for w in c['warnings']))

    def test_publication_and_invalid_chinese_dates_are_not_deadlines(self):
        for text in ('实验一\n发布时间：十一月三号','实验一：截止十一月三十一日','实验一：截止十十月三日'):
            with self.subTest(text=text):self.assertIsNone(self.guard(text)['deadline'])

    def test_chinese_date_extraction_does_not_rewrite_source_or_borrow_clock(self):
        text='截止十一月三日。次日晚上八点开会。'
        due,precision,evidence,_=extract_deadline(text,parse_timestamp(AT))
        self.assertEqual(due,'2026-11-03');self.assertEqual(precision,'date');self.assertEqual(evidence,'十一月三日')

    def test_evidence_points_to_the_deadline_not_an_earlier_equivalent_date(self):
        text='实验一：11月3日讨论。截止十一月三日。'
        due,_,evidence,_=extract_deadline(text,parse_timestamp(AT))
        self.assertEqual(due,'2026-11-03');self.assertEqual(evidence,'十一月三日')

    def test_model_chinese_deadline_is_accepted_by_source_guard(self):
        import tempfile
        from pathlib import Path
        from courseinbox.core import Inbox
        from courseinbox.agent import Agent
        from tests.test_agent import FakeProvider,reply,call
        text='实验一：十一月 三号前提交报告。'
        with tempfile.TemporaryDirectory() as directory:
            box=Inbox(Path(directory)/'test.sqlite3')
            provider=FakeProvider([reply([call('propose_task',dict(title='实验一',course='测试课程',deadline='2026-11-03',deadline_evidence='十一月 三号前提交报告',evidence=text))]),reply(content='完成')])
            result=Agent(box,provider).analyze(text,AT,'测试课程')
            self.assertNotIn('error',result['trace'][0]['result'])
            self.assertEqual(result['candidates'][0]['deadline'],'2026-11-03')


if __name__=='__main__':unittest.main()
