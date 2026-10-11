import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rag_index_cli import IndexManager, main


class FakeClient:
    def embed(self, model, input):
        values = input if isinstance(input, list) else [input]
        return {"embeddings": [[float(len(value)), 1.0] for value in values]}


class RagIndexCliTests(unittest.TestCase):
    def make_docs(self):
        temp = tempfile.TemporaryDirectory()
        docs = Path(temp.name) / "docs"
        docs.mkdir()
        (docs / "facts.md").write_text("太阳从东方升起", encoding="utf-8")
        index = Path(temp.name) / "rag-index.json"
        self.addCleanup(temp.cleanup)
        return docs, index

    def test_rebuild_persists_index_and_status_is_available_offline(self):
        docs, index = self.make_docs()
        manager = IndexManager(docs, index, client=FakeClient())

        rebuilt = manager.rebuild()
        status = manager.status()

        self.assertTrue(rebuilt["ok"])
        self.assertTrue(index.is_file())
        self.assertEqual(rebuilt["records"], 1)
        self.assertTrue(status["ok"])
        self.assertEqual(status["documents"], 1)
        self.assertEqual(status["records"], 1)
        self.assertEqual(status["index_version"], 1)

    def test_verify_detects_changed_source_without_calling_ollama(self):
        docs, index = self.make_docs()
        manager = IndexManager(docs, index, client=FakeClient())
        manager.rebuild()
        (docs / "facts.md").write_text("太阳从西方升起", encoding="utf-8")

        result = manager.verify()

        self.assertFalse(result["ok"])
        self.assertTrue(any("哈希不一致" in error for error in result["errors"]))

    def test_json_cli_output_has_structured_status_and_failure_exit_code(self):
        docs, index = self.make_docs()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["status", "--docs-dir", str(docs), "--index-path", str(index), "--json"])

        self.assertEqual(code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("index", payload)
        self.assertTrue(any("索引文件不存在" in error for error in payload["errors"]))


if __name__ == "__main__":
    unittest.main()
