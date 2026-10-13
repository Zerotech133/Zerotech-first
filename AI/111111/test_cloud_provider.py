from __future__ import annotations

import io
import importlib
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

cloud_provider = importlib.import_module("AI.111111.cloud_provider")
ProviderAuthError = cloud_provider.ProviderAuthError
ProviderNotConfigured = cloud_provider.ProviderNotConfigured
ZhipuProvider = cloud_provider.ZhipuProvider


class Response:
    def __init__(self, payload: bytes, status: int = 200):
        self._stream = io.BytesIO(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._stream.readline, b"")


class CloudProviderTests(unittest.TestCase):
    def test_missing_key_is_reported_without_exposing_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ZhipuProvider()
        with self.assertRaises(ProviderNotConfigured) as ctx:
            list(provider.stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(str(ctx.exception), "智谱未配置，请设置 GLM_API_KEY 或 ZHIPU_API_KEY")

    @patch.object(cloud_provider, "urlopen")
    def test_sse_is_normalized_to_message_thinking_and_done(self, urlopen):
        payload = "".join(
            [
                'data: {"choices":[{"delta":{"reasoning_content":"先分析"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"答案"}}]}\n\n',
                "data: [DONE]\n\n",
            ]
        ).encode()
        urlopen.return_value = Response(payload)
        with patch.dict(os.environ, {"GLM_API_KEY": "test-key"}, clear=True):
            events = list(ZhipuProvider().stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(events, [{"thinking": "先分析"}, {"message": {"content": "答案"}}, {"done": True}])
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["model"], "glm-4-flash")
        self.assertTrue(body["stream"])
        self.assertEqual(urlopen.call_args.args[0].headers["Authorization"], "Bearer test-key")

    @patch.object(cloud_provider, "urlopen")
    def test_unauthorized_response_maps_to_auth_error(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://example.test", 401, "unauthorized", {}, io.BytesIO(b"secret detail")
        )
        with patch.dict(os.environ, {"GLM_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(ProviderAuthError) as ctx:
                list(ZhipuProvider().stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(str(ctx.exception), "智谱鉴权失败，请检查 API key")


if __name__ == "__main__":
    unittest.main()
