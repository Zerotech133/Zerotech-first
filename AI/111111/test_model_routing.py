import sys
import unittest
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_routing import (  # noqa: E402
    CHAT_MODELS,
    EMBED_MODEL,
    RAG_CHAT_MODEL,
    ModelRoutingError,
    select_model,
)
from ollama_client import OllamaClient  # noqa: E402
from rag_web_backend import RagWebService  # noqa: E402


class ModelRoutingTests(unittest.TestCase):
    def test_chat_accepts_only_supported_models(self):
        self.assertEqual(select_model("chat", "qwen3.5-trae").model, "qwen3.5-trae")
        self.assertEqual(select_model("chat", "qwen3.5:9b").model, "qwen3.5:9b")
        self.assertEqual(select_model("chat", "qwen3.6:35b-a3b").model, "qwen3.6:35b-a3b")
        self.assertEqual(tuple(CHAT_MODELS), ("qwen3.5-trae", "qwen3.5:9b", "qwen3.6:35b-a3b"))
        with self.assertRaises(ModelRoutingError):
            select_model("chat", "qwen3.5:1b")

    def test_missing_default_chat_model_falls_back_with_hint(self):
        selection = select_model("chat", installed=["qwen3.5-trae"])
        self.assertEqual(selection.model, "qwen3.5-trae")
        self.assertTrue(selection.degraded)
        self.assertIn("qwen3.5:9b", selection.message)
        self.assertIn("qwen3.5-trae", selection.message)

    def test_rag_is_fixed_to_9b_without_silent_light_fallback(self):
        selection = select_model("rag", installed=["qwen3.5-trae"])
        self.assertEqual(selection.model, RAG_CHAT_MODEL)
        self.assertTrue(selection.degraded)
        self.assertIn("qwen3.5:9b", selection.message)
        with self.assertRaises(ModelRoutingError):
            select_model("rag", "qwen3.5-trae")

    def test_embedding_is_fixed_to_bge_m3(self):
        selection = select_model("embedding", installed=["qwen3.5:9b"])
        self.assertEqual(selection.model, EMBED_MODEL)
        self.assertTrue(selection.degraded)
        self.assertIn("bge-m3", selection.message)
        self.assertEqual(select_model("embedding", "bge-m3:latest").model, EMBED_MODEL)

    def test_unknown_capability_is_rejected(self):
        with self.assertRaises(ModelRoutingError):
            select_model("vision")

    def test_client_defaults_follow_central_route(self):
        self.assertEqual(inspect.signature(OllamaClient.chat).parameters["model"].default, RAG_CHAT_MODEL)
        self.assertEqual(inspect.signature(OllamaClient.chat_stream).parameters["model"].default, RAG_CHAT_MODEL)
        self.assertEqual(inspect.signature(OllamaClient.embed).parameters["model"].default, EMBED_MODEL)

    def test_rag_rejects_non_contract_model_overrides(self):
        with self.assertRaises(ModelRoutingError):
            RagWebService(chat_model="qwen3.5:35b")
        with self.assertRaises(ModelRoutingError):
            RagWebService(embed_model="nomic-embed-text")


if __name__ == "__main__":
    unittest.main()
