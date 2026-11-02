import hashlib
import hmac
import json
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from courseinbox.core import Inbox,ValidationError
from courseinbox.qq_connector import QQConnector


class QQConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.box=Inbox(Path(self.tmp.name)/'inbox.sqlite3')
        self.qq=QQConnector(self.box,'test-main-token-long-enough',port=0,background=False)
        self.addCleanup(self.qq.close)
        self.qq.configure(dict(enabled=True,groups=[dict(id='12345',name='软件工程课程群',course='软件工程')]))

    def event(self,**changes):
        return dict(post_type='message',message_type='group',self_id=11111,group_id=12345,user_id=22222,message_id=101,time=1793253600,message=[dict(type='text',data={'text':'实验报告：明天20:00前提交PDF报告。'})],sender=dict(nickname='王老师'),**changes)

    def test_ingest_keeps_publication_and_provenance_and_creates_only_draft(self):
        self.qq.accept(self.event());self.qq.run_once()
        m=self.box.messages()[0]
        self.assertEqual(m['chat_import']['transport'],'onebot11')
        self.assertEqual(m['chat_import']['group_id'],'12345')
        self.assertEqual(m['chat_import']['published_at'],'2026-10-29T14:00:00+08:00')
        self.assertEqual(self.box.proposals()[0]['candidate']['deadline'],'2026-10-30T20:00:00+08:00')
        self.assertFalse(self.box.tasks())

    def test_exact_replay_is_idempotent_but_changed_content_is_retained(self):
        event=self.event();self.qq.accept(event);self.qq.run_once()
        self.assertTrue(self.qq.accept(event)['duplicate'])
        event['message']=[dict(type='text',data={'text':'实验报告截止改为2026年11月2日20:00。'})]
        self.qq.accept(event);self.qq.run_once()
        self.assertEqual(len(self.box.messages()),2)

    def test_nonallowed_group_self_private_and_heartbeat_are_ignored(self):
        for changes in ({'group_id':99999},{'user_id':11111},{'message_type':'private'},{'post_type':'meta_event'}):
            event=self.event();event.update(changes)
            self.assertTrue(self.qq.accept(event)['ignored'])
        self.qq.run_once();self.assertFalse(self.box.messages())

    def test_unknown_time_and_media_do_not_invent_deadline(self):
        event=self.event();event.pop('time');event['message'].append(dict(type='image',data={'file':'notice.png','url':'http://127.0.0.1/private'}))
        self.qq.accept(event);self.qq.run_once()
        m=self.box.messages()[0]
        self.assertIsNone(m['chat_import']['published_at'])
        self.assertEqual(m['chat_import']['media_pending_count'],1)
        self.assertIsNone(self.box.proposals()[0]['candidate']['deadline'])

    def test_failed_ingest_retries_after_restart_without_duplicate_business_writes(self):
        self.qq.accept(self.event())
        with patch.object(self.box,'ingest',side_effect=RuntimeError('secret body')):self.qq.run_once()
        self.assertEqual(self.qq.status()['counts']['failed'],1)
        self.assertNotIn('secret body',json.dumps(self.qq.status()))
        self.qq.close()
        self.qq=QQConnector(self.box,'test-main-token-long-enough',port=0,background=False)
        self.addCleanup(self.qq.close);self.qq.retry();self.qq.run_once()
        self.assertEqual(len(self.box.messages()),1)

    def test_receiver_requires_signature_and_rejects_browser_origin(self):
        endpoint=self.qq.status()['endpoint'];raw=json.dumps(self.event()).encode()
        def send(headers):
            request=urllib.request.Request(endpoint,raw,headers={'Content-Type':'application/json',**headers})
            try:
                with urllib.request.urlopen(request,timeout=3) as r:return r.status
            except urllib.error.HTTPError as e:return e.code
        self.assertEqual(send({}),401)
        signature='sha1='+hmac.new(self.qq.credential().encode(),raw,hashlib.sha1).hexdigest()
        self.assertEqual(send({'X-Signature':signature}),200)
        self.assertEqual(send({'X-Signature':signature,'Origin':'http://evil.invalid'}),403)

    def test_invalid_settings_and_invalid_time_do_not_write_messages(self):
        for body in (dict(enabled=True,groups=[]),dict(enabled=True,groups=[dict(id='bad',name='群')]),dict(enabled='yes',groups=[])):
            with self.assertRaises(ValidationError):self.qq.configure(body)
        event=self.event();event['time']=True
        with self.assertRaises(ValidationError):self.qq.accept(event)
        self.assertFalse(self.box.messages())

    def test_same_named_groups_do_not_merge_tasks(self):
        self.qq.configure(dict(enabled=True,groups=[dict(id=str(i),name='同名课程群',course='软件工程') for i in (12345,54321)]))
        event=self.event();event['message']=[dict(type='text',data={'text':'第三次作业：明天20:00前提交报告。'})]
        self.qq.accept(event);self.qq.run_once();self.box.confirm(self.box.proposals()[0]['id'])
        event['group_id']=54321;self.qq.accept(event);self.qq.run_once()
        self.assertIsNone(self.box.proposals()[0]['target_id'])

    def test_crash_after_business_commit_does_not_duplicate_on_retry(self):
        self.qq.accept(self.event());ingest=self.box.ingest
        def crash(*args,**kwargs):ingest(*args,**kwargs);raise RuntimeError('crash')
        with patch.object(self.box,'ingest',side_effect=crash):self.qq.run_once()
        self.qq.retry();self.qq.run_once()
        self.assertEqual(len(self.box.messages()),1)
        self.assertEqual(len(self.box.proposals()),1)

    def test_queued_event_is_not_processed_after_group_is_removed(self):
        self.qq.accept(self.event());self.qq.configure(dict(enabled=True,groups=[dict(id='77777',name='其他群')]))
        self.qq.run_once();self.assertFalse(self.box.messages());self.assertEqual(self.qq.status()['counts']['blocked'],1)

    def test_media_only_and_cq_text_and_task_alerts(self):
        event=self.event();event['message']='实验报告：明天20:00前提交PDF。[CQ:image,file=notice.png]'
        self.qq.accept(event);self.qq.run_once()
        from courseinbox.desktop_alerts import DesktopAlerts
        from courseinbox.core import parse_timestamp
        alerts=DesktopAlerts(self.box)
        with self.box.connection() as db:events=alerts._events(db,parse_timestamp('2026-10-29T14:01:00+08:00'),initialized_at='2000-01-01T00:00:00+08:00')
        self.assertTrue(any(e['title'].startswith('QQ') for e in events))
        event['message_id']=102;event['message']=[dict(type='image',data={'file':'only.png'})]
        self.qq.accept(event);self.qq.run_once()
        self.assertEqual(len(self.box.proposals()),1)

    def test_pause_during_analysis_preserves_pending_without_import(self):
        from types import SimpleNamespace
        self.qq.accept(self.event())
        def analyze(*args):
            self.qq.configure(dict(enabled=False,groups=self.qq.config['groups']))
            return dict(candidates=[],mode='rules',trace=[])
        self.qq.analyzer=SimpleNamespace(analyze=analyze);self.qq.run_once()
        self.assertFalse(self.box.messages())
        self.assertEqual(self.qq.status()['counts']['pending'],1)

    def test_json_object_key_order_does_not_create_new_event(self):
        event=self.event();self.qq.accept(event)
        event['message']=[{'data':{'text':event['message'][0]['data']['text']},'type':'text'}]
        self.assertTrue(self.qq.accept(event).get('duplicate'))
