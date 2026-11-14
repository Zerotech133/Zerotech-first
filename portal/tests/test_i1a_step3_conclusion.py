# -*- coding: utf-8 -*-
"""I1a 步 3 单测：三个活页「一句话结论 + asof」。

纪律：
· 日期一律 date() 构造（判例：Write/Edit 通道会串味日期字面量）；
· 生成的 HTML 必须过 HTMLParser（判例：style 属性引号嵌套，子串断言查不出）；
· 数字可溯源断言：结论声称的每个数字必须等于 fixture 里的真实计数
  （施工单验收「每个数字都能在页内找到出处，禁止前端自算」）。
用 importlib 按文件路径加载，不依赖 rootdir / sys.path（report_page
依赖 strategies，需把 quant_backtest 目录放进 sys.path）。
"""
import importlib.util
import os
import re
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser

_HERE = os.path.dirname(os.path.abspath(__file__))
_PORTAL = os.path.dirname(_HERE)
_QBT = os.path.dirname(_PORTAL)
for _p in (_PORTAL, os.path.join(_QBT, "quant_backtest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


U = _load("ui_parts_step3", os.path.join(_PORTAL, "ui_parts.py"))
ACP = _load("acp_step3", os.path.join(_PORTAL, "api_chanlun_pages.py"))
ABP = _load("abpa2_step3", os.path.join(_PORTAL, "api_backtest_pages_a2.py"))
RP = _load("rp_step3", os.path.join(_QBT, "quant_backtest",
                                    "report_page.py"))

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _d(y, m, d):
    return date(y, m, d).isoformat()


class _Walker(HTMLParser):
    """收集标签序列与可见文本（convert_charrefs 解实体）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())


def _parse(html_str):
    w = _Walker()
    w.feed(html_str)
    w.close()
    return w


def _texts(w):
    return " ".join(w.text)  # 拼全文供子串断言（HTMLParser 已解实体）


# ══════════════ 1. 缠论预警池结论 ══════════════
def _pool_fixture():
    last = _d(2026, 11, 13)
    pool = []
    for _ in range(5):
        pool.append({"detect_date": last, "type": "2买", "tbe": None,
                     "tradable": True, "code": "600000", "name": "甲"})
    for _ in range(3):
        pool.append({"detect_date": last, "type": "1卖", "tbe": True,
                     "tradable": False, "code": "000001", "name": "乙"})
    pool.append({"detect_date": _d(2026, 11, 12), "type": "1卖",
                 "tbe": True, "tradable": True, "code": "000002",
                 "name": "丙"})  # 前一日，不计入
    pool.append({"detect_date": last, "type": "3买", "tbe": None,
                 "tradable": None, "code": "300001", "name": "丁"})  # 旧口径缺字段
    strong = [r for r in pool if r["detect_date"] == last
              and r["type"] == "1卖" and r["tbe"] is True]
    paper = {"open": [{"code": "600000"}], "closed": [{"code": "600001"},
                                                      {"code": "600002"}]}
    return last, pool, paper, strong


def test_pool_conclusion_numbers_traceable():
    last, pool, paper, strong = _pool_fixture()
    html_str = ACP.chanlun_pool_conclusion(last, pool, paper, strong)
    w = _parse(html_str)
    txt = _texts(w)
    # 逐个数字对账 fixture（当日 9 = 5+3+1；屏蔽 3（三条 1卖全 False）；
    # 强预警 3；持仓 1；已平 2）
    assert "入池 9 条" in txt
    assert "涨停屏蔽 3 条不可成交" in txt
    assert "另 1 条旧口径缺该字段" in txt
    assert "强预警 3 只" in txt
    assert "持仓 1 笔" in txt
    assert "已平 2 笔" in txt
    assert last in txt                      # asof：数据日 = 最新检出日
    assert "1卖·趋✓" in txt


def test_pool_conclusion_structure_parses():
    last, pool, paper, strong = _pool_fixture()
    html_str = ACP.chanlun_pool_conclusion(last, pool, paper, strong)
    w = _parse(html_str)  # 解析器不抛异常 = 标签/属性合法
    assert ("div", {"class": "ui-sec"}) in w.tags
    assert "一句话结论" in w.text
    assert not re.search(r"%[sd]", html_str), "不得残留未格式化占位符"


def test_pool_conclusion_all_tradable_missing():
    last = _d(2026, 11, 13)
    pool = [{"detect_date": last, "type": "2买", "tbe": None,
             "tradable": None, "code": "600000", "name": "甲"}]
    html_str = ACP.chanlun_pool_conclusion(last, pool, {}, [])
    txt = _texts(_parse(html_str))
    assert "可成交字段缺失" in txt, "全缺字段必须如实标注，不得当全过"


def test_pool_conclusion_no_strong():
    last = _d(2026, 11, 13)
    pool = [{"detect_date": last, "type": "2买", "tbe": None,
             "tradable": True, "code": "600000", "name": "甲"}]
    html_str = ACP.chanlun_pool_conclusion(last, pool, {}, [])
    txt = _texts(_parse(html_str))
    assert "今日无 1卖·趋✓ 强预警" in txt


def test_pool_empty_no_fabricated_date():
    html_str = ACP.chanlun_pool_conclusion("", [], {}, [])
    txt = _texts(_parse(html_str))
    assert "池文件为空或缺失" in txt
    assert not ISO_DATE.search(html_str), "空池不得编造任何日期"
    assert "数据日未知" in txt  # asof_badge(None) 的诚实渲染


# ══════════════ 2. AI 晨报页结论 ══════════════
def _ai_row(day, ok=1, mark="【一句话总览】", overview="总览句。"):
    """(ts, model, ok, brief) 元组；日期走 date() 构造防串味。"""
    ts = date(day, 11, 8).strftime("%Y-%m-%d") + " 08:00"
    brief = "%s\n%s\n\n【数据面】\n指数 xx\n" % (mark, overview)
    return (ts, "qwen3.5:9b", ok, brief)


def test_brief_conclusion_quotes_pipeline_judgement():
    b = _ai_row(2026, overview="A 股港股普跌，英伟达成焦点。")
    r = _ai_row(2026, mark="【大盘复盘】", overview="三大指数全线下跌。")
    html_str = ABP.brief_conclusion(b, r)
    txt = _texts(_parse(html_str))
    assert "A 股港股普跌，英伟达成焦点" in txt, "必须引用管线判定句"
    assert "三大指数全线下跌" in txt
    assert _d(2026, 11, 8) in txt           # asof = 最新 ts 日期
    assert "（降级" not in txt


def test_brief_conclusion_missing_mark_honest():
    b = (date(2026, 11, 14).strftime("%Y-%m-%d") + " 08:00",
         "qwen3.5:9b", 1, "自由文本无结构段。")
    html_str = ABP.brief_conclusion(b, None)
    txt = _texts(_parse(html_str))
    assert "缺【一句话总览】结构字段" in txt, "缺判定必须如实标注"
    assert "不编造" in txt
    assert _d(2026, 11, 14) in txt


def test_brief_conclusion_both_none():
    html_str = ABP.brief_conclusion(None, None)
    txt = _texts(_parse(html_str))
    assert "暂无生成记录" in txt
    assert not ISO_DATE.search(html_str), "无记录不得编造日期"


def test_brief_conclusion_degraded_flagged():
    b = _ai_row(2026, ok=0, overview="降级日的总览。")
    html_str = ABP.brief_conclusion(b, None)
    txt = _texts(_parse(html_str))
    assert "（降级：模型未参与）" in txt


def test_brief_conclusion_escapes_overview():
    b = _ai_row(2026, overview="<script>alert(1)</script>")
    html_str = ABP.brief_conclusion(b, None)
    assert "<script>" not in html_str, "总览句是 LLM 文本，必须转义"
    assert "&lt;script&gt;" in html_str


# ══════════════ 3. 全池策略信号页结论 ══════════════
def _sig_fixture():
    st = {"trend_follow": 1, "ma_cross": 1, "boll_breakout": 0,
          "rsi_reversal": 0}

    def row(day, sigs):
        since = {k: _d(2026, 11, 1) for k in sigs}
        return {"market": "CN", "symbol": "600000.SH", "name": "甲",
                "px": 10.0, "date": _d(*day), "sigs": sigs,
                "since": since, "res": sum(sigs.values())}

    return [
        row((2026, 11, 13), {k: 1 for k in st}),          # res 4
        row((2026, 11, 13), dict(st)),                    # res 2
        row((2026, 11, 12), {k: 0 for k in st}),          # res 0
        row((2026, 11, 12), {"trend_follow": 1, "ma_cross": 1,
                             "boll_breakout": 1, "rsi_reversal": 0}),  # res 3
    ]


def test_signals_conclusion_numbers_traceable():
    rows = _sig_fixture()
    sent = RP.signals_conclusion(rows)
    assert _d(2026, 11, 13) in sent          # asof = max(date)
    assert "全池 4 只" in sent
    assert "3 只任一策略翻多" in sent         # res>=1 计数
    assert "共振 ≥3 共 2 只" in sent          # res 4 与 3 两行
    assert "逐行核对" in sent, "必须写明页内出处路径"


def test_signals_conclusion_empty_honest():
    sent = RP.signals_conclusion([])
    assert "结论缺失" in sent
    assert not ISO_DATE.search(sent), "空数据不得编造日期"


def test_signals_page_writes_conclusion_html():
    """整页生成走真实 write_signals_html（SIGNALS 重定向到临时目录），
    HTMLParser 过一遍：结论区存在、数字齐全、无占位符残留。"""
    rows = _sig_fixture()
    tmpdir = tempfile.mkdtemp(prefix="sig_conc_")
    old = RP.SIGNALS
    RP.SIGNALS = os.path.join(tmpdir, "signals.html")
    try:
        out = RP.write_signals_html(
            rows, date(2026, 11, 14).strftime("%Y-%m-%d %H:%M"))
        with open(out, encoding="utf-8") as f:
            page = f.read()
    finally:
        RP.SIGNALS = old
    w = _parse(page)
    txt = _texts(w)
    assert "一句话结论" in txt
    assert _d(2026, 11, 13) in txt
    assert "全池 4 只" in txt
    assert "共振 ≥3 共 2 只" in txt
    assert not re.search(r"%[sd]", page), "不得残留未格式化占位符"
