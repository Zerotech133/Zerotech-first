"""轻量级本地 RAG 知识库，依赖注入 Ollama client，运行时无第三方依赖。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.request import Request, urlopen


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
                 embed_model="bge-m3", chat_model="qwen3.5:9b"):
        if chunk_size <= 0 or top_k <= 0:
            raise ValueError("chunk_size 和 top_k 必须为正数")
        self.client = client or _OllamaClient()
        self.chunk_size, self.top_k = chunk_size, top_k
        self.embed_model, self.chat_model = embed_model, chat_model
        self._records = []

    def read_documents(self, path):
        """读取目录或单个文本/Markdown 文件，返回 source 到文本的映射。"""
        target = Path(path)
        files = [target] if target.is_file() else sorted(
            p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in {".txt", ".md", ".markdown"})
        return {str(item): item.read_text(encoding="utf-8") for item in files}

    def _embed(self, texts):
        if not texts:
            return []
        result = self.client.embed(model=self.embed_model, input=texts)
        vectors = result.get("embeddings") if isinstance(result, dict) else None
        if vectors is None and isinstance(result, dict):
            vectors = [result.get("embedding", [])]
        return vectors or []

    def ingest(self, documents):
        """切块、向量化并加入索引；documents 可为映射或 (source, text) 序列。"""
        if isinstance(documents, (str, Path)):
            documents = self.read_documents(documents)
        items = documents.items() if hasattr(documents, "items") else documents
        pending = []
        for source, text in items:
            text = str(text)
            for index, start in enumerate(range(0, len(text), self.chunk_size)):
                chunk = text[start:start + self.chunk_size]
                if chunk:
                    pending.append({"text": chunk, "source": str(source), "index": index})
        vectors = self._embed([item["text"] for item in pending])
        for item, vector in zip(pending, vectors):
            item["embedding"] = vector
            self._records.append(item)
        return pending

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
        Path(path).write_text(json.dumps(self._records, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path):
        self._records = json.loads(Path(path).read_text(encoding="utf-8"))
        return self._records
