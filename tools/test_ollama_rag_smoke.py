import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

import tools.ollama_rag_smoke as smoke


class LiveGetTests(unittest.TestCase):
    def test_live_get_accepts_html_homepage(self):
        class FakeHeaders:
            def get_content_type(self):
                return "text/html"

        class FakeResponse:
            status = 200
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"<!doctype html><title>AI Chat</title>"

        with patch.object(smoke.urllib.request, "urlopen", return_value=FakeResponse()):
            result = smoke._live_get("http://127.0.0.1:8899/")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["content_type"], "text/html")

    def test_live_get_keeps_json_body_keys(self):
        class FakeHeaders:
            def get_content_type(self):
                return "application/json"

        class FakeResponse:
            status = 200
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"models": []}'

        with patch.object(smoke.urllib.request, "urlopen", return_value=FakeResponse()):
            result = smoke._live_get("http://127.0.0.1:11434/api/tags")

        self.assertTrue(result["ok"])
        self.assertEqual(result["body_keys"], ["models"])

    def test_live_smoke_allows_missing_optional_index(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            docs = root / "knowledge_docs"
            docs.mkdir()
            (docs / "one.md").write_text("本地测试文档", encoding="utf-8")
            args = SimpleNamespace(
                web_url="http://127.0.0.1:8899",
                ollama_url="http://127.0.0.1:11434",
                question="测试",
            )
            with (
                patch.object(smoke, "_live_get", return_value={"ok": True, "status": 200}),
                patch.object(smoke, "_live_rag", return_value={"ok": True}),
            ):
                result = smoke._run_smoke(args, root, docs, root / "missing.json", False)

        self.assertTrue(result["ok"])
        self.assertFalse(result["checks"]["index"]["exists"])


if __name__ == "__main__":
    unittest.main()
