# -*- coding: utf-8 -*-
"""market_analysis 单测（2026-11-06 工程化）。

覆盖点（都是踩过的坑）：
1. 涨跌停按板块规则判定，不读 limit_list_d；
2. 分桶标签是区间不是"涨停"别名（创业板涨 15% 不能算涨停）；
3. 空壳 parquet 判 empty 而不是 ok；
4. 指数缺失不回退、degraded 可见、--strict 返回 1；
5. 限期已知降级：登记内放行、过期/缺窗口/未登记一律阻断（不是永久免死金牌）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import market_analysis as m  # noqa: E402


def _daily(rows):
    return pd.DataFrame(rows, columns=["ts_code", "name", "pct_chg", "amount"])


def test_limit_pct_by_board():
    assert m.limit_pct("600000.SH", "浦发银行") == 10.0
    assert m.limit_pct("300001.SZ", "特锐德") == 20.0
    assert m.limit_pct("301001.SZ", "凯淳股份") == 20.0
    assert m.limit_pct("688001.SH", "华兴源创") == 20.0
    assert m.limit_pct("830001.BJ", "艾融软件") == 30.0
    assert m.limit_pct("600001.SH", "ST 某某") == 5.0
    # 创业板 ST 仍按 20%，不能被 ST 规则误降到 5%
    assert m.limit_pct("300002.SZ", "ST 创业") == 20.0


def test_breadth_counts_across_boards():
    df = _daily([
        ("600000.SH", "主板涨停", 10.00, 1e5),
        ("300001.SZ", "创业涨停", 20.00, 1e5),
        ("830001.BJ", "北交涨停", 30.00, 1e5),
        ("600001.SH", "ST 涨停", 5.00, 1e5),
        ("000001.SZ", "主板跌停", -10.00, 1e5),
        ("600002.SH", "平盘", 0.00, 1e5),
        ("600003.SH", "涨一半", 6.00, 1e5),
    ])
    b = m.breadth(df)
    assert b["n"] == 7
    assert b["limit_up"] == 4, b
    assert b["limit_down"] == 1, b
    assert b["up"] == 5 and b["down"] == 1 and b["flat"] == 1
    assert b["ge_9_9"] == 3           # 10/20/30，不含 ST 5% 与 6%
    assert b["amount_yi"] == pytest.approx(7.0, abs=0.01)


def test_distribution_label_is_range_not_limitup():
    """创业板涨 15% 落在 ">=9.9%"，但 breadth 不把它算涨停 —— 两个口径必须分开。"""
    s = pd.Series([15.0, 10.0, 6.0, -10.0, 0.0])
    d = m.distribution(s)
    assert d[">=9.9%"] == 2
    assert d["5~7%"] == 1
    assert d["<=-9.9%"] == 1
    assert d["0~1%"] == 1
    assert sum(d.values()) == 5


def test_concentration_topn_share():
    # amount 单位千元，1 亿元 = 1e5 千元：50e5 千元 = 50 亿
    df = _daily([("A", "a", 1.0, 50e5), ("B", "b", 1.0, 30e5), ("C", "c", 1.0, 20e5)])
    c = m.concentration(df, topn=2)
    assert c["total_amount_yi"] == pytest.approx(100.0, abs=0.1)
    assert c["top2_amount_yi"] == pytest.approx(80.0, abs=0.1)
    assert c["top2_share_pct"] == pytest.approx(80.0, abs=0.1)


def _write_min_root(tmp_path: Path, with_index: bool = False, empty_index: bool = False) -> Path:
    root = tmp_path / "tushare"
    (root / "daily").mkdir(parents=True)
    (root / "stock_basic").mkdir(parents=True)
    pd.DataFrame({
        "ts_code": ["600000.SH", "300001.SZ"], "name": ["主板", "创业"],
        "pct_chg": [10.0, -1.0], "amount": [1e5, 2e5],
    }).to_parquet(root / "daily" / "d_20261105.parquet", index=False)
    pd.DataFrame({"ts_code": ["600000.SH", "300001.SZ"], "name": ["主板", "创业"],
                  "industry": ["银行", "电气设备"]}).to_parquet(
        root / "stock_basic" / "part_0000.parquet", index=False)
    if with_index or empty_index:
        (root / "index_daily").mkdir()
        rows = [{"ts_code": "000001.SH", "trade_date": "20261105", "close": 3600.0,
                 "pct_chg": 0.5}] if with_index else []
        pd.DataFrame(rows, columns=["ts_code", "trade_date", "close", "pct_chg"]).to_parquet(
            root / "index_daily" / "d_20261105.parquet", index=False)
    return root


def test_quality_flags_empty_shell_as_empty(tmp_path):
    root = _write_min_root(tmp_path, empty_index=True)
    rows, degraded = m.quality(root, "20261105")
    status = {r["table"]: r["status"] for r in rows}
    assert status["daily"] == "ok"
    assert status["index_daily"] == "empty"      # 空壳不能算 ok
    assert "index_daily" in degraded


def test_build_does_not_fallback_index(tmp_path):
    root = _write_min_root(tmp_path)             # 完全没有 index_daily
    res = m.build("20261105", root, known={})    # 不读仓库真实登记文件
    assert res["index"] == []                    # 不回退到昨日
    assert "index_daily" in res["degraded"]
    assert "index_daily" in res["degraded_unregistered"]


def test_main_strict_returns_1_on_degraded(tmp_path):
    root = _write_min_root(tmp_path)
    out = tmp_path / "out"
    rc = m.main(["--date", "20261105", "--data-root", str(root), "--out-dir", str(out),
                 "--strict", "--known-degraded", str(tmp_path / "none.json")])
    assert rc == 1
    md = (out / "market_analysis_20261105.md").read_text(encoding="utf-8")
    assert "数据降级" in md
    assert "index_daily" in md


def test_main_nonstrict_writes_both_outputs(tmp_path):
    root = _write_min_root(tmp_path, with_index=True)
    out = tmp_path / "out"
    rc = m.main(["--date", "20261105", "--data-root", str(root), "--out-dir", str(out)])
    assert rc == 0
    js = out / "market_analysis_20261105.json"
    assert js.is_file() and (out / "market_analysis_20261105.md").is_file()
    import json
    res = json.loads(js.read_text(encoding="utf-8"))
    assert res["schema"] == "market_analysis/v1"
    assert res["breadth"]["limit_up"] == 1
    assert res["index"][0]["ts_code"] == "000001.SH"


def test_main_rejects_missing_data_root(tmp_path):
    rc = m.main(["--date", "20261105", "--data-root", str(tmp_path / "nope"), "--no-write"])
    assert rc == 2


# ---------------------------------------------------------------------------
# 限期已知降级（2026-11-06）：不是永久免死金牌
# ---------------------------------------------------------------------------
def _entry(since="20261101", until="20261130"):
    return {"reason": "r", "since": since, "until": until}


def test_split_degraded_registered_within_window():
    ok, bad = m.split_degraded(["index_daily"], "20261105",
                               {"index_daily": _entry()})
    assert ok == ["index_daily"] and bad == []


def test_split_degraded_expired_goes_red():
    """过期即重新阻断 —— 逼人回来收口，而不是假装还豁免。"""
    ok, bad = m.split_degraded(["index_daily"], "20261201",
                               {"index_daily": _entry(until="20261130")})
    assert ok == [] and bad == ["index_daily"]


def test_split_degraded_before_since_not_registered():
    ok, bad = m.split_degraded(["index_daily"], "20261031",
                               {"index_daily": _entry(since="20261101")})
    assert ok == [] and bad == ["index_daily"]


def test_split_degraded_without_window_is_invalid():
    """只写表名不给 since/until 的"永久豁免"必须无效。"""
    ok, bad = m.split_degraded(["limit_list_d"], "20261105",
                               {"limit_list_d": {"reason": "没窗口"}})
    assert ok == [] and bad == ["limit_list_d"]


def test_split_degraded_unknown_table_and_broken_file():
    ok, bad = m.split_degraded(["daily_basic"], "20261105", {})
    assert ok == [] and bad == ["daily_basic"]
    # 登记文件不存在 / 损坏 -> 空 dict，绝不放行
    assert m.load_known(Path("does-not-exist.json")) == {}


def test_main_strict_passes_when_all_degraded_registered(tmp_path):
    """11-05 的真实形态：两张表缺、都在登记期内 -> --strict 放行 rc=0。"""
    import json
    root = _write_min_root(tmp_path)
    known = {
        "index_daily": _entry(since="20261105", until="20261113"),
        "limit_list_d": _entry(since="20261104", until="20261113"),
        # 其余可选表在这个最小数据根里也缺，一并登记，才能测到"全登记即放行"
        "daily_basic": _entry(),
        "index_member_all": _entry(),
        "sw_classify_l1": _entry(),
    }
    kp = tmp_path / "known.json"
    kp.write_text(json.dumps({"tables": known}, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    rc = m.main(["--date", "20261105", "--data-root", str(root), "--out-dir", str(out),
                 "--strict", "--known-degraded", str(kp)])
    assert rc == 0
    md = (out / "market_analysis_20261105.md").read_text(encoding="utf-8")
    assert "已登记缺口" in md
    assert "未登记缺口" not in md


def test_repo_known_degraded_file_is_well_formed():
    """仓库里那份登记本身必须合规：每条都有 since/until 且 since <= until。"""
    k = m.load_known(m.KNOWN_DEGRADED)
    assert k, "tools/research/known_degraded.json 缺失或为空"
    for name, e in k.items():
        assert isinstance(e, dict), name
        assert e.get("since") and e.get("until"), "%s 缺窗口" % name
        assert e["since"] <= e["until"], name
        assert e.get("reason"), "%s 缺 reason" % name
