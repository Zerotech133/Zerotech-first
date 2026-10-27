# -*- coding: utf-8 -*-
"""ui_keyboard_audit 单测。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_keyboard_audit as k  # noqa: E402


HTML = """<html><body>
<a href="#main">跳到主要内容</a>
<button>刷新</button>
<div hidden><button>隐藏按钮</button></div>
<input type="hidden" value="x">
<a>无 href 不算</a>
<div tabindex="0">自定义焦点</div>
<button tabindex="2">坏顺序</button>
</body></html>"""


def test_focus_order(tmp_path):
    p = tmp_path / "x.html"
    p.write_text(HTML, encoding="utf-8")
    r = k.audit(str(p))
    labels = [x["label"] for x in r["order"]]
    assert labels[0] == "跳到主要内容"
    assert "刷新" in labels
    assert "隐藏按钮" not in labels
    assert "自定义焦点" in labels
    assert r["skip_first"] is True
    assert r["positive_tabindex"] == ["button"]


def test_markdown(tmp_path):
    p = tmp_path / "x.html"
    p.write_text(HTML, encoding="utf-8")
    md = k.to_markdown(k.audit(str(p)))
    assert "键盘焦点顺序审计" in md and "跳到主要内容" in md
