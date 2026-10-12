import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(ROOT))
from store import Store
from source_export import export
from service import handler


def snapshot(messages=None):
    return {'collected_at': 100000, 'topics': [{'name': 'backup', 'evidence': 'access rule'}],
            'messages': messages or [], 'cache_seconds': 86400, 'truncated': False}


def message(mid='one', topic='backup', timestamp=99999, body='Backup successful'):
    return {'id': mid, 'topic': topic, 'time': timestamp, 'title': 'Report', 'message': body,
            'priority': 3, 'tags': ['white_check_mark']}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'archive.db'
        self.store = Store(self.path, retention_days=90)

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_updates_and_restart(self):
        self.store.ingest(snapshot([message()]), now=100000)
        self.store.ingest(snapshot([message(body='Updated report')]), now=100030)
        restarted = Store(self.path)
        self.assertEqual(restarted.messages()['total'], 1)
        self.assertEqual(restarted.messages()['messages'][0]['message'], 'Updated report')

    def test_topic_survives_cache_and_archive_expiry(self):
        self.store.ingest(snapshot([message()]), now=100000)
        self.store.ingest(snapshot(), now=100000 + 91 * 86400)
        self.assertEqual(self.store.messages()['total'], 0)
        self.assertEqual(self.store.topics()[0]['name'], 'backup')
        self.assertEqual(self.store.topics()[0]['last_message_at'], 99999)

    def test_new_topic_is_discovered_and_filtered_for_ai(self):
        self.store.ingest(snapshot([message(topic='new-topic')]), now=100000)
        self.store.update_topic('new-topic', {'ai_enabled': False})
        self.assertEqual(self.store.messages()['total'], 1)
        self.assertEqual(self.store.messages(ai=True)['total'], 0)
        self.assertEqual(len(self.store.topics()), 2)

    def test_query_pagination_and_sql_injection(self):
        self.store.ingest(snapshot([message(str(i), timestamp=99900+i) for i in range(5)]), now=100000)
        self.assertEqual(len(self.store.messages(limit=2, offset=2)['messages']), 2)
        self.assertEqual(self.store.messages(q='successful')['total'], 5)
        self.assertEqual(self.store.messages(q="' OR 1=1 --")['total'], 0)
        self.assertEqual(self.store.messages(topic="' OR 1=1 --")['total'], 0)
        with self.assertRaises(ValueError): self.store.messages(limit=1001)
        with self.assertRaises(ValueError): self.store.messages(offset=-1)

    def test_collection_error_and_recovery(self):
        self.store.ingest(snapshot(), now=100000)
        self.store.record_error('source unavailable', now=100030)
        self.assertEqual(self.store.status(now=100040)['collection_state'], 'error')
        self.store.ingest(snapshot(), now=100060)
        self.assertEqual(self.store.status(now=100061)['collection_state'], 'ok')

    def test_schedule_and_validation(self):
        self.store.ingest(snapshot([message()]), now=100000)
        self.store.update_topic('backup', {'expected_hours': 1, 'notes': 'Nightly backup'})
        self.assertEqual(self.store.topics(now=104000)[0]['report_state'], 'overdue')
        with self.assertRaises(ValueError): self.store.update_topic('backup', {'ai_enabled': 'false'})
        with self.assertRaises(ValueError): self.store.update_topic('backup', {'expected_hours': -1})
        with self.assertRaises(ValueError): self.store.update_topic('backup', {'unknown': 1})

    def test_long_collection_gap_is_reported(self):
        self.store.ingest(snapshot(), now=100000)
        self.store.ingest(snapshot(), now=200000)
        self.assertEqual(self.store.status(now=200001)['gap_count'], 1)

    def test_retention_is_independent_of_source_success(self):
        self.store.ingest(snapshot([message()]),now=100000)
        self.store.record_error('source unavailable',now=100030)
        self.store.expire(now=100000+91*86400)
        self.assertEqual(self.store.messages()['total'],0)
        self.assertEqual(len(self.store.topics()),1)

    def test_truncation_history_survives_recovery(self):
        partial=snapshot();partial['truncated']=True
        self.store.ingest(partial,now=100000)
        self.store.ingest(partial,now=100030)
        self.store.ingest(snapshot(),now=100060)
        self.assertFalse(self.store.status(now=100061)['source_truncated'])
        self.assertEqual(self.store.status(now=100061)['truncation_count'],1)


class ExportTests(unittest.TestCase):
    def test_missing_auth_database_still_exports_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp)/'cache.db'
            with sqlite3.connect(cache) as db:
                db.execute('CREATE TABLE messages(mid,topic,time,event,title,message,priority,tags,published)')
                db.execute("INSERT INTO messages VALUES ('one','backup',99999,'message','Report','ok',3,'check',1)")
            result=export(cache,Path(tmp)/'missing.db',now=100000)
            self.assertEqual(len(result['messages']),1)

    def test_export_does_not_expose_auth_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)/'cache.db'; auth = Path(tmp)/'auth.db'
            with sqlite3.connect(cache) as db:
                db.execute('CREATE TABLE messages(mid,topic,time,event,title,message,priority,tags,published)')
                db.execute("INSERT INTO messages VALUES ('one','backup',99999,'message','Report','ok',3,'check',1)")
            with sqlite3.connect(auth) as db:
                db.execute('CREATE TABLE user_access(topic)')
                db.executemany('INSERT INTO user_access VALUES (?)', [('quiet',), ('alerts-*',)])
                db.execute('CREATE TABLE user(user,pass,prefs)')
                db.execute("INSERT INTO user VALUES ('admin','SENSITIVE_PASSWORD','{}')")
            result = export(cache, auth, now=100000)
            self.assertNotIn('SENSITIVE_PASSWORD', json.dumps(result))
            self.assertEqual({t['name'] for t in result['topics']}, {'backup','quiet'})
            self.assertEqual(result['messages'][0]['id'], 'one')


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'archive.db')
        self.store.ingest(snapshot([message()]))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store,'api-secret','ui-secret',{'https://example.test'}))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,token='',method='GET',body=None,origin=''):
        headers={'Authorization':'Bearer '+token}
        if body is not None:headers['Content-Type']='application/json'
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.url+path,headers=headers,method=method,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:return r.status,r.read()
        except urllib.error.HTTPError as e:
            with e:return e.code,e.read()

    def test_no_authentication_no_data(self):
        for path in ['/','/api/status','/api/topics','/api/messages','/style.css']:
            self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.request('/health')[0],200)

    def test_ai_cannot_write_or_read_excluded_topic(self):
        self.assertEqual(self.request('/api/topics/backup','api-secret','PUT',{'ai_enabled':False},'https://example.test')[0],403)
        self.assertEqual(self.request('/api/topics/backup','ui-secret','PUT',{'ai_enabled':False},'https://example.test')[0],200)
        self.assertEqual(json.loads(self.request('/api/messages','api-secret')[1])['total'],0)
        self.assertEqual(self.request('/api/topics/backup','ui-secret','PUT',{'ai_enabled':True},'https://attacker.test')[0],403)

    def test_bounded_queries_and_static_traversal(self):
        self.assertEqual(self.request('/api/messages?limit=101','api-secret')[0],400)
        self.assertEqual(self.request('/api/messages?since=-1','ui-secret')[0],400)
        self.assertEqual(self.request('/../store.py','ui-secret')[0],404)
        self.assertEqual(self.request('/','ui-secret')[0],200)


if __name__ == '__main__': unittest.main()
