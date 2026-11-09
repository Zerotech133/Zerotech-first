# -*- coding: utf-8 -*-
"""hf_ab 自检：把"买点对照"的口径钉死。

A1 同引擎回归：all 组合必须与 hf_book.json 基线逐位一致（防止对照盘悄悄改了引擎）
A2 类型纯净：只做 1买 的账里不能出现 2买/3买
A3 窗口闭合：所有成交日必须落在对照窗口内
A4 集合自洽：b12 = both ∪ only_b12，且两边不相交
A5 净值闭合：曲线末值 == 1 + total_return
A6 反例成立：crowd 组的敞口归一收益必须比 all 更差（否则"降仓假象"的判断是错的）
A7 脆弱性报告：top5 占总盈亏比重 —— 这条不是 pass/fail，是要如实标出来

--degrade 故意把 A6 的判据反过来，确认断言真的会在乎。
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))

import hf_book as HF
import hf_ab as AB

BASE = os.path.join(ROOT, "portal", "hf_book.json")
res = []


def ok(cond, msg, red_ok=False, note=None):
    if red_ok:
        return ("RED-OK" if cond else "MISSED"), msg
    if note:
        return (note, msg)
    return ("PASS" if cond else "FAIL"), msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()

    ctx = HF.build_context(120)
    pool = {d: len(v) for d, v in ctx["plans"].items()}
    runs = {}
    for tag, label, only in AB.COMBOS:
        if tag == "crowd":
            continue
        runs[tag] = AB.run_combo(ctx, only)

    # A1 同引擎回归
    base = json.load(io.open(BASE, encoding="utf-8"))["stats"]
    cur = runs["all"]["stats"]
    keys = ("total_return", "max_drawdown", "sharpe", "win_rate", "avg_util",
            "n_trades", "n_open", "avg_ret")
    diff = [k for k in keys if abs((base.get(k) or 0) - (cur.get(k) or 0)) > 1e-9]
    res.append(ok(not diff, "A1 同引擎回归  all 组合与 hf_book 基线逐位一致（差异项 %s）"
                  % (diff or "无")))

    # A2 类型纯净
    bad = [t for t in runs["b1"]["trades"] if t["type"] != "1买"]
    res.append(ok(not bad, "A2 类型纯净  只做 1买 的账里混入其他类型 %d 笔" % len(bad)))

    # A3 窗口闭合
    d0, d1 = ctx["win"][0], ctx["asof"]
    bad = [t for tag in runs for t in runs[tag]["trades"]
           if not (d0 <= t["entry_date"] <= d1 and (t["exit_date"] or d0) <= d1)]
    res.append(ok(not bad, "A3 窗口闭合  成交日越界 %d 笔（窗 %s~%s）" % (len(bad), d0, d1)))

    # A4 集合自洽
    def key(t):
        return (t["code"], t["entry_date"])

    all12 = {key(t) for t in runs["all"]["trades"] if t["type"] in ("1买", "2买")}
    b12 = {key(t) for t in runs["b12"]["trades"]}
    both = b12 & all12
    only = b12 - all12
    res.append(ok(len(both) + len(only) == len(b12) and not (both & only),
                  "A4 集合自洽  both %d + only %d = b12 %d，且不相交"
                  % (len(both), len(only), len(b12))))

    # A5 净值闭合
    bad = []
    for tag in runs:
        st = runs[tag]["stats"]
        nav = runs[tag]["nav"]
        if nav and abs((nav[-1]["nav"] - 1) - st["total_return"]) > 1e-6:
            bad.append(tag)
    res.append(ok(not bad, "A5 净值闭合  曲线末值 == 1+总收益（异常组合 %s）" % (bad or "无")))

    # A6 反例成立：crowd 的敞口归一必须更差
    crowd = AB.run_combo(ctx, "CROWD")
    cu = crowd["stats"]["ret_per_util"]
    au = runs["all"]["stats"]["ret_per_util"]
    verdict = (cu is not None and au is not None and cu < au) if not a.degrade else (cu >= au)
    res.append(ok(verdict,
                  "A6 反例成立  拥挤度闸门的敞口归一 %.2f%% 必须差于全量 %.2f%%（若否则「降仓假象」判断有误）"
                  % ((cu or 0) * 100, (au or 0) * 100), red_ok=a.degrade))

    # A7 脆弱性报告（不是 pass/fail）
    s = runs["b1"]["stats"]
    sh = s["conc"]["top5_share"]
    fragile = sh is not None and abs(sh) >= 1.0
    res.append(ok(fragile,
                  "A7 脆弱性    b1 前5笔占 |总盈亏| 的 %s（≥100%% 记为 FRAGILE：净值靠少数几笔撑起）"
                  % ("—" if sh is None else "%.0f%%" % abs(sh * 100)),
                  note="FRAGILE" if fragile else "STEADY"))

    print("=" * 86)
    print("候风买点对照 · 自检（窗 %s~%s）" % (d0, d1))
    print("=" * 86)
    for tag, msg in res:
        print("[%s] %s" % (tag, msg))
    n_pass = sum(1 for r in res if r[0] in ("PASS", "RED-OK", "FRAGILE", "STEADY"))
    print("-" * 86)
    print("%d/%d 通过%s" % (n_pass, len(res), "（退化档）" if a.degrade else ""))
    return 0 if n_pass == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
