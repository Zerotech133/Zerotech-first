"""智谱云端聊天 provider（标准库实现，不落盘保存密钥）。"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
DEFAULT_ZHIPU_MODEL = "glm-4-flash"


class ProviderError(RuntimeError):
    """云端 provider 的可展示错误基类。"""


class ProviderNotConfigured(ProviderError):
    """未配置云端密钥。"""


class ProviderAuthError(ProviderError):
    """云端鉴权失败。"""


class ProviderUnavailable(ProviderError):
    """云端服务不可达或暂时不可用。"""


class ProviderProtocolError(ProviderError):
    """云端流协议不符合预期。"""


def _api_key(value: str | None) -> str:
    if value is not None:
        return value.strip()
    return (os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPU_API_KEY") or "").strip()


class ZhipuProvider:
    """调用智谱 OpenAI 兼容流式接口，并输出统一事件。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str = DEFAULT_ZHIPU_MODEL,
        timeout: float = 120,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ZHIPU_BASE_URL") or ZHIPU_BASE_URL).rstrip("/")
        self.model = model
        self.timeout = timeout
        self._api_key = _api_key(api_key)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        think: bool = False,
    ):
        if not self.configured:
            raise ProviderNotConfigured("智谱未配置，请设置 GLM_API_KEY 或 ZHIPU_API_KEY")
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }
        if think:
            payload["thinking"] = {"type": "enabled"}
        request = Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderAuthError("智谱鉴权失败，请检查 API key") from exc
            raise ProviderUnavailable(f"智谱服务返回 HTTP {exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise ProviderUnavailable("智谱服务暂不可用") from exc
        return self._events(response)

    def _events(self, response):
        seen_done = False
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    seen_done = True
                    yield {"done": True}
                    break
                try:
                    record = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderProtocolError("智谱流响应不是有效 JSON") from exc
                if isinstance(record, dict) and record.get("error"):
                    raise ProviderUnavailable("智谱返回了错误响应")
                choices = record.get("choices") if isinstance(record, dict) else None
                delta = choices[0].get("delta", {}) if choices and isinstance(choices[0], dict) else {}
                if not isinstance(delta, dict):
                    continue
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    yield {"thinking": reasoning}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield {"message": {"content": content}}
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if not seen_done:
            raise ProviderProtocolError("智谱流响应缺少 done 标记")


__all__ = [
    "DEFAULT_ZHIPU_MODEL",
    "ProviderAuthError",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderProtocolError",
    "ProviderUnavailable",
    "ZHIPU_BASE_URL",
    "ZhipuProvider",
]
