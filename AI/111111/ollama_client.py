"""Ollama HTTP API 客户端（仅使用 Python 标准库）。"""
import json, math, urllib.error, urllib.request

class OllamaError(RuntimeError):
    """服务、协议或 API 返回错误。"""

class OllamaClient:
    def __init__(self, base_url="http://127.0.0.1:11434", timeout=300):
        self.base_url = base_url.rstrip("/"); self.timeout = timeout

    def _request(self, path, payload=None, method="GET"):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type":"application/json"})
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            try: detail = e.read().decode("utf-8", "replace")
            except Exception: detail = str(e)
            raise OllamaError(f"Ollama HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OllamaError(f"无法连接 Ollama 服务 ({self.base_url}): {e}") from e

    @staticmethod
    def _json_response(resp):
        try: result = json.loads(resp.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e: raise OllamaError(f"响应不是有效 JSON: {e}") from e
        if isinstance(result, dict) and result.get("error"): raise OllamaError(str(result["error"]))
        return result

    def list_models(self):
        with self._request("/api/tags") as resp: result = self._json_response(resp)
        return [m.get("name", "") for m in result.get("models", []) if isinstance(m, dict) and m.get("name")]

    def chat_stream(self, messages, model="qwen3.5:35b", think=None):
        payload = {"model": model, "messages": messages, "stream": True}
        if think is not None:
            payload["think"] = bool(think)
        with self._request("/api/chat", payload, "POST") as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line: continue
                try: chunk = json.loads(line)
                except json.JSONDecodeError as e: raise OllamaError(f"流响应不是有效 JSON: {line}") from e
                if chunk.get("error"): raise OllamaError(str(chunk["error"]))
                text = chunk.get("message", {}).get("content", "")
                if text: yield text
                if chunk.get("done"): break

    def chat(self, messages, model="qwen3.5:35b", think=None):
        return "".join(self.chat_stream(messages, model, think=think))

    def embed(self, texts, model="bge-m3:latest"):
        single = isinstance(texts, str); items = [texts] if single else list(texts)
        with self._request("/api/embed", {"model":model,"input":items}, "POST") as resp: result = self._json_response(resp)
        vectors = result.get("embeddings", [])
        if not isinstance(vectors, list): raise OllamaError("embed 响应缺少 embeddings")
        return vectors[0] if single and vectors else ([] if single else vectors)

    @staticmethod
    def similarity(vec_a, vec_b):
        if len(vec_a) != len(vec_b): raise ValueError("向量维度不一致")
        norm = math.sqrt(sum(x*x for x in vec_a) * sum(y*y for y in vec_b))
        return sum(x*y for x,y in zip(vec_a, vec_b)) / norm if norm else 0.0
