# -*- coding: utf-8 -*-
"""qd_scanner 多源降级（[20261014_p1_tail] 腾讯→新浪→东财）单测。

覆盖（四条主路径 + 快照降级，mock HTTP 层，纯合成数据，不联网不真拉）:
  T1 源切换顺序：腾讯失败→新浪顶上；腾讯+新浪失败→东财顶上
  T2 错日判定：末根≠expect_day 该源视为失败，自动切下家（判例 W3-4）
  T3 全源失败显式报错不假绿：返回 (None,"all_failed")；intraday_recheck
     端到端——候选跳过、checked 不计入、note 留痕
  T4 开关关闭：qd_multi_source=false → 仅腾讯（新浪/东财零调用），
     含 config 缺省开/显式关两种读法
  T5 降级命中端到端：腾讯失败、新浪顶上 → 真引擎重算，note 落
     source=sina_fallback
  T6 快照降级：新浪快照字段映射（价格/涨跌幅/成交量/时间戳）

运行:
  "C:/Program Files/Python311/python.exe" portal/test_qd_multisource.py
"""

import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))
sys.path.insert(0, str(PORTAL.parent / "agent_platform" / "engine"))

import qd_scanner as qd  # noqa: E402

PASS = []
FAILS = []


def check(name, cond, detail=""):
    print(
        "%s %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            "｜" + detail if detail and not cond else "",
        )
    )
    (PASS if cond else FAILS).append(name)


TODAY = "20261014"


def fake_rows(last_day, n=150):
    """合成日K行（腾讯行格式）：[[date, open, close, high, low, vol], ...]"""
    return [
        [
            "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28),
            10.0 + i * 0.01,
            10.5,
            10.6,
            9.9,
            12345.0,
        ]
        for i in range(n - 1)
    ] + [
        [
            "%s-%s-%s" % (last_day[:4], last_day[4:6], last_day[6:8]),
            11.0,
            11.5,
            11.6,
            10.9,
            23456.0,
        ]
    ]


def reset_sources(tencent=None, sina=None, east=None):
    """mock HTTP 边界：直接替换三个源拉取函数（其内部才是真 HTTP）"""
    calls = {"sina": 0, "east": 0}

    def _t(code, n):
        if tencent is None:
            raise IOError("tencent mock fail")
        return tencent

    def _s(code, n):
        calls["sina"] += 1
        if sina is None:
            raise IOError("sina mock fail")
        return sina

    def _e(code, n):
        calls["east"] += 1
        if east is None:
            raise IOError("east mock fail")
        return east

    qd._kline_tencent, qd._kline_sina, qd._kline_east = _t, _s, _e
    return calls


def main():
    good = fake_rows(TODAY)
    wrong_day = fake_rows("20260101")

    # T1 源切换顺序：腾讯失败→新浪；腾讯+新浪失败→东财
    calls = reset_sources(tencent=None, sina=good, east=good)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY)
    check("T1 腾讯败新浪顶", rows is good and src == "sina_fallback", "src=%s" % src)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY)
    check("T1 降级链留痕", note.startswith("tencent:"), note)
    calls = reset_sources(tencent=None, sina=None, east=good)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY)
    check("T1 双败东财顶", rows is good and src == "east_fallback", "src=%s" % src)

    # T2 错日判定：腾讯末根错日=源失败→新浪；新浪也错日→东财
    calls = reset_sources(tencent=wrong_day, sina=good, east=good)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY)
    check(
        "T2 腾讯错日切新浪",
        rows is good and src == "sina_fallback" and "tencent:last=20260101" in note,
        "src=%s note=%s" % (src, note),
    )
    calls = reset_sources(tencent=wrong_day, sina=wrong_day, east=good)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY)
    check(
        "T2 双错日切东财",
        rows is good and src == "east_fallback" and "sina:last=20260101" in note,
        "src=%s note=%s" % (src, note),
    )

    # T3 全源失败显式报错不假绿
    calls = reset_sources(tencent=None, sina=wrong_day, east=None)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY)
    check("T3 全源失败返回None", rows is None and src == "all_failed", "src=%s" % src)
    check(
        "T3 全源失败note留痕",
        "tencent" in note and "sina" in note and "east" in note,
        note,
    )
    # T3b 端到端：intraday_recheck 全源失败→候选跳过、不产生信号
    orig_km = qd.kline_multisource
    qd.kline_multisource = lambda c, n=150, expect_day=None, enabled=None: (
        None,
        "all_failed",
        "tencent:HTTPError",
    )
    try:
        sigs, checked, note = qd.intraday_recheck(["600519.SH"])
    finally:
        qd.kline_multisource = orig_km
    check(
        "T3b 候选跳过不假绿",
        sigs == [] and checked == 0 and "全源失败" in note,
        "checked=%d note=%s" % (checked, note),
    )

    # T4 开关关闭：仅腾讯，新浪/东财零调用；enabled=None 读 config
    calls = reset_sources(tencent=good, sina=good, east=good)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY, enabled=False)
    check(
        "T4 关闭时腾讯命中",
        rows is good and src == "tencent" and calls == {"sina": 0, "east": 0},
        "src=%s calls=%s" % (src, calls),
    )
    calls = reset_sources(tencent=None, sina=good, east=good)
    rows, src, note = qd.kline_multisource("600519", expect_day=TODAY, enabled=False)
    check(
        "T4 关闭时腾讯败即败",
        rows is None and calls == {"sina": 0, "east": 0} and "sina" not in note,
        "calls=%s note=%s" % (calls, note),
    )
    orig_cfg = qd._load_cfg
    qd._load_cfg = lambda: {"qd_multi_source": False}
    try:
        calls = reset_sources(tencent=good, sina=good, east=good)
        rows, src, _ = qd.kline_multisource("600519", expect_day=TODAY, enabled=None)
        check(
            "T4 config 显式关",
            rows is good and src == "tencent" and calls == {"sina": 0, "east": 0},
            "calls=%s" % calls,
        )
        qd._load_cfg = lambda: {}
        calls = reset_sources(tencent=None, sina=good, east=None)
        rows, src, _ = qd.kline_multisource("600519", expect_day=TODAY, enabled=None)
        check("T4 config 缺省开（缺 key=true）", src == "sina_fallback", "src=%s" % src)
    finally:
        qd._load_cfg = orig_cfg

    # T5 降级命中端到端：真引擎重算，note 落 source=sina_fallback
    calls = reset_sources(tencent=None, sina=good, east=good)
    try:
        sigs, checked, note = qd.intraday_recheck(["600519.SH"])
    except Exception as e:
        sigs, checked, note = None, -1, "EXC %s: %s" % (type(e).__name__, e)
    check(
        "T5 降级命中复核通过",
        checked == 1 and sigs is not None and "source=sina_fallback" in note,
        "checked=%d note=%s" % (checked, note),
    )

    # T6 快照降级：新浪字段映射（mock _http_text，不真拉）
    orig_http = qd._http_text
    _SINA_RAW = (
        'var hq_str_sh600519="贵州茅台,1500.00,1480.00,1495.00,'
        "1510.00,1470.00,1494.99,1495.00,12345678,0,0,0,0,0,0,0,0,"
        '0,0,0,0,0,0,0,0,0,0,0,0,0,2026-10-14,15:00:03,00";'
    )
    qd._http_text = lambda url, headers=None, timeout=10, encoding="utf-8": _SINA_RAW
    qd._quote_tencent = lambda c: (_ for _ in ()).throw(IOError("down"))
    try:
        qs, src, _ = qd.quote_multisource("600519")
    finally:
        qd._http_text = orig_http
        reset_sources()
    check(
        "T6 新浪快照映射",
        src == "sina_fallback"
        and qs
        and qs["name"] == "贵州茅台"
        and qs["price"] == 1495.00
        and qs["change_pct"] == 1.01
        and qs["volume"] == 123456.78
        and qs["ts"].startswith("2026-10-14"),
        "src=%s q=%s" % (src, qs),
    )

    print("\n%d PASS / %d FAIL" % (len(PASS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
