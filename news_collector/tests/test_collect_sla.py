# -*- coding: utf-8 -*-
"""collect_sla 验收测试（研发方案 R-04 验收判据的可执行版）。

锁死四条语义，防止「报表看着绿、其实判错」：
  1) 新鲜 → GREEN；滞后过 AMBER 线 → AMBER；过 RED 线 → RED，且 main() 退出码 1；
  2) 覆盖分母按采集器真实范围：actionable（不是 all）、上证 e 互动只算沪市 60/68；
  3) 周末放宽：过去 3 天每含一个周末日 +24h，周末/周一不必然误报；
  4) 跨轮记忆：consecutive_bad ≥ 阈值时即使滞后未到 RED 线也提级 RED。

做法：全部用 tmp 目录 + `os.utime` 造 mtime，时间一律相对 `now` 计算，
**不写任何日期字面量**（判例：日期/编号字面量须码点构造，本测试用相对量绕开）。
不触网、不写仓库内文件。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "news_collector") not in sys.path:
    sys.path.insert(0, str(REPO / "news_collector"))

import collect_sla as cs  # noqa: E402

NOW = datetime.now().replace(microsecond=0)


def _mk(root, rel):
    p = root / rel
    p.mkdir(parents=True, exist_ok=True)
    return p


def _touch_dir(path, codes, hours_ago, size=10):
    path.mkdir(parents=True, exist_ok=True)
    ts = (NOW - timedelta(hours=hours_ago)).timestamp()
    for c in codes:
        fp = path / ("%s.jsonl" % c)
        fp.write_text('{"id":"x"}\n', encoding="utf-8")
        os.utime(fp, (ts, ts))


def _mk_news_db(news_dir, hours_ago):
    fp = news_dir / "news.db"
    con = sqlite3.connect(str(fp))
    try:
        con.execute("create table news (id text, ts text, title text)")
        ts = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        con.execute("insert into news values (?,?,?)", ("1", ts, "t"))
        con.commit()
    finally:
        con.close()
    return fp


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """造一个最小 aios 结构，并把模块级 HERE/ROOT 指过去。"""
    root = tmp_path
    news = _mk(root, "news_collector")
    data = _mk(news, "data")
    _mk(root, "data")
    _mk(root, "quant_backtest/data")

    codes = ["600000", "600001", "000001", "300001", "920001"]
    queue = {
        "actionable": [{"code": "%s.SH" % c if c[0] == "6" else "%s.SZ" % c} for c in codes],
        "all": [{"code": "%s.SH" % c} for c in codes + ["600002", "600003"]],
    }
    (news / "target_queue_chanlun.json").write_text(
        json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    (news / "selection_universe.json").write_text(
        json.dumps({"sources": {"chanlun": {"file": "target_queue_chanlun.json",
                                            "scope": "all", "xq_include": True}}},
                   ensure_ascii=False), encoding="utf-8")

    for rel in ("data/irm", "data/sse", "data/taoguba", "quant_backtest/data/guba"):
        _touch_dir(root / rel, codes, hours_ago=1)
    _touch_dir(news / "data/xq_timeline", codes, hours_ago=1)
    _mk_news_db(news, hours_ago=1)
    (data / "qianping").mkdir(parents=True, exist_ok=True)
    (data / "qianping" / "latest.json").write_text(json.dumps({
        "generated_at": (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "trade_day": "x", "n_queue": len(codes)}), encoding="utf-8")

    monkeypatch.setattr(cs, "HERE", str(news))
    monkeypatch.setattr(cs, "ROOT", str(root))
    monkeypatch.setattr(cs, "OUT_F", str(data / "collect_sla.json"))
    monkeypatch.setattr(cs, "STATE_F", str(data / "collect_sla_state.json"))
    return root


def _row(rep, sid):
    return next(r for r in rep["rows"] if r["source"] == sid)


def test_fresh_all_green(sandbox):
    rep = cs.build_report(now=NOW, state={})
    assert rep["summary"]["n_red"] == 0
    assert rep["summary"]["verdict"] == "GREEN"
    assert all(r["state"] == cs.STATE_GREEN for r in rep["rows"]), \
        [(r["source"], r["state"], r["note"]) for r in rep["rows"]]


def test_stale_thresholds_and_exit_code(sandbox):
    codes = ["600000", "600001", "000001", "300001", "920001"]
    _touch_dir(sandbox / "data/taoguba", codes, hours_ago=37)   # AMBER 区
    _touch_dir(sandbox / "data/irm", codes, hours_ago=60)       # RED 区
    rep = cs.build_report(now=NOW, state={})
    assert _row(rep, "taoguba")["state"] == cs.STATE_AMBER
    assert _row(rep, "irm")["state"] == cs.STATE_RED
    assert rep["summary"]["n_red"] == 1
    assert rep["summary"]["verdict"] == "RED"
    assert cs.main(["--no-state", "--no-write", "--now",
                    NOW.strftime("%Y-%m-%dT%H:%M:%S")]) == 1


def test_coverage_denominator_actionable_and_sh_only(sandbox):
    rep = cs.build_report(now=NOW, state={})
    irm = _row(rep, "irm")
    # 分母=actionable(5)，不是 all(7)
    assert irm["expected"] == 5 and irm["covered"] == 5 and irm["gap"] == 0
    assert irm["expected_all"] == 7
    sse = _row(rep, "sse")
    # 上证 e 互动只算沪市：actionable 里 60x 两只
    assert sse["expected"] == 2, sse["expected"]
    assert "仅沪市" in sse["expected_from"]


def test_weekend_slack_prevents_false_red(sandbox):
    # 取一个周一（相对 now 回退，不用日期字面量）
    monday = NOW - timedelta(days=(NOW.weekday() - 0) % 7)
    monday = monday.replace(hour=12, minute=0, second=0)
    codes = ["600000", "600001", "000001", "300001", "920001"]
    _touch_dir(sandbox / "data/irm", codes, hours_ago=60)  # 相对 monday 前 60h
    ts = (monday - timedelta(hours=60)).timestamp()
    for fp in (sandbox / "data/irm").glob("*.jsonl"):
        os.utime(fp, (ts, ts))
    rep = cs.build_report(now=monday, state={})
    # 周一 slack = 周日 +24 + 周六 +24 = 48h；60h < 48+26 → 不得判 RED
    assert rep["weekend_slack_hours"] == 48.0, rep["weekend_slack_hours"]
    assert _row(rep, "irm")["state"] != cs.STATE_RED


def test_consecutive_bad_escalates_to_red(sandbox):
    codes = ["600000", "600001", "000001", "300001", "920001"]
    _touch_dir(sandbox / "data/qianping", [], hours_ago=0)
    (sandbox / "news_collector/data/qianping/latest.json").write_text(json.dumps({
        "generated_at": (NOW - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"),
        "trade_day": "x", "n_queue": 5}), encoding="utf-8")
    rep = cs.build_report(now=NOW, state={"qianping": {"state": "AMBER", "consecutive_bad": 3}})
    q = _row(rep, "qianping")
    assert q["state"] == cs.STATE_RED, q
    assert q["consecutive_bad"] == 4
    assert "连续" in q["note"]
    _ = codes


def test_missing_dir_is_nodata_not_crash(sandbox):
    import shutil
    shutil.rmtree(sandbox / "data/taoguba")
    rep = cs.build_report(now=NOW, state={})
    assert _row(rep, "taoguba")["state"] == cs.STATE_NODATA
    assert _row(rep, "taoguba")["last_ok_ts"] is None


def test_render_md_and_table_smoke(sandbox):
    rep = cs.build_report(now=NOW, state={})
    assert "| 源 | 判定 |" in cs.render_md(rep)
    assert "判定=" in cs.render_table(rep)
