import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from courseinbox.core import Inbox
from courseinbox.agent import Agent
from courseinbox.http import create_server


class QQConnectorHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.box=Inbox(Path(self.tmp.name)/'inbox.sqlite3')
        self.token='main-test-token-at-least-24-chars'
        self.server=create_server('127.0.0.1',0,self.box,self.token,agent=Agent(self.box),reminder_port=0,qq_port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,body=None,token=None):
        req=urllib.request.Request(self.base+path,None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+(token or self.token)})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:return response.code,json.load(response)

    def test_admin_settings_and_scoped_credential_boundary(self):
        code,status=self.request('/api/qq-connector/status');self.assertEqual(code,200);self.assertFalse(status['enabled'])
        _,key=self.request('/api/qq-connector/credential');secret=key['secret']
        self.assertNotEqual(secret,self.token);self.assertNotIn(secret,json.dumps(status))
        for path in ('/api/tasks','/api/qq-connector/status','/api/qq-connector/credential'):
            self.assertEqual(self.request(path,token=secret)[0],401)
        code,status=self.request('/api/qq-connector/settings',dict(enabled=True,groups=[dict(id='12345',name='课程群')]))
        self.assertEqual(code,200);self.assertTrue(status['listening']);self.assertIn('127.0.0.1',status['endpoint'])
        self.assertEqual(self.request('/api/qq-connector/retry',{})[0],200)
        self.assertEqual(self.request('/api/qq-connector/settings',dict(enabled=False,groups=[]))[0],200)

    def test_invalid_configuration_is_not_saved(self):
        self.assertEqual(self.request('/api/qq-connector/settings',dict(enabled=True,groups=[]))[0],400)
        self.assertFalse(self.request('/api/qq-connector/status')[1]['enabled'])
