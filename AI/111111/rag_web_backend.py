"""RAG-Web 只读适配层。"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from knowledge_base import KnowledgeBase
from ollama_client import OllamaClient


class RagUnavailable(RuntimeError):
    """知识库目录或上游模型当前不可用。"""


class _KnowledgeClient:
    """把 OllamaClient 的接口适配成 KnowledgeBase 所需的形态。"""

    def __init__(self, client, embed_model):
        self.client = client
        self.embed_model = embed_model

    def embed(self, model, input):
        try:
            result = self.client.embed(model=self.embed_model, input=input)
        except TypeError:
            result = self.client.embed(input, model=self.embed_model)
        if isinstance(result, dict):
            return result
        values = result if isinstance(result, list) else [result]
        if values and values and not isinstance(values[0], (list, tuple)):
            values = [values]
        return {"embeddings": values}


class RagWebService:
    def __init__(self, docs_dir="knowledge_docs", top_k=4,
                 embed_model="bge-m3", chat_model="qwen3.5:9b", client=None):
        self.docs_dir = Path(docs_dir)
        self.top_k = top_k
        self.embed_model = embed_model
        self.chat_model = chat_model
        self.client = client or OllamaClient()
        self._kb = None
        self._index_key = None
        self._lock = threading.RLock()

    def _files_and_key(self):
        if not self.docs_dir.is_dir():
            raise RagUnavailable("知识库目录不可用")
        files = sorted(
            path for path in self.docs_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".txt", ".md", ".markdown"}
        )
        key = tuple(
            (path.relative_to(self.docs_dir).as_posix(), path.stat().st_mtime_ns, path.stat().st_size)
            for path in files
        )
        return files, key

    def ensure_index(self) -> int:
        try:
            files, key = self._files_and_key()
            if not files:
                raise RagUnavailable("知识库没有支持的文档")
            if self._kb is not None and self._index_key == key:
                return len(self._kb._records)
            adapter = _KnowledgeClient(self.client, self.embed_model)
            kb = KnowledgeBase(client=adapter, top_k=self.top_k,
                               embed_model=self.embed_model, chat_model=self.chat_model)
            documents = kb.read_documents(self.docs_dir)
            kb.ingest(documents)
            self._kb = kb
            self._index_key = key
            return len(kb._records)
        except RagUnavailable:
            raise
        except Exception as exc:
            raise RagUnavailable("知识库索引不可用") from exc

    @staticmethod
    def _validate(question, top_k):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("问题不能为空")
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 8):
            raise ValueError("top_k 必须是 1 到 8 的整数")

    def _source_name(self, source):
        try:
            return Path(source).relative_to(self.docs_dir).as_posix()
        except ValueError:
            return os.fspath(source)

    def _search(self, question, top_k):
        with self._lock:
            self.ensure_index()
            requested = self.top_k if top_k is None else top_k
            old_top_k = self._kb.top_k
            self._kb.top_k = requested
            try:
                matches = self._kb.search(question)
            finally:
                self._kb.top_k = old_top_k
        return [dict(match, source=self._source_name(match.get("source", ""))) for match in matches]

    def search(self, question, top_k=None):
        self._validate(question, top_k)
        try:
            return self._search(question, top_k)
        except (ValueError, RagUnavailable):
            raise
        except Exception as exc:
            raise RagUnavailable("知识库检索不可用") from exc

    def build_messages(self, question, matches):
        context = "\n\n".join(
            "[来源: %s]\n%s" % (match.get("source", ""), match.get("text", ""))
            for match in matches
        )
        return [
            {"role": "system", "content": "你是严谨的知识库问答助手，只依据用户提供的上下文回答。上下文不足时明确回答‘不知道’，不要编造。"},
            {"role": "user", "content": "上下文：\n%s\n\n问题：%s\n若上下文不足，请明确回答‘不知道’，不要编造。" % (context, question)},
        ]

    def stream(self, question, top_k=None):
        matches = self.search(question, top_k)
        if not matches:
            yield {"message": {"content": "我不知道。"}}
            yield {"sources": []}
            yield {"done": True}
            return
        try:
            chunks = self.client.chat_stream(self.build_messages(question, matches), model=self.chat_model)
            for chunk in chunks:
                if isinstance(chunk, dict):
                    content = chunk.get("message", {}).get("content", "")
                else:
                    content = str(chunk)
                if content:
                    yield {"message": {"content": content}}
        except Exception as exc:
            raise RagUnavailable("对话模型不可用") from exc
        yield {"sources": [{"source": match.get("source", ""), "score": round(float(match.get("score", 0)), 2)} for match in matches]}
        yield {"done": True}
