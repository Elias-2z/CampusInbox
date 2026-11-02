"""Loopback-only OneBot v11 inbound bridge. Never sends QQ messages or confirms tasks."""
import hashlib
import hmac
import html
import json
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .core import CN,ValidationError,Conflict,clean_string,dump,now_iso
from .chat_import import candidates

MAX_BODY=256*1024


def identifier(value,label,signed=False):
    if isinstance(value,bool) or not re.fullmatch(r'-?\d{1,20}' if signed else r'[1-9]\d{0,19}','' if value is None else str(value)):
        raise ValidationError(label+'无效。')
    return str(value)


def contents(message):
    text=[];media=[];reply=[]
    if isinstance(message,str):
        parts=[];at=0
        for match in re.finditer(r'\[CQ:([\w-]+)(?:,([^\]]*))?\]',message):
            parts.append(dict(type='text',data={'text':html.unescape(message[at:match.start()])}))
            data={k:html.unescape(v) for k,_,v in (item.partition('=') for item in (match[2] or '').split(',')) if k}
            parts.append(dict(type=match[1],data=data));at=match.end()
        parts.append(dict(type='text',data={'text':html.unescape(message[at:])}));message=parts
    if not isinstance(message,list) or len(message)>200:raise ValidationError('QQ消息段无效。')
    for segment in message:
        if not isinstance(segment,dict) or not isinstance(segment.get('data'),dict):raise ValidationError('QQ消息段无效。')
        kind=segment.get('type');data=segment['data']
        if kind=='text':text.append(clean_string(data.get('text',''),'QQ正文',30000))
        elif kind=='reply':reply.append(clean_string(str(data.get('id','')),'引用消息编号',100))
        elif kind not in ('at','face'):
            media.append(dict(type=clean_string(kind,'消息段类型',40,True),name=clean_string(str(data.get('name') or data.get('file') or ''),'附件名',200),url=clean_string(str(data.get('url','')),'附件地址',2000)))
    return clean_string(''.join(text),'QQ正文',30000),media,reply


class QQConnector:
    def __init__(self,inbox,main_token,port=8767,analyzer=None,background=True):
        self.inbox,self.port,self.analyzer,self.background=inbox,port,analyzer,background
        self._secret=hmac.new(main_token.encode(),b'campusinbox:qq-onebot11:v1',hashlib.sha256).hexdigest()
        self.lock=threading.RLock();self.processing=threading.Lock();self.wake=threading.Event()
        self.server=None;self.listener=None;self.worker=None;self.closed=False;self.error=''
        with inbox.connection(notify=False) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS qq_connector_settings(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qq_connector_events(id TEXT PRIMARY KEY,state TEXT NOT NULL,data TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_retry REAL NOT NULL DEFAULT 0,message_id TEXT,error TEXT NOT NULL DEFAULT '');''')
            row=db.execute('SELECT data FROM qq_connector_settings WHERE id=1').fetchone()
            self.config=json.loads(row['data']) if row else dict(enabled=False,groups=[])
            db.execute("UPDATE qq_connector_events SET state='pending' WHERE state='processing'")
        if self.config.get('enabled'):
            try:self._start()
            except OSError:self.error='QQ接收端口不可用，请检查端口或关闭后重试。'

    def credential(self):return self._secret

    @staticmethod
    def validate_config(body):
        if not isinstance(body,dict) or set(body)!={'enabled','groups'} or type(body['enabled']) is not bool:raise ValidationError('QQ接入设置无效。')
        groups=body['groups']
        if not isinstance(groups,list) or len(groups)>50 or (body['enabled'] and not groups):raise ValidationError('请配置1至50个允许接收的QQ群。')
        result=[];seen=set()
        for group in groups:
            if not isinstance(group,dict) or set(group)-{'id','name','course'}:raise ValidationError('QQ群设置无效。')
            gid=identifier(group.get('id'),'QQ群号')
            if gid in seen:raise ValidationError('QQ群号重复。')
            seen.add(gid);result.append(dict(id=gid,name=clean_string(group.get('name',''),'群名',100,True),course=clean_string(group.get('course',''),'课程',100)))
        return dict(enabled=body['enabled'],groups=result)

    def configure(self,body):
        config=self.validate_config(body)
        with self.lock:
            if self.closed:raise Conflict('QQ接入服务已关闭。')
            if config['enabled'] and self.server is None:
                try:self._start()
                except OSError:raise ValidationError('QQ接收端口不可用，设置未保存。')
            self.config=config;self.error=''
            with self.inbox.connection(notify=False) as db:db.execute('INSERT OR REPLACE INTO qq_connector_settings VALUES(1,?)',(dump(config),))
        if not config['enabled']:self._stop_listener()
        self.wake.set();return self.status()

    def status(self):
        with self.lock,self.inbox.connection(notify=False) as db:
            counts=dict.fromkeys(('pending','processing','done','failed','blocked'),0)
            for row in db.execute('SELECT state,COUNT(*) AS count FROM qq_connector_events GROUP BY state'):counts[row['state']]=row['count']
            row=db.execute('SELECT data FROM qq_connector_events ORDER BY rowid DESC LIMIT 1').fetchone()
            latest=json.loads(row['data']) if row else {}
            return dict(enabled=self.config['enabled'],listening=self.server is not None,groups=self.config['groups'],
                endpoint='http://127.0.0.1:'+str(self.server.server_port if self.server else self.port)+'/events',
                counts=counts,last_received_at=latest.get('received_at'),last_published_at=latest.get('published_at'),error=self.error,
                transport='onebot11',requires_bridge=True,media_pending_count=sum(json.loads(r['data']).get('media_pending_count',0) for r in db.execute('SELECT data FROM qq_connector_events')))

    def accept(self,event):
        if not isinstance(event,dict):raise ValidationError('QQ事件必须是对象。')
        with self.lock:
            if not self.config['enabled']:raise Conflict('QQ接入未开启。')
            if event.get('post_type')!='message' or event.get('message_type')!='group':return dict(ignored=True)
            account=identifier(event.get('self_id'),'QQ接收账号');group_id=identifier(event.get('group_id'),'QQ群号')
            sender_id=identifier(event.get('user_id'),'QQ发件人');mid=identifier(event.get('message_id'),'QQ消息编号',True)
            group=next((g for g in self.config['groups'] if g['id']==group_id),None)
            if not group or sender_id==account:return dict(ignored=True)
            stamp=event.get('time');published=None
            if stamp is not None:
                if type(stamp) is not int or not 946684800<=stamp<=4102444800:raise ValidationError('QQ发布时间无效。')
                published=datetime.fromtimestamp(stamp,CN).isoformat()
            text,media,replies=contents(event.get('message',event.get('raw_message','')))
            sender=event.get('sender',{})
            if not isinstance(sender,dict):raise ValidationError('QQ发件人字段无效。')
            name=clean_string(sender.get('card') or sender.get('nickname') or sender_id,'QQ发件人',100)
            identity=dict(account_id=account,group_id=group_id,source_message_id=mid,sender_id=sender_id,published_at=published,text=text,attachments=media,replies=replies,raw_message=event.get('message',event.get('raw_message','')))
            fingerprint=hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            data=dict(identity,conversation=group['name'],course=group['course'],sender=name,received_at=now_iso(),media_pending_count=len(media))
            with self.inbox.connection(notify=False) as db:
                if db.execute('SELECT 1 FROM qq_connector_events WHERE id=?',(fingerprint,)).fetchone():return dict(duplicate=True,event_id=fingerprint)
                if db.execute("SELECT COUNT(*) FROM qq_connector_events WHERE state!='done'").fetchone()[0]>=1000:raise Conflict('QQ接收队列已满，请处理失败记录后重试。')
                db.execute("INSERT INTO qq_connector_events(id,state,data) VALUES(?,'pending',?)",(fingerprint,dump(data)))
        self.wake.set();return dict(accepted=True,event_id=fingerprint)

    def run_once(self):
        if self.closed or not self.processing.acquire(blocking=False):return False
        try:
            with self.lock,self.inbox.connection(notify=False) as db:
                if not self.config['enabled']:return False
                row=db.execute("SELECT * FROM qq_connector_events WHERE state='pending' OR (state='failed' AND attempts<3 AND next_retry<=?) ORDER BY rowid LIMIT 1",(time.time(),)).fetchone()
                if not row:return False
                data=json.loads(row['data']);event_id=row['id']
                if data['group_id'] not in {g['id'] for g in self.config['groups']}:
                    db.execute("UPDATE qq_connector_events SET state='blocked',error='群已移出允许列表。' WHERE id=?",(event_id,));return True
                db.execute("UPDATE qq_connector_events SET state='processing',attempts=attempts+1 WHERE id=?",(event_id,))
            try:
                text=data['text'] or '【QQ非文本消息，需打开原消息核对】'
                scope=data['conversation'][:70]+' · QQ群'+data['group_id']
                mode='rules';trace=[]
                if self.analyzer is not None and data['text']:
                    options={} if data['published_at'] else dict(publication_known=False)
                    analysis=self.analyzer.analyze(text,data['published_at'] or '2000-01-01T00:00:00+08:00',data['course'],scope,**options)
                    parsed,mode,trace=analysis['candidates'],analysis['mode'],analysis['trace']
                else:parsed=candidates(text,data['published_at'],data['course'],scope)
                if data['media_pending_count']:
                    for c in parsed:c.setdefault('warnings',[]).append('QQ消息含图片或附件，未自动下载或读取，请核对原文。')
                for c in parsed:
                    c['scope']=scope
                    if data['course']:c['course']=data['course']
                meta={k:v for k,v in data.items() if k not in ('text','course','received_at')}
                meta.update(platform='qq',transport='onebot11',event_id=event_id)
                with self.lock:
                    if self.closed or not self.config['enabled']:
                        with self.inbox.connection(notify=False) as db:db.execute("UPDATE qq_connector_events SET state='pending' WHERE id=?",(event_id,))
                        return False
                    if data['group_id'] not in {g['id'] for g in self.config['groups']}:
                        with self.inbox.connection(notify=False) as db:db.execute("UPDATE qq_connector_events SET state='blocked',error='群已移出允许列表。' WHERE id=?",(event_id,))
                        return True
                    result=self.inbox.ingest(text,source='QQ · Connector',course=data['course'],scope=scope,captured_at=data['published_at'],
                        candidates=parsed,mode=mode,trace=trace,_chat_import=meta)
                with self.inbox.connection(notify=False) as db:db.execute("UPDATE qq_connector_events SET state='done',message_id=?,error='' WHERE id=?",(result['message']['id'],event_id))
            except Exception:
                with self.inbox.connection(notify=False) as db:db.execute("UPDATE qq_connector_events SET state='failed',error='消息处理失败，可重试；原事件已保存。',next_retry=? WHERE id=?",(time.time()+min(300,5*2**row['attempts']),event_id))
            return True
        finally:self.processing.release()

    def retry(self):
        with self.lock,self.inbox.connection(notify=False) as db:db.execute("UPDATE qq_connector_events SET state='pending',attempts=0,next_retry=0 WHERE state='failed'")
        self.wake.set();return self.status()

    def _loop(self):
        while not self.closed:
            try:processed=self.run_once()
            except Exception:self.error='QQ队列暂不可用，将继续重试。';processed=False
            if not processed:self.wake.wait(1);self.wake.clear()

    def _start(self):
        connector=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,status,data):
                raw=dump(data).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                self.connection.settimeout(5)
                if self.headers.get('Origin') or self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):
                    self.close_connection=True;return self.respond(403,{'error':'仅允许本机消息桥接。'})
                if self.path!='/events':return self.respond(404,{'error':'入口不存在。'})
                try:
                    length=int(self.headers.get('Content-Length','-1'))
                    if self.headers.get('Transfer-Encoding') or not 0<length<=MAX_BODY or self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':raise ValidationError('需要256KB以内的JSON事件。')
                    raw=self.rfile.read(length)
                    signature='sha1='+hmac.new(connector._secret.encode(),raw,hashlib.sha1).hexdigest()
                    if not (hmac.compare_digest(self.headers.get('X-Signature',''),signature) or hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+connector._secret)):
                        return self.respond(401,{'error':'QQ接入密钥无效。'})
                    result=connector.accept(json.loads(raw));return self.respond(200,result)
                except (ValidationError,ValueError,UnicodeError):self.respond(400,{'error':'QQ事件字段或格式无效。'})
                except Conflict:self.respond(409,{'error':'QQ接收暂停或队列已满，请稍后重试。'})
                except (OSError,TimeoutError):self.close_connection=True
                except Exception:self.respond(503,{'error':'事件暂未保存，请稍后重试。'})
        server=ThreadingHTTPServer(('127.0.0.1',self.port),Handler);server.daemon_threads=True
        self.server=server;self.listener=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.1},daemon=True);self.listener.start()
        if self.background and not self.worker:
            self.worker=threading.Thread(target=self._loop,daemon=True);self.worker.start()

    def _stop_listener(self):
        server=self.server;self.server=None
        if server:server.shutdown();server.server_close()

    def close(self):
        self.closed=True;self.wake.set();self._stop_listener()
        if self.worker:self.worker.join(timeout=3)
