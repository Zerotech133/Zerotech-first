# -*- coding: utf-8 -*-
"""W1-2 单测：九维警报池命中提醒（盘前作战卡 + 持仓页）。

覆盖四条路径：
  ① 命中（含过期记录过滤：只认池内最近 N 个交易日）
  ② 未命中
  ③ 非 CN 市场跳过（九维只覆盖 A 股）
  ④ 文件缺失 fail-open（缺文件 = 无提醒，不炸卡不炸页）
另覆盖持仓页 write_positions_html 的命中标记与 fail-open 渲染。
"""
import json

import report_page as rp


def _mk_pool(tmp_path, rows):
    """构造临时警报池 jsonl（字段对齐 nine_dim.py 入池段）。"""
    p = tmp_path / "nine_dim_history.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def _pool_rows():
    """22 个池内交易日；600519.SH 新近命中、000045.SZ 过期命中（应被滤掉）。"""
    dts = ["202609%02d" % d for d in range(1, 23)]  # 22 天，前 2 天会滑出近 20 窗口
    rows = []
    for i, dt in enumerate(dts):
        rows.append({"code": "600519.SH", "name": "贵州茅台", "dt": dt,
                     "score": 78.0 + i * 0.1, "band": "alert",
                     "pool_type": "alert", "first_seen": "2026-01-01T00:00:00"})
    rows.append({"code": "000045.SZ", "name": "深纺织A", "dt": dts[0],
                 "score": 86.0, "band": "alert2", "pool_type": "alert"})
    rows.append({"code": "300684.SZ", "name": "中石科技", "dt": dts[-1],
                 "score": 91.2, "band": "alert2", "pool_type": "alert"})
    # 噪音：非警报池记录、坏行不干扰
    rows.append({"code": "688112.SH", "name": "鼎阳科技", "dt": dts[-1],
                 "score": 30.0, "band": "watch", "pool_type": "full"})
    return rows


# ── ① 命中 ─────────────────────────────────────────────────────
def test_hit_recent_and_drop_expired(tmp_path):
    p = _mk_pool(tmp_path, _pool_rows())
    hits = rp.nine_dim_alert_check({"600519.SH", "300684.SZ"}, path=p)
    assert set(hits) == {"600519.SH", "300684.SZ"}
    dt, score, band = hits["600519.SH"]
    assert dt == "20260922" and band == "alert"      # 同码取最近 dt
    assert abs(score - 80.1) < 1e-6                   # 78.0 + 21*0.1
    assert hits["300684.SZ"][2] == "alert2"
    # 过期命中：000045.SZ 在最早一天，滑出近 20 窗口 → 不提醒
    assert "000045.SZ" not in hits
    # 非警报池（full）不入结果
    assert "688112.SH" not in hits


# ── ② 未命中 ───────────────────────────────────────────────────
def test_miss(tmp_path):
    p = _mk_pool(tmp_path, _pool_rows())
    assert rp.nine_dim_alert_check({"000001.SZ"}, path=p) == {}
    assert rp.nine_dim_alert_check(set(), path=p) == {}   # 空集合直接返回


# ── ③ 非 CN 跳过 + 代码对齐 ────────────────────────────────────
def test_non_cn_skipped_and_code_align():
    assert rp.nine_dim_cn_code("HK", "01810") is None
    assert rp.nine_dim_cn_code("US", "NVDA") is None
    assert rp.nine_dim_cn_code("CRYPTO", "BTC/USDT") is None
    assert rp.nine_dim_cn_code("CN", None) is None
    # CN 对齐：全码原样、裸码按首位推断后缀
    assert rp.nine_dim_cn_code("CN", "600519.SH") == "600519.SH"
    assert rp.nine_dim_cn_code("cn", "300684.SZ") == "300684.SZ"
    assert rp.nine_dim_cn_code("CN", "600519") == "600519.SH"
    assert rp.nine_dim_cn_code("CN", "000045") == "000045.SZ"
    assert rp.nine_dim_cn_code("CN", "920028.BJ") == "920028.BJ"
    # HK/US 永不进检查集合（non-CN → None 被 caller 过滤）
    assert rp.nine_dim_alert_check(
        [rp.nine_dim_cn_code("HK", "01810")], path="") == {}


# ── ④ 文件缺失 fail-open ───────────────────────────────────────
def test_missing_file_fail_open(tmp_path):
    gone = str(tmp_path / "no_such_pool.jsonl")
    assert rp.nine_dim_alert_check({"600519.SH"}, path=gone) == {}
    assert rp.nine_dim_alert_check({"600519.SH"}, path="") == {}


# ── 持仓页渲染：命中行标记 + 页脚口径 + fail-open 不炸页 ─────────
def _fake_ledger():
    return {
        "pending": [{"market": "CN", "symbol": "600519.SH", "name": "贵州茅台",
                     "strategy": "trend_follow", "sig_date": "2026-10-12",
                     "last_px": 1500.0}],
        "open": [{"market": "HK", "symbol": "01810", "name": "小米集团",
                  "strategy": "ma_cross", "entry_date": "2026-09-01",
                  "entry_px": 20.0, "last_px": 22.0, "pnl": 0.1, "days": 30},
                 {"market": "CN", "symbol": "300684.SZ", "name": "中石科技",
                  "strategy": "ma_cross", "entry_date": "2026-09-01",
                  "entry_px": 10.0, "last_px": 11.0, "pnl": 0.1, "days": 30}],
        "closed": [],
    }


def test_positions_html_alert_marks(tmp_path, monkeypatch):
    p = _mk_pool(tmp_path, _pool_rows())
    monkeypatch.setattr(rp, "POSITIONS", str(tmp_path / "positions.html"))
    hits = rp.nine_dim_alert_check({"600519.SH", "300684.SZ"}, path=p)
    out = rp.write_positions_html(_fake_ledger(), "2026-10-14 08:00",
                                  alert_hits=hits)
    html = open(out, encoding="utf-8").read()
    # 命中行：暗红背景 + ⚠️警报池 徽标（待买 600519、持仓 300684 各一次）
    assert html.count("⚠️警报池") == 2
    assert 'style="background:#3a1d1d"' in html
    assert rp.POOL_ROW_BG in html
    # 页脚标注数据源与口径
    assert "nine_dim_history.jsonl" in html and "非买入信号" in html


def test_positions_html_fail_open(tmp_path, monkeypatch):
    """池文件缺失：页面正常生成，无标记无页脚口径（fail-open）。"""
    monkeypatch.setattr(rp, "POSITIONS", str(tmp_path / "positions.html"))
    monkeypatch.setattr(rp, "NINE_DIM_HIST",
                        str(tmp_path / "no_such_pool.jsonl"))
    out = rp.write_positions_html(_fake_ledger(), "2026-10-14 08:00")
    html = open(out, encoding="utf-8").read()
    assert "⚠️警报池" not in html
    assert "nine_dim_history.jsonl" not in html


def test_positions_html_switch_off(tmp_path, monkeypatch):
    """alert_hits 注入空映射（开关关/无命中）→ 不出标记。"""
    monkeypatch.setattr(rp, "POSITIONS", str(tmp_path / "positions.html"))
    out = rp.write_positions_html(_fake_ledger(), "2026-10-14 08:00",
                                  alert_hits={})
    html = open(out, encoding="utf-8").read()
    assert "⚠️警报池" not in html
