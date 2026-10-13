import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ollama_client import OllamaClient, OllamaError


class Handler(BaseHTTPRequestHandler):
    last_chat_payload = None

    def do_GET(self):
        if self.path == "/api/tags":
            self.send_json({"models": [{"name": "demo"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n))
        if self.path == "/api/chat":
            type(self).last_chat_payload = body
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
            for item in ({"message": {"content": "你"}}, {"message": {"content": "好"}, "done": True}):
                self.wfile.write((json.dumps(item, ensure_ascii=False) + "\n").encode()); self.wfile.flush()
        elif self.path == "/api/embed":
            self.send_json({"embeddings": [[3, 4] for _ in body["input"]]})

    def send_json(self, data):
        raw = json.dumps(data).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self, *args): pass


class OllamaClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler); threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = OllamaClient(f"http://127.0.0.1:{cls.server.server_port}")
    @classmethod
    def tearDownClass(cls): cls.server.shutdown()
    def test_all_operations(self):
        self.assertEqual(self.client.list_models(), ["demo"])
        self.assertEqual(list(self.client.chat_stream([], "demo")), ["你", "好"])
        self.assertEqual(self.client.chat([], "demo"), "你好")
        self.assertEqual(self.client.embed("x"), [3, 4]); self.assertEqual(self.client.embed(["x", "y"]), [[3, 4], [3, 4]])
        self.assertAlmostEqual(self.client.similarity([3, 4], [3, 4]), 1.0); self.assertEqual(self.client.similarity([0], [1]), 0.0)

    def test_client_sends_fast_chat_contract(self):
        list(self.client.chat_stream([], "demo"))
        self.assertFalse(Handler.last_chat_payload["think"])
        self.assertEqual(Handler.last_chat_payload["keep_alive"], "30m")

    def test_ndjson_malformed_and_error(self):
        with self.assertRaises(OllamaError):
            list(OllamaClient("http://127.0.0.1:1", timeout=.01).chat_stream([], "x"))


if __name__ == "__main__": unittest.main()
