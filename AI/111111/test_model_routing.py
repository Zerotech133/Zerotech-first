import unittest

from model_routing import ModelRoutingError, select_model


class ModelRoutingTests(unittest.TestCase):
    def test_unknown_chat_model_lists_all_supported_models(self):
        with self.assertRaisesRegex(ModelRoutingError, "qwen3.5:35b"):
            select_model("chat", "unknown-model")


if __name__ == "__main__":
    unittest.main()
