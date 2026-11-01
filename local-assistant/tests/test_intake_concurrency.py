import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from courseinbox.core import Inbox
from courseinbox.agent import Agent
from tests.test_agent import reply


class IntakeConcurrencyTests(unittest.TestCase):
    def test_text_chat_and_chaoxing_coalesce_parallel_replays(self):
        from courseinbox.text_import import ingest as text_import
        from courseinbox.chat_import import ingest as chat_import
        from courseinbox.chaoxing import ingest_web_message
        calls=[
            lambda box,agent:text_import(box,dict(text='普通测试消息'),agent),
            lambda box,agent:chat_import(box,dict(platform='qq',conversation='课程群',text='普通测试消息',mode='agent'),agent),
            lambda box,agent:ingest_web_message(box,dict(kind='course_notice',message_id='concurrent',title='普通通知',text='普通测试消息',course='软件工程',conversation='课程群',captured_at='2026-10-30T12:00:00+08:00',url='https://notice.chaoxing.com/pc/notice/concurrent/detail',attachments=[]),agent),
        ]
        for receive in calls:
            with self.subTest(source=receive),tempfile.TemporaryDirectory() as directory:
                box=Inbox(Path(directory)/'audit.db');entered=threading.Event();release=threading.Event()
                class Counting:
                    model='local-audit'
                    count=0
                    def chat(self,*args):
                        self.count+=1;entered.set();release.wait(2)
                        return reply(content='没有学习任务')
                provider=Counting();agent=Agent(box,provider)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    first=pool.submit(receive,box,agent)
                    self.assertTrue(entered.wait(2))
                    second=pool.submit(receive,box,agent)
                    # Database reads remain available during the paid request.
                    reading=pool.submit(box.messages)
                    try:self.assertEqual(reading.result(timeout=1),[])
                    finally:release.set()
                    results=[first.result(timeout=4),second.result(timeout=4)]
                self.assertEqual(provider.count,1)
                self.assertEqual(sum(r['duplicate'] for r in results),1)
                self.assertEqual(len(box.messages()),1)
                self.assertEqual(box.tasks(),[])
