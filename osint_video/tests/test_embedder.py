"""``knowledge.embedder`` 单元测试（全部离线，不依赖真实 Ollama）。

做法：注入同签名假客户端（只需 ``post(url, json=..., timeout=...)``），
断言请求体契约、分批次数、输出同序、维度校验与异常类型。
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge.embedder import Embedder, EmbeddingUnavailableError


class FakeResponse:
    """最小 ``httpx.Response`` 替身：只暴露 status_code / text / json()。"""

    def __init__(
        self, payload: Any, *, status_code: int = 200, text: str = ""
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    """假 Ollama 客户端：记录每次请求，按文本序号生成可识别的向量。

    文本形如 ``t7`` 时首元素为 7.0，其余补 0，便于断言「输出与输入同序」。
    """

    def __init__(
        self,
        *,
        dim: int = 4,
        status_code: int = 200,
        payload: Any | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.dim = dim
        self.status_code = status_code
        self.payload = payload
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def post(
        self, url: str, json: dict[str, Any] | None = None, **kwargs: Any
    ) -> FakeResponse:
        assert json is not None
        self.calls.append({"url": url, "json": json, "kwargs": kwargs})
        if self.exc is not None:
            raise self.exc
        if self.payload is not None:
            return FakeResponse(self.payload, status_code=self.status_code)
        embeddings = [
            [self._seed(text)] + [0.0] * (self.dim - 1) for text in json["input"]
        ]
        return FakeResponse({"embeddings": embeddings}, status_code=self.status_code)

    @staticmethod
    def _seed(text: str) -> float:
        if text.startswith("t") and text[1:].isdigit():
            return float(text[1:])
        return float(len(text))


def test_embed_texts_preserves_order_and_splits_batches() -> None:
    """70 条 + batch=32 → 发 3 次（32/32/6），输出顺序与输入一致。"""
    client = FakeClient(dim=4)
    embedder = Embedder(client, dim=4, batch_size=32)

    texts = [f"t{index}" for index in range(70)]
    vectors = embedder.embed_texts(texts)

    assert len(vectors) == 70
    assert [vector[0] for vector in vectors] == [float(i) for i in range(70)]
    assert len(client.calls) == 3
    assert [len(call["json"]["input"]) for call in client.calls] == [32, 32, 6]


def test_empty_input_returns_empty_without_request() -> None:
    client = FakeClient()
    assert Embedder(client, dim=4).embed_texts([]) == []
    assert client.calls == []


def test_dimension_mismatch_raises_value_error() -> None:
    """后端返回 3 维而期望 4 维 → ValueError（硬错误，不静默）。"""
    client = FakeClient(dim=3)
    with pytest.raises(ValueError, match="维度"):
        Embedder(client, dim=4).embed_texts(["t0"])


def test_backend_500_raises_unavailable() -> None:
    client = FakeClient(status_code=500, payload={"error": "boom"}, dim=4)
    with pytest.raises(EmbeddingUnavailableError, match="500"):
        Embedder(client, dim=4).embed_one("t0")


def test_network_error_raises_unavailable_with_hint() -> None:
    client = FakeClient(exc=TimeoutError("timed out"))
    with pytest.raises(EmbeddingUnavailableError, match="排障指引"):
        Embedder(client, dim=4).embed_texts(["t0"])


def test_missing_embeddings_field_raises_unavailable() -> None:
    client = FakeClient(payload={"model": "bge-m3"})
    with pytest.raises(EmbeddingUnavailableError, match="embeddings"):
        Embedder(client, dim=4).embed_one("t0")


def test_returned_count_mismatch_raises_unavailable() -> None:
    """后端只回 1 条却请求 2 条：无法保证同序，必须拒绝。"""
    client = FakeClient(payload={"embeddings": [[0.0, 0.0, 0.0, 0.0]]})
    with pytest.raises(EmbeddingUnavailableError, match="条数"):
        Embedder(client, dim=4).embed_texts(["t0", "t1"])


def test_embed_one_returns_single_vector_and_default_contract() -> None:
    """默认读 settings：模型 bge-m3、1024 维；一次请求一个向量。"""
    client = FakeClient(dim=1024)
    embedder = Embedder(client)

    vector = embedder.embed_one("t7")

    assert embedder.dim == 1024
    assert len(vector) == 1024
    assert vector[0] == 7.0
    assert len(client.calls) == 1
    assert client.calls[0]["json"] == {"model": "bge-m3", "input": ["t7"]}
    assert client.calls[0]["url"].endswith("/api/embed")


def test_base_url_is_normalized() -> None:
    client = FakeClient(dim=4)
    Embedder(client, dim=4, base_url="http://example.test:11434/").embed_one("t0")
    assert client.calls[0]["url"] == "http://example.test:11434/api/embed"


def test_empty_strings_are_kept_in_place() -> None:
    """空字符串不擅自丢弃，否则上层按索引回填会错位。"""
    client = FakeClient(dim=4)
    vectors = Embedder(client, dim=4).embed_texts(["t1", "", "t2"])

    assert client.calls[0]["json"]["input"] == ["t1", "", "t2"]
    assert len(vectors) == 3
    assert [vector[0] for vector in vectors] == [1.0, 0.0, 2.0]


def test_health_false_does_not_raise() -> None:
    client = FakeClient(exc=ConnectionError("connection refused"))
    assert Embedder(client, dim=4).health() is False


def test_health_false_on_bad_status() -> None:
    client = FakeClient(status_code=503, payload={"error": "loading"}, dim=4)
    assert Embedder(client, dim=4).health() is False


def test_health_true_when_backend_ok() -> None:
    client = FakeClient(dim=4)
    embedder = Embedder(client, dim=4)
    assert embedder.health() is True
    assert len(client.calls) == 1


def test_batch_size_falls_back_to_one() -> None:
    """batch_size<=0 会卡住分批循环，构造时兜底为 1。"""
    client = FakeClient(dim=4)
    embedder = Embedder(client, dim=4, batch_size=0)
    assert embedder.batch_size == 1
    assert len(embedder.embed_texts(["t0", "t1"])) == 2
    assert len(client.calls) == 2
