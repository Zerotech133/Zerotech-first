import unittest
from unittest.mock import patch

import ops.health_check as health_check
from ollama_service.health import HealthCheckResult, HealthReport


class OpsHealthCheckTests(unittest.TestCase):
    def test_ollama_probe_failure_is_returned_with_explicit_message(self):
        report = HealthReport(
            ok=False,
            checks={
                "ollama_http": HealthCheckResult(
                    False,
                    "无法连接 Ollama 服务（http://127.0.0.1:11434）",
                )
            },
        )

        with patch.object(health_check, "check_ollama_health", return_value=report):
            ok, message = health_check._check_ollama_service()

        self.assertFalse(ok)
        self.assertIn("无法连接 Ollama 服务", message)
        self.assertIn("11434", message)

    def test_check_services_keeps_boolean_shape_and_collects_ollama_failure(self):
        report = HealthReport(
            ok=False,
            checks={
                "ollama_http": HealthCheckResult(
                    False,
                    "无法连接 Ollama 服务（http://127.0.0.1:11434）",
                )
            },
        )
        failures = {}

        with (
            patch.object(health_check, "check_ollama_health", return_value=report),
            patch.object(health_check, "_http_ok", return_value=True),
            patch.object(health_check, "_tcp_ok", return_value=True),
            patch.object(health_check, "_jev_py_ok", return_value=True),
        ):
            services = health_check.check_services(failures)

        self.assertFalse(services["Ollama(11434)"])
        self.assertIsInstance(services, dict)
        self.assertIn("Ollama(11434)", failures)
        self.assertIn("无法连接 Ollama 服务", failures["Ollama(11434)"])


if __name__ == "__main__":
    unittest.main()
