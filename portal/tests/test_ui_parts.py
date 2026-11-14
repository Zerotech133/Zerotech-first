# -*- coding: utf-8 -*-
"""ui_parts 单测（I1a 步 1 验收）。

纪律：日期一律用 date(整数,整数,整数) 构造，不写字符串字面量
（判例：Write/Edit 通道同样会把日期串味，见用户级铁律）。
用 importlib 按文件路径加载，不依赖 rootdir / sys.path。
"""
import importlib.util
import os
import re
from datetime import date

_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "ui_parts.py")
_spec = importlib.util.spec_from_file_location("ui_parts_under_test", _P)
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)

TODAY = date(2026, 11, 14)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _d(y, m, d):
    return date(y, m, d).isoformat()


# ── 1. 缺失数据日不得编造日期（本模块最重要的一条） ──
def test_asof_none_has_no_date():
    out = U.asof_badge(None, today=TODAY)
    assert "数据日未知" in out
    assert not ISO_DATE.search(out), "传 None 时不得出现任何日期"
    assert "ui-asof--unknown" in out


def test_asof_illegal_string_is_unknown():
    out = U.asof_badge("not-a-date", today=TODAY)
    assert "数据日未知" in out
    assert not ISO_DATE.search(out)


# ── 2. 状态判定边界 ──
def test_state_boundaries():
    assert "ui-asof--fresh" in U.asof_badge(_d(2026, 11, 14), today=TODAY)
    assert "ui-asof--fresh" in U.asof_badge(_d(2026, 11, 12), today=TODAY)  # 2 天
    assert "ui-asof--lag" in U.asof_badge(_d(2026, 11, 11), today=TODAY)    # 3 天
    assert "ui-asof--lag" in U.asof_badge(_d(2026, 11, 4), today=TODAY)     # 10 天
    assert "ui-asof--dead" in U.asof_badge(_d(2026, 11, 3), today=TODAY)    # 11 天


def test_state_override():
    # 施工单场景：/bt-opt 数据日 09-26，强制 dead
    out = U.asof_badge(_d(2026, 9, 26), state="dead", today=TODAY)
    assert "ui-asof--dead" in out
    assert _d(2026, 9, 26) in out


def test_dead_shows_gap_days():
    out = U.asof_badge(_d(2026, 9, 26), today=TODAY)
    gap = (TODAY - date(2026, 9, 26)).days
    assert ("停更 %d 天" % gap) in out


def test_today_injectable():
    day = _d(2026, 9, 26)
    a = U.asof_badge(day, today=date(2026, 9, 27))
    b = U.asof_badge(day, today=date(2026, 12, 31))
    assert "ui-asof--fresh" in a
    assert "ui-asof--dead" in b


# ── 3. sec：空 note 不产生空 div ──
def test_sec_empty_note_no_empty_div():
    out = U.sec("今日结论")
    assert "ui-sec-title" in out
    assert "ui-sec-note" not in out
    assert 'class=""' not in out and "<div >" not in out


def test_sec_with_note_and_body():
    note = U.asof_badge(_d(2026, 11, 11), today=TODAY)   # 3 天 → lag
    out = U.sec("预警分诊", note=note, body="<p>x</p>", anchor="radar")
    assert 'id="radar"' in out
    assert "ui-asof--lag" in out
    assert "<p>x</p>" in out


def test_sec_escapes_title():
    out = U.sec('<script>alert(1)</script>')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ── 4. kpi ──
def test_kpi_plain():
    out = U.kpi("信号数", 18)
    assert "ui-kpi-v" in out and "18" in out


def test_kpi_pair():
    out = U.kpi("胜率", ("42%", "21/50"), note="近 30 日")
    assert "42%" in out and "21/50" in out
    assert "近 30 日" in out


def test_kpi_escapes():
    out = U.kpi("a<b", "<i>")
    assert "<i>" not in out


# ── 5. 样式块：不硬编码 hex，只引语义变量 ──
def test_style_uses_tokens_no_hex():
    css = U.style_block()
    assert css.startswith("<style>") and css.endswith("</style>")
    assert "--color-ok" in css and "--text-dim" in css
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css), "禁止硬编码 hex"
