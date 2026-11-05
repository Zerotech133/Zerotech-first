import json
import math
import urllib.request
import urllib.error


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, base_url="http://127.0.0.1:11434", timeout=300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path, payload=None, method="GET"):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            # 返回打开的响应对象，由调用方负责读取并关闭
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.URLError as e:
            raise OllamaError(f"无法连接 Ollama 服务 ({self.base_url})，请确认服务已启动: {e}") from e

    def list_models(self):
        """返回已安装模型名列表，如 ['qwen3.5:35b']"""
        with self._request("/api/tags") as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in result.get("models", [])]

    def chat_stream(self, messages, model="qwen3.5:35b"):
        """
        流式多轮对话。messages 格式:
        [{"role": "user"|"assistant"|"system", "content": "..."}]
        逐段 yield 文本片段。
        """
        payload = {"model": model, "messages": messages, "stream": True}
        with self._request("/api/chat", payload, method="POST") as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                if chunk.get("error"):
                    raise OllamaError(chunk["error"])
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
                if chunk.get("done"):
                    break

    def chat(self, messages, model="qwen3.5:35b"):
        """非流式：一次性返回完整回复文本"""
        return "".join(self.chat_stream(messages, model))

    def embed(self, texts, model="bge-m3:latest"):
        """
        文本向量化（配合 bge-m3 等嵌入模型）。
        texts 为单个字符串时返回单个向量，为列表时返回向量列表。
        """
        single = isinstance(texts, str)
        # num_ctx 必须显式下发：服务级 OLLAMA_CONTEXT_LENGTH=16384 会被套到
        # bge-m3（n_ctx_train=8192）上，首次加载触发 "requested context size
        # too large" 告警并被静默钳制。显式 8192 与模型训练长度对齐。
        payload = {"model": model, "input": [texts] if single else texts,
                   "options": {"num_ctx": 8192}}
        with self._request("/api/embed", payload, method="POST") as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if "error" in result:
            raise OllamaError(result["error"])
        vectors = result.get("embeddings", [])
        return vectors[0] if single else vectors

    @staticmethod
    def similarity(vec_a, vec_b):
        """余弦相似度，范围 [-1, 1]，越接近 1 语义越相近"""
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm = math.sqrt(sum(a * a for a in vec_a)) * math.sqrt(sum(b * b for b in vec_b))
        return dot / norm if norm else 0.0
