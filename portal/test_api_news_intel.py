# -*- coding: utf-8 -*-
"""K-ORG-207/208：api_news_intel 判读输出台聚合单测。

monkeypatch env AIOS_ROOT 把 REPO 锚指向 tmp（沿 test_api_notify 判例）；
api_xq_intel.REPORT_ROOT 一并 monkeypatch 指向 tmp（其模块常量锚在
C:/aios 真数据目录，测试必须隔离不读真数据）；FakeHandler 注入 _json
捕获 payload（_json 由 portal_api 主 Handler 提供，单测用 fake 等价替换）。
K-ORG-208 新增：overview 多空计数 / verdicts 冲击优先排序 / jev 坏 JSON
不炸 / 新响应结构 fail-visible 链路。K-ORG-209 新增：判读条目带 content
（截 300 字，政策栏同口径），无正文置空串。
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_news_intel
import api_perf
import api_xq_intel

NOW = datetime.now()


class FakeHandler(api_news_intel.Handler, api_perf.Handler):
    # api_perf.Handler 仅提供 _norm_qs（portal_api 真机 MRO 里同源），无状态
    def __init__(self):
        self.captured = None
        self.code = 200

    def _json(self, payload, code=200):
        self.captured = payload
        self.code = code


def get(qs=""):
    h = FakeHandler()
    h._news_intel(qs)
    return h


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    monkeypatch.setattr(api_xq_intel, "REPORT_ROOT", tmp_path / "xq_report")
    return tmp_path


def make_db(root, rows):
    # 表结构与真库同构（news 表含 stocks/jev 列，K-ORG-208 判读区依赖）
    (root / "news_collector").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "news_collector" / "news.db")
    con.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, ts TEXT, title TEXT,"
        " content TEXT, source TEXT, hit INTEGER DEFAULT 0,"
        " pushed INTEGER DEFAULT 0, digested INTEGER DEFAULT 0,"
        " created_at TEXT, stocks TEXT, jev TEXT)"
    )
    con.executemany(
        "INSERT INTO news (id,ts,title,content,source,hit,pushed,stocks,jev)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        [
            (
                r["id"],
                r["ts"],
                r.get("title", ""),
                r.get("content", ""),
                r.get("source", ""),
                r.get("hit", 0),
                r.get("pushed", 0),
                r.get("stocks"),
                r.get("jev"),
            )
            for r in rows
        ],
    )
    con.commit()
    con.close()


def hm(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M")


BASE_ROWS = [
    # 政策条目 × 权威源（govpolicy / chinanews 均在白名单）
    {
        "id": "govpol:1",
        "ts": hm(1),
        "title": "国务院关于XX试点的批复",
        "content": "同意开展试点",
        "source": "govpolicy",
        "hit": 1,
        "pushed": 1,
    },
    {
        "id": "cns:scroll:1",
        "ts": hm(2),
        "title": "央行发布新管理办法征求意见",
        "content": "面向社会征求意见",
        "source": "chinanews",
    },
    # 政策条目 × 一般源（sina 不在白名单 → 营养度分层为 false）
    {
        "id": "sina:1",
        "ts": hm(3),
        "title": "央行开展逆回购操作",
        "content": "规模 1000 亿元",
        "source": "sina",
        "hit": 0,
    },
    # 非政策条目（不进政策栏，进速报栏）
    {
        "id": "em:1",
        "ts": hm(4),
        "title": "某股盘中涨停",
        "content": "普通快讯",
        "source": "eastmoney",
    },
    # 窗口外旧条目（48h 之外，两栏都不收）
    {
        "id": "sina:old",
        "ts": hm(72),
        "title": "国务院旧闻一条",
        "content": "",
        "source": "sina",
    },
]


def test_policy_keyword_and_authority_layering(root):
    make_db(root, BASE_ROWS)
    p = get().captured
    assert p["errors"] == {}
    # 政策栏：关键词命中 3 条（旧闻在窗外不算），权威源 2 条
    assert p["policy"]["total"] == 3
    assert p["policy"]["authority_count"] == 2
    by_title = {i["title"]: i["authority"] for i in p["policy"]["items"]}
    assert by_title["国务院关于XX试点的批复"] is True  # govpolicy 权威
    assert by_title["央行发布新管理办法征求意见"] is True  # chinanews 权威
    assert by_title["央行开展逆回购操作"] is False  # sina 一般源
    # 政策条目带 content 供前端点击展开
    assert p["policy"]["items"][0]["content"]


def test_flash_structure_and_window(root):
    make_db(root, BASE_ROWS)
    p = get().captured
    fl = p["flash"]
    assert fl["total"] == 4  # 窗口内 4 条（旧闻被 48h 过滤）
    assert len(fl["items"]) == 4
    ids = [i["source"] + ":" + (i["title"][:4]) for i in fl["items"]]
    assert all("旧闻" not in t for t in ids)
    # ts DESC（2026-10-31 拍板：id 倒序会让旧闻顶第一屏，已改 ts 优先）：
    # govpol(hm1) > cns(hm2) > sina(hm3) > em(hm4)
    assert fl["items"][0]["title"].startswith("国务院关于XX试点")
    # ts 解析出 hm（HH:MM）+ hit 转 bool
    it = fl["items"][0]
    assert it["hm"] == it["ts"][-5:] and len(it["hm"]) == 5
    assert it["hit"] is True and it["pushed"] is True
    gov = next(i for i in fl["items"] if i["source"] == "govpolicy")
    assert gov["hit"] is True and gov["pushed"] is True
    # BASE_ROWS 未带 jev → 速报条目 jev 投影为 null（不炸，K-ORG-208）
    assert it["jev"] is None


def test_fail_visible_when_db_missing(root):
    # 不建 news.db：速报/政策/判读三路 fail-visible 记 errors，其余照常 200
    h = get()
    p = h.captured
    assert h.code == 200
    assert p["flash"]["items"] == [] and p["policy"]["items"] == []
    assert "flash" in p["errors"] and "policy" in p["errors"]
    assert "news.db" in p["errors"]["flash"]
    # K-ORG-208 判读区与速报同源：overview/verdicts 两键同步进 errors 不假绿
    assert "overview" in p["errors"] and "verdicts" in p["errors"]
    assert p["overview"]["total_judged"] == 0 and p["verdicts"]["items"] == []
    # 舆情：报告未生成是合法空态，不记 error
    assert p["sentiment"]["report_exists"] is False
    assert "sentiment" not in p["errors"]
    assert p["generated_at"]


def test_sentiment_summary_and_bad_json(root):
    d = root / "xq_report"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(
        json.dumps(
            {
                "date": "2026-10-19",
                "generated_at": "2026-10-19 19:00:00",
                "ingested_today": 3,
                "classified_today": 2,
                "sentiment": {"bullish": 1, "neutral": 1, "bearish": 0},
                "top_themes": {"白酒": 1, "算力": 2},
                "top_codes": {"600519": 3},
                "high_confidence": [
                    {
                        "code": "600519",
                        "title": "外资加仓高端白酒",
                        "sentiment": "bullish",
                        "confidence": 0.9,
                        "event_type": "增仓",
                    }
                ],
                "anomaly_signals": [
                    {"code": "300684", "n": 10, "bearish": 6, "baseline6d": 3}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p = get().captured
    st = p["sentiment"]
    assert st["report_exists"] is True and st["date"] == "2026-10-19"
    assert st["classified_today"] == 2
    assert st["top_themes"] == [["算力", 2], ["白酒", 1]]  # 按计数降序
    assert st["high_confidence"][0]["code"] == "600519"
    assert st["anomaly"][0]["n"] == 10
    assert "sentiment" not in p["errors"]
    # 坏 JSON → fail-visible 记 error，不炸不假绿
    (d / "latest.json").write_text("{bad", encoding="utf-8")
    p2 = get().captured
    assert "sentiment" in p2["errors"] and "bad json" in p2["errors"]["sentiment"]


def test_zero_param_whitelist(root):
    make_db(root, BASE_ROWS)
    assert get("a=1").code == 400
    assert get("").code == 200


def test_config_override_and_builtin_fallback(root):
    make_db(root, BASE_ROWS)
    # 内置默认表兜底（无 news_config.json）：国务院/央行命中照常分类
    p = get().captured
    assert p["policy"]["total"] == 3
    # config 新键覆盖默认表：白名单/关键词整表替换
    cfg = root / "news_collector" / "news_config.json"
    cfg.write_text(
        json.dumps(
            {"policy_keywords": ["特殊词"], "policy_authority_sources": ["sina"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p2 = get().captured
    assert p2["policy"]["total"] == 0  # 默认关键词不再生效
    con = sqlite3.connect(root / "news_collector" / "news.db")
    con.execute(
        "INSERT INTO news (id,ts,title,content,source) VALUES(?,?,?,?,?)",
        ("em:2", hm(1), "关于特殊词的配套细则", "", "sina"),
    )
    con.commit()
    con.close()
    p3 = get().captured
    assert p3["policy"]["total"] == 1
    assert p3["policy"]["items"][0]["authority"] is True  # sina 被配置抬为权威


# ── K-ORG-208 判读输出台用例 ─────────────────────────────────────────────


def jrow(jid, hours, title, jev_obj, source="xd_news", stocks=None, content=""):
    """判读区造数：jev_obj 为 dict 时序列化，字符串原样写入（造坏 JSON 用）。"""
    raw = (
        jev_obj if isinstance(jev_obj, str) else json.dumps(jev_obj, ensure_ascii=False)
    )
    return {
        "id": jid,
        "ts": hm(hours),
        "title": title,
        "content": content,
        "source": source,
        "stocks": stocks,
        "jev": raw,
    }


JEV_ROWS = [
    # 甲：高冲击 + 低置信（0.5）——拍板口径下应压过乙（低冲击 + 高置信 0.95）；
    # content 400 字 → 判读条目截 300 字（K-ORG-209 与政策栏同口径）
    jrow(
        "j:a",
        1,
        "甲高冲击低置信",
        {
            "bias": "bull",
            "bias_cn": "利多",
            "conf": 0.5,
            "impact_cn": "高",
            "scope_cn": "A股",
        },
        stocks='["300001.SZ"]',
        content="甲" * 400,
    ),
    # 乙：低冲击 + 高置信（对照项）
    jrow(
        "j:b",
        2,
        "乙低冲击高置信",
        {"bias": "bear", "bias_cn": "利空", "conf": 0.95, "impact_cn": "低"},
    ),
    # 丙：高冲击 + 高置信 → 应排最前；无 bias_cn → 按 bias 兜底出中文
    jrow(
        "j:c",
        3,
        "丙高冲击高置信",
        {"bias": "bear", "conf": 0.9, "impact_cn": "高"},
        source="xd_notice",
    ),
    # 丁：中冲击中性
    jrow(
        "j:d",
        4,
        "丁中冲击中性",
        {"bias": "neutral", "bias_cn": "中性", "conf": 0.7, "impact_cn": "中"},
    ),
    # 窗外旧判读：有 jev 也不收（48h 窗口过滤）
    jrow(
        "j:old",
        72,
        "窗外旧判读",
        {"bias": "bull", "conf": 1.0, "impact_cn": "高"},
        source="sina",
    ),
]


def test_overview_counts_and_verdict_impact_priority(root):
    make_db(root, JEV_ROWS)
    p = get().captured
    assert p["errors"] == {}
    # 总览计数：窗外旧判读不计；方向分桶 + 高冲击计数
    ov = p["overview"]
    assert ov["total_judged"] == 4
    assert ov["bull"] == 1 and ov["bear"] == 2 and ov["neutral"] == 1
    assert ov["high_impact"] == 2
    # 关键判读排序（2026-11-10 conf 弃用后新口径）：impact 权重优先、
    # 事件类型分层次级（strong 优先、|signal| 再次）；甲/丙同层（无 event_type）
    # → conf 不再分高下，按 id 基础序；丁(中冲击)、乙(低冲击)随后
    vd = p["verdicts"]
    assert vd["total"] == 4 and len(vd["items"]) == 4
    assert [i["title"][0] for i in vd["items"]] == ["甲", "丙", "丁", "乙"]
    # 字段投影：hm 解析 / stocks JSON 数组解析 / bias_cn 兜底 / scope_cn 带出
    top = vd["items"][1]
    assert top["hm"] == top["ts"][-5:] and top["source"] == "xd_notice"
    assert top["jev"]["bias_cn"] == "利空"  # bias 兜底出中文
    second = vd["items"][0]
    assert second["stocks"] == ["300001.SZ"]
    assert second["jev"]["bias"] == "bull" and second["jev"]["conf"] == 0.5
    assert second["jev"]["scope_cn"] == "A股"
    # K-ORG-209：判读条目带 content 供行卡展开；300 截断与政策栏同口径
    assert all("content" in i for i in vd["items"])
    assert second["content"] == "甲" * 300  # 400 字截到 300
    # 无正文的条目 content 置空串（不缺键，前端灰字兜底）
    no_body = [i for i in vd["items"] if i["title"] != "甲高冲击低置信"]
    assert no_body and all(i["content"] == "" for i in no_body)


def test_verdicts_and_flash_survive_bad_jev(root):
    rows = [
        # 好判读：正常进 overview/verdicts
        jrow(
            "j:good",
            1,
            "好判读一条",
            {"bias": "bull", "bias_cn": "利多", "conf": 0.8, "impact_cn": "高"},
        ),
        # 坏 JSON：flash 条目 jev 置 null，不进统计，整包不炸
        jrow("j:bad", 2, "坏JSON一条", "{bad json", source="sina"),
        # 空串 jev：判例过滤 jev!=''（api_backtest 同款），静默
        jrow("j:empty", 3, "空jev一条", "", source="sina"),
        # 非 dict JSON（数组）：投影置 null 不进统计
        jrow("j:arr", 4, "数组jev一条", "[1,2]", source="sina"),
        # conf 非法值：条目仍有效进统计，conf 置 None（不炸）
        jrow(
            "j:conf",
            5,
            "conf非法一条",
            {"bias": "bear", "conf": "abc", "impact_cn": "低"},
            source="sina",
        ),
    ]
    make_db(root, rows)
    p = get().captured
    assert p["errors"] == {}
    # 只有 有效dict 判读计数：坏 JSON/空串/非 dict 三条被剔除
    ov = p["overview"]
    assert ov["total_judged"] == 2
    assert ov["bull"] == 1 and ov["bear"] == 1 and ov["high_impact"] == 1
    vd = p["verdicts"]
    assert vd["total"] == 2
    titles = [i["title"] for i in vd["items"]]
    assert "好判读一条" in titles and "conf非法一条" in titles
    bad_conf = next(i for i in vd["items"] if i["title"] == "conf非法一条")
    assert bad_conf["jev"]["conf"] is None
    # flash 逐条 jev 投影：好=dict，坏/空/非 dict=null（fail-visible 不适用单条）
    by_title = {i["title"]: i["jev"] for i in p["flash"]["items"]}
    assert by_title["好判读一条"]["bias"] == "bull"
    assert by_title["坏JSON一条"] is None
    assert by_title["空jev一条"] is None
    assert by_title["数组jev一条"] is None
