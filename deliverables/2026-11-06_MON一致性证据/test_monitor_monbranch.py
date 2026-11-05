# -*- coding: utf-8 -*-
"""analysis/monitor.py 离线单元测试（工单 K-STOCK-ANALYSIS-MON-20261104）。

隔离原则：
  * 每个用例用 pytest 的 tmp_path 做临时 ANALYSIS_WORK（自动 monkeypatch 环境变量）；
  * 行情/事件/评级全部用合成 DataFrame / 临时 SQLite / 临时 parquet 注入；
  * 不读、不写生产数据目录（C:/项目/analysis_work、生产 curated、生产 analysis.db）。

运行：
    python -X utf8 -m pytest analysis/tests/test_monitor.py -q
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, timedelta

import pandas as pd
import pytest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if WT not in sys.path:
    sys.path.insert(0, WT)

from analysis import monitor  # noqa: E402

CODE_A = "600337.SH"
CODE_B = "605258.SH"
CODE_C = "000001.SZ"
DAILY_COLUMNS = ["ts_code", "trade_date", "close", "pct_chg", "amount"]


# ============================ 测试夹具/工具 ============================

@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """临时数据工作根 + 临时默认观察池文件，隔离一切落盘。"""
    work = tmp_path / "work"
    work.mkdir()
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text(
        "# 测试观察池（含 1 行非法代码，验证宽松读取）\n%s\n%s\n123456.SH\n" % (CODE_A, CODE_B),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANALYSIS_WORK", str(work))
    monkeypatch.setenv("ANALYSIS_WATCHLIST", str(watchlist))
    monkeypatch.delenv("ANALYSIS_DB", raising=False)
    monkeypatch.delenv("ANALYSIS_CURATED", raising=False)
    return work


def daily_frame(rows):
    """根据 (ts_code, trade_date, close, pct_chg, amount) 元组构造行情 DataFrame。"""
    return pd.DataFrame(list(rows), columns=DAILY_COLUMNS)


def rows_series(code, pct_last=0.5, amount_last=100.0, amount_base=100.0,
                pct_base=0.5, days=21, end="2026-11-03", close=10.0):
    """构造以 end 结尾的连续自然日行情（规则只关心序列，不校验交易日历）。"""
    end_date = date.fromisoformat(end)
    rows = []
    for index in range(days):
        day = end_date - timedelta(days=days - 1 - index)
        is_last = index == days - 1
        rows.append((
            code,
            day.strftime("%Y%m%d"),
            close,
            pct_last if is_last else pct_base,
            amount_last if is_last else amount_base,
        ))
    return rows


def provider_of(daily_rows=None, runs=None, events=None, market_latest="", basic=None):
    """构造 data_provider：返回固定数据快照，并校验 spec 契约。"""
    daily = daily_rows if isinstance(daily_rows, pd.DataFrame) else daily_frame(daily_rows or [])

    def _provider(spec):
        assert isinstance(spec, dict)
        assert "codes" in spec and "as_of" in spec
        return {
            "daily": daily.copy(),
            "basic": basic if basic is not None else pd.DataFrame(),
            "runs": list(runs or []),
            "events": list(events or []),
            "market_latest": market_latest,
        }

    return _provider


def sqlite_count(db_path, table):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    finally:
        connection.close()


# ============================ 一、股票池管理 ============================

def test_load_pools_initializes_default_watchlist(sandbox):
    """pools.json 不存在时用默认 watchlist 自动初始化（非法行被忽略）。"""
    pools = monitor.load_pools()
    assert set(pools) == {"watchlist"}
    assert pools["watchlist"] == [CODE_A, CODE_B]
    assert (sandbox / "monitor" / "pools.json").exists()
    assert monitor.list_pools() == {"watchlist": 2}


def test_set_add_remove_and_list_pools(sandbox):
    """set_pool 覆盖 / add_codes 追加 / remove_codes 移除 / list_pools 计数。"""
    assert monitor.set_pool("focus", ["300750.SZ", "688023.SH"]) == ["300750.SZ", "688023.SH"]
    assert monitor.list_pools()["focus"] == 2
    assert monitor.add_codes("focus", ["000001.SZ"]) == ["000001.SZ"]
    assert monitor.add_codes("focus", ["000001.SZ"]) == []  # 已存在，不重复
    assert monitor.remove_codes("focus", ["300750.SZ"]) == ["300750.SZ"]
    pools = monitor.load_pools()
    assert pools["focus"] == ["688023.SH", "000001.SZ"]
    assert monitor.list_pools() == {"watchlist": 2, "focus": 2}


def test_set_pool_accepts_single_string_and_dedupes(sandbox):
    """代码参数兼容单字符串，且重复代码去重保序。"""
    assert monitor.set_pool("focus", "600337.SH") == [CODE_A]
    assert monitor.set_pool("focus", [CODE_A, CODE_A, CODE_C]) == [CODE_A, CODE_C]


@pytest.mark.parametrize("bad_code", ["600337", "600337.sh", "123456.SH", "999999.SH", "SZ000001"])
def test_invalid_codes_rejected_and_pool_unchanged(sandbox, bad_code):
    """非法代码拒绝入库（ValueError），且不产生半写入。"""
    before = monitor.load_pools()
    with pytest.raises(ValueError):
        monitor.add_codes("watchlist", [bad_code])
    assert monitor.load_pools() == before


def test_pools_file_corrupted_fail_visible(sandbox):
    """pools.json 损坏时 fail-visible（MonitorError），不静默清空股票池。"""
    monitor.load_pools()
    pool_path = monitor._pools_path()
    with open(pool_path, "w", encoding="utf-8") as handle:
        handle.write("{不是 JSON")
    with pytest.raises(monitor.MonitorError):
        monitor.load_pools()


def test_remove_from_unknown_pool_raises(sandbox):
    with pytest.raises(ValueError):
        monitor.remove_codes("nope", [CODE_A])


def _concurrent_codes():
    return ["600000.SH", "600036.SH", "601318.SH", "000858.SZ"]


def test_concurrent_add_codes_threads_no_lost_update(sandbox, monkeypatch):
    """4 线程并发 add 不同代码：进程内 RLock 串行化，不得丢更新/写坏 JSON。"""
    monitor.set_pool("focus", [])
    codes = _concurrent_codes()
    orig_read = monitor._read_pools_file

    def _slow_read():
        # 放大 RMW 窗口：无锁实现会在「读完之后、写回之前」被其他线程覆盖
        pools = orig_read()
        time.sleep(0.05)
        return pools

    monkeypatch.setattr(monitor, "_read_pools_file", _slow_read)
    errors = []

    def _worker(code):
        try:
            monitor.add_codes("focus", [code])
        except Exception as exc:  # pragma: no cover - 失败时显式暴露
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(code,)) for code in codes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    pools = monitor.load_pools()
    assert sorted(pools["focus"]) == sorted(codes)
    # 临时文件必须已被 os.replace 消费，不留残骸
    leftovers = list((sandbox / "monitor").glob("pools.json.tmp-*"))
    assert leftovers == []


def test_concurrent_add_codes_processes_no_lost_update(sandbox):
    """4 进程并发 add 不同代码：跨进程文件锁下池内应全部存在（P1-3）。"""
    monitor.set_pool("focus", [])
    codes = _concurrent_codes()
    start_at = time.time() + 3.0  # 留足 4 个解释器冷启动时间，制造真实并发
    script = (
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[3])\n"
        "from analysis import monitor\n"
        "_orig = monitor._read_pools_file\n"
        "def _slow():\n"
        "    data = _orig()\n"
        "    time.sleep(0.05)\n"
        "    return data\n"
        "monitor._read_pools_file = _slow\n"
        "while time.time() < float(sys.argv[2]):\n"
        "    time.sleep(0.01)\n"
        "monitor.add_codes('focus', [sys.argv[1]])\n"
    )
    procs = []
    for code in codes:
        procs.append(subprocess.Popen(
            [sys.executable, "-X", "utf8", "-c", script, code, str(start_at), WT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
    failures = []
    for proc, code in zip(procs, codes):
        out, err = proc.communicate(timeout=120)
        if proc.returncode != 0:
            failures.append((code, proc.returncode, err.decode("utf-8", "replace")))
    assert failures == []
    pools = monitor.load_pools()
    assert sorted(pools["focus"]) == sorted(codes)
    assert list((sandbox / "monitor").glob("pools.json.tmp-*")) == []


def test_module_constants_monkeypatch_isolation(tmp_path, monkeypatch):
    """除环境变量外，直接 monkeypatch 模块常量 DB/POOLS/WATCHLIST 也应隔离（验收测试友好）。"""
    monkeypatch.delenv("ANALYSIS_WORK", raising=False)
    monkeypatch.delenv("ANALYSIS_DB", raising=False)
    watchlist = tmp_path / "wl.txt"
    watchlist.write_text(CODE_A + "\n", encoding="utf-8")
    monkeypatch.setattr(monitor, "MON_DIR", str(tmp_path / "mon"))
    monkeypatch.setattr(monitor, "DB", str(tmp_path / "mon" / "custom.db"))
    monkeypatch.setattr(monitor, "POOLS", str(tmp_path / "mon" / "custom_pools.json"))
    monkeypatch.setattr(monitor, "WATCHLIST", str(watchlist))
    pools = monitor.load_pools()
    assert pools == {"watchlist": [CODE_A]}
    assert (tmp_path / "mon" / "custom_pools.json").exists()
    assert monitor._db_path() == str(tmp_path / "mon" / "custom.db")
    result = monitor.run_monitor(pool="watchlist", as_of="2026-11-03",
                                 data_provider=provider_of(rows_series(CODE_A, pct_last=6.2, days=3)))
    assert result["ok"] is True and result["alerts"] == 1
    assert (tmp_path / "mon" / "custom.db").exists()


# ============================ 二、规则评估 ============================

def test_evaluate_price_move_medium_and_high():
    """price_move：6.2% -> medium；9.6% -> high（两档阈值）。"""
    medium_alerts = monitor.evaluate([CODE_A], as_of="2026-11-03",
                                     data_provider=provider_of(rows_series(CODE_A, pct_last=6.2)))
    move = [item for item in medium_alerts if item["rule"] == "price_move"]
    assert len(move) == 1
    assert move[0]["code"] == CODE_A
    assert move[0]["severity"] == "medium"
    assert move[0]["trade_date"] == "2026-11-03"
    assert move[0]["dedupe_key"] == "%s|price_move|2026-11-03" % CODE_A
    # 未达 5% 阈值不告警
    calm = monitor.evaluate([CODE_A], as_of="2026-11-03",
                            data_provider=provider_of(rows_series(CODE_A, pct_last=4.99)))
    assert not [item for item in calm if item["rule"] == "price_move"]
    high_alerts = monitor.evaluate([CODE_A], as_of="2026-11-03",
                                   data_provider=provider_of(rows_series(CODE_A, pct_last=9.6)))
    assert [item["severity"] for item in high_alerts if item["rule"] == "price_move"] == ["high"]


def test_evaluate_limit_move_up_and_down():
    """limit_move：>=9.5% 触发，标题注明 涨停/跌停。"""
    up = monitor.evaluate([CODE_A], as_of="2026-11-03",
                          data_provider=provider_of(rows_series(CODE_A, pct_last=10.0)))
    up_alerts = [item for item in up if item["rule"] == "limit_move"]
    assert len(up_alerts) == 1
    assert up_alerts[0]["severity"] == "high"
    assert "涨停" in up_alerts[0]["title"]
    down = monitor.evaluate([CODE_A], as_of="2026-11-03",
                            data_provider=provider_of(rows_series(CODE_A, pct_last=-10.2)))
    down_alerts = [item for item in down if item["rule"] == "limit_move"]
    assert len(down_alerts) == 1
    assert "跌停" in down_alerts[0]["title"]


def test_evaluate_volume_spike():
    """volume_spike：当日成交额 / 前 20 日均额 >= 2.5 -> medium。"""
    spike = monitor.evaluate(
        [CODE_A], as_of="2026-11-03",
        data_provider=provider_of(rows_series(CODE_A, pct_last=0.5, amount_last=300.0, amount_base=100.0)))
    volume = [item for item in spike if item["rule"] == "volume_spike"]
    assert len(volume) == 1
    assert volume[0]["severity"] == "medium"
    normal = monitor.evaluate(
        [CODE_A], as_of="2026-11-03",
        data_provider=provider_of(rows_series(CODE_A, pct_last=0.5, amount_last=150.0, amount_base=100.0)))
    assert not [item for item in normal if item["rule"] == "volume_spike"]


def test_evaluate_new_event_risk_takes_high_and_single_alert():
    """new_event：风险类 -> high；同一代码只出一条，detail 最多列 3 条标题。"""
    events = [
        {"code": CODE_A, "date": "2026-11-02", "category": "退市/ST", "category_final": "",
         "reviewed": 1, "title": "关于公司股票被实施退市风险警示的公告"},
        {"code": CODE_A, "date": "2026-11-01", "category": "业绩", "category_final": "业绩",
         "reviewed": 0, "title": "2026年第三季度报告"},
        {"code": CODE_B, "date": "2026-11-02", "category": "业绩", "category_final": "业绩",
         "reviewed": 0, "title": "关于股份回购进展的公告"},
    ]
    alerts = monitor.evaluate(
        [CODE_A, CODE_B], as_of="2026-11-04",
        data_provider=provider_of(rows_series(CODE_A, days=3) + rows_series(CODE_B, days=3),
                                  events=events, market_latest="2026-11-04"))
    event_alerts = {item["code"]: item for item in alerts if item["rule"] == "new_event"}
    assert set(event_alerts) == {CODE_A, CODE_B}  # 每代码只出一条
    assert event_alerts[CODE_A]["severity"] == "high"
    assert event_alerts[CODE_A]["trade_date"] == "2026-11-02"
    assert "退市风险警示" in event_alerts[CODE_A]["detail"]
    assert event_alerts[CODE_B]["severity"] == "medium"


def test_evaluate_new_event_window_3_natural_days():
    """new_event 窗口：as_of-3 天内包含边界日，as_of-4 天排除。"""
    events = [
        {"code": CODE_A, "date": "2026-10-31", "category": "停牌", "title": "关于停牌的公告"},
        {"code": CODE_B, "date": "2026-11-01", "category": "停牌", "title": "关于停牌的公告"},
    ]
    alerts = monitor.evaluate(
        [CODE_A, CODE_B], as_of="2026-11-04",
        data_provider=provider_of(rows_series(CODE_A, days=3) + rows_series(CODE_B, days=3),
                                  events=events, market_latest="2026-11-04"))
    codes = {item["code"] for item in alerts if item["rule"] == "new_event"}
    assert codes == {CODE_B}  # 10-31 是 as_of-4，已出窗口


def test_evaluate_rating_change():
    """rating_change：最近两次 run 评级不同 -> medium，标题「评级变化: A→B」。"""
    runs_early = [
        {"code": CODE_A, "as_of": "2026-10-30", "started": "2026-10-30 16:00:00",
         "status": "ok", "rating": "观察"},
        {"code": CODE_A, "as_of": "2026-10-31", "started": "2026-10-31 16:00:00",
         "status": "ok", "rating": "观察偏多"},
        {"code": CODE_A, "as_of": "2026-11-02", "started": "2026-11-02 16:00:00",
         "status": "ok", "rating": "事件观察"},
    ]
    alerts = monitor.evaluate([CODE_A], as_of="2026-11-03",
                              data_provider=provider_of(rows_series(CODE_A, days=3),
                                                        runs=runs_early, market_latest="2026-11-03"))
    rating = [item for item in alerts if item["rule"] == "rating_change"]
    assert len(rating) == 1
    assert rating[0]["severity"] == "medium"
    assert rating[0]["title"] == "评级变化: 观察偏多→事件观察"
    assert rating[0]["trade_date"] == "2026-11-02"
    # 最近两次评级相同 -> 不告警
    runs_same = [
        {"code": CODE_A, "as_of": "2026-11-01", "status": "ok", "rating": "观察"},
        {"code": CODE_A, "as_of": "2026-11-02", "status": "ok", "rating": "观察"},
    ]
    calm = monitor.evaluate([CODE_A], as_of="2026-11-03",
                            data_provider=provider_of(rows_series(CODE_A, days=3),
                                                      runs=runs_same, market_latest="2026-11-03"))
    assert not [item for item in calm if item["rule"] == "rating_change"]


def test_evaluate_data_stale():
    """data_stale：个股最新行情 < 市场最新交易日 -> low，标题「数据滞后」。"""
    daily = daily_frame(rows_series(CODE_A, days=3, end="2026-10-30")
                        + rows_series(CODE_B, days=3, end="2026-11-03"))
    alerts = monitor.evaluate([CODE_A, CODE_B, CODE_C], as_of="2026-11-03",
                              data_provider=provider_of(daily, market_latest="2026-11-03"))
    stale = {item["code"]: item for item in alerts if item["rule"] == "data_stale"}
    assert set(stale) == {CODE_A, CODE_C}  # C 无行情也算滞后（detail 注明无数据）
    assert stale[CODE_A]["severity"] == "low"
    assert stale[CODE_A]["title"] == "数据滞后"
    assert "2026-10-30" in stale[CODE_A]["detail"]
    assert "无行情数据" in stale[CODE_C]["detail"]


def test_evaluate_pit_truncates_future_market_data():
    """PIT：as_of 之后的行情不参与评估（即使之后有涨停）。"""
    daily = daily_frame([
        (CODE_A, "20261101", 10.0, 0.5, 100.0),
        (CODE_A, "20261102", 10.0, 0.5, 100.0),
        (CODE_A, "20261103", 10.0, 10.0, 100.0),
    ])
    before = monitor.evaluate([CODE_A], as_of="2026-11-02", rules=["price_move"],
                              data_provider=provider_of(daily, market_latest="2026-11-02"))
    assert before == []  # 11-03 的涨停尚未发生
    after = monitor.evaluate([CODE_A], as_of="2026-11-03", rules=["price_move"],
                             data_provider=provider_of(daily, market_latest="2026-11-03"))
    assert len(after) == 1
    assert after[0]["trade_date"] == "2026-11-03"


def test_evaluate_accepts_iso_trade_date():
    """daily.trade_date 兼容 ISO 与紧凑两种格式，告警统一输出 ISO。"""
    daily = daily_frame([(CODE_A, "2026-11-03", 10.0, 6.2, 100.0)])
    alerts = monitor.evaluate([CODE_A], as_of="2026-11-04", rules=["price_move"],
                              data_provider=provider_of(daily))
    assert alerts[0]["trade_date"] == "2026-11-03"
    assert alerts[0]["dedupe_key"] == "%s|price_move|2026-11-03" % CODE_A


def test_evaluate_rules_filter_and_unknown_rule():
    """rules 过滤：只跑指定规则；未知规则 fail-visible。"""
    daily = daily_frame(rows_series(CODE_A, pct_last=6.2, days=3, end="2026-11-02"))
    only_move = monitor.evaluate([CODE_A], as_of="2026-11-03", rules=["price_move"],
                                 data_provider=provider_of(daily, market_latest="2026-11-03"))
    assert {item["rule"] for item in only_move} == {"price_move"}
    only_stale = monitor.evaluate([CODE_A], as_of="2026-11-03", rules="data_stale",
                                  data_provider=provider_of(daily, market_latest="2026-11-03"))
    assert {item["rule"] for item in only_stale} == {"data_stale"}
    with pytest.raises(ValueError):
        monitor.evaluate([CODE_A], rules=["bogus_rule"], data_provider=provider_of(daily))


def test_evaluate_deterministic_dedupe_keys():
    """同一数据重复评估得到相同 dedupe_key（去重语义稳定的前提）。"""
    provider = provider_of(rows_series(CODE_A, pct_last=6.2, days=3))
    first = monitor.evaluate([CODE_A], as_of="2026-11-03", data_provider=provider)
    second = monitor.evaluate([CODE_A], as_of="2026-11-03", data_provider=provider)
    assert [item["dedupe_key"] for item in first] == [item["dedupe_key"] for item in second]
    assert len({item["dedupe_key"] for item in first}) == len(first)


# ============================ 三、run_monitor 持久化与去重 ============================

def test_run_monitor_dry_run_does_not_persist(sandbox, monkeypatch):
    """dry_run 只评估不落库：不建 DB、不建告警，new_alerts 为预演新增数。"""
    monitor.set_pool("watchlist", [CODE_A])
    monkeypatch.setattr(monitor, "_default_provider",
                        provider_of(rows_series(CODE_A, pct_last=6.2, days=3)))
    result = monitor.run_monitor(pool="watchlist", as_of="2026-11-03", dry_run=True)
    assert result["ok"] is True
    assert result["run_id"] is None
    assert result["scanned"] == 1
    assert result["alerts"] == 1
    assert result["new_alerts"] == 1
    assert result["by_severity"] == {"high": 0, "medium": 1, "low": 0}
    assert not (sandbox / "monitor" / "monitor.db").exists()
    assert monitor.list_alerts() == []


def test_run_monitor_persists_then_dedupes(sandbox, monkeypatch):
    """正常落库；同一天同规则二次运行 new_alerts=0（dedupe_key 主键去重）。"""
    monitor.set_pool("watchlist", [CODE_A])
    monkeypatch.setattr(monitor, "_default_provider",
                        provider_of(rows_series(CODE_A, pct_last=6.2, days=3)))
    first = monitor.run_monitor(pool="watchlist", as_of="2026-11-03")
    assert first["ok"] is True
    assert first["alerts"] == 1 and first["new_alerts"] == 1
    assert first["run_id"]
    db_path = sandbox / "monitor" / "monitor.db"
    assert db_path.exists()
    assert sqlite_count(db_path, "monitor_alerts") == 1
    second = monitor.run_monitor(pool="watchlist", as_of="2026-11-03")
    assert second["ok"] is True
    assert second["alerts"] == 1 and second["new_alerts"] == 0
    assert sqlite_count(db_path, "monitor_alerts") == 1
    assert sqlite_count(db_path, "monitor_runs") == 2
    # 告警查询
    alerts = monitor.list_alerts(limit=10)
    assert len(alerts) == 1
    assert alerts[0]["code"] == CODE_A
    assert alerts[0]["rule"] == "price_move"
    assert alerts[0]["dedupe_key"] == "%s|price_move|2026-11-03" % CODE_A
    # 总览
    overview = monitor.summary()
    assert overview["pools"] == {"watchlist": 1}
    assert overview["alerts_total"] == 1
    assert overview["by_severity"] == {"high": 0, "medium": 1, "low": 0}
    assert overview["last_run"]["new_alerts"] == 0
    assert overview["last_run"]["alerts"] == 1


def test_run_monitor_unknown_pool_fail_visible(sandbox):
    result = monitor.run_monitor(pool="nope", as_of="2026-11-03")
    assert result["ok"] is False
    assert "股票池不存在" in result["error"]


def test_run_monitor_provider_error_fail_visible(sandbox, monkeypatch):
    """provider 异常 fail-visible 且不落库。"""
    monitor.set_pool("watchlist", [CODE_A])

    def _boom(spec):
        raise RuntimeError("provider 挂了")

    monkeypatch.setattr(monitor, "_default_provider", _boom)
    result = monitor.run_monitor(pool="watchlist", as_of="2026-11-03")
    assert result["ok"] is False
    assert "provider 挂了" in result["error"]
    assert not (sandbox / "monitor" / "monitor.db").exists()


def test_run_monitor_missing_curated_data_fail_visible(sandbox, monkeypatch):
    """默认数据源缺少 curated parquet 时 fail-visible。"""
    monitor.set_pool("watchlist", [CODE_A])
    empty_curated = sandbox / "empty_curated"
    empty_curated.mkdir()
    monkeypatch.setenv("ANALYSIS_CURATED", str(empty_curated))
    result = monitor.run_monitor(pool="watchlist", as_of="2026-11-03")
    assert result["ok"] is False
    assert "行情数据不存在" in result["error"]


# ============================ 四、list_alerts / summary ============================

def test_list_alerts_filters(sandbox, monkeypatch):
    """list_alerts：按池/严重级别/limit 过滤，ts 倒序。"""
    monitor.set_pool("pool_a", [CODE_A])
    monitor.set_pool("pool_b", [CODE_B])
    provider = provider_of(rows_series(CODE_A, pct_last=6.2, days=3)
                           + rows_series(CODE_B, pct_last=6.2, days=3))
    monkeypatch.setattr(monitor, "_default_provider", provider)
    assert monitor.run_monitor(pool="pool_a", as_of="2026-11-03")["ok"] is True
    assert monitor.run_monitor(pool="pool_b", as_of="2026-11-03")["ok"] is True
    everything = monitor.list_alerts()
    assert len(everything) == 2
    assert everything[0]["code"] == CODE_B  # 后写入的 ts 更大/rowid 更大，倒序在前
    pool_a_alerts = monitor.list_alerts(pool="pool_a")
    assert len(pool_a_alerts) == 1 and pool_a_alerts[0]["code"] == CODE_A
    assert len(monitor.list_alerts(severity="medium")) == 2
    assert monitor.list_alerts(severity="high") == []
    assert len(monitor.list_alerts(limit=1)) == 1
    assert monitor.list_alerts(pool="不存在的池") == []
    with pytest.raises(ValueError):
        monitor.list_alerts(severity="critical")


def test_summary_empty_state(sandbox):
    """空库 summary：计数为 0、三级别齐全、last_run 为 None。"""
    overview = monitor.summary()
    assert overview == {
        "pools": {"watchlist": 2},
        "alerts_total": 0,
        "by_severity": {"high": 0, "medium": 0, "low": 0},
        "last_run": None,
    }


# ============================ 五、默认数据源（临时 parquet + 临时 SQLite） ============================

def _write_curated_fixture(curated_dir):
    """在临时目录造一份最小 curated daily/daily_basic parquet（生产同构紧凑日期）。"""
    curated_dir.mkdir(parents=True, exist_ok=True)
    daily_rows = [
        (CODE_A, "20260801", 9.0, 1.0, 100.0),   # 60 天窗口外，应被剔除
        (CODE_A, "20261028", 10.0, 0.5, 100.0),
        (CODE_A, "20261029", 10.0, 0.5, 100.0),
        (CODE_A, "20261030", 10.0, 0.5, 100.0),
        (CODE_A, "20261031", 10.0, 0.5, 100.0),
        (CODE_A, "20261101", 10.0, 0.5, 100.0),
        (CODE_A, "20261102", 10.0, 5.5, 100.0),
        (CODE_A, "20261103", 10.5, 6.2, 300.0),
    ]
    daily_frame(daily_rows).to_parquet(curated_dir / "daily.parquet", index=False)
    pd.DataFrame([(CODE_A, "20261103", 10.5)],
                 columns=["ts_code", "trade_date", "close"]).to_parquet(
        curated_dir / "daily_basic.parquet", index=False)


def _write_analysis_db_fixture(db_path, with_category_final=True):
    """造一份 runs + events 的最小分析库（生产列结构）。"""
    connection = sqlite3.connect(db_path)
    events_ddl = ("CREATE TABLE events (event_id TEXT PRIMARY KEY, code TEXT, date TEXT,"
                  " category TEXT, title TEXT, reviewed INTEGER DEFAULT 0%s);"
                  % (", category_final TEXT" if with_category_final else ""))
    try:
        connection.executescript(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, code TEXT, as_of TEXT, started TEXT,"
            " status TEXT, rating TEXT);" + events_ddl)
        connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                           ("r1", CODE_A, "2026-10-30", "2026-10-30 16:00:00", "ok", "观察"))
        connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                           ("r2", CODE_A, "2026-11-02", "2026-11-02 16:00:00", "ok", "事件观察"))
        if with_category_final:
            connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                               ("e1", CODE_A, "2026-11-02", "停牌/控制权",
                                "关于控股股东签署控制权转让协议的公告", 1, "停牌/控制权"))
        else:
            connection.execute(
                "INSERT INTO events (event_id, code, date, category, title, reviewed)"
                " VALUES (?,?,?,?,?,?)",
                ("e1", CODE_A, "2026-11-02", "停牌/控制权",
                 "关于控股股东签署控制权转让协议的公告", 1))
        connection.commit()
    finally:
        connection.close()


def test_default_provider_reads_curated_and_analysis_db(sandbox, monkeypatch):
    """默认数据源：临时 parquet + 临时分析库，端到端 run_monitor 无需注入 provider。"""
    curated = sandbox / "curated"
    _write_curated_fixture(curated)
    monkeypatch.setenv("ANALYSIS_CURATED", str(curated))
    _write_analysis_db_fixture(sandbox / "analysis.db")
    data = monitor._default_provider({"codes": [CODE_A], "as_of": "2026-11-03"})
    assert set(data) == {"daily", "basic", "runs", "events", "market_latest"}
    assert data["market_latest"] == "2026-11-03"
    assert set(data["daily"]["ts_code"]) == {CODE_A}
    assert "20260801" not in set(data["daily"]["trade_date"].astype(str))  # 窗口外被剔除
    assert len(data["runs"]) == 2
    assert len(data["events"]) == 1
    # 端到端：不注入 provider，走默认数据源
    monitor.set_pool("watchlist", [CODE_A])
    result = monitor.run_monitor(pool="watchlist", as_of="2026-11-03",
                                 rules=["price_move", "new_event"])
    assert result["ok"] is True
    assert result["alerts"] == 2
    assert result["by_severity"]["high"] == 1   # 控制权事件风险类
    assert result["by_severity"]["medium"] == 1  # 单日 +6.2%
    rules = {item["rule"] for item in result["items"]}
    assert rules == {"price_move", "new_event"}


def test_default_provider_market_latest_truncated_by_as_of(sandbox, monkeypatch):
    """P1-2：回看 as_of 时市场最新交易日不得取到未来行情（否则全池误判 data_stale）。"""
    curated = sandbox / "curated"
    _write_curated_fixture(curated)
    monkeypatch.setenv("ANALYSIS_CURATED", str(curated))
    _write_analysis_db_fixture(sandbox / "analysis.db")
    data = monitor._default_provider({"codes": [CODE_A], "as_of": "2026-10-30"})
    assert data["market_latest"] == "2026-10-30"
    assert max(data["daily"]["trade_date"].astype(str)) <= "20261030"
    # 11-03 才出现的 +6.2% 不得进入 10-30 的评估
    monitor.set_pool("watchlist", [CODE_A])
    result = monitor.run_monitor(pool="watchlist", as_of="2026-10-30", rules=["price_move"])
    assert result["items"] == []


def test_default_provider_runs_filtered_by_as_of(sandbox, monkeypatch):
    """P1-2：runs 读取按 as_of 截断，评级变化不读未来 run（含 SQL 层过滤）。"""
    curated = sandbox / "curated"
    _write_curated_fixture(curated)
    monkeypatch.setenv("ANALYSIS_CURATED", str(curated))
    _write_analysis_db_fixture(sandbox / "analysis.db")
    runs = monitor._read_runs([CODE_A], "2026-10-30")
    assert [row["as_of"] for row in runs] == ["2026-10-30"]
    data = monitor._default_provider({"codes": [CODE_A], "as_of": "2026-10-30"})
    assert len(data["runs"]) == 1
    assert monitor.evaluate([CODE_A], as_of="2026-10-30", rules=["rating_change"],
                            data_provider=monitor._default_provider) == []


def test_evaluate_ignores_future_runs_from_injected_provider():
    """P1-2：注入数据也不得让评级读未来 run（evaluate 层再截断一次）。"""
    runs = [
        {"code": CODE_A, "as_of": "2026-10-30", "status": "ok", "rating": "观察"},
        {"code": CODE_A, "as_of": "2026-11-05", "status": "ok", "rating": "事件观察"},
    ]
    alerts = monitor.evaluate(
        [CODE_A], as_of="2026-10-31", rules=["rating_change"],
        data_provider=provider_of(rows_series(CODE_A, days=3, end="2026-10-31"), runs=runs))
    assert [item for item in alerts if item["rule"] == "rating_change"] == []


def test_evaluate_clamps_future_market_latest_and_dedupe_key():
    """P1-2：告警 trade_date 夹到 as_of，去重键不得含未来日期。"""
    alerts = monitor.evaluate(
        [CODE_A], as_of="2026-10-30", rules=["data_stale"],
        data_provider=provider_of(rows_series(CODE_A, days=3, end="2026-10-28"),
                                  market_latest="2026-11-03"))
    assert len(alerts) == 1
    assert alerts[0]["trade_date"] == "2026-10-30"
    assert alerts[0]["dedupe_key"] == "%s|data_stale|2026-10-30" % CODE_A
    assert "2026-10-30" in alerts[0]["detail"]


def test_read_events_legacy_schema_without_category_final(sandbox, monkeypatch):
    """旧 events 表无 category_final 列时降级读取，不抛异常。"""
    db_path = sandbox / "legacy.db"
    _write_analysis_db_fixture(db_path, with_category_final=False)
    monkeypatch.setenv("ANALYSIS_DB", str(db_path))
    events = monitor._read_events([CODE_A], "2026-11-03")
    assert len(events) == 1
    assert events[0]["category"] == "停牌/控制权"
    assert events[0]["category_final"] == ""


# ============================ 六、CLI ============================

def test_cli_pools_add_remove_and_invalid_code(sandbox, capsys):
    """CLI --pools/--add/--remove 输出 JSON；非法代码 rc=1。"""
    rc = monitor.main(["--pools"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["pools"] == {"watchlist": 2}
    assert out["detail"]["watchlist"] == [CODE_A, CODE_B]

    rc = monitor.main(["--add", "300750.SZ", "--pool", "focus"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == ["300750.SZ"]
    assert out["pools"]["focus"] == 1

    rc = monitor.main(["--remove", "300750.SZ", "--pool", "focus"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["removed"] == ["300750.SZ"]

    rc = monitor.main(["--add", "BADCODE"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "非法股票代码" in out["error"]


def test_cli_run_and_list_alerts(sandbox, monkeypatch, capsys):
    """CLI --run 落库、--list-alerts 查询、--dry-run 预演新增数。"""
    monitor.set_pool("watchlist", [CODE_A])
    monkeypatch.setattr(monitor, "_default_provider",
                        provider_of(rows_series(CODE_A, pct_last=6.2, days=3)))
    rc = monitor.main(["--run", "--pool", "watchlist", "--as-of", "2026-11-03"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["alerts"] == 1 and out["new_alerts"] == 1

    rc = monitor.main(["--list-alerts", "--limit", "5"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
    assert out["items"][0]["rule"] == "price_move"

    rc = monitor.main(["--run", "--pool", "watchlist", "--as-of", "2026-11-03", "--dry-run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["new_alerts"] == 0  # 已入库，预演新增为 0


def test_cli_no_action_returns_rc1(capsys):
    rc = monitor.main([])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
