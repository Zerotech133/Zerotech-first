# -*- coding: utf-8 -*-
"""JEV 研报 AI 观点块渲染回归。

P0-1：report_events.jsonl 的研报块格式串含 8 个占位符
（jev_target 与 kg/stock 各一个 code），但参数只传了 7 个，
TypeError 被外层 except 吞掉，整块静默消失。本测试直接调用
_bt_jev 并断言两个下钻链接都出现在返回 HTML 中。
"""
import io
import json
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_backtest_pages_a2 as jev_page  # noqa: E402


class _FakeHandler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = []

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers.append((key, value))

    def end_headers(self):
        pass


def test_jev_report_block_renders_both_drilldown_links(tmp_path, monkeypatch):
    qbt = tmp_path / "qbt"
    semantica = qbt / "data" / "semantica"
    semantica.mkdir(parents=True)
    event = {
        "kind": "report",
        "bias": "bull",
        "conf": 0.82,
        "tag": "回归研报",
        "ts": "2026-11-07T10:30:00",
        "stocks": ["600519.SH"],
        "text": "P0-1 研报块渲染回归",
    }
    (semantica / "report_events.jsonl").write_text(
        json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    monkeypatch.setattr(jev_page, "QBT_DIR", str(qbt))
    monkeypatch.setattr(jev_page, "STATION", str(tmp_path / "station"))

    handler = _FakeHandler()
    jev_page._bt_jev(handler)

    assert handler.status == 200
    body = handler.wfile.getvalue().decode("utf-8")
    assert "研报 AI 观点" in body
    assert "href='/jev_target.html#600519'" in body
    assert "href='/kg/stock/600519'" in body
    assert "P0-1 研报块渲染回归" in body
    assert "%s" not in body  # 未格式化的占位符绝不应出现在成品页
