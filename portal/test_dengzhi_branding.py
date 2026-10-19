# -*- coding: utf-8 -*-
"""FB-N · 邓芝 branding 测试（P1 波次，全部离线只读）。

验证：
1. portal/static/notify_command.html 含「邓芝」页头标注与 DD-5 决策账说明区块；
2. notify_command.html 不含任何写操作（无 POST / 无 <form>）；
3. 8787 在役 UI（hermes-webui-next/static/index.html，路径按侦察结论硬编码，
   start_hermes_bg.py SERVER/_CWD 指向 next 基）含「邓芝」品牌；
4. 在役 UI title 已改为「邓芝 · 飞书通讯中枢」。
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 侦察结论（start_hermes_bg.py:47-48 + restart_hermes_8787.ps1）：8787 生产基 =
# hermes-webui-next；hermes-webui 为回滚资产（勿改）。此处硬编码在役路径。
NOTIFY_HTML = REPO / "portal" / "static" / "notify_command.html"
ACTIVE_INDEX_HTML = REPO / "Hermes" / "hermes-webui-next" / "static" / "index.html"
ACTIVE_CONSOLE_HTML = REPO / "Hermes" / "hermes-webui-next" / "static" / "console.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class NotifyCommandDengzhiTests(unittest.TestCase):
    """notify_command.html 品牌标注与 DD-5 说明区块。"""

    def setUp(self):
        self.content = _read(NOTIFY_HTML)

    def test_contains_dengzhi(self):
        self.assertIn("邓芝", self.content)
        self.assertIn("邓芝（飞书通讯中枢）统辖", self.content)

    def test_eyebrow_deng_zhi_hub(self):
        self.assertIn("NOTIFY COMMAND · DENG ZHI HUB", self.content)

    def test_dd5_block_present(self):
        self.assertIn("DD-5", self.content)
        self.assertIn("notification_decisions.sqlite3", self.content)
        self.assertIn("decision:", self.content)
        self.assertIn("预留入口位", self.content)

    def test_no_write_operations(self):
        # 只读纪律：页面不得引入任何写操作（无 POST 请求、无表单提交）
        self.assertNotIn("POST", self.content)
        self.assertNotIn("<form", self.content)

    def test_readonly_banner_intact(self):
        # 原只读语义保持完整
        self.assertIn("只读投影 · 审批与重发请往飞书", self.content)
        self.assertIn("本页不审批、不重发、不触发任何发送", self.content)


class ActiveUIDengzhiTests(unittest.TestCase):
    """8787 在役 UI（hermes-webui-next）品牌显示。"""

    def test_index_contains_dengzhi(self):
        self.assertTrue(ACTIVE_INDEX_HTML.exists(), ACTIVE_INDEX_HTML)
        content = _read(ACTIVE_INDEX_HTML)
        self.assertIn("邓芝", content)
        self.assertIn("邓芝 · 飞书通讯中枢（原 HERMES）", content)

    def test_index_title_changed(self):
        content = _read(ACTIVE_INDEX_HTML)
        self.assertIn("<title>邓芝 · 飞书通讯中枢（原 HERMES）</title>", content)
        self.assertNotIn("<title>Hermes</title>", content)

    def test_console_title_contains_dengzhi(self):
        self.assertTrue(ACTIVE_CONSOLE_HTML.exists(), ACTIVE_CONSOLE_HTML)
        content = _read(ACTIVE_CONSOLE_HTML)
        self.assertIn("<title>中枢驾驶舱 · 邓芝 Console（原 HERMES）</title>", content)


if __name__ == "__main__":
    unittest.main()
