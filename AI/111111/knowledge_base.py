"""轻量级本地 RAG 知识库，依赖注入 Ollama client，运行时无第三方依赖。"""
from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .model_routing import EMBED_MODEL, RAG_CHAT_MODEL, validate_model
except ImportError:
    from model_routing import EMBED_MODEL, RAG_CHAT_MODEL, validate_model


class _OllamaClient:
    def __init__(self, host="http://127.0.0.1:11434"):
        self.host = host.rstrip("/")

    def _post(self, endpoint, payload):
        request = Request(self.host + endpoint, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    def embed(self, model, input):
        return self._post("/api/embed", {"model": model, "input": input})

    def chat(self, model, messages):
        return self._post("/api/chat", {"model": model, "messages": messages, "stream": False})


class KnowledgeBase:
    def __init__(self, client=None, chunk_size=500, top_k=4,
                 embed_model=EMBED_MODEL, chat_model=RAG_CHAT_MODEL):
        if chunk_size <= 0 or top_k <= 0:
            raise ValueError("chunk_size 和 top_k 必须为正数")
        self.client = client or _OllamaClient()
        self.chunk_size, self.top_k = chunk_size, top_k
        self.embed_model = validate_model("embedding", embed_model)
        self.chat_model = validate_model("rag", chat_model)
        self._records = []
        self.index_version = 0

    def read_documents(self, path):
        """读取目录或单个文本/Markdown 文件，返回 source 到文本的映射。"""
        target = Path(path)
        files = [target] if target.is_file() else sorted(
            p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in {".txt", ".md", ".markdown"})
        return {str(item): item.read_text(encoding="utf-8") for item in files}

    def _embed(self, texts, batch_size=32):
        """批量向量化：Ollama /api/embed 单次批量过大会返回 400，分批调用后拼接。"""
        if not texts:
            return []
        out = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start:start + batch_size])
            result = self.client.embed(model=self.embed_model, input=batch)
            vectors = result.get("embeddings") if isinstance(result, dict) else None
            if vectors is None and isinstance(result, dict):
                vectors = [result.get("embedding", [])]
            out.extend(vectors or [])
        return out

    @staticmethod
    def _document_hash(text):
        return hashlib.sha256(str(text).encode("utf-8")).hexdigest()

    def _chunk_records(self, source, text, document_hash=None):
        document_hash = document_hash or self._document_hash(text)
        return [
            {
                "text": chunk,
                "source": str(source),
                "index": index,
                "document_hash": document_hash,
            }
            for index, start in enumerate(range(0, len(text), self.chunk_size))
            if (chunk := str(text)[start:start + self.chunk_size])
        ]

    def ingest(self, documents):
        """切块、向量化并加入索引；documents 可为映射或 (source, text) 序列。"""
        if isinstance(documents, (str, Path)):
            documents = self.read_documents(documents)
        items = documents.items() if hasattr(documents, "items") else documents
        pending = []
        for source, text in items:
            pending.extend(self._chunk_records(source, str(text)))
        vectors = self._embed([item["text"] for item in pending])
        if len(vectors) != len(pending):
            raise ValueError(
                "embedding 数量与文本块数量不一致："
                f"{len(vectors)} != {len(pending)}"
            )
        for item, vector in zip(pending, vectors):
            item["embedding"] = vector
            self._records.append(item)
        self.index_version += 1
        return pending

    def update_documents(self, documents, index_path=None):
        """增量更新索引，只为新增或内容变化的来源重新向量化。

        ``documents`` 可以是目录、单文件、映射或 ``(source, text)`` 序列。
        已删除的来源会从索引移除；传入 ``index_path`` 时会立即持久化。
        """
        if isinstance(documents, (str, Path)):
            documents = self.read_documents(documents)
        items = {str(source): str(text) for source, text in (
            documents.items() if hasattr(documents, "items") else documents
        )}
        existing = {}
        for record in self._records:
            existing.setdefault(str(record.get("source", "")), []).append(record)

        unchanged, pending = [], []
        changed = False
        for source, text in items.items():
            digest = self._document_hash(text)
            records = existing.get(source, [])
            if records and all(record.get("document_hash") == digest for record in records):
                unchanged.extend(records)
            else:
                changed = True
                pending.extend(self._chunk_records(source, text, digest))

        if set(existing) - set(items):
            changed = True

        vectors = self._embed([item["text"] for item in pending])
        if len(vectors) != len(pending):
            raise ValueError(
                "embedding 数量与文本块数量不一致："
                f"{len(vectors)} != {len(pending)}"
            )
        for item, vector in zip(pending, vectors):
            item["embedding"] = vector
        self._records = unchanged + pending
        if changed:
            self.index_version += 1
        if index_path is not None:
            self.save(index_path)
        return list(self._records)

    @staticmethod
    def _cosine(left, right):
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        dot = sum(float(left[i]) * float(right[i]) for i in range(size))
        norm = math.sqrt(sum(float(x) ** 2 for x in left)) * math.sqrt(sum(float(x) ** 2 for x in right))
        return dot / norm if norm else 0.0

    def search(self, query, embedding=None):
        if not self._records:
            return []
        vector = embedding if embedding is not None else (self._embed([query]) or [[]])[0]
        ranked = [(self._cosine(vector, record.get("embedding", [])), record) for record in self._records]
        ranked = [(score, record) for score, record in ranked if score > 0]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [dict(record, score=score) for score, record in ranked[:self.top_k]]

    def answer(self, question, embedding=None):
        matches = self.search(question, embedding=embedding)
        if not matches:
            return "不知道。"
        context = "\n\n".join("[来源: %s]\n%s" % (item["source"], item["text"]) for item in matches)
        messages = [{"role": "system", "content": "你是严谨的知识库问答助手，只依据用户提供的上下文回答。上下文不足时明确回答‘不知道’，不要编造。"},
                    {"role": "user", "content": "上下文：\n%s\n\n问题：%s\n若上下文不足，请明确回答‘不知道’，不要编造。" % (context, question)}]
        result = self.client.chat(model=self.chat_model, messages=messages)
        if isinstance(result, dict):
            message = result.get("message", {})
            return message.get("content", result.get("response", "不知道。"))
        return str(result)

    def save(self, path):
        payload = {
            "version": 2,
            "index_version": self.index_version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "records": self._records,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "records" in payload:
            self._records = payload.get("records", [])
            self.index_version = int(payload.get("index_version", 0))
        else:
            # 兼容早期直接保存 records 列表的索引文件。
            self._records = payload
            self.index_version = 1
        return self._records
