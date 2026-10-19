"""RAG-Web 只读适配层。"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

try:
    from .knowledge_base import KnowledgeBase
    from .ollama_client import OllamaClient, OllamaError
    from .model_routing import EMBED_MODEL, RAG_CHAT_MODEL, validate_model
    from .chat_profiles import ProfileError, get_profile
except ImportError:
    from knowledge_base import KnowledgeBase
    from ollama_client import OllamaClient, OllamaError
    from model_routing import EMBED_MODEL, RAG_CHAT_MODEL, validate_model
    from chat_profiles import ProfileError, get_profile


class RagUnavailable(RuntimeError):
    """知识库目录或上游模型当前不可用。"""

    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


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
                 embed_model=EMBED_MODEL, chat_model=RAG_CHAT_MODEL, client=None,
                 index_path=None):
        self.docs_dir = Path(docs_dir)
        self.top_k = top_k
        # 保留旧参数名以兼容调用方，但 RAG 的模型角色由集中契约固定。
        self.embed_model = validate_model("embedding", embed_model)
        self.chat_model = validate_model("rag", chat_model)
        self.client = client or OllamaClient()
        self.index_path = Path(index_path) if index_path else self.docs_dir / ".rag_index.json"
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
            (
                path.relative_to(self.docs_dir).as_posix(),
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
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
            documents = (self._kb.read_documents(self.docs_dir)
                         if self._kb is not None
                         else KnowledgeBase(client=adapter, top_k=self.top_k,
                                            embed_model=self.embed_model,
                                            chat_model=self.chat_model).read_documents(self.docs_dir))
            if self._kb is None:
                self._kb = KnowledgeBase(client=adapter, top_k=self.top_k,
                                         embed_model=self.embed_model, chat_model=self.chat_model)
                if self.index_path.is_file():
                    try:
                        self._kb.load(self.index_path)
                    except (OSError, ValueError, TypeError):
                        self._kb._records = []
                        self._kb.index_version = 0
            else:
                self._kb.client = adapter
            try:
                self._kb.update_documents(documents, index_path=self.index_path)
            except OSError:
                self._kb.update_documents(documents)
            self._index_key = key
            return len(self._kb._records)
        except RagUnavailable:
            raise
        except OllamaError as exc:
            raise RagUnavailable("上游模型不可用", status_code=502) from exc
        except Exception as exc:
            raise RagUnavailable("知识库索引不可用", status_code=503) from exc

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
        except OllamaError as exc:
            raise RagUnavailable("上游模型不可用", status_code=502) from exc
        except Exception as exc:
            raise RagUnavailable("知识库检索不可用", status_code=503) from exc

    def build_messages(self, question, matches, profile=None):
        try:
            role = get_profile(profile)
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
        context = "\n\n".join(
            "[来源: %s]\n%s" % (match.get("source", ""), match.get("text", ""))
            for match in matches
        )
        return [
            {"role": "system", "content": role["system_prompt"] + "\n\n你还必须只依据用户提供的知识库上下文回答。上下文不足时明确回答‘不知道’，不要编造。"},
            {"role": "user", "content": "上下文：\n%s\n\n问题：%s\n若上下文不足，请明确回答‘不知道’，不要编造。" % (context, question)},
        ]

    def stream(self, question, top_k=None, profile=None):
        try:
            role = get_profile(profile)
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
        matches = self.search(question, top_k)
        if not matches:
            yield {"message": {"content": "我不知道。"}}
            yield {"sources": []}
            yield {"done": True}
            return
        try:
            chunks = self.client.chat_stream(
                self.build_messages(question, matches, profile=role["id"]), model=self.chat_model, think=False
            )
            for chunk in chunks:
                if isinstance(chunk, dict):
                    content = chunk.get("message", {}).get("content", "")
                else:
                    content = str(chunk)
                if content:
                    yield {"message": {"content": content}}
        except Exception as exc:
            raise RagUnavailable("对话模型不可用", status_code=502) from exc
        yield {"sources": [
            {
                "source": match.get("source", ""),
                "score": round(float(match.get("score", 0)), 2),
                "document_hash": match.get("document_hash", ""),
                "text": match.get("text", ""),
                "index_version": self._kb.index_version,
            }
            for match in matches
        ], "profile": role["id"], "model": self.chat_model}
        yield {"done": True}
