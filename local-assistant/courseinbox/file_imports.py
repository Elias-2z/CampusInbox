"""Persistent attachment drafts feeding the existing task review workflow."""
import hashlib
import json
import re
import uuid
from pathlib import Path

from .core import Conflict, NotFound, ValidationError, clean_string, dump, now_iso, task_key, validate_candidate
from .mineru import MinerUError, MinerUFailed, MinerUProvider, api_key

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TEXT_TYPES = {'.txt', '.md'}
CLOUD_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.pdf', '.doc', '.docx', '.ppt', '.pptx'}


def local_text(content):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = content.decode(encoding).strip()
        except UnicodeError:
            continue
        if not text or '\x00' in text:
            break
        if len(text) > 200000:
            raise ValidationError('文本过长，请拆分为较短通知后上传。')
        return text
    raise ValidationError('无法读取文字，请使用 UTF-8 或 GB18030 编码的文本文件。')


class FileImports:
    def __init__(self, inbox, root, provider_factory=None):
        self.inbox, self.root = inbox, Path(root)
        self.directory = inbox.path.parent / 'uploads'
        self.factory = provider_factory or (lambda: MinerUProvider(self.root))
        self.lock = inbox.lock
        with self.lock, inbox.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS file_imports(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            for row in db.execute('SELECT data FROM file_imports').fetchall():
                item = json.loads(row['data'])
                if item['status'] == 'uploading':
                    item.update(status='failed', error='上次上传被中断，可重新解析。')
                    self._save(db, item)

    def capabilities(self):
        return dict(mineru_configured=bool(api_key(self.root)), max_bytes=MAX_UPLOAD_BYTES, extensions=sorted(TEXT_TYPES | CLOUD_TYPES))

    def _read(self, db, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise NotFound('附件不存在。')
        row = db.execute('SELECT data FROM file_imports WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise NotFound('附件不存在。')
        return json.loads(row['data'])

    def _save(self, db, item):
        db.execute('INSERT INTO file_imports VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (item['id'], dump(item)))

    @staticmethod
    def public(item):
        return {name: item.get(name) for name in ('id', 'filename', 'size', 'status', 'text', 'error', 'created_at', 'message_id')}

    def get(self, ident):
        with self.inbox.connection() as db:
            return self.public(self._read(db, ident))

    def list(self):
        with self.inbox.connection() as db:
            items = [json.loads(row['data']) for row in db.execute('SELECT data FROM file_imports ORDER BY rowid DESC')]
        return [self.public(item) for item in items if not item.get('message_id')][:20]

    def _path(self, item):
        return self.directory / (item['id'] + Path(item['filename']).suffix.lower())

    def upload(self, filename, content, *, consent=False):
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 180 or re.search(r'[/\\\x00-\x1f\x7f]', filename):
            raise ValidationError('文件名无效。')
        extension = Path(filename).suffix.lower()
        if extension not in TEXT_TYPES | CLOUD_TYPES:
            raise ValidationError('支持 TXT、Markdown、PDF、Word、PPT 和 PNG/JPG/WebP 图片。')
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_UPLOAD_BYTES:
            raise ValidationError('请选择非空且不超过 20 MB 的文件。')
        if extension in CLOUD_TYPES:
            if consent is not True:
                raise ValidationError('请先确认允许将此文件发送到 MinerU 进行解析。')
            if not api_key(self.root):
                raise ValidationError('尚未配置 MinerU Token，可先粘贴文本或手动填写。')
        text = local_text(content) if extension in TEXT_TYPES else ''
        item = dict(id=uuid.uuid4().hex, filename=filename, size=len(content), text=text,
            status='ready' if extension in TEXT_TYPES else 'uploading', error='', batch_id='',
            generation=uuid.uuid4().hex, created_at=now_iso(), message_id=None)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(item)
        try:
            with path.open('xb') as target:
                target.write(content)
            with self.lock, self.inbox.connection() as db:
                self._save(db, item)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return self._submit(item) if extension in CLOUD_TYPES else self.public(item)

    def _submit(self, item):
        try:
            batch = self.factory().submit(self._path(item), item['filename'], item['id'])
            if not isinstance(batch, str) or not re.fullmatch(r'[\w-]{1,128}', batch):
                raise MinerUError('解析服务返回了无效的任务。')
            values = dict(batch_id=batch, status='parsing', error='')
        except MinerUError as error:
            values = dict(status='failed', error=str(error))
        with self.lock, self.inbox.connection() as db:
            current = self._read(db, item['id'])
            if current['generation'] == item['generation'] and current['status'] == 'uploading':
                current.update(values)
                self._save(db, current)
            return self.public(current)

    def poll(self, ident):
        with self.inbox.connection() as db:
            item = self._read(db, ident)
        if item['status'] != 'parsing':
            return self.public(item)
        try:
            text = self.factory().poll(item['batch_id'])
            if text is not None and (not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 1024 * 1024):
                raise MinerUFailed('解析结果为空或过长，请拆分文件后重试。')
            values = dict(error='') if text is None else dict(text=text, status='ready', error='')
        except MinerUFailed as error:
            values = dict(status='failed', error=str(error), batch_id='')
        except MinerUError as error:
            values = dict(error=str(error))
        with self.lock, self.inbox.connection() as db:
            current = self._read(db, ident)
            if current['generation'] == item['generation'] and current['status'] == 'parsing':
                current.update(values)
                self._save(db, current)
            return self.public(current)

    def retry(self, ident):
        with self.lock, self.inbox.connection() as db:
            item = self._read(db, ident)
            if item.get('message_id') or item['status'] == 'ready':
                raise Conflict('文件已解析或提交，请使用原有记录。')
            if item.get('batch_id'):
                return self.public(item)
            if item['status'] == 'uploading':
                raise Conflict('文件正在上传，请稍后再查询。')
            if not api_key(self.root):
                raise ValidationError('尚未配置 MinerU Token，请更新项目根目录 .env。')
            item.update(status='uploading', error='', generation=uuid.uuid4().hex)
            self._save(db, item)
        return self._submit(item)

    def download(self, ident):
        with self.inbox.connection() as db:
            item = self._read(db, ident)
        try:
            return item['filename'], self._path(item).read_bytes()
        except OSError:
            raise NotFound('原始附件文件不存在，请核对备份中的 uploads 目录。') from None

    def ingest(self, body, analyzer=None):
        from .agent import AgentError
        from .text_import import ingest
        if not isinstance(body, dict) or set(body) - {'text', 'source', 'course', 'scope', 'published_at', 'mode', 'attachment_id'}:
            raise ValidationError('新建任务字段不支持。')
        ident = body.get('attachment_id')
        if not ident:
            clean_string(body.get('text', ''), '消息正文', 20000, True)
            return ingest(self.inbox, {key: value for key, value in body.items() if key != 'attachment_id'}, analyzer)
        with self.lock:
            with self.inbox.connection() as db:
                item = self._read(db, ident)
                if item.get('message_id'):
                    return dict(message=self.inbox.message(item['message_id']), proposals=[], duplicate=True)
                if item['status'] != 'ready':
                    raise Conflict('附件尚未解析完成，请先查询或重试。')
                submitted = {key: value for key, value in body.items() if key != 'attachment_id'}
                clean_string(submitted.get('text', ''), '消息正文', 20000, True)
                # Persist the first submission so a restart after ingestion reuses its fingerprint.
                submitted = item.setdefault('submission', submitted)
                self._save(db, item)
            try:
                result = ingest(self.inbox, submitted, analyzer)
            except (ValidationError, AgentError):
                with self.inbox.connection() as db:
                    item.pop('submission', None)
                    self._save(db, item)
                raise
            with self.inbox.connection() as db:
                row = db.execute('SELECT data FROM messages WHERE id=?', (result['message']['id'],)).fetchone()
                message = json.loads(row['data'])
                files = message.setdefault('external', {}).setdefault('attachments', [])
                if not any(value.get('upload_id') == ident for value in files):
                    files.append(dict(upload_id=ident, name=item['filename'], status='extracted', text=submitted['text']))
                db.execute('UPDATE messages SET data=? WHERE id=?', (dump(message), message['id']))
                item.update(message_id=message['id'])
                item.pop('submission', None)
                self._save(db, item)
            result['message'] = message
            return result


def create_manual_task(inbox, body):
    allowed = {'request_id', 'title', 'course', 'scope', 'kind', 'deadline', 'requirements', 'submission_method', 'teacher'}
    if not isinstance(body, dict) or set(body) - allowed or not re.fullmatch(r'[a-f0-9]{32}', str(body.get('request_id', ''))):
        raise ValidationError('手动任务字段或请求标识无效。')
    stamp = now_iso()
    candidate = {key: value for key, value in body.items() if key != 'request_id'}
    candidate.update(evidence='', matchable=True)
    candidate = validate_candidate(candidate, dict(text='', scope='', captured_at=stamp))
    fingerprint = hashlib.sha256(dump(body).encode()).hexdigest()
    with inbox.lock, inbox.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS manual_task_requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,task_id TEXT NOT NULL)')
        prior = db.execute('SELECT * FROM manual_task_requests WHERE id=?', (body['request_id'],)).fetchone()
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise Conflict('此创建请求已经提交，请刷新后重新新建。')
            return inbox._task(db, prior['task_id'])
        if inbox._matching_tasks(db, candidate) or inbox._duplicate_review(db, candidate):
            raise Conflict('已有同名或疑似重复任务，请核对已有任务。')
        task = dict(id=uuid.uuid4().hex, title=candidate['title'], course=candidate['course'], scope=candidate['scope'], kind=candidate['kind'],
            deadline=candidate['deadline'], date_precision=candidate['date_precision'],
            checklist=[dict(text=value, done=False) for value in candidate['requirements']], status='pending', version=1,
            created_at=stamp, updated_at=stamp, last_source_at=stamp, sample=False, submission_method=candidate['submission_method'],
            teacher=candidate['teacher'], source_url='', platform_status='', confidence='high', field_evidence={})
        inbox._save_task(db, task, task_key(candidate) or 'manual:' + task['id'])
        inbox._history(db, task, None, 'manual_create')
        db.execute('INSERT INTO manual_task_requests VALUES(?,?,?)', (body['request_id'], fingerprint, task['id']))
        return task
