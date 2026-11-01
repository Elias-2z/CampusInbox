import unittest

from courseinbox.core import parse_message
from courseinbox.intake_policy import automatic_candidate


class ColloquialDeadlineTests(unittest.TestCase):
    def test_half_and_quarter_hours_are_not_rounded_to_the_hour(self):
        for expression,expected in [('下午4点半','16:30'),('上午9点一刻','09:15'),('晚上8点三刻','20:45'),('下午4点30分','16:30')]:
            with self.subTest(expression=expression):
                text='实验一：请在10月31日'+expression+'前提交报告。'
                at='2026-10-29T10:36:00+08:00'
                c=parse_message(text,at,'软件工程')[0]
                self.assertEqual(c['deadline'],'2026-10-31T'+expected+':00+08:00')
                self.assertIn(expression,c['deadline_evidence'])
                guarded=automatic_candidate(c,dict(text=text,captured_at=at,chat_import=dict(platform='qq',published_at=at)))
                self.assertEqual(guarded['deadline'],c['deadline'])


if __name__=='__main__':unittest.main()
