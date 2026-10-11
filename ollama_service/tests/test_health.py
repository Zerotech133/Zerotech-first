import json
import unittest

from ollama_service.health import HealthCheckConfig, check_ollama_health


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class HealthyOpener:
    def __call__(self, request, timeout):
        return FakeResponse({
            "models": [
                {"name": "qwen3.5:35b"},
                {"name": "bge-m3:latest"},
            ]
        })


class FailingOpener:
    def __call__(self, request, timeout):
        raise OSError("connection refused")


class HealthCheckTests(unittest.TestCase):
    def test_reports_healthy_ollama_models_and_web_chat_port(self):
        report = check_ollama_health(
            HealthCheckConfig(web_chat_port=8899),
            opener=HealthyOpener(),
            port_checker=lambda host, port, timeout: port == 8899,
        )

        self.assertTrue(report.ok)
        self.assertTrue(all(check.ok for check in report.checks.values()))
        self.assertEqual(report.checks["chat_model"].details["model"], "qwen3.5:35b")
        self.assertTrue(report.to_dict()["ok"])

    def test_reports_missing_models_and_unavailable_web_chat_in_chinese(self):
        report = check_ollama_health(
            opener=lambda request, timeout: FakeResponse({"models": []}),
            port_checker=lambda host, port, timeout: False,
        )

        self.assertFalse(report.ok)
        self.assertIn("未安装目标聊天模型", report.checks["chat_model"].message)
        self.assertIn("未安装嵌入模型", report.checks["embedding_model"].message)
        self.assertIn("8899", report.checks["web_chat_8899"].message)

    def test_connection_failure_returns_structured_failure_results(self):
        report = check_ollama_health(
            opener=FailingOpener(),
            port_checker=lambda host, port, timeout: True,
        )

        self.assertFalse(report.ok)
        self.assertFalse(report.checks["ollama_http"].ok)
        self.assertIn("无法连接 Ollama 服务", report.checks["ollama_http"].message)
        self.assertFalse(report.checks["chat_model"].ok)
        self.assertIn("无法检查目标聊天模型", report.checks["chat_model"].message)
        self.assertTrue(report.checks["web_chat_8899"].ok)


if __name__ == "__main__":
    unittest.main()
