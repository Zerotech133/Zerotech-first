"""Ollama 文本嵌入客户端（bge-m3，1024 维，架构 §2.5 向量化层）。

职责边界：
- 只做**文本 → 向量**，不写 Qdrant、不建 collection、不碰 PG；
  :class:`knowledge.indexer.VectorIndex` 仍要求调用方显式传入 vector，
  由本模块产出后交给它，职责单一不越界；
- 后端为 Ollama HTTP 接口 ``POST {OLLAMA_URL}/api/embed``，
  请求体 ``{"model": "bge-m3", "input": [...]}``，响应 ``{"embeddings": [[...], ...]}``；
- 支持注入 client（``httpx.Client`` 或同签名假对象），便于单测离线运行，
  不依赖真实 Ollama 服务；
- 批量按 ``embed_batch_size`` 切分（默认 32），逐批请求后**按原序拼接**，
  保证输出与输入同序同长（上层可直接按索引回填）；
- 后端不可用（未启动 / 超时 / 非 2xx / 响应缺字段）统一抛
  :class:`EmbeddingUnavailableError`，异常信息自带排障指引；
- 维度不符抛 :class:`ValueError`——这是硬错误，绝不静默截断或补零。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

try:  # httpx 为核心依赖；缺失时降级为运行时异常，不阻断模块导入
    import httpx
except ImportError:  # pragma: no cover - 取决于部署环境
    httpx = None  # type: ignore[assignment]

from config.settings import get_settings

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """嵌入后端不可用（未启动 / 网络失败 / 超时 / 非 2xx / 响应格式非法）。

    调用方应**降级**（跳过本轮向量化，PG 事实源仍可查），而非崩溃主流程。
    """


_TROUBLESHOOT_HINT = (
    "排障指引：1) 确认 Ollama 已启动（curl http://127.0.0.1:11434/api/tags）；"
    "2) 拉取模型：ollama pull bge-m3；"
    "3) 检查项目根 .env 的 OLLAMA_URL / EMBED_MODEL / EMBED_DIM / EMBED_TIMEOUT_SEC 配置"
)


class Embedder:
    """bge-m3 文本嵌入器（Ollama HTTP 后端，批量同序）。

    用法::

        embedder = Embedder()                 # 默认读 settings
        vectors = embedder.embed_texts(["第一段", "第二段"])   # [[1024 个 float], ...]
        embedder.embed_one("单条文本")        # [1024 个 float]
        embedder.health()                     # True / False（绝不抛异常）

    :param client: 可注入的 HTTP 客户端（需提供 ``post(url, json=..., timeout=...)``）；
                   传 ``None`` 时内部惰性创建 ``httpx.Client``。
    :param model: 模型名，默认取 ``settings.embed_model``（bge-m3）。
    :param dim: 向量维度，默认取 ``settings.embed_dim``（1024）；用于校验后端输出。
    :param base_url: Ollama 根地址，默认取 ``settings.ollama_url``。
    :param timeout: 单次请求超时秒数，默认取 ``settings.embed_timeout_sec``；
                    仅用于内部创建的 ``httpx.Client``，注入 client 时由调用方自管。
    :param batch_size: 单批文本条数，默认取 ``settings.embed_batch_size``（32）。
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
        dim: int | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self._client: Any | None = client
        self.model: str = model or settings.embed_model
        self._dim: int = int(dim if dim is not None else settings.embed_dim)
        self._base_url: str = (base_url or settings.ollama_url).rstrip("/")
        self._timeout: float = float(
            timeout if timeout is not None else settings.embed_timeout_sec
        )
        configured_batch = (
            batch_size if batch_size is not None else settings.embed_batch_size
        )
        # 兜底为 1：batch_size<=0 会让分批循环无法推进
        self._batch_size: int = max(1, int(configured_batch))

    # -------------------------------------------------- 只读属性 --
    @property
    def dim(self) -> int:
        """期望的向量维度（bge-m3 为 1024）。"""
        return self._dim

    @property
    def model_name(self) -> str:
        """当前使用的模型名。"""
        return self.model

    @property
    def endpoint(self) -> str:
        """Ollama 嵌入接口完整地址。"""
        return f"{self._base_url}/api/embed"

    @property
    def batch_size(self) -> int:
        """单批最大文本条数。"""
        return self._batch_size

    # -------------------------------------------------- 对外接口 --
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """批量嵌入，返回与输入**严格同序同长**的向量列表。

        - 空输入直接返回 ``[]``，不发起任何 HTTP 请求；
        - 空字符串照常送入后端（是否过滤由上层决定，此处丢弃会导致顺序错位）；
        - 按 :attr:`batch_size` 切分，逐批请求后按原序拼接。

        :raises EmbeddingUnavailableError: 后端不可用 / 响应非法；
        :raises ValueError: 后端返回向量维度与 :attr:`dim` 不符。
        """
        items = list(texts)
        if not items:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        """单条文本嵌入（内部走一次 batch=1 的请求）。

        :raises EmbeddingUnavailableError: 后端不可用 / 响应非法；
        :raises ValueError: 向量维度不符。
        """
        return self.embed_texts([text])[0]

    def health(self) -> bool:
        """健康探测：真实发一次极短文本嵌入请求（**绝不抛异常**）。

        用真嵌入而非 ``/api/tags``，可同时验证「服务在线」与「模型已拉取」。
        """
        try:
            self.embed_one("ping")
            return True
        except Exception as exc:  # noqa: BLE001 - 健康探测必须吞掉所有异常
            logger.warning("嵌入后端健康探测失败：%s", exc)
            return False

    def close(self) -> None:
        """关闭内部创建的 HTTP 客户端（注入的 client 由调用方自行管理）。"""
        client = self._client
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                logger.debug("关闭嵌入 HTTP 客户端失败", exc_info=True)

    # -------------------------------------------------- 内部实现 --
    def _http(self) -> Any:
        """惰性获取 HTTP 客户端（缺失 httpx 时按后端不可用处理）。"""
        if self._client is not None:
            return self._client
        if httpx is None:
            raise EmbeddingUnavailableError(
                f"未安装 httpx，无法调用 Ollama 嵌入接口（{self.endpoint}）。\n"
                "排障指引：pip install 'httpx>=0.28.0'"
            )
        self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _embed_batch(self, batch: Sequence[str]) -> list[list[float]]:
        """请求单批文本，校验条数与维度后返回向量列表。"""
        payload = {"model": self.model, "input": list(batch)}
        response = self._post(payload)
        embeddings = self._extract_embeddings(response, expected=len(batch))
        vectors: list[list[float]] = []
        for index, raw in enumerate(embeddings):
            values = self._to_float_vector(raw, index)
            if len(values) != self._dim:
                raise ValueError(
                    f"嵌入维度不匹配：模型 {self.model} 期望 {self._dim} 维，"
                    f"实际返回 {len(values)} 维（第 {index} 条）。"
                    "请核对 EMBED_DIM 与模型输出维度（bge-m3 应为 1024），"
                    "或改用与 collection 维度一致的模型。"
                )
            vectors.append(values)
        return vectors

    def _post(self, payload: dict[str, Any]) -> Any:
        """发起一次嵌入请求，网络层异常统一转为 EmbeddingUnavailableError。

        超时统一配置在客户端实例上（见 :meth:`_http`），请求只带 url/json，
        保证注入的鸭子类型 client 只需实现最小签名。
        """
        try:
            return self._http().post(self.endpoint, json=payload)
        except EmbeddingUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/超时/连接拒绝统一降级
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入请求失败（{self.endpoint}，模型 {self.model}）：{exc}\n"
                f"{_TROUBLESHOOT_HINT}"
            ) from exc

    def _extract_embeddings(self, response: Any, *, expected: int) -> list[Any]:
        """校验 HTTP 状态与响应结构，返回 ``embeddings`` 原始列表。"""
        status = getattr(response, "status_code", 200)
        if not isinstance(status, int) or not 200 <= status < 300:
            body = str(getattr(response, "text", "") or "")[:300]
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口返回非 2xx 状态 {status}（{self.endpoint}）：{body}\n"
                f"{_TROUBLESHOOT_HINT}"
            )
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - 非法 JSON 同样是后端异常
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口响应不是合法 JSON（{self.endpoint}）：{exc}\n"
                f"{_TROUBLESHOOT_HINT}"
            ) from exc
        if not isinstance(data, dict) or "embeddings" not in data:
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口响应缺少 embeddings 字段（{self.endpoint}）：{str(data)[:300]}\n"
                f"{_TROUBLESHOOT_HINT}"
            )
        embeddings = data["embeddings"]
        if not isinstance(embeddings, list):
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口 embeddings 字段类型非法（{self.endpoint}）："
                f"{type(embeddings).__name__}"
            )
        if len(embeddings) != expected:
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口返回条数与请求不一致（{self.endpoint}）："
                f"请求 {expected} 条，返回 {len(embeddings)} 条；"
                "无法保证与输入同序，已拒绝采用该批次结果"
            )
        return embeddings

    @staticmethod
    def _to_float_vector(raw: Any, index: int) -> list[float]:
        """把单条原始向量转为 ``list[float]``，格式非法时按后端异常处理。"""
        if not isinstance(raw, (list, tuple)):
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口第 {index} 条向量不是数组：{type(raw).__name__}"
            )
        try:
            return [float(item) for item in raw]
        except (TypeError, ValueError) as exc:
            raise EmbeddingUnavailableError(
                f"Ollama 嵌入接口第 {index} 条向量含非数值元素：{exc}"
            ) from exc


__all__ = ["Embedder", "EmbeddingUnavailableError"]
