# -*- coding: utf-8 -*-
"""runner 纸面账本去重 + max_holding_days（[20261014_p1_tail]）单测。

覆盖（四条路径，纯合成数据；update_ledger 路径参数化写 tmp，
不读写真实 positions.json，不推飞书）:
  T1 重复信号跳过：同 code+signal_date+strategy 已入账（历史已平仓）
     → 不再入账，dedup_skip 计数落日志
  T2 超期强平：open 持有 ≥ max_holding_days 且信号仍多头 → 强制平仓
     入 closed 并标注 forced_close（区别于翻空平仓）
  T3 config 缺省值：backtest 节缺失 / 键缺失 → 60
  T4 与既有 ffill 语义共存：信号连续多日为 1（ffill）时只建一次仓、
     无重复 pending；重复跑同日幂等不胀计数

运行:
  "C:/Program Files/Python311/python.exe" quant_backtest/test_runner_ledger_p1tail.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner                                     # noqa: E402
import report_page as rp                          # noqa: E402

PASS = []
FAILS = []


def check(name, cond, detail=""):
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       "｜" + detail if detail and not cond else ""))
    (PASS if cond else FAILS).append(name)


def _stub_heavy():
    """隔离副作用：信号/页面/看板全部打桩（不碰生产数据与产物页）"""
    runner._collect_signals = lambda: _stub_heavy.rows
    rp.write_signals_html = lambda *a, **k: None
    rp.write_positions_html = lambda *a, **k: None
    sys.modules["doc_board"] = types.SimpleNamespace(refresh=lambda: None)


def sig_row(market, symbol, date, sig_map, px=10.0, opx=10.0):
    """合成 _collect_signals 行（ffill 语义复刻：since_full 只在信号
    首日 == date，后续日为历史首日——与真实 _collect_signals 口径一致）"""
    since_full = {k: (date if v == 1 else "2026-01-01")
                  for k, v in sig_map.items()}
    return {"market": market, "symbol": symbol, "name": symbol,
            "px": px, "open": opx, "date": date,
            "sigs": sig_map, "since": {k: date[5:] for k in sig_map},
            "since_full": since_full,
            "res": sum(1 for v in sig_map.values() if v == 1)}


def run_ledger(rows, seed=None):
    """带桩跑一次 update_ledger（tmp 路径），返回 (led, stdout)"""
    _stub_heavy()
    _stub_heavy.rows = rows
    fp = os.path.join(tempfile.mkdtemp(prefix="p1tail_"), "positions.json")
    if seed is not None:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(seed, f, ensure_ascii=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        led = runner.update_ledger(refresh_signals=False, ledger_path=fp)
    return led, buf.getvalue(), fp


def main():
    S = "trend_follow"

    # T1 重复信号跳过：历史已平仓同键（code+signal_date+strategy）→ 不再入账
    rows = [sig_row("CN", "600519.SH", "2026-10-13", {S: 1})]
    seed = {"pending": [], "open": [], "closed": [
        {"market": "CN", "symbol": "600519.SH", "name": "600519.SH",
         "strategy": S, "sig_date": "2026-10-13",
         "entry_date": "2026-10-14", "entry_px": 10.0,
         "last_px": 10.5, "last_date": "2026-10-20",
         "exit_date": "2026-10-20", "exit_px": 10.5,
         "days": 6, "pnl": 0.05}]}
    led, out, _ = run_ledger(rows, seed)
    check("T1 重复信号不入账",
          len(led["pending"]) == 0 and len(led["closed"]) == 1,
          "pending=%d" % len(led["pending"]))
    check("T1 dedup_skip 落日志", "dedup_skip=1" in out, out.strip())
    # T1b 账内既有重复对也计数
    seed2 = {"pending": [
        {"market": "CN", "symbol": "000001.SZ", "name": "x", "strategy": S,
         "sig_date": "2026-10-13", "last_px": 9.0},
        {"market": "CN", "symbol": "000001.SZ", "name": "x", "strategy": S,
         "sig_date": "2026-10-13", "last_px": 9.0}],
        "open": [], "closed": []}
    led2, out2, _ = run_ledger([], seed2)
    check("T1b 账内重复计数", "dedup_skip=1" in out2, out2.strip())

    # T2 超期强平：bars_held 到期 + 信号仍多头 → closed + forced_close
    rows = [sig_row("CN", "000001.SZ", "2026-10-14", {S: 1}, px=11.0)]
    seed = {"pending": [], "closed": [], "open": [
        {"market": "CN", "symbol": "000001.SZ", "name": "x", "strategy": S,
         "sig_date": "2026-07-01", "entry_date": "2026-07-02",
         "entry_px": 10.0, "last_px": 11.0, "last_date": "2026-10-13",
         "bars_held": 59}]}
    led, out, _ = run_ledger(rows, seed)
    e = (led["closed"] or [{}])[0]
    check("T2 超期强平入 closed", len(led["open"]) == 0
          and e.get("forced_close") is True,
          "open=%d closed=%d" % (len(led["open"]), len(led["closed"])))
    check("T2 强平价与标注", e.get("exit_px") == 11.0
          and e.get("exit_date") == "2026-10-14"
          and e.get("bars_held") == 60, "e=%s" % e)
    check("T2 强平落日志", "forced_close=1" in out and "max_holding_days=60"
          in out, out.strip())
    # T2b 未到期不平：bars_held=59+1=60 边界内侧（58+1=59 < 60 继续持有）
    seed = {"pending": [], "closed": [], "open": [
        dict(seed["open"][0], bars_held=58)]}
    led, out, _ = run_ledger(rows, seed)
    check("T2b 未到期继续持有", len(led["open"]) == 1
          and led["open"][0]["bars_held"] == 59 and not led["closed"],
          "open=%d" % len(led["open"]))

    # T3 config 缺省值：节点/键缺失 → 60；显式值生效
    check("T3 空配置缺省 60", runner.max_holding_days({}) == 60)
    check("T3 键缺失缺省 60", runner.max_holding_days({"backtest": {}}) == 60)
    check("T3 显式值生效", runner.max_holding_days(
        {"backtest": {"max_holding_days": 30}}) == 30)
    check("T3 真实 config=60", runner.max_holding_days() == 60)

    # T4 与 ffill 语义共存：信号连续 1 → 一次入账；建仓→持有计数递增不误平
    rows = [sig_row("CN", "600036.SH", "2026-10-13", {S: 1}, px=10.0, opx=10.2)]
    led, out, fp = run_ledger(rows)
    check("T4 首日进 pending", len(led["pending"]) == 1
          and "dedup_skip" not in out, out.strip())
    with open(fp, encoding="utf-8") as f:
        disk1 = json.load(f)
    # 同日重跑（ffill 信号未变）：幂等，不重复入账
    led, out, fp = run_ledger(rows, seed=disk1)
    check("T4 同日重跑幂等", len(led["pending"]) == 1
          and "dedup_skip" not in out, "pending=%d out=%s"
          % (len(led["pending"]), out.strip()))
    # 次日信号仍为 1（ffill，since_full 停在首日）：pending→open，无新 pending
    rows2 = [sig_row("CN", "600036.SH", "2026-10-14", {S: 1}, px=10.5, opx=10.3)]
    led, out, fp = run_ledger(rows2, seed=disk1)
    check("T4 ffill 次日建仓不重复", len(led["open"]) == 1
          and len(led["pending"]) == 0
          and led["open"][0].get("bars_held") == 1,
          "open=%d pending=%d" % (len(led["open"]), len(led["pending"])))
    with open(fp, encoding="utf-8") as f:
        disk2 = json.load(f)
    # 第三日仍多头：bars_held 递增，远未到 60 不误平（与强平语义共存）
    rows3 = [sig_row("CN", "600036.SH", "2026-10-15", {S: 1}, px=10.8, opx=10.6)]
    led, out, fp = run_ledger(rows3, seed=disk2)
    check("T4 ffill 持有计数递增不误平", len(led["open"]) == 1
          and led["open"][0]["bars_held"] == 2 and not led["closed"],
          "bars=%s closed=%d" % (led["open"][0].get("bars_held"),
                                 len(led["closed"])))
    # T4b 翻空正常平仓后不留 forced_close 标注（语义不串）
    rows4 = [sig_row("CN", "600036.SH", "2026-10-16", {S: 0}, px=10.9)]
    led, out, fp = run_ledger(rows4, seed=disk2)
    e = (led["closed"] or [{}])[0]
    check("T4b 正常平仓无 forced_close", "forced_close" not in e
          and e.get("exit_date") == "2026-10-16", "e=%s" % e)

    print("\n%d PASS / %d FAIL" % (len(PASS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
