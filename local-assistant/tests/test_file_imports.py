import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from courseinbox.core import Inbox, Conflict, ValidationError
from courseinbox.file_imports import FileImports, create_manual_task
from courseinbox.mineru import MinerUError, MinerUFailed, MinerUProvider, NoRedirect, api_key, markdown_from_zip, safe_url


class FakeMinerU:
    def __init__(self):
        self.submissions = 0
        self.text = None
        self.failure = None

    def submit(self, path, filename, attachment_id):
        self.submissions += 1
        return 'batch-' + str(self.submissions)

    def poll(self, batch_id):
        if self.failure:
            raise self.failure
        return self.text


class FileImportsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inbox = Inbox(self.root / 'inbox.sqlite3')
        self.provider = FakeMinerU()
        self.workspace = FileImports(self.inbox, self.root, lambda: self.provider)
        self.key = patch('courseinbox.file_imports.api_key', return_value='test-key')
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.temporary.cleanup()

    def upload(self, filename='课程.txt', content=None, consent=False):
        content = content if content is not None else '《软件工程》 第一次作业：提交报告。截止2026年11月8日。'.encode()
        return self.workspace.upload(filename, content, consent=consent)

    def test_text_is_local_and_persists_with_original(self):
        content = '《软件工程》 第一次作业：提交报告。'.encode('gb18030')
        item = self.upload(content=content)
        self.assertEqual(item['status'], 'ready')
        self.assertIn('软件工程', item['text'])
        self.assertEqual(self.provider.submissions, 0)
        reopened = FileImports(self.inbox, self.root, lambda: self.provider)
        self.assertEqual(reopened.list()[0]['id'], item['id'])
        self.assertEqual(reopened.download(item['id']), ('课程.txt', content))

    def test_validation_before_cloud_or_original_save(self):
        for filename, content in [('../evil.txt', b'x'), ('a.zip', b'x'), ('a.txt', b''), ('a.txt', b'bad\x00text'), ('a.txt', b'x' * (20 * 1024 * 1024 + 1))]:
            with self.subTest(filename=filename, size=len(content)):
                with self.assertRaises(ValidationError):
                    self.upload(filename, content)
        with self.assertRaises(ValidationError):
            self.upload('image.png', b'png')
        self.key.stop()
        with patch('courseinbox.file_imports.api_key', return_value=''):
            with self.assertRaises(ValidationError):
                self.upload('image.png', b'png', True)
        self.key.start()
        self.assertEqual(self.provider.submissions, 0)
        self.assertEqual(self.workspace.list(), [])

    def test_transient_failure_keeps_batch_and_resume_does_not_resubmit(self):
        item = self.upload('image.png', b'png', True)
        self.provider.failure = MinerUError('暂时断网')
        item = self.workspace.poll(item['id'])
        self.assertEqual(item['status'], 'parsing')
        self.assertIn('断网', item['error'])
        self.workspace.retry(item['id'])
        self.assertEqual(self.provider.submissions, 1)
        self.provider.failure = None
        self.provider.text = '《软件工程》 第一次作业截止2026年11月8日。'
        reopened = FileImports(self.inbox, self.root, lambda: self.provider)
        self.assertEqual(reopened.poll(item['id'])['status'], 'ready')

    def test_expired_batch_can_retry_but_ready_cannot(self):
        item = self.upload('a.pdf', b'pdf', True)
        self.provider.failure = MinerUFailed('批次已过期')
        self.assertEqual(self.workspace.poll(item['id'])['status'], 'failed')
        self.provider.failure = None
        self.workspace.retry(item['id'])
        self.assertEqual(self.provider.submissions, 2)
        self.provider.text = '课程文字'
        self.workspace.poll(item['id'])
        with self.assertRaises(Conflict):
            self.workspace.retry(item['id'])

    def test_late_old_poll_cannot_overwrite_completed_retry(self):
        item = self.upload('a.pdf', b'pdf', True)
        entered, release = threading.Event(), threading.Event()
        calls = 0
        def poll(batch):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(5)
                return '旧结果'
            raise MinerUFailed('任务已过期')
        self.provider.poll = poll
        result = []
        worker = threading.Thread(target=lambda: result.append(self.workspace.poll(item['id'])))
        worker.start()
        self.assertTrue(entered.wait(3))
        try:
            self.workspace.poll(item['id'])
            self.workspace.retry(item['id'])
            self.provider.poll = lambda batch: '新结果'
            self.workspace.poll(item['id'])
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(self.workspace.get(item['id'])['text'], '新结果')
        self.assertEqual(result[0]['text'], '新结果')

    def test_concurrent_retry_submits_only_once(self):
        item = self.upload('a.pdf', b'pdf', True)
        self.provider.failure = MinerUFailed('已过期')
        self.workspace.poll(item['id'])
        entered, release = threading.Event(), threading.Event()
        def submit(*args):
            self.provider.submissions += 1
            entered.set()
            release.wait(5)
            return 'new-batch'
        self.provider.submit = submit
        worker = threading.Thread(target=lambda: self.workspace.retry(item['id']))
        worker.start()
        self.assertTrue(entered.wait(3))
        try:
            with self.assertRaises(Conflict):
                self.workspace.retry(item['id'])
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(self.provider.submissions, 2)

    def test_attachment_submission_is_reviewed_idempotent_and_downloadable(self):
        item = self.upload()
        body = dict(attachment_id=item['id'], text=item['text'], source='课程文件', published_at='2026-11-01T08:00:00+08:00', mode='rules')
        result = self.workspace.ingest(body)
        again = self.workspace.ingest(dict(body, text='重复点击时不同文本'))
        self.assertEqual(result['message']['id'], again['message']['id'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.inbox.tasks(), [])
        self.assertEqual(len(self.inbox.proposals()), 1)
        message = self.inbox.message(result['message']['id'])
        self.assertEqual(message['external']['attachments'][0]['upload_id'], item['id'])
        self.assertEqual(self.workspace.list(), [])
        task = self.inbox.confirm(result['proposals'][0]['id'])
        self.assertEqual(self.inbox.task_detail(task['id'])['sources'][0]['external']['attachments'][0]['upload_id'], item['id'])

    def test_bad_submission_and_unready_attachment_do_not_create_messages(self):
        item = self.upload('a.pdf', b'pdf', True)
        with self.assertRaises(Conflict):
            self.workspace.ingest(dict(attachment_id=item['id'], text='尚未解析', source='附件'))
        item = self.upload()
        with self.assertRaises(ValidationError):
            self.workspace.ingest(dict(attachment_id=item['id'], text='x' * 20001, source='附件'))
        self.assertEqual(self.inbox.messages(), [])

    def test_submission_recovers_after_message_commit_before_link_commit(self):
        item = self.upload()
        body = dict(attachment_id=item['id'], text=item['text'], source='附件', published_at='2026-11-01T08:00:00+08:00', mode='rules')
        save = self.workspace._save
        def interrupted(db, record):
            if record.get('message_id'):
                raise RuntimeError('simulate process failure before linking')
            return save(db, record)
        with patch.object(self.workspace, '_save', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.workspace.ingest(body)
        reopened = FileImports(self.inbox, self.root, lambda: self.provider)
        result = reopened.ingest(dict(body, text='后来编辑的内容不能重复创建'))
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(self.inbox.messages()), 1)
        self.assertEqual(len(self.inbox.proposals()), 1)
        self.assertEqual(result['message']['text'], item['text'])

    def test_interrupted_upload_is_recoverable_after_restart(self):
        item = self.upload('a.pdf', b'pdf', True)
        with self.inbox.connection() as db:
            saved = self.workspace._read(db, item['id'])
            saved.update(status='uploading', batch_id='')
            self.workspace._save(db, saved)
        reopened = FileImports(self.inbox, self.root, lambda: self.provider)
        self.assertEqual(reopened.get(item['id'])['status'], 'failed')
        self.assertEqual(reopened.retry(item['id'])['status'], 'parsing')

    def test_manual_task_preserves_date_only_and_retries_once(self):
        body = dict(request_id='a' * 32, title='实验三', course='软件工程', scope='1班', kind='experiment', deadline='2026-11-08', requirements=['实验报告'], submission_method='学习通')
        task = create_manual_task(self.inbox, body)
        self.assertEqual(task['deadline'], '2026-11-08')
        self.assertEqual(task['date_precision'], 'date')
        self.assertEqual(task['status'], 'pending')
        self.assertEqual(task['checklist'], [dict(text='实验报告', done=False)])
        self.assertEqual(create_manual_task(self.inbox, body)['id'], task['id'])
        self.assertEqual(self.inbox.proposals(), [])
        with self.assertRaises(Conflict):
            create_manual_task(self.inbox, dict(body, title='修改后重复提交'))
        with self.assertRaises(Conflict):
            create_manual_task(self.inbox, dict(body, request_id='b' * 32))
        with self.assertRaises(ValidationError):
            create_manual_task(self.inbox, dict(body, request_id='c' * 32, deadline='2026-02-30'))


class FileImportsHTTPTests(unittest.TestCase):
    def setUp(self):
        from courseinbox.http import create_server
        self.temporary = tempfile.TemporaryDirectory()
        self.inbox = Inbox(Path(self.temporary.name) / 'inbox.sqlite3')
        self.token = 'file-import-http-isolated-test-token'
        self.server = create_server('127.0.0.1', 0, self.inbox, self.token, reminder_port=0, qq_port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temporary.cleanup()

    def call(self, path, data=None, *, binary=False, authenticated=True):
        headers = {'Content-Type': 'application/octet-stream' if binary else 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url + path, data=data if binary or data is None else json.dumps(data).encode(), headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, raw if 'application/octet-stream' in response.headers.get('Content-Type', '') else json.loads(raw)

    def test_authenticated_upload_submit_and_download(self):
        status, item = self.call('/api/imports/files?filename=notice.txt', '《测试课程》 第一次作业截止2026年11月8日。'.encode(), binary=True)
        self.assertEqual(status, 201)
        self.assertEqual(item['status'], 'ready')
        self.assertNotIn('batch_id', item)
        self.assertEqual(self.call('/api/imports/files')[1][0]['id'], item['id'])
        status, result = self.call('/api/imports/submit', dict(attachment_id=item['id'], text=item['text'], source='课程文件', published_at='2026-11-01T08:00:00+08:00', mode='rules'))
        self.assertEqual(status, 201)
        self.assertEqual(len(result['proposals']), 1)
        self.assertEqual(self.call('/api/attachments/' + item['id'] + '/download')[1], '《测试课程》 第一次作业截止2026年11月8日。'.encode())
        self.assertEqual(self.call('/api/attachments/' + item['id'] + '/download', authenticated=False)[0], 401)

    def test_manual_create_and_validation_routes(self):
        self.assertEqual(self.call('/api/imports/capabilities', authenticated=False)[0], 401)
        config = self.call('/api/imports/capabilities')[1]
        self.assertEqual(config['max_bytes'], 20 * 1024 * 1024)
        self.assertIn('.pptx', config['extensions'])
        self.assertNotIn('api_key', config)
        self.assertEqual(self.call('/api/imports/files?filename=test.zip', b'zip', binary=True)[0], 400)
        body = dict(request_id='f' * 32, title='直接创建', course='软件工程', deadline='2026-11-08', kind='assignment')
        status, task = self.call('/api/tasks', body)
        self.assertEqual(status, 201)
        self.assertEqual(self.call('/api/tasks', body)[1]['id'], task['id'])
        self.assertEqual(self.call('/api/proposals')[1], [])


class MinerUSafetyTests(unittest.TestCase):
    def test_token_is_only_sent_to_api_and_results_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('MINERU_API_KEY=file-token\n', encoding='utf-8')
            path = root / 'a.pdf'
            path.write_bytes(b'pdf-original')
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('full.md', '解析正文')
            calls = []
            responses = [json.dumps(dict(code=0, data=dict(batch_id='batch-1', file_urls=['https://bucket.oss-cn-shanghai.aliyuncs.com/upload']))).encode(), b'', json.dumps(dict(code=0, data=dict(extract_result=[dict(state='done', full_zip_url='https://cdn-mineru.openxlab.org.cn/result.zip')]))).encode(), archive.getvalue()]
            class Opener:
                def open(self, request, timeout):
                    calls.append(request)
                    return io.BytesIO(responses.pop(0))
            with patch.dict(os.environ, {'MINERU_API_KEY': 'env-token'}):
                provider = MinerUProvider(root, Opener())
                self.assertEqual(provider.submit(path, 'a.pdf', 'a' * 32), 'batch-1')
                self.assertEqual(provider.poll('batch-1'), '解析正文')
            self.assertEqual(calls[0].get_header('Authorization'), 'Bearer env-token')
            self.assertEqual(calls[2].get_header('Authorization'), 'Bearer env-token')
            self.assertIsNone(calls[1].get_header('Authorization'))
            self.assertIsNone(calls[3].get_header('Authorization'))
            self.assertEqual(calls[1].data, b'pdf-original')
            with patch.dict(os.environ, {'MINERU_API_KEY': ''}):
                self.assertEqual(api_key(root), 'file-token')
                (root / '.env').write_text('MINERU_API_KEY=updated-token\n', encoding='utf-8')
                self.assertEqual(api_key(root), 'updated-token')
            self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_malformed_remote_shapes_do_not_expose_remote_response(self):
        class Opener:
            def __init__(self, raw):
                self.raw = raw
            def open(self, request, timeout):
                return io.BytesIO(self.raw)
        with patch.dict(os.environ, {'MINERU_API_KEY': 'private-test-token'}):
            for raw in [b'invalid-json', b'[]', b'{"code":0,"data":[]}', b'{"code":0,"data":{"extract_result":[null]}}']:
                with self.subTest(raw=raw), self.assertRaises(MinerUError) as captured:
                    MinerUProvider(opener=Opener(raw)).poll('batch-1')
                self.assertNotIn('private-test-token', str(captured.exception))

    def test_urls_reject_non_https_foreign_hosts_and_credentials(self):
        for value in ['http://mineru.net/x', 'https://mineru.net.evil.test/x', 'https://127.0.0.1/x', 'https://token@mineru.net/x', 'https://mineru.net:invalid/x']:
            with self.subTest(value=value):
                with self.assertRaises(MinerUError):
                    safe_url(value)
        self.assertEqual(safe_url('https://cdn-mineru.openxlab.org.cn/result.zip'), 'https://cdn-mineru.openxlab.org.cn/result.zip')

    def test_zip_reads_body_and_deduplicated_headers_without_extracting(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('../../full.md', '实验三\n11月8日截止')
            archive.writestr('x_content_list.json', json.dumps([dict(type='header', text='软件工程'), dict(type='header', text='软件工程'), dict(type='footer', text='作业材料'), dict(type='page_number', text='1')]))
        text = markdown_from_zip(buffer.getvalue())
        self.assertEqual(text.count('软件工程'), 1)
        self.assertIn('11月8日截止', text)
        self.assertIn('作业材料', text)
        with self.assertRaises(MinerUError):
            markdown_from_zip(b'not a ZIP')


if __name__ == '__main__':
    unittest.main()
