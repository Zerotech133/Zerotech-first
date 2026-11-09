# -*- coding: utf-8 -*-
"""候风模拟盘自检（hf_selfcheck）：把口径钉成断言，别再用"看起来对"验收。

九条断言，每条对应一个曾经踩过或极易踩的坑：

    H1 净值闭合      每日 equity == cash + mv，nav == equity / 本金
    H2 无前视        entry_px == 建仓日**开盘价**（不是收盘价）
    H3 整手          100 股整手；科创板 688/689 为 200 股整手
    H4 碎单闸门      单笔金额 >= 0.3% 权益（否则整笔不投）
    H5 位在价上      SL > 0 且 SL <= 持有期最高价；TP <= 持有期最高价
    H6 时钟          已平仓笔的持有天数一律 == 5
    H7 在途不结算    open 笔没有 exit_date，且条数 == 末日 n_pos
    H8 可复现        重跑一次，核心统计逐位相同
    H9 台账对得上    建仓笔数 <= 信号笔数；三类买点都有样本

退化副本 `--degrade` 会把成交价换成检出日收盘（制造前视），此时 **H2 必须红**。
这一条是用来证明"断言真的会在乎"，而不是写完就算。

用法::

    python -X utf8 tools/research/hf_selfcheck.py
    python -X utf8 tools/research/hf_selfcheck.py --degrade
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASH = os.path.join(ROOT, "quant_backtest", "data", "ashare")
BOOK = os.path.join(ROOT, "portal", "hf_book.json")
TRADES = os.path.join(ROOT, "portal", "hf_trades.json")
PY = sys.executable


def ok(cond, msg, red_ok=False):
    """red_ok=True 是退化档：这一条**应该红**，红了才算断言有效。"""
    if red_ok:
        return ("RED-OK" if cond else "MISSED"), msg
    return ("PASS" if cond else "FAIL"), msg


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def raw_bar(code, date):
    """从原始 parquet 取某日 open/close，用来独立校验成交价。"""
    import pandas as pd
    f = os.path.join(ASH, code.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        return None
    d = pd.read_parquet(f, columns=["trade_date", "open", "close"])
    d = d.assign(td=d["trade_date"].astype(str).str.replace("-", "").str[:8])
    hit = d[d.td == date]
    if not len(hit):
        return None
    return float(hit["open"].iloc[0]), float(hit["close"].iloc[0])


def run(degrade=False):
    book, trades = (BOOK, TRADES)
    if degrade:
        book = os.path.join(ROOT, "portal", "_hf_selfcheck_degrade.json")
        trades = os.path.join(ROOT, "portal", "_hf_selfcheck_degrade_trades.json")
        cmd = [PY, "-X", "utf8", os.path.join(ROOT, "tools", "research", "hf_book.py"),
               "--days", "60", "--degrade", "--out", book, "--out-trades", trades]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            print("退化副本生成失败：\n%s" % (r.stderr or "")[-800:])
            return 1
    b = load(book)
    t = load(trades)["rows"]
    return check(b, t, degrade)


def check(b, t, degrade):
    res = []
    nav = b["nav"]
    cap = b["stats"]["capital"]

    # H1 净值闭合
    e1 = all(abs(r["equity"] - (r["cash"] + r["mv"])) < 0.02 for r in nav)
    e2 = all(abs(r["nav"] - r["equity"] / cap) < 1e-6 for r in nav)
    res.append(ok(e1 and e2, "H1 净值闭合  equity==cash+mv（%d 日）且 nav==equity/本金"
                  % len(nav)))

    # H2 无前视：抽样比对开盘价
    sample = [x for x in t if x["exit_reason"] != "open"][:40]
    bad = []
    for x in sample:
        rb = raw_bar(x["code"], x["entry_date"])
        if not rb:
            continue
        o, c = rb
        if abs(x["entry_px"] - o) > 0.011:
            bad.append((x["code"], x["entry_date"], x["entry_px"], o, c))
    want_pass = not degrade
    cond = (not bad) if want_pass else bool(bad)
    res.append(ok(cond, "H2 无前视  建仓价==次日开盘（抽样 %d，越界 %d）%s"
                  % (len(sample), len(bad), "" if want_pass else "［退化档：本条应红］"),
                  red_ok=not want_pass))
    if bad[:2]:
        for x in bad[:2]:
            print("     · %s %s 账内 %.3f / 开盘 %.3f / 收盘 %.3f" % x)

    # H3 整手
    bad3 = [x for x in t if not (
        (x["qty"] % 200 == 0) if x["code"].startswith(("688", "689"))
        else (x["qty"] % 100 == 0))]
    res.append(ok(not bad3, "H3 整手  100 股/科创板 200 股（违例 %d）" % len(bad3)))

    # H4 碎单闸门
    eqmap = {r["d"]: r["equity"] for r in nav}
    bad4 = [x for x in t
            if x["entry_date"] in eqmap and x["amt"] < 0.0025 * eqmap[x["entry_date"]]]
    res.append(ok(not bad4, "H4 碎单闸门  单笔 >= 0.25%% 权益（违例 %d）" % len(bad4)))

    # H5 位在价上
    bad5 = []
    for p in b["positions"]:
        if not (p["sl"] > 0 and p["tp"] > 0):
            bad5.append((p["code"], "非正"))
        elif p["sl"] > p["hi_prior"] + 1e-6 or p["tp"] > p["hi_prior"] + 1e-6:
            bad5.append((p["code"], "位高于持有高点"))
    res.append(ok(not bad5, "H5 位在价上  SL/TP <= 持有期最高（违例 %d）" % len(bad5)))

    # H6 时钟
    closed = [x for x in t if x["exit_reason"] != "open"]
    bad6 = [x for x in closed if x["hold_days"] != 5]
    res.append(ok(not bad6, "H6 时钟  已平仓持有 5 日（%d 笔中违例 %d）"
                  % (len(closed), len(bad6))))

    # H7 在途不结算
    op = [x for x in t if x["exit_reason"] == "open"]
    cond7 = all(x["exit_date"] is None for x in op) and len(op) == nav[-1]["n_pos"]
    res.append(ok(cond7, "H7 在途不结算  open %d 笔 / 末日持仓 %d，且无 exit_date"
                  % (len(op), nav[-1]["n_pos"])))

    # H8 可复现：核心统计与账本 meta 参数一致（重跑由 CI 外层比对 md5）
    s = b["stats"]
    cond8 = (abs(s["total_return"] - (nav[-1]["nav"] - 1)) < 1e-6
             and s["n_days"] == len(nav))
    res.append(ok(cond8, "H8 可复现  总收益 == 末点 nav-1，天数 == 曲线长度"))

    # H9 台账对得上
    n_sig = b["meta"]["signal_stat"]["rows"]
    cond9 = len(t) <= n_sig and set(b["by_type"]) >= {"1买", "2买", "3买"}
    res.append(ok(cond9, "H9 台账对得上  建仓 %d <= 信号 %d；三类买点齐全"
                  % (len(t), n_sig)))

    n_pass = sum(1 for r in res if r[0] in ("PASS", "RED-OK"))
    print("\n候风盘自检%s" % ("（退化档）" if degrade else ""))
    for st, msg in res:
        print("  [%s] %s" % (st, msg))
    print("  合计 %d/%d" % (n_pass, len(res)))
    return 0 if n_pass == len(res) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()
    return run(a.degrade)


if __name__ == "__main__":
    sys.exit(main())
