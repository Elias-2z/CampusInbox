import tempfile
import unittest
from pathlib import Path
from courseinbox.agent import Agent
from courseinbox.core import Inbox,parse_message
from tests.test_agent import FakeProvider,reply

AT='2026-10-25T10:00:00+08:00'
TEXT='课程名称：数据库\n作业名称：实验一\n截止时间：2026-10-30 18:00'


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.box=Inbox(Path(tmp.name)/'db.sqlite3')

    def test_complete_template_preserves_draft_without_model_call(self):
        provider=FakeProvider([reply(content='无需补充')])
        result=Agent(self.box,provider).analyze(TEXT,AT,'数据库','一班')
        self.assertEqual(provider.seen,[])
        self.assertEqual(result['candidates'][0]['deadline'],'2026-10-30T18:00:00+08:00')
        self.assertEqual(result['trace'][0]['result']['route'],'rules_verified')
        self.assertEqual(self.box.tasks(),[])

    def test_explicit_full_review_bypasses_complete_template_shortcut(self):
        provider=FakeProvider([reply(content='已核对，规则字段一致。')])
        result=Agent(self.box,provider).analyze(TEXT,AT,'数据库','一班',force_model=True)
        self.assertEqual(len(provider.seen),1);self.assertEqual(len(result['model_calls']),1)
        self.assertEqual(result['candidates'][0]['deadline'],'2026-10-30T18:00:00+08:00')

    def test_conditions_extra_prose_relative_dates_and_multiple_names_use_model(self):
        for text in (TEXT+'\n补交同学除外。',TEXT.replace('2026-10-30 18:00','明天18:00'),TEXT.replace('实验一','实验一和实验二'),TEXT+'\n截止时间：2026-11-01',TEXT.replace('截止时间','开始时间')):
            with self.subTest(text=text):
                provider=FakeProvider([reply(content='无需补充')])
                Agent(self.box,provider).analyze(text,AT,'数据库','一班')
                self.assertTrue(provider.seen)

    def test_incorrect_rule_value_never_bypasses_model_verification(self):
        baseline=parse_message(TEXT,AT,'数据库','一班')
        baseline[0]['deadline']='2026-10-29T18:00:00+08:00'
        provider=FakeProvider([reply(content='核查')])
        Agent(self.box,provider).analyze(TEXT,AT,'数据库','一班',rule_candidates=baseline)
        self.assertTrue(provider.seen)

    def test_incorrect_kind_and_hidden_submission_instructions_use_model(self):
        baseline=parse_message(TEXT,AT,'数据库','一班')
        baseline[0]['kind']='exam'
        provider=FakeProvider([reply(content='核查')])
        Agent(self.box,provider).analyze(TEXT,AT,'数据库','一班',rule_candidates=baseline)
        self.assertTrue(provider.seen)
        conditional=TEXT+'\n提交方式：学习通上传，补交同学除外'
        baseline=parse_message(conditional,AT,'数据库','一班')
        baseline[0]['submission_method']='学习通上传，补交同学除外'
        provider=FakeProvider([reply(content='核查')])
        Agent(self.box,provider).analyze(conditional,AT,'数据库','一班',rule_candidates=baseline)
        self.assertTrue(provider.seen)

    def test_rule_parser_exception_uses_model_fallback(self):
        from unittest.mock import patch
        provider=FakeProvider([reply(content='未发现任务')])
        with patch('courseinbox.agent.parse_message',side_effect=RuntimeError('parser failed')):
            result=Agent(self.box,provider).analyze('新的通知',AT)
        self.assertEqual(len(provider.seen),1)
        self.assertTrue(any(x.get('tool')=='rule_fallback' for x in result['trace']))

    def test_empty_extension_result_does_not_skip_model(self):
        provider=FakeProvider([reply(content='需要核对原任务')])
        Agent(self.box,provider).analyze('实验一延期三天',AT,rule_candidates=[])
        self.assertEqual(len(provider.seen),1)

    def test_template_verification_exception_still_uses_model(self):
        from unittest.mock import patch
        provider=FakeProvider([reply(content='已按原文复核')])
        with patch('courseinbox.model_routing.verified_template',side_effect=RuntimeError('verification failed')):
            result=Agent(self.box,provider).analyze(TEXT,AT)
        self.assertEqual(len(provider.seen),1)
        self.assertEqual(result['candidates'][0]['deadline'],'2026-10-30T18:00:00+08:00')
        self.assertTrue(any(x['tool']=='rule_fallback' for x in result['trace']))

    def test_complete_natural_notice_skips_paid_review(self):
        text='《数据库》实验一：截止2026年11月3日20:00，提交方式：学习通上传。请使用Python实现排序。'
        provider=FakeProvider([reply(content='不应调用')])
        result=Agent(self.box,provider).analyze(text,AT)
        self.assertEqual(provider.seen,[])
        self.assertEqual(result['candidates'][0]['deadline'],'2026-11-03T20:00:00+08:00')
        self.assertEqual(self.box.tasks(),[])

    def test_explicit_unannounced_deadline_does_not_trigger_paid_review(self):
        text='实验一：提交报告，截止时间另行通知。'
        provider=FakeProvider([reply(content='不应调用')])
        result=Agent(self.box,provider).analyze(text,AT,'数据库')
        self.assertEqual(provider.seen,[])
        self.assertIsNone(result['candidates'][0]['deadline'])

    def test_source_submission_instruction_missing_from_rules_uses_model(self):
        provider=FakeProvider([reply(content='需补充邮箱提交方式')])
        Agent(self.box,provider).analyze('实验一：请将报告投递到课程邮箱。',AT,'数据库')
        self.assertEqual(len(provider.seen),1)

    def test_source_without_deadline_does_not_call_model_just_for_blank_date(self):
        provider=FakeProvider([reply(content='不应调用')])
        result=Agent(self.box,provider).analyze('实验一：请提交报告。',AT,'数据库')
        self.assertEqual(provider.seen,[])
        self.assertIsNone(result['candidates'][0]['deadline'])

    def test_uncaptured_instructions_and_conflicting_dates_require_review(self):
        texts=[
            '实验一：2026年11月3日20:00截止，2026年11月5日20:00截止，请提交报告。',
            '实验一：截止2026年11月3日20:00。按刚才讨论的方案整理好材料。',
            '实验一：截止2026年11月3日20:00，请提交报告，发到老师邮箱。',
            '实验一：截止2026年11月3日20:00，请提交报告，交到学习委员处。',
            '实验一：截止2026年11月3日20:00，请提交报告；另做一份答辩PPT。',
            '实验一：截止2026年11月3日20:00，需使用Python编写程序，附运行截图。',
        ]
        for text in texts:
            with self.subTest(text=text):
                provider=FakeProvider([reply(content='需按原文补充核对')])
                Agent(self.box,provider).analyze(text,AT,'数据库')
                self.assertEqual(len(provider.seen),1)
