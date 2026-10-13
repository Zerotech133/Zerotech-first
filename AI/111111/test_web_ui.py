from __future__ import annotations

import importlib
import unittest


web_ui = importlib.import_module("AI.111111.web_ui")


class WebUiTests(unittest.TestCase):
    def test_page_uses_same_origin_static_assets(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('href="/static/tokens.css"', page)
        self.assertIn('src="/static/nav.js"', page)
        self.assertNotIn("127.0.0.1:8734/static", page)

    def test_page_selects_nine_b_as_default_and_exposes_context_budget(self):
        page = web_ui.render_chat_page(("qwen3.5:4b", "qwen3.5:9b", "qwen3.5:35b"))
        self.assertIn('value="qwen3.5:9b" selected', page)
        self.assertIn("MAX_CONTEXT_CHARS", page)

    def test_page_has_off_by_default_deep_thinking_switch_and_model_cold_start_notice(self):
        page = web_ui.render_chat_page(("qwen3.5:4b", "qwen3.5:9b"))
        self.assertIn('id="think"', page)
        self.assertIn('type="checkbox"', page)
        self.assertIn('深度思考', page)
        self.assertIn('think: think.checked', page)
        self.assertIn('将触发冷启动', page)

    def test_page_labels_cloud_provider(self):
        page = web_ui.render_chat_page(("qwen3.5:9b", "zhipu/glm-4-flash"))
        self.assertIn('智谱 · glm-4-flash', page)

    def test_page_uses_zero_boundary_shell_layout(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('class="ai-page-shell"', page)
        self.assertIn('class="chat-grid"', page)
        self.assertIn('class="panel context-panel"', page)
        self.assertIn('零界对话台', page)
        self.assertIn('/static/shortscreen.js', page)

    def test_page_has_chat_and_rag_modes(self):
        page = web_ui.render_chat_page(("qwen3.5:9b", "qwen3.5:4b"))
        self.assertIn('value="chat"', page)
        self.assertIn('value="rag"', page)
        self.assertIn('/api/rag', page)
        self.assertIn('sources', page)

    def test_page_has_role_profiles_and_sends_history(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",), (
            {"id": "general", "name": "通用助手", "model": "qwen3.5:9b"},
            {"id": "research", "name": "研究助手", "model": "qwen3.5:9b"},
            {"id": "risk_review", "name": "风控复核", "model": "qwen3.5:9b"},
        ))
        self.assertIn('id="profile"', page)
        self.assertIn('value="research"', page)
        self.assertIn("研究助手", page)
        self.assertIn('history: historyMessages()', page)
        self.assertIn('profile: profile.value', page)
        self.assertIn("historyMessages()", page)

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
