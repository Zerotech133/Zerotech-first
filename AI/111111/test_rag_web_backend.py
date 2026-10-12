import sys
import tempfile
import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rag_web_backend import RagUnavailable, RagWebService


class FakeRagClient:
    def __init__(self):
        self.embed_calls = 0
        self.embed_items = []
        self.chat_calls = 0
        self.chat_think_values = []

    def embed(self, model, input):
        self.embed_calls += 1
        values = input if isinstance(input, list) else [input]
        self.embed_items.extend(values)
        return {"embeddings": [[float(len(value)), 1.0] for value in values]}

    def chat_stream(self, messages, model, think=None):
        self.chat_calls += 1
        self.chat_think_values.append(think)
        yield {"message": {"content": "答案来自东方。"}}


class RagWebBackendTests(unittest.TestCase):
    def make_docs(self, text="太阳从东方升起"):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "facts.md"
        path.write_text(text, encoding="utf-8")
        return temp, path

    def test_stream_emits_answer_sources_and_done(self):
        temp, _ = self.make_docs()
        self.addCleanup(temp.cleanup)
        client = FakeRagClient()
        service = RagWebService(client=client, docs_dir=temp.name)

        events = list(service.stream("太阳从哪里升起？"))

        self.assertEqual(events[-1], {"done": True})
        self.assertEqual(events[-2]["sources"][0]["source"], "facts.md")
        self.assertTrue(events[-2]["sources"][0]["document_hash"])
        self.assertEqual(events[-2]["sources"][0]["index_version"], 1)
        self.assertIn("东方", "".join(event["message"]["content"] for event in events if "message" in event))
        self.assertEqual(client.chat_calls, 1)

    def test_stream_disables_model_thinking_for_rag_answer(self):
        temp, _ = self.make_docs()
        self.addCleanup(temp.cleanup)
        client = FakeRagClient()
        service = RagWebService(client=client, docs_dir=temp.name)

        list(service.stream("太阳从哪里升起？"))

        self.assertEqual(client.chat_think_values, [False])

    def test_missing_directory_raises_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            with self.assertRaises(RagUnavailable):
                RagWebService(docs_dir=missing, client=FakeRagClient()).ensure_index()

    def test_empty_directory_raises_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "ignored.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(RagUnavailable):
                RagWebService(docs_dir=temp, client=FakeRagClient()).ensure_index()

    def test_index_is_cached_and_invalidated_when_document_changes(self):
        temp, path = self.make_docs()
        self.addCleanup(temp.cleanup)
        client = FakeRagClient()
        service = RagWebService(client=client, docs_dir=temp.name)

        self.assertEqual(service.ensure_index(), 1)
        self.assertEqual(service.ensure_index(), 1)
        self.assertEqual(client.embed_calls, 1)
        path.write_text("太阳从西方升起" * 2, encoding="utf-8")
        self.assertEqual(service.ensure_index(), 1)
        self.assertEqual(client.embed_calls, 2)

    def test_index_reuses_unchanged_document_vectors(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        first = Path(temp.name) / "first.md"
        second = Path(temp.name) / "second.md"
        first.write_text("第一份文档", encoding="utf-8")
        second.write_text("第二份文档", encoding="utf-8")
        client = FakeRagClient()
        service = RagWebService(client=client, docs_dir=temp.name)

        self.assertEqual(service.ensure_index(), 2)
        embedded_after_first_index = len(client.embed_items)
        second.write_text("第二份文档已更新", encoding="utf-8")
        self.assertEqual(service.ensure_index(), 2)

        self.assertEqual(len(client.embed_items), embedded_after_first_index + 1)
        self.assertEqual(service._kb.index_version, 2)

    def test_index_persists_and_reloads_without_reembedding(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "facts.md"
        index = Path(temp.name) / "rag-index.json"
        path.write_text("太阳从东方升起", encoding="utf-8")
        client = FakeRagClient()

        first = RagWebService(client=client, docs_dir=temp.name, index_path=index)
        self.assertEqual(first.ensure_index(), 1)
        self.assertTrue(index.is_file())
        version = first._kb.index_version
        embedded = len(client.embed_items)

        second = RagWebService(client=client, docs_dir=temp.name, index_path=index)
        self.assertEqual(second.ensure_index(), 1)
        self.assertEqual(len(client.embed_items), embedded)
        self.assertEqual(second._kb.index_version, version)

    def test_search_validates_question_and_top_k(self):
        temp, _ = self.make_docs()
        self.addCleanup(temp.cleanup)
        service = RagWebService(client=FakeRagClient(), docs_dir=temp.name)
        for question in ("", "   "):
            with self.assertRaises(ValueError):
                service.search(question)
        for top_k in (True, False, 0, 9, 1.5, "2"):
            with self.assertRaises(ValueError):
                service.search("问题", top_k=top_k)

    def test_no_matches_does_not_call_chat(self):
        temp, _ = self.make_docs()
        self.addCleanup(temp.cleanup)
        client = FakeRagClient()
        service = RagWebService(client=client, docs_dir=temp.name)
        service._kb = object()
        service._index_key = ()
        service._search = lambda question, top_k: []

        events = list(service.stream("问题"))

        self.assertEqual(events, [{"message": {"content": "我不知道。"}}, {"sources": []}, {"done": True}])
        self.assertEqual(client.chat_calls, 0)

    def test_build_messages_contains_only_matches(self):
        service = RagWebService(client=FakeRagClient())
        messages = service.build_messages("问题", [{"source": "facts.md", "text": "上下文"}])
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("上下文", messages[-1]["content"])
        self.assertIn("问题", messages[-1]["content"])

    def test_concurrent_searches_keep_requested_top_k(self):
        class SlowKnowledgeBase:
            def __init__(self):
                self.top_k = 4
                self._records = []

            def search(self, question):
                time.sleep(0.01)
                return [{"source": "facts.md", "text": str(index), "score": 1.0}
                        for index in range(self.top_k)]

        service = RagWebService(client=FakeRagClient())
        service._kb = SlowKnowledgeBase()
        service.ensure_index = lambda: 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(service.search, "问题", top_k) for top_k in (1, 8)]
            lengths = [len(future.result()) for future in futures]
        self.assertEqual(lengths, [1, 8])


if __name__ == "__main__":
    unittest.main()
