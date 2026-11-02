import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from courseinbox.core import Inbox
from courseinbox.http import create_server

class IntegratedFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = create_server('127.0.0.1', 0, Inbox(Path(cls.tmp.name)/'test.sqlite3'), 'ui-test-token-with-no-real-secrets', root=Path(__file__).resolve().parents[1], reminder_port=0, qq_port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.tmp.cleanup()
    def get(self, path):
        try:
            with urllib.request.urlopen(self.url+path) as r: return r.status, r.read(), r.headers
        except urllib.error.HTTPError as r: return r.code, r.read(), r.headers
    def test_original_workspace_remains_available(self):
        status, body, _ = self.get('/legacy.html')
        self.assertEqual(status, 200)
        self.assertIn(b'id="content"', body)
    def test_integrated_theme_and_bridge_are_served(self):
        for path in ('/integration.css', '/integration-bridge.js'):
            with self.subTest(path=path): self.assertEqual(self.get(path)[0], 200)
    def test_unknown_assets_and_traversal_are_rejected(self):
        for path in ('/assets/../../.env', '/assets/%2e%2e/.env', '/assets/missing.js', '/frontend/package.json'):
            with self.subTest(path=path): self.assertEqual(self.get(path)[0], 404)
    def test_only_same_origin_legacy_workspace_can_be_embedded(self):
        self.assertIn("frame-ancestors 'self'", self.get('/legacy.html')[2]['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", self.get('/')[2]['Content-Security-Policy'])
    def test_api_auth_is_unchanged(self):
        self.assertEqual(self.get('/api/state')[0], 401)

if __name__ == '__main__': unittest.main()
