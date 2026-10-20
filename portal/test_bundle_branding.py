# -*- coding: utf-8 -*-
"""P2.6 FE-A：8787 大 bundle 品牌残余分拣抽查。

逐文件断言：用户可见文案已改（Hermes→邓芝）+ 关键标识符仍在（接口契约不破）。
读文件硬编码路径断言；Hermes 文件在 .gitignore 内不入库，本测试即其入库快照。
"""
import os
import unittest

STATIC = r"C:\项目\老田的AI助手\Hermes\hermes-webui-next\static"


def _read(name):
    with open(os.path.join(STATIC, name), "r", encoding="utf-8") as f:
        return f.read()


class BootJsBrandingTests(unittest.TestCase):
    """boot.js：bot 名回退与关停弹窗文案已改，接口标识符不动。"""

    def test_boot_user_visible_copy_changed(self):
        src = _read("boot.js")
        self.assertIn("window._botName=s.bot_name||'邓芝'", src)
        self.assertIn("window._botName='邓芝'", src)
        self.assertIn("'Stop 邓芝 WebUI'", src)
        self.assertIn("'Stop the 邓芝 WebUI server?'", src)
        self.assertNotIn("||'Hermes'", src)
        self.assertNotIn("'Stop Hermes WebUI'", src)

    def test_boot_identifiers_intact(self):
        src = _read("boot.js")
        self.assertIn("window.HermesPWA", src)                      # PWA 接口对象
        self.assertIn("new BroadcastChannel('hermes-webui-shutdown')", src)
        self.assertIn("localStorage.getItem('hermes-webui-session')", src)
        self.assertIn("window.registerHermesTtsEngine=function", src)


class PanelsJsBrandingTests(unittest.TestCase):
    """panels.js：设置面板/看板 fallback 文案已改，CSS 类与 localStorage 键不动。"""

    def test_panels_user_visible_copy_changed(self):
        src = _read("panels.js")
        self.assertIn("'邓芝 profiles'", src)
        self.assertIn("Blank uses 邓芝 default.", src)
        self.assertIn("In 邓芝 WebUI, scheduled jobs require the Hermes gateway daemon.", src)
        self.assertIn("Toggle available from configured 邓芝 Agent setting.", src)
        self.assertIn("botNameField.value=settings.bot_name||'邓芝'", src)
        self.assertIn("window._botName=body.bot_name||'邓芝'", src)
        self.assertIn("body.bot_name=botName||'邓芝'", src)
        self.assertNotIn("'Hermes profiles'", src)
        self.assertNotIn("Blank uses Hermes default.", src)

    def test_panels_identifiers_intact(self):
        src = _read("panels.js")
        self.assertIn("hermes-kanban-code", src)                    # CSS 类名
        self.assertIn("KANBAN_BOARD_LS_KEY = 'hermes-kanban-active-board'", src)
        self.assertIn("window.HermesExtensionSettings", src)
        self.assertIn("'__hermes_dashboard__'", src)
        self.assertIn("`hermes gateway`", src)                      # CLI 命令名保留


class UiJsBrandingTests(unittest.TestCase):

    def test_ui_user_visible_copy_changed(self):
        src = _read("ui.js")
        self.assertIn("return window._botName||'邓芝'", src)
        self.assertIn("title.textContent='邓芝 agent is not responding'", src)
        self.assertIn("const label='Latest 邓芝 response'", src)
        self.assertNotIn("||'Hermes'", src)
        self.assertNotIn("'Hermes agent is not responding'", src)
        self.assertNotIn("'Latest Hermes response'", src)

    def test_ui_identifiers_intact(self):
        src = _read("ui.js")
        self.assertIn("window.__HERMES_CONFIG__", src)
        self.assertIn("function openHermesDashboard(event)", src)
        self.assertIn("MODEL_STATE_KEY='hermes-webui-model-state'", src)
        self.assertIn("window._bindHermesExtensionMessageActions=function", src)
        self.assertIn("\\`hermes model\\`", src)                    # CLI 命令名保留（模板串转义反引号）


class MessagesJsBrandingTests(unittest.TestCase):
    """messages.js：取消提示回退名与导出头已改，会话键与通知 tag 不动。"""

    def test_messages_user_visible_copy_changed(self):
        src = _read("messages.js")
        self.assertIn("(assistantDisplayName()+'').trim()||'邓芝'", src)
        self.assertIn("`# 邓芝 session ${S.session?.session_id||''}`", src)
        self.assertNotIn("||'Hermes'", src)
        self.assertNotIn("`# Hermes session ", src)

    def test_messages_identifiers_intact(self):
        src = _read("messages.js")
        self.assertIn("localStorage.removeItem('hermes-webui-session')", src)
        self.assertIn("window.HermesExtensionSettings", src)
        self.assertIn("_hermesSessionStreamVisibilityHook", src)
        self.assertIn("'hermes_dismissed_approvals'", src)
        self.assertIn("'hermes-webui-sessionless'", src)            # 通知 tag 契约


class CommandsJsBrandingTests(unittest.TestCase):

    def test_commands_user_visible_copy_changed(self):
        src = _read("commands.js")
        self.assertIn("is a 邓芝 CLI-only command and cannot run inside the WebUI.", src)
        self.assertNotIn("is a Hermes CLI-only command", src)

    def test_commands_identifiers_intact(self):
        src = _read("commands.js")
        self.assertIn("window.__HERMES_WEBUI_DESKTOP_COMPANION_STATUS__", src)
        self.assertIn("window.__hermesHandlePetSlashCommand", src)
        self.assertIn("localStorage.setItem('hermes-webui-session'", src)
        self.assertIn("`hermes chat`", src)                         # CLI 命令名保留


class IdentifierOnlyFilesUntouchedTests(unittest.TestCase):
    """纯标识符文件：汇报 0 处可改，断言关键标识符仍在、未被硬改。"""

    def test_extension_settings_untouched(self):
        src = _read("extension_settings.js")
        self.assertIn("const SETTINGS_PREFIX='hermes.ext.settings.'", src)
        self.assertIn("window.HermesExtensionSettings=api", src)
        self.assertIn("window.__HERMES_EXTENSION_CONFIG__", src)

    def test_sessions_untouched(self):
        src = _read("sessions.js")
        self.assertIn("_hermesNotifySessionOpen", src)
        self.assertIn("SESSION_VIEWED_COUNTS_KEY = 'hermes-session-viewed-counts'", src)
        self.assertIn("title==='Hermes WebUI'", src)                # 标题匹配契约

    def test_pwa_workspace_anchors_untouched(self):
        self.assertIn("window.HermesPWA={", _read("pwa-startup.js"))
        self.assertIn("dispatch('hermes:pwa-installable')", _read("pwa-startup.js"))
        self.assertIn("'hermes-webui-expanded:'", _read("workspace.js"))
        self.assertIn("ROOT.HermesAssistantTurnAnchors=Object.freeze", _read("assistant_turn_anchors.js"))
        self.assertIn(".hermes-action-grid{", _read("style.css"))   # CSS 类名


if __name__ == "__main__":
    unittest.main()
