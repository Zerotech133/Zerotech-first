import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from knowledge_base import KnowledgeBase


class FakeClient:
    def __init__(self):
        self.embedded = []
        self.last_messages = None

    def embed(self, model, input):
        self.embedded.extend(input if isinstance(input, list) else [input])
        vectors = []
        for text in (input if isinstance(input, list) else [input]):
            vectors.append([float(len(text)), float(sum(map(ord, text)) % 101)])
        return {"embeddings": vectors}

    def chat(self, model, messages):
        self.last_messages = messages
        return {"message": {"content": "基于上下文的回答"}}


class ShortEmbeddingClient(FakeClient):
    def embed(self, model, input):
        values = super().embed(model, input)["embeddings"]
        return {"embeddings": values[:-1]}


class KnowledgeBaseTests(unittest.TestCase):
    def test_ingest_chunks_and_preserves_source_and_sequence(self):
        client = FakeClient()
        kb = KnowledgeBase(client=client, chunk_size=5)
        records = kb.ingest({"guide.md": "abcdefghij"})
        self.assertEqual([r["text"] for r in records], ["abcde", "fghij"])
        self.assertEqual([r["source"] for r in records], ["guide.md", "guide.md"])
        self.assertEqual([r["index"] for r in records], [0, 1])

    def test_search_returns_top_k_by_cosine_similarity(self):
        kb = KnowledgeBase(client=FakeClient(), top_k=2)
        kb._records = [
            {"text": "a", "source": "a", "index": 0, "embedding": [1, 0]},
            {"text": "b", "source": "b", "index": 0, "embedding": [0, 1]},
            {"text": "c", "source": "c", "index": 0, "embedding": [0.9, 0.1]},
        ]
        results = kb.search("query", embedding=[1, 0])
        self.assertEqual([r["source"] for r in results], ["a", "c"])

    def test_empty_and_no_hit_are_safe(self):
        kb = KnowledgeBase(client=FakeClient())
        self.assertEqual(kb.search("anything"), [])
        kb._records = [{"text": "x", "source": "x", "index": 0, "embedding": [0, 0]}]
        self.assertEqual(kb.search("anything", embedding=[1, 0]), [])

    def test_answer_prompt_contains_only_context_and_admits_unknown(self):
        client = FakeClient()
        kb = KnowledgeBase(client=client)
        kb._records = [{"text": "太阳从东方升起", "source": "facts.md", "index": 0, "embedding": [1, 0]}]
        answer = kb.answer("太阳从哪里升起？", embedding=[1, 0])
        self.assertEqual(answer, "基于上下文的回答")
        prompt = client.last_messages[-1]["content"]
        self.assertIn("太阳从东方升起", prompt)
        self.assertIn("不知道", prompt)

    def test_json_round_trip_and_read_documents(self):
        client = FakeClient()
        kb = KnowledgeBase(client=client, chunk_size=100)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.txt"
            path.write_text("hello", encoding="utf-8")
            kb.ingest(kb.read_documents(temp))
            index = Path(temp) / "index.json"
            kb.save(index)
            loaded = KnowledgeBase(client=client)
            loaded.load(index)
            self.assertEqual(loaded._records[0]["source"], str(path))

    def test_ingest_rejects_incomplete_embedding_batch_without_partial_index(self):
        kb = KnowledgeBase(client=ShortEmbeddingClient(), chunk_size=5)

        with self.assertRaisesRegex(ValueError, "embedding 数量"):
            kb.ingest({"guide.md": "abcdefghij"})

        self.assertEqual(kb._records, [])

    def test_update_documents_reuses_unchanged_source_and_removes_deleted_source(self):
        client = FakeClient()
        kb = KnowledgeBase(client=client, chunk_size=5)
        kb.update_documents({"a.md": "abcdefghij", "b.md": "keep"})
        first_embed_count = len(client.embedded)

        kb.update_documents({"a.md": "abcdefghij", "c.md": "new"})

        # a.md 未变化，不应再次嵌入；b.md 已删除，c.md 新增。
        self.assertEqual(len(client.embedded), first_embed_count + 1)
        self.assertEqual({record["source"] for record in kb._records}, {"a.md", "c.md"})
        self.assertTrue(all(record.get("document_hash") for record in kb._records))

    def test_update_documents_persists_versioned_metadata_and_loads_legacy_list(self):
        client = FakeClient()
        kb = KnowledgeBase(client=client, chunk_size=10)
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "index.json"
            kb.update_documents({"guide.md": "hello"}, index_path=index)
            payload = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["index_version"], 1)

            legacy = Path(temp) / "legacy.json"
            legacy.write_text(json.dumps(payload["records"], ensure_ascii=False), encoding="utf-8")
            loaded = KnowledgeBase(client=client)
            loaded.load(legacy)
            self.assertEqual(loaded.index_version, 1)
            self.assertEqual(loaded._records[0]["source"], "guide.md")

    def test_update_documents_does_not_bump_version_when_nothing_changed(self):
        kb = KnowledgeBase(client=FakeClient(), chunk_size=10)
        kb.update_documents({"guide.md": "hello"})
        self.assertEqual(kb.index_version, 1)
        kb.update_documents({"guide.md": "hello"})
        self.assertEqual(kb.index_version, 1)


if __name__ == "__main__":
    unittest.main()
