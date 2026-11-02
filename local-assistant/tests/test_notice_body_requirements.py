import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from courseinbox.core import Inbox
from courseinbox.chaoxing import ingest_web_message


class NoticeBodyRequirementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.box=Inbox(Path(self.tmp.name)/'inbox.sqlite3')

    def receive(self,title,body,**changes):
        value=dict(kind='course_notice',message_id='notice',title=title,text=body,course='哈哈哈',conversation='哈哈哈',captured_at='2026-10-29T10:36:00+08:00',url='https://notice.chaoxing.com/pc/notice/$CACG$body/detail',attachments=[])
        return ingest_web_message(self.box,{**value,**changes})

    def test_short_homework_body_and_dated_hand_in_are_retained(self):
        r=self.receive('《哈哈哈》课程要结课了，作业通知','作业 10 月 31 日交')
        c=r['proposals'][0]['candidate']
        self.assertIn('作业 10 月 31 日交',c['requirements'])
        self.assertEqual(c['deadline'],'2026-10-31')
        self.assertEqual(c['date_precision'],'date')

    def test_exam_schedule_is_requirement_not_invented_deadline(self):
        r=self.receive('《哈哈哈》课程考试','明天考试，注意考试')
        c=r['proposals'][0]['candidate']
        self.assertIn('明天考试，注意考试',c['requirements'])
        self.assertIsNone(c['deadline'])

    def test_dated_collection_instruction_is_a_date_only_deadline(self):
        r=self.receive('《哈哈哈》课程作业 1 安排','10 月 29 号收作业！')
        c=r['proposals'][0]['candidate']
        self.assertEqual(c['deadline'],'2026-10-29')
        self.assertEqual(c['date_precision'],'date')
        self.assertIn(c['deadline_evidence'],'10 月 29 号收作业！')

    def test_collection_history_and_negation_do_not_become_deadlines(self):
        for i,body in enumerate(('10月29号不收作业','上次10月29号收作业','10月29号收到作业反馈')):
            with self.subTest(body=body):
                r=self.receive('《哈哈哈》课程作业 1 安排',body,message_id='collection-'+str(i))
                self.assertIsNone(r['proposals'][0]['candidate']['deadline'])

    def test_generic_old_notice_reanalysis_updates_its_existing_task(self):
        from courseinbox.chaoxing import refine_notice_candidates
        def legacy(candidates,external):
            result=refine_notice_candidates(candidates,external)
            for c in result:c['requirements']=[]
            return result
        with patch('courseinbox.chaoxing.refine_notice_candidates',side_effect=legacy):
            r=self.receive('《哈哈哈》课程考试','明天考试，注意考试')
        task=self.box.confirm(r['proposals'][0]['id'])
        refreshed=self.box.reanalyze(r['message']['id'])['proposals'][0]
        self.assertEqual(refreshed['target_id'],task['id'])
        updated=self.box.confirm(refreshed['id'])
        self.assertEqual(updated['id'],task['id'])
        self.assertEqual(len(self.box.tasks()),1)
        self.assertTrue(any(x['text']=='明天考试，注意考试' for x in updated['checklist']))
        self.assertIsNone(updated['deadline'])

    def test_exchange_word_does_not_become_hand_in_deadline(self):
        r=self.receive('《哈哈哈》作业讨论','10月31日交流作业思路')
        self.assertIsNone(r['proposals'][0]['candidate']['deadline'])

    def test_unknown_publication_does_not_guess_year(self):
        r=self.receive('《哈哈哈》作业通知','作业10月31日交',captured_at=None)
        self.assertTrue(all(p['candidate']['deadline'] is None for p in r['proposals']))

    def test_system_countdown_is_not_copied_as_a_new_requirement(self):
        r=self.receive('作业结束提醒','课程名称：哈哈哈\n作业名称：作业一\n你的作业还有24个小时就要结束，请尽快作答')
        self.assertEqual(r['proposals'][0]['candidate']['requirements'],[])

    def test_deadline_update_does_not_replace_existing_requirements_with_notice(self):
        r=self.receive('《哈哈哈》第一次作业','请使用C++语言编写程序。')
        task=self.box.confirm(r['proposals'][0]['id'])
        changed=self.receive('《哈哈哈》第一次作业延期','第一次作业延期到2026年11月2日20:00。',message_id='later',captured_at='2026-10-29T11:36:00+08:00')
        self.assertEqual(changed['proposals'][0].get('target_id'),task['id'])
        updated=self.box.confirm(changed['proposals'][0]['id'])
        self.assertEqual(updated['checklist'],task['checklist'])
