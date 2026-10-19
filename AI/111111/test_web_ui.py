from __future__ import annotations

import importlib
import unittest


web_ui = importlib.import_module("AI.111111.web_ui")


class WebUiTests(unittest.TestCase):
    def test_page_has_chat_and_rag_modes(self):
        page = web_ui.render_chat_page(("qwen3.5:4b", "qwen3.5:9b", "qwen3.5:35b"), default_model="qwen3.5:9b")
        self.assertIn('value="chat"', page)
        self.assertIn('value="rag"', page)
        self.assertIn('/api/rag', page)
        self.assertIn('sources', page)
        self.assertIn('qwen3.5:35b', page)

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

    def test_page_uses_zero_boundary_workspace_shell(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('id="appShell"', page)
        self.assertIn('class="contextRail"', page)
        self.assertIn('class="chatStage"', page)
        self.assertIn('id="composer"', page)
        self.assertIn('id="connectionBadge"', page)
        self.assertIn('/static/tokens.css', page)
        self.assertIn('/static/nav.js', page)
        self.assertIn("prefers-reduced-motion", page)

    def test_page_exposes_accessible_composer_controls(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('aria-label="发送消息"', page)
        self.assertIn('aria-label="取消请求"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('id="mobileContextToggle"', page)

    def test_page_has_conversation_workbench_controls(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('id="newChat"', page)
        self.assertIn('id="conversationSearch"', page)
        self.assertIn('id="conversationList"', page)
        self.assertIn('CONVERSATIONS_KEY', page)
        self.assertIn('renderConversationList', page)

    def test_page_has_reasoning_and_message_actions(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('id="reasoning"', page)
        self.assertIn('id="reasoningText"', page)
        self.assertIn('copyMessage', page)
        self.assertIn('retryMessage', page)
        self.assertIn('copy.dataset.action="copy"', page)

    def test_page_has_context_and_search_controls(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('id="useContext"', page)
        self.assertIn('id="webSearch"', page)
        self.assertIn('history: useContext.checked ? historyMessages() : []', page)
        self.assertIn('id="scrollTop"', page)

    def test_page_has_deep_thinking_switch(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('id="thinkMode"', page)
        self.assertIn('think: thinkMode.checked', page)

    def test_model_switch_warns_about_cold_start_and_keeps_session_model(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('sessionModel', page)
        self.assertIn('冷启动', page)

    def test_history_uses_character_budget(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('MAX_CONTEXT_CHARS', page)
        self.assertIn('historyMessages()', page)

    def test_reminders_use_a9_protocol_and_due_notifications(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        for control in ('reminderForm', 'reminderTitle', 'reminderDueAt',
                        'reminderSeverity', 'reminderRepeat', 'reminderList', 'reminderStatus'):
            self.assertIn(f'id="{control}"', page)
        self.assertIn('if(repeat)payload.repeat_minutes=Number(repeat)', page)
        self.assertIn('postJSON("/api/reminders",payload)', page)
        self.assertIn('postJSON("/api/reminders/ack",{id:item.id})', page)
        self.assertIn('apiJSON("/api/reminders")', page)
        self.assertIn('setInterval(loadReminders,30000)', page)
        self.assertIn('item.status==="due"', page)
        self.assertIn('Notification.permission!=="granted"', page)
        self.assertIn('notifiedReminders.has(key)', page)
        self.assertIn('item.cycle||0', page)
        self.assertIn('暂无提醒', page)
        self.assertIn('加载提醒失败', page)

    def test_agents_show_evidence_and_manual_read_only_check(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        for control in ('agentList', 'agentStatus', 'checkAgents'):
            self.assertIn(f'id="{control}"', page)
        self.assertIn('apiJSON("/api/agents")', page)
        self.assertIn('postJSON("/api/agents/check",{})', page)
        self.assertIn('result.created.length', page)
        self.assertIn('item.last_activity', page)
        self.assertIn('item.evidence', page)
        self.assertIn('item.error_count', page)
        self.assertIn('暂无 Agent 活动记录', page)
        self.assertIn('巡检失败', page)
        self.assertNotIn('/api/agents/assign', page)

    def test_supervision_uses_text_content_and_responsive_tokens(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('title.textContent=String(item.title', page)
        self.assertIn('evidence.textContent=value?', page)
        self.assertIn('.supervision-grid { grid-template-columns: minmax(0, 1fr); }', page)
        self.assertIn('var(--color-err)', page)

    def test_daily_brief_is_read_only_safe_and_covers_fact_sections(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertLess(page.index('id="briefHeading"'), page.index('class="supervision-grid"'))
        for control in ('refreshBrief', 'briefStatus', 'briefContent'):
            self.assertIn(f'id="{control}"', page)
        self.assertIn('loadBrief();loadReminders()', page)
        self.assertIn('refreshBrief.onclick=loadBrief', page)
        self.assertIn('apiJSON("/api/brief/daily")', page)
        self.assertIn('briefValue(value)', page)
        self.assertIn('"未接入"', page)
        for field in ('data.day', 'data.generated_at', 'metrics?.yesterday', 'metrics?.current',
                      'focus_tasks', 'task.task_id', 'task.execution_status', 'output.exists_nonempty',
                      'acceptance.automatic', 'acceptance.business', 'evidence.activity',
                      'event.ts', 'shadow?.usable', 'agent_activity', 'quality.issues',
                      'recommendations_pending_approval'):
            self.assertIn(field, page)
        self.assertIn('item.textContent=line', page)
        self.assertIn('briefContent.textContent=""', page)
        self.assertIn('加载简报失败', page)
        self.assertIn('暂无记录', page)
        self.assertNotIn('briefContent.innerHTML', page)
        self.assertNotIn('postJSON("/api/brief', page)
        self.assertNotIn('setInterval(loadBrief', page)

    def test_rag_source_cards_expose_details_and_citation_copy(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn("source-detail", page)
        self.assertIn("source-snippet", page)
        self.assertIn("copySourceCitation", page)
        self.assertIn("copy-source", page)


    def test_page_uses_cainiao_message_inline_source_layout(self):
        page = web_ui.render_chat_page(("qwen3.5:9b",))
        self.assertIn('class="inline-source-panel"', page)
        self.assertIn('id="quickPrompts"', page)
        self.assertIn('data-prompt=', page)
        self.assertIn("renderInlineSources", page)
        self.assertIn("item.sources", page)


if __name__ == "__main__":
    unittest.main()
