import unittest
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from smoke_local import run_smoke


class _Response:
    def __init__(self, body, content_type="application/json"):
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self._body


class SmokeLocalTests(unittest.TestCase):
    def test_smoke_result_has_required_checks(self):
        def opener(request, timeout=0):
            url = request.full_url
            if url.endswith("/api/tags"):
                return _Response(b'{"models":[{"name":"qwen3.5:9b"}]}')
            if url.endswith("/static/tokens.css"):
                return _Response(b":root{--bg-page:#000}", "text/css; charset=utf-8")
            if url.endswith("/static/nav.js"):
                return _Response(b"window.nav=true", "application/javascript; charset=utf-8")
            if url.endswith("/api/chat"):
                return _Response(b'{"message":{"content":"ok"}}\n{"done":true}\n', "application/x-ndjson")
            if url.endswith("/api/rag"):
                return _Response(b'{"sources":[]}\n{"done":true}\n', "application/x-ndjson")
            return _Response(b"<!doctype html>", "text/html; charset=utf-8")

        with patch("smoke_local.urlopen", opener):
            result = run_smoke(
                base_url="http://127.0.0.1:11434",
                web_url="http://127.0.0.1:8899",
            )
        self.assertTrue({"ollama_tags", "web_page", "static_assets", "chat", "rag"} <= set(result["checks"]))


if __name__ == "__main__":
    unittest.main()
