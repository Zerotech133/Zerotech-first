import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock

from ollama_service.health import HealthCheckResult, HealthReport
from ollama_service.launcher import launch_web, main


def report(web_ok=True, overall=True):
    checks = {
        "ollama_http": HealthCheckResult(True, "Ollama HTTP 服务正常"),
        "chat_model": HealthCheckResult(True, "目标聊天模型已安装"),
        "embedding_model": HealthCheckResult(True, "嵌入模型已安装"),
        "web_chat_8899": HealthCheckResult(web_ok, "8899 状态"),
    }
    return HealthReport(overall and all(item.ok for item in checks.values()), checks)


class LauncherTests(unittest.TestCase):
    def test_launch_skips_duplicate_web_service(self):
        popen = Mock()
        output = io.StringIO()
        with redirect_stdout(output):
            code = launch_web(report(web_ok=True), popen=popen)
        self.assertEqual(code, 0)
        popen.assert_not_called()
        self.assertIn("跳过重复启动", output.getvalue())

    def test_launch_returns_nonzero_without_starting_on_failed_preflight(self):
        popen = Mock()
        code = launch_web(report(web_ok=False, overall=False), popen=popen)
        self.assertEqual(code, 2)
        popen.assert_not_called()

    def test_check_only_reports_success_without_starting(self):
        health = Mock(return_value=report(web_ok=True))
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--check-only"], health_check=health)
        self.assertEqual(code, 0)
        self.assertIn("ollama_http", output.getvalue())


if __name__ == "__main__":
    unittest.main()
