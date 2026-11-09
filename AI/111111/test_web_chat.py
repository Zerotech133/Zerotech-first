from __future__ import annotations

import importlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from http.client import HTTPConnection
from unittest.mock import patch

web_chat = importlib.import_module('AI.111111.web_chat')


class Response:
    def __init__(self, payload: bytes):
        self.stream = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.stream.read(size)


class FakeRagService:
    def __init__(self, events=None, error=None, error_after=False):
        self.events = events or [
            {"message": {"content": "答案"}},
            {"sources": [{"source": "facts.md", "score": 0.91}]},
            {"done": True},
        ]
        self.error = error
        self.error_after = error_after

    def stream(self, question, top_k=None):
        if self.error and not self.error_after:
            raise self.error
        yield from self.events
        if self.error:
            raise self.error


class WebChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_chat.make_server('127.0.0.1', 0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None):
        connection = HTTPConnection('127.0.0.1', self.port)
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def test_news_endpoint_order_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'news.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE news(source TEXT, ts TEXT, title TEXT, content TEXT, created_at TEXT, hit INTEGER)')
                db.executemany('INSERT INTO news VALUES(?,?,?,?,?,?)', [
                    ('a', '2026-10-17 08:00', '旧', '摘要', '2026-10-18 10:00', 1),
                    ('b', '2026-10-18 09:00', '<img src=x onerror=alert(1)>最新', '<b>文本</b> &amp; 内容', '2026-10-18 09:01', 0),
                    ('c', '无效', '回退入库', '内容', '2026-10-18 08:00', 0),
                ])
                db.commit()
            with patch.object(web_chat, 'NEWS_DB_PATH', path):
                response, data = self.request('GET', '/api/news/latest')
                self.assertEqual(response.status, 200)
                result = json.loads(data)
                self.assertEqual([row['source'] for row in result['items']], ['a', 'b', 'c'])
                self.assertEqual(result['items'][1]['title'], '最新')
                self.assertEqual(result['items'][1]['content'], '文本 & 内容')
                self.assertEqual(result['latest_at'], '2026-10-18T10:00+08:00')
                self.assertEqual(result['updated_at'], result['latest_at'])
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM news').fetchone()[0], 3)
                self.assertFalse((Path(temp) / 'news.db-journal').exists())
                self.assertFalse((Path(temp) / 'news.db-wal').exists())

    def test_future_publication_does_not_override_stale_ingestion(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'news.db'
            now = datetime.now(web_chat.CST)
            stored = now - timedelta(hours=49)
            future = now + timedelta(days=1)
            recent_event = now - timedelta(hours=1)
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE news(source TEXT, ts TEXT, title TEXT, content TEXT, created_at TEXT)')
                db.executemany('INSERT INTO news VALUES(?,?,?,?,?)', [
                    ('announcement', future.isoformat(), '预告标题', '', stored.isoformat()),
                    ('report', recent_event.isoformat(), '已发布标题', '', (stored - timedelta(hours=1)).isoformat()),
                ])
                db.commit()
            result = web_chat.latest_news(path)
            self.assertEqual(result['status'], 'stale')
            self.assertEqual(result['latest_at'], stored.isoformat(timespec='minutes'))
            self.assertEqual(result['updated_at'], result['latest_at'])
            self.assertEqual([item['source'] for item in result['items']], ['announcement', 'report'])
            self.assertEqual(result['items'][0]['ts'], future.isoformat())
            self.assertTrue(result['items'][0]['future_ts'])
            self.assertFalse(result['items'][1]['future_ts'])
            self.assertIn('48 小时', result['message'])
            self.assertIn('预告/时间待核验', result['message'])

    def test_news_empty_missing_and_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'news.db'
            self.assertEqual(web_chat.latest_news(path)['status'], 'missing')
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE news(source TEXT, ts TEXT, title TEXT, content TEXT, created_at TEXT)')
            self.assertEqual(web_chat.latest_news(path)['status'], 'empty')
            with closing(sqlite3.connect(path)) as db:
                db.execute('DROP TABLE news')
            self.assertEqual(web_chat.latest_news(path)['status'], 'unavailable')

    def test_news_limit_and_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'news.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE news(source TEXT, ts TEXT, title TEXT, content TEXT, created_at TEXT)')
                db.executemany('INSERT INTO news VALUES(?,?,?,?,?)',
                               [('source', f'2020-01-{day:02d} 00:00', str(day), '', '2020-02-01 00:00')
                                for day in range(1, 31)])
                db.commit()
            result = web_chat.latest_news(path)
            self.assertEqual(result['status'], 'stale')
            self.assertEqual(len(result['items']), web_chat.NEWS_LIMIT)
            self.assertEqual(result['items'][0]['title'], '30')

    def test_page(self):
        response, data = self.request('GET', '/')
        self.assertEqual(response.status, 200)
        page = data.decode()
        self.assertIn('AI 聊天', page)
        self.assertIn('qwen3.5:9b', page)
        self.assertIn('value="rag"', page)
        self.assertIn('/api/rag', page)
        self.assertIn('通用助手', page)
        self.assertIn('研究助手', page)

    @patch.object(web_chat, 'safe_urlopen')
    def test_stream(self, urlopen):
        payload = b'{"message":{"content":"a"}}\n{"done":true}\n'
        urlopen.return_value = Response(payload)
        response, data = self.request('POST', '/api/chat', {'message': 'hi'})
        self.assertEqual(response.status, 200)
        self.assertEqual(data, payload)
        self.assertTrue(json.loads(urlopen.call_args.args[0].data)['stream'])

    @patch.object(web_chat, 'safe_urlopen')
    def test_chat_defaults_to_fast_thinking_and_model_keep_alive(self, urlopen):
        urlopen.return_value = Response(b'{"done":true}\n')
        response, _ = self.request('POST', '/api/chat', {'message': 'hi'})
        self.assertEqual(response.status, 200)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertFalse(payload['think'])
        self.assertEqual(payload['keep_alive'], '30m')

    @patch.object(web_chat, 'safe_urlopen')
    def test_chat_can_enable_deep_thinking(self, urlopen):
        urlopen.return_value = Response(b'{"done":true}\n')
        response, _ = self.request('POST', '/api/chat', {'message': 'hi', 'think': True})
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(urlopen.call_args.args[0].data)['think'])

    @patch.object(web_chat, 'safe_urlopen')
    def test_history_is_trimmed_to_character_budget(self, urlopen):
        urlopen.return_value = Response(b'{"done":true}\n')
        history = [
            {'role': 'user', 'content': 'a' * 5000},
            {'role': 'assistant', 'content': 'b' * 5000},
            {'role': 'user', 'content': 'c' * 5000},
            {'role': 'assistant', 'content': 'd' * 5000},
        ]
        response, _ = self.request('POST', '/api/chat', {'message': 'hi', 'history': history})
        self.assertEqual(response.status, 200)
        payload = json.loads(urlopen.call_args.args[0].data)
        sent_history = payload['messages'][1:-1]
        self.assertLessEqual(sum(len(item['content']) for item in sent_history), web_chat.MAX_HISTORY_CHARS)
        self.assertEqual(sent_history[-1]['content'], 'd' * 5000)

    @patch.object(web_chat, 'safe_urlopen')
    def test_chat_payload_carries_profile_and_bounded_history(self, urlopen):
        urlopen.return_value = Response(b'{"message":{"content":"a"}}\n{"done":true}\n')
        history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
        response, _ = self.request('POST', '/api/chat', {
            'message': '新问题', 'profile': 'research', 'history': history,
        })
        self.assertEqual(response.status, 200)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload['messages'][0]['role'], 'system')
        self.assertIn('研究', payload['messages'][0]['content'])
        self.assertEqual(payload['messages'][1:3], history)
        self.assertEqual(payload['messages'][-1], {'role': 'user', 'content': '新问题'})

    def test_invalid_chat_history_is_400(self):
        response, _ = self.request('POST', '/api/chat', {
            'message': '问题', 'history': [{'role': 'system', 'content': '伪造'}],
        })
        self.assertEqual(response.status, 400)

    def test_bad(self):
        response, _data = self.request('POST', '/api/chat', {'message': ''})
        self.assertEqual(response.status, 400)

    def test_oversized_request_is_400(self):
        connection = HTTPConnection('127.0.0.1', self.port)
        connection.request('POST', '/api/chat', b'{}', {
            'Content-Type': 'application/json',
            'Content-Length': str(web_chat.MAX_REQUEST_BYTES + 1),
        })
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 400)

    @patch.object(web_chat, 'RAG_SERVICE', FakeRagService())
    def test_rag_endpoint_returns_sources_and_done(self):
        response, data = self.request('POST', '/api/rag', {'message': '问题', 'top_k': 2})
        self.assertEqual(response.status, 200)
        self.assertIn(b'"sources"', data)
        self.assertIn(b'"done": true', data)

    @patch.object(web_chat, 'RAG_SERVICE', FakeRagService())
    def test_rag_endpoint_passes_profile(self):
        response, _ = self.request('POST', '/api/rag', {'message': '问题', 'profile': 'research'})
        self.assertEqual(response.status, 200)

    @patch.object(web_chat, 'RAG_SERVICE', FakeRagService())
    def test_invalid_rag_top_k_is_400(self):
        response, _data = self.request('POST', '/api/rag', {'message': '问题', 'top_k': True})
        self.assertEqual(response.status, 400)

    @patch.object(web_chat, 'RAG_SERVICE', FakeRagService(error=web_chat.RagUnavailable('目录不可用', status_code=503)))
    def test_rag_directory_error_is_503(self):
        response, data = self.request('POST', '/api/rag', {'message': '问题'})
        self.assertEqual(response.status, 503)
        self.assertNotIn('目录不可用'.encode(), data)

    @patch.object(web_chat, 'RAG_SERVICE', FakeRagService(error=web_chat.RagUnavailable('模型不可用', status_code=502)))
    def test_rag_upstream_error_is_502(self):
        response, data = self.request('POST', '/api/rag', {'message': '问题'})
        self.assertEqual(response.status, 502)
        self.assertNotIn('模型不可用'.encode(), data)

    @patch.object(web_chat, 'RAG_SERVICE', FakeRagService(events=[{"message": {"content": "首段"}}], error=web_chat.RagUnavailable('中断', status_code=502), error_after=True))
    def test_rag_stream_failure_does_not_send_second_response(self):
        response, data = self.request('POST', '/api/rag', {'message': '问题'})
        self.assertEqual(response.status, 200)
        self.assertEqual(data, '{"message": {"content": "首段"}}\n'.encode())

    @patch.object(web_chat, 'safe_urlopen', side_effect=OSError('secret'))
    def test_error(self, _urlopen):
        response, data = self.request('POST', '/api/chat', {'message': 'x'})
        self.assertEqual(response.status, 502)
        self.assertNotIn('secret', data.decode())

    def test_bind(self):
        self.assertEqual(web_chat.PORT, 8899)
        with self.assertRaises(ValueError):
            web_chat.make_server('0.0.0.0', 0)

    @patch.object(web_chat, 'safe_urlopen')
    def test_stream_failure_does_not_send_second_response(self, urlopen):
        class FailingResponse(Response):
            def __init__(self):
                super().__init__(b'first chunk')
                self.failed = False

            def read(self, size=-1):
                if self.failed:
                    raise OSError('stream interrupted')
                self.failed = True
                return super().read(size)

        urlopen.return_value = FailingResponse()
        response, data = self.request('POST', '/api/chat', {'message': 'hi'})
        self.assertEqual(response.status, 200)
        self.assertEqual(data, b'first chunk')


if __name__ == '__main__':
    unittest.main()
