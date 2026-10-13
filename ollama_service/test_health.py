import unittest

from ollama_service.health import HealthCheckConfig


class HealthConfigTests(unittest.TestCase):
    def test_default_chat_model_matches_local_interactive_default(self):
        self.assertEqual(HealthCheckConfig().chat_model, "qwen3.5:9b")


if __name__ == "__main__":
    unittest.main()
