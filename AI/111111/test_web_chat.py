from __future__ import annotations

import importlib
import io
import json
import threading
import unittest
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

    def test_page(self):
        response, data = self.request('GET', '/')
        self.assertEqual(response.status, 200)
        page = data.decode()
        self.assertIn('AI 聊天', page)
        self.assertIn('qwen3.5:9b', page)

    @patch.object(web_chat, 'urlopen')
    def test_stream(self, urlopen):
        payload = b'{"message":{"content":"a"}}\n{"done":true}\n'
        urlopen.return_value = Response(payload)
        response, data = self.request('POST', '/api/chat', {'message': 'hi'})
        self.assertEqual(response.status, 200)
        self.assertEqual(data, payload)
        self.assertTrue(json.loads(urlopen.call_args.args[0].data)['stream'])

    def test_bad(self):
        response, _data = self.request('POST', '/api/chat', {'message': ''})
        self.assertEqual(response.status, 400)

    @patch.object(web_chat, 'urlopen', side_effect=OSError('secret'))
    def test_error(self, _urlopen):
        response, data = self.request('POST', '/api/chat', {'message': 'x'})
        self.assertEqual(response.status, 502)
        self.assertNotIn('secret', data.decode())

    def test_bind(self):
        self.assertEqual(web_chat.PORT, 8899)
        with self.assertRaises(ValueError):
            web_chat.make_server('0.0.0.0', 0)

    @patch.object(web_chat, 'urlopen')
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
