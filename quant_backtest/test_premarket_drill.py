# -*- coding: utf-8 -*-
"""K-STOCK-389：盘前镜像文档股票下钻——涨停/公告/解禁/研报/新闻关联个股
挂 /kg/stock 链接；portal_url 缺省回落纯文本。零数据依赖。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import premarket  # noqa: E402
import runner  # noqa: E402


def _fixture():
    return {
        "overall": "偏多",
        "verdict": "外围暖+流动性稳",
        "prob": 62,
        "ts": "10-15 08:30",
        "vals": {"外围": 1, "政策": 0, "流动性": 1, "板块": 0, "情绪": -1},
        "l1": {"rows": [{"name": "纳指", "pct": 0.55, "date": "10-14"}]},
        "l2": {
            "macro": [],
            "jev": {"n": 2, "bull": 1, "bear": 1, "neu": 0},
            "news": [
                {
                    "title": "某政策",
                    "bias_cn": "利多",
                    "conf": 0.8,
                    "stocks": ["600853.SH", "300124.SZ"],
                }
            ],
        },
        "l3": {
            "margin_yi": "18000",
            "margin_date": "10-13",
            "margin_yoy": 0.1,
            "north_yi": "30",
            "north_date": "10-13",
            "shibor_on": "1.5",
            "shibor_chg": 0.01,
        },
        "l4": [{"sector": "半导体", "n": 3, "avg": 1.2, "asof": "10-14", "cn": "芯片"}],
        "l5": {
            "n": 5,
            "bull": 3,
            "bear": 2,
            "vix": {"val": 15.0, "chg": -1.0},
            "money": {
                "date": "10-14",
                "limit_up": 60,
                "limit_dn": 5,
                "max_streak": 5,
                "streak_name": "光智科技",
                "up": 2800,
                "dn": 2000,
                "flat": 200,
                "broke_rate": 20,
                "broke_n": 15,
                "cm20_share": 30,
                "top": [
                    {
                        "name": "光智科技",
                        "code": "300489.SZ",
                        "pct": 20.0,
                        "streak": 5,
                        "amt_yi": 12.3,
                    }
                ],
            },
        },
        "l6": {
            "ann_stats": {"total": 3, "bull": 1, "bear": 1, "neu": 1},
            "announcements": [
                {
                    "cat": "bull",
                    "code": "600853.SH",
                    "name": "龙建股份",
                    "title": "中标公告",
                    "date": "10-14",
                    "url": "",
                }
            ],
            "unlock": [
                {
                    "code": "002594.SZ",
                    "name": "比亚迪",
                    "title": "解禁 5%",
                    "date": "10-16",
                    "url": "",
                }
            ],
            "rpt_stats": {"total": 2, "rated": 2, "buy": 2, "hold": 0, "sell": 0},
            "reports": [
                {
                    "code": "300750.SZ",
                    "org": "中信",
                    "rating": "买入",
                    "name": "宁德时代",
                    "date": "10-13",
                    "url": "https://example.com/r",
                }
            ],
        },
        "alerts": ["北向流出超阈值"],
    }


def test_doc_drill_links(monkeypatch):
    monkeypatch.setitem(runner.CFG["report"], "portal_url", "http://192.168.0.3:8080/")
    md = premarket.build_doc_md(_fixture())
    base = "http://192.168.0.3:8080/kg/stock/"
    assert "[光智科技](%s300489)" % base in md  # 涨停 Top5
    assert "[龙建股份](%s600853)" % base in md  # 公告
    assert "[比亚迪](%s002594)" % base in md  # 解禁
    assert "[宁德时代](%s300750)" % base in md  # 研报
    assert ("[600853](%s600853)" % base) in md  # 新闻关联个股(码作标签)
    assert "关联个股" in md  # 表头列出


def test_doc_drill_fallback(monkeypatch):
    monkeypatch.setitem(runner.CFG["report"], "portal_url", "")
    md = premarket.build_doc_md(_fixture())
    bad = [ln for ln in md.splitlines() if "/kg/stock/" in ln]
    assert not bad, "回落纯文本被破坏，残留链接行: %r" % bad[:3]
    assert "光智科技" in md and "宁德时代" in md
