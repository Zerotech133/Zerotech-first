from __future__ import annotations

import importlib
import unittest


web_ui = importlib.import_module("AI.111111.web_ui")


class WebUiTests(unittest.TestCase):
    def test_page_has_chat_and_rag_modes(self):
        page = web_ui.render_chat_page(("qwen3.5:9b", "qwen3.5:4b"))
        self.assertIn('value="chat"', page)
        self.assertIn('value="rag"', page)
        self.assertIn('/api/rag', page)
        self.assertIn('sources', page)


if __name__ == "__main__":
    unittest.main()
