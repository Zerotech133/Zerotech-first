# -*- coding: utf-8 -*-
"""pool_watch 单测（WO-QT-20261020-070）：行情/时间/推送全 mock，零网络零推送。

  1. 监控集：T3/无买入 tag/回避 tag 不入；T2+「1买」入
  2. entry 选取 tie-break：dt 最近优先；dt 平手取列表靠后者
  3. 触发判定：PULL ≤ -1% / BREAK ≥ +3% / 区间内不触发
  4. 择势门：reg=down 的 break → reg_flag_only 落池不推送
  5. 冷却：同票同方向 4h 内不复发；超窗 r2；异方向互不干扰
  6. dry：零落池、零 state、零推送
  7. 时段门控：非 CN 盘中 → skipped_gate，零抓取
  8. 落池事件过 normalize_signal 契约（WO-005）

state/池/日志全落 tmp_path（test_guard_alert.py autouse 判例），不碰生产 data/。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "k_stock" / "paper"))
import pool_watch as pw  # noqa: E402
import signal_pool as sp  # noqa: E402
import signal_consumer as sc  # noqa: E402

CST = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 21, 10, 30, 0, tzinfo=CST)  # 周三盘中


@pytest.fixture(autouse=True)
def log_fp(tmp_path, monkeypatch):
    """隔离 pw.LOG_PATH→tmp_path（test_guard_alert.py 判例：测试不得污染
    生产 quant_backtest/logs/ 取证面）。"""
    fp = str(tmp_path / "pool_watch.log")
    monkeypatch.setattr(pw, "LOG_PATH", fp)
    return fp


@pytest.fixture
def env(tmp_path):
    """tmp 池文件 + tmp state + tmp jsonl + push 调用记录，返回 dict。"""
    return {"pool": str(tmp_path / "todays_picks.json"),
            "state": str(tmp_path / "pool_watch_state.json"),
            "jsonl": str(tmp_path / "signal_pool.jsonl"),
            "push": []}


def write_pool(env, stocks):
    Path(env["pool"]).write_text(json.dumps(
        {"scan_run_date": "2026-10-19",
         "generated_at": "2026-10-20 15:40:09",
         "stocks": stocks}, ensure_ascii=False), encoding="utf-8")


def pick_stock(code="002007.SZ", name="华兰生物", tier="T2", entry_px=13.07,
               reg="down", dt="2026-09-16", tag="1买", n_hit=1):
    return {"code": code, "name": name, "tier": tier, "close": entry_px,
            "n_hit": n_hit,
            "sources": [{"s": "chanlun", "tag": tag, "dt": dt,
                         "entry_px": entry_px, "reg": reg}]}


def fake_fetch(prices_by_code):
    """prices_by_code: {池代码: 现价}；返回 sina 码格式行情（同真实接口）。"""
    def _f(codes):
        out = []
        for c in codes:
            for pc, p in prices_by_code.items():
                if pw.to_sina(pc) == c and p is not None:
                    out.append({"code": c, "price": p})
        return out
    return _f


def run(env, prices, now=T0, send=False, dry=False, gate=True):
    return pw.run_once(fake_fetch(prices), now, pool_path=env["pool"],
                       state_path=env["state"], pool_jsonl=env["jsonl"],
                       send=send, dry=dry,
                       push=lambda t: (env["push"].append(t), True)[1],
                       gate=gate)


# —— 监控集与 entry 选取 ——


def test_watchlist_filters_tier_and_buy_tag(env):
    write_pool(env, [
        pick_stock(),                                          # T2+1买 → 入
        pick_stock(code="000001.SZ", name="平安银行", tier="T3"),  # T3 → 排除
        pick_stock(code="000002.SZ", name="万科A", tag="2卖"),     # 卖 → 排除
        pick_stock(code="000003.SZ", name="×", tag="买入回避"),    # 回避 → 排除
        {"code": "000004.SZ", "name": "无买点", "tier": "T2",
         "sources": [{"tag": "2卖", "dt": "2026-09-16", "entry_px": 5.0}]},
    ])
    picks = pw.load_watchlist(env["pool"])
    assert [p["code"] for p in picks] == ["002007.SZ"]
    assert picks[0]["entry_px"] == 13.07 and picks[0]["reg"] == "down"


def test_entry_tiebreak_latest_dt_then_list_order(env):
    srcs = [
        {"tag": "1买", "dt": "2026-09-10", "entry_px": 10.0, "reg": "up"},
        {"tag": "2买", "dt": "2026-09-16", "entry_px": 12.0, "reg": "down"},
        {"tag": "3买", "dt": "2026-09-16", "entry_px": 12.5, "reg": "flat"},
        {"tag": "1卖", "dt": "2026-09-20", "entry_px": 9.0, "reg": "up"},  # 非买入不参选
    ]
    assert pw.pick_entry(srcs) == (12.5, "flat", "2026-09-16")


def test_evaluate_thresholds():
    assert pw.evaluate(12.9, 13.07) == "pull"      # ≤ -1%（12.9393）
    assert pw.evaluate(13.0, 13.07) is None        # 区间内
    assert pw.evaluate(13.48, 13.07) == "break"    # ≥ +3%（13.4621）
    assert pw.evaluate(13.07, 13.07) is None       # 平价不触发


# —— 触发/落池/推送 ——


def test_pull_trigger_appends_and_no_push_by_default(env):
    write_pool(env, [pick_stock()])
    rep = run(env, {"002007.SZ": 12.8})  # -2.1% 回踩
    assert rep["n_trigger"] == 1 and rep["appended"] == 1
    assert env["push"] == []  # 默认 dry：落池不推送
    evs = sp.read_events(path=env["jsonl"])
    assert len(evs) == 1
    sig = sc.normalize_signal(evs[0])  # 过 WO-005 契约
    assert sig["rule_id"] == "pool_watch_pull" and sig["action"] == "BUY"
    assert sig["evidence"]["trigger_price"] == 12.8
    assert sig["pool_stamp"]["pool_id"] == "todays_picks"
    st = json.load(open(env["state"], encoding="utf-8"))
    assert "pull" in st["002007.SZ"]


def test_break_reg_down_degrades_flag_only(env):
    write_pool(env, [pick_stock(reg="down")])
    rep = run(env, {"002007.SZ": 13.5}, send=True)  # +3.3% 突破
    assert rep["n_trigger"] == 1
    assert rep["triggers"][0]["reg_flag_only"] is True
    assert env["push"] == []  # 降级：落池但绝不提醒
    evs = sp.read_events(path=env["jsonl"])
    assert evs[0]["evidence"]["reg_flag_only"] is True


def test_break_reg_flat_pushes_when_send(env):
    write_pool(env, [pick_stock(reg="flat")])
    rep = run(env, {"002007.SZ": 13.5}, send=True)
    assert rep["n_trigger"] == 1 and len(env["push"]) == 1
    assert "002007.SZ" in env["push"][0] and "突破" in env["push"][0]
    st = json.load(open(env["state"], encoding="utf-8"))
    assert "break" in st["002007.SZ"]


# —— 冷却 ——


def test_cooldown_same_direction_4h(env):
    write_pool(env, [pick_stock()])
    r1 = run(env, {"002007.SZ": 12.8})
    r2 = run(env, {"002007.SZ": 12.7},
             now=T0 + timedelta(hours=3))  # 冷却内不复发
    r3 = run(env, {"002007.SZ": 12.6},
             now=T0 + timedelta(hours=4, minutes=1))  # 超窗复发
    assert (r1["n_trigger"], r2["n_trigger"], r3["n_trigger"]) == (1, 0, 1)
    evs = sp.read_events(path=env["jsonl"])
    assert [e["signal_id"] for e in evs] == [
        "todays_picks.002007.SZ.pull.20261021.r1",
        "todays_picks.002007.SZ.pull.20261021.r2"]  # 轮次递增


def test_cooldown_directions_independent(env):
    write_pool(env, [pick_stock()])
    r1 = run(env, {"002007.SZ": 12.8})                       # pull
    r2 = run(env, {"002007.SZ": 13.6})                       # break（异向不互斥）
    assert (r1["n_trigger"], r2["n_trigger"]) == (1, 1)


# —— dry / 门控 ——


def test_dry_writes_nothing(env):
    write_pool(env, [pick_stock()])
    rep = run(env, {"002007.SZ": 12.8}, dry=True)
    assert rep["n_trigger"] == 1 and rep["appended"] == 0
    assert env["push"] == []
    assert not Path(env["jsonl"]).exists()
    assert not Path(env["state"]).exists()


def test_market_gate_skips_when_closed(env):
    write_pool(env, [pick_stock()])
    rep = run(env, {"002007.SZ": 12.8},
              now=T0.replace(hour=20), gate=True)  # 夜间
    assert rep["skipped_gate"] is True and rep["n_trigger"] == 0
    assert not Path(env["state"]).exists()


def test_missing_quote_skipped(env):
    write_pool(env, [pick_stock()])
    rep = run(env, {"002007.SZ": None})  # 行情缺失
    assert rep["n_trigger"] == 0 and rep["appended"] == 0


def test_fetch_failure_no_crash(env):
    write_pool(env, [pick_stock()])

    def boom(codes):
        raise IOError("network down")
    rep = pw.run_once(boom, T0, pool_path=env["pool"], state_path=env["state"],
                      pool_jsonl=env["jsonl"], gate=False)
    assert rep["n_trigger"] == 0  # fail-visible 于 summary，不炸进程


def test_two_stocks_both_trigger(env):
    write_pool(env, [pick_stock(), pick_stock(code="600519.SH",
                                              name="贵州茅台",
                                              entry_px=100.0, reg="up")])
    rep = run(env, {"002007.SZ": 12.8, "600519.SH": 104.0})
    assert rep["n_trigger"] == 2 and rep["appended"] == 2
    dirs = {t["code"]: t["direction"] for t in rep["triggers"]}
    assert dirs == {"002007.SZ": "pull", "600519.SH": "break"}
