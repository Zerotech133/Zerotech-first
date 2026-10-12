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

    def test_done_keeps_sources_received_before_it(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn("latestSources = record.sources", page)
        self.assertIn("if (record.done) renderSources(latestSources)", page)

    def test_page_persists_and_clears_conversation_history(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn("localStorage", page)
        self.assertIn("conversationHistory", page)
        self.assertIn("clearHistory", page)
        self.assertIn("localStorage.removeItem(HISTORY_KEY)", page)

    def test_page_has_cancel_timeout_and_error_states(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn("AbortController", page)
        self.assertIn("REQUEST_TIMEOUT_MS", page)
        self.assertIn("请求已取消", page)
        self.assertIn("请求超时", page)
        self.assertIn("status", page)

    def test_rag_sources_are_collapsible(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn("<details", page)
        self.assertIn("<summary>来源", page)
        self.assertIn("document_hash", page)
        self.assertIn("index_version", page)

    def test_page_has_read_only_runtime_status_area(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('id="runtimeStatus"', page)
        self.assertIn('id="modeStatus"', page)
        self.assertIn('id="indexStatus"', page)
        self.assertIn('id="sourceStatus"', page)
        self.assertIn("当前模式", page)
        self.assertIn("索引版本", page)
        self.assertIn("文档来源", page)

    def test_runtime_status_tracks_mode_and_source_metadata(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn("setRuntimeStatus", page)
        self.assertIn("mode.onchange", page)
        self.assertIn("读取中", page)
        self.assertIn("已命中", page)
        self.assertIn("未命中来源", page)


if __name__ == "__main__":
    unittest.main()
