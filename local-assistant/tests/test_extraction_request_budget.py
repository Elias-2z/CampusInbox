import tempfile
import unittest
from pathlib import Path

from courseinbox.agent import Agent
from courseinbox.core import Inbox
from tests.test_agent import FakeProvider, call, reply


class ExtractionRequestBudgetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.box = Inbox(Path(tmp.name) / 'test.sqlite3')
        self.text = '实验一：请提交报告。\n实验二：请提交源码。'

    def analyze(self, provider):
        return Agent(self.box, provider).analyze(
            self.text, '2026-10-31T10:00:00+08:00', '数据库', force_model=True)

    def proposal(self, title):
        return call('propose_task', dict(title=title, course='数据库', evidence=self.text), title)

    def test_all_proposals_in_one_response_need_no_paid_closing_response(self):
        provider = FakeProvider([reply([self.proposal('实验一'), self.proposal('实验二')])])
        result = self.analyze(provider)
        self.assertEqual(len(provider.seen), 1)
        self.assertEqual(len(result['model_calls']), 1)
        self.assertEqual({c['title'] for c in result['candidates']}, {'实验一', '实验二'})
        self.assertFalse(result['limited'])
        self.assertEqual(self.box.tasks(), [])

    def test_invalid_arguments_allow_one_correction(self):
        provider = FakeProvider([reply([call('delete_task', {})]), reply([self.proposal('实验一')])])
        result = self.analyze(provider)
        self.assertEqual(len(provider.seen), 2)
        self.assertFalse(result['limited'])
        self.assertTrue(any(c['title'] == '实验一' for c in result['candidates']))

    def test_repeated_invalid_arguments_stop_at_two_requests_and_keep_rules(self):
        provider = FakeProvider([reply([call('delete_task', {})])] * 8)
        result = self.analyze(provider)
        self.assertEqual(len(provider.seen), 2)
        self.assertEqual(len(result['model_calls']), 2)
        self.assertTrue(result['limited'])
        self.assertTrue(result['candidates'])
        self.assertEqual(self.box.tasks(), [])
