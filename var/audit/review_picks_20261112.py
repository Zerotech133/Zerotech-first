# -*- coding: utf-8 -*-
"""11-12 盘前选票复盘（只读）。

信号日 20261111 收盘，执行日 20261112 开盘。三种收益必须分开说：
  A 信号次日 = 11-12 收 / 11-11 收 - 1     含隔夜，不等同可执行
  B 开盘浮盈 = 11-12 收 / 11-12 开 - 1     按报告口径的执行点，但 T+1 当日卖不掉
  C 盘中最好 = 11-12 高 / 11-12 开 - 1     上限，不可兑现
基准用全市场等权 + 共识池等权，不用指数（本地指数分片停在 10 月中）。
"""
import glob
import json
import os
import statistics
import sys

import pandas as pd

ROOT = r"C:\项目\老田的AI助手"
ASHARE = os.path.join(ROOT, "quant_backtest", "data", "ashare")
PICK_JSON = r"C:\项目\deliverables\preopen_pick15_v2_adj_check_20261111.json"
LIMIT_FILE = os.path.join(ROOT, "quant_backtest", "data", "tushare",
                          "limit_list_d", "d_20261112.parquet")
TARGET = "20261112"
PREV = "20261111"
COST_PCT = 0.30  # 双边，A股日频默认


def code_to_file(code):
    return os.path.join(ASHARE, code.replace(".", "_") + ".parquet")


def load_rows(code):
    p = code_to_file(code)
    if not os.path.exists(p):
        return None
    d = pd.read_parquet(p, columns=["trade_date", "open", "high", "low",
                                    "close", "pre_close", "pct_chg"])
    d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
    a = d[d["trade_date"] == PREV]
    b = d[d["trade_date"] == TARGET]
    if a.empty or b.empty:
        return None
    return dict(prev=float(a.iloc[-1]["close"]),
                open=float(b.iloc[-1]["open"]),
                high=float(b.iloc[-1]["high"]),
                low=float(b.iloc[-1]["low"]),
                close=float(b.iloc[-1]["close"]),
                pct=float(b.iloc[-1]["pct_chg"]))


def pct(x, base):
    return (x / base - 1.0) * 100.0


def stat(rows, key):
    v = [r[key] for r in rows]
    if not v:
        return dict(n=0)
    return dict(n=len(v), mean=round(statistics.mean(v), 3),
                median=round(statistics.median(v), 3),
                win=round(100.0 * sum(1 for x in v if x > 0) / len(v), 1),
                best=round(max(v), 2), worst=round(min(v), 2))


def spearman(xs, ys):
    """秩相关，n 小只作描述，不做显著性宣称。"""
    def rank(a):
        order = sorted(range(len(a)), key=lambda i: a[i])
        r = [0.0] * len(a)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    n = len(xs)
    if n < 3:
        return None
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(num / (dx * dy), 3) if dx and dy else None


def main():
    meta = json.load(open(PICK_JSON, encoding="utf-8"))
    top15 = meta["top15_adj"]
    pool = meta["full_rated"]

    # 涨跌停名单（当日），用于标注不可买入 / 不可卖出
    limits = {}
    limit_note = "文件不存在"
    if os.path.exists(LIMIT_FILE):
        lim = pd.read_parquet(LIMIT_FILE)
        for _, r in lim.iterrows():
            limits[str(r.get("ts_code", ""))] = str(r.get("limit", ""))
        limit_note = "%d 条" % len(lim)
        if len(lim) == 0:
            # 「上游给空」和「当天确实没有涨停」是两件事，不能混成一句「无涨停」
            limit_note = "0 条 —— 上游返回空表，无法据此断言当日无涨跌停；改用 OHLC 自算一字板"
    print("当日涨跌停名单:", limit_note)

    def one_word_board(row):
        """一字板：开=高=低=收 且涨幅达板，开盘无法买入。"""
        band = 19.5 if row["code"][:3] in ("300", "301", "688") else 9.7
        return (abs(row["open"] - row["high"]) < 1e-6 and
                abs(row["high"] - row["low"]) < 1e-6 and
                abs(row["low"] - row["close"]) < 1e-6 and
                row["pct"] >= band)

    # 15 只 + 全池
    picks = []
    missing = []
    for rank, item in enumerate(top15, 1):
        code = item["ts_code"]
        row = load_rows(code)
        if row is None:
            missing.append(code)
            continue
        row.update(rank=rank, code=code, name=item.get("name", ""),
                   industry=item.get("industry", ""), score=item["score"],
                   limit=limits.get(code, ""))
        row["one_board"] = one_word_board(row)
        row["a"] = pct(row["close"], row["prev"])
        row["b"] = pct(row["close"], row["open"])
        row["c"] = pct(row["high"], row["open"])
        row["d"] = pct(row["low"], row["open"])
        picks.append(row)

    poolrows = []
    pool_missing = []
    for item in pool:
        row = load_rows(item["ts_code"])
        if row is None:
            pool_missing.append(item["ts_code"])
            continue
        poolrows.append(dict(code=item["ts_code"], score=item["score"],
                             a=pct(row["close"], row["prev"]),
                             b=pct(row["close"], row["open"])))
    not_picked = [r for r in poolrows if r["code"] not in {p["code"] for p in picks}]

    # 全市场等权（只读当日行，文件多，逐片读）
    mkt = []
    files = sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
    for p in files:
        try:
            d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open",
                                            "close", "pre_close", "pct_chg"])
            d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
            hit = d[d["trade_date"] == TARGET]
            if not hit.empty:
                r = hit.iloc[-1]
                code = str(r["ts_code"])
                if code.endswith((".SH", ".SZ", ".BJ")) and not code.startswith(("000001.SH", "399006.SZ")):
                    op, cl, pc = float(r["open"]), float(r["close"]), float(r["pre_close"])
                    if op > 0 and pc > 0:
                        # 与选票完全同口径：B=开盘→收盘，A=昨收→今收
                        mkt.append(dict(b=pct(cl, op), a=pct(cl, pc)))
        except Exception:
            continue

    print("\n=== 数据完整性 ===")
    print("15只缺当日行情:", missing if missing else "无")
    print("共识池82只缺当日行情:", len(pool_missing), "只",
          (pool_missing[:5] if pool_missing else ""))
    print("全市场当日有行情的股票数:", len(mkt))

    print("\n=== 15 只明细（按评分序）===")
    print("%-3s %-11s %-8s %6s %8s %8s %8s %8s %6s" %
          ("#", "代码", "名称", "评分", "昨收", "开盘", "收盘", "开盘浮盈%", "涨跌%"))
    for r in picks:
        print("%-3d %-11s %-8s %6.3f %8.2f %8.2f %8.2f %+8.2f %+7.2f%s" %
              (r["rank"], r["code"], r["name"], r["score"], r["prev"],
               r["open"], r["close"], r["b"], r["pct"],
               "  [一字板/买不进]" if r["one_board"] else
               ("  [" + r["limit"] + "]" if r["limit"] else "")))
    print("  一字板（开盘买不进）只数:", sum(1 for r in picks if r["one_board"]))

    print("\n=== 三层对照（口径 B：开盘买入→收盘浮盈，单位 %）===")
    for label, rows in (("选出15只", [dict(b=r["b"], a=r["a"]) for r in picks]),
                        ("池内未选%d只" % len(not_picked), not_picked),
                        ("共识池82只全量", poolrows)):
        s = stat(rows, "b")
        print("  %-16s n=%-3d 均值=%+6.3f 中位=%+6.3f 胜率=%5.1f%% 最好=%+6.2f 最差=%+6.2f"
              % (label, s["n"], s["mean"], s["median"], s["win"], s["best"], s["worst"]))
    if mkt:
        ms = stat(mkt, "b")
        print("  %-16s n=%-3d 均值=%+6.3f 中位=%+6.3f 胜率=%5.1f%% 最好=%+6.2f 最差=%+6.2f"
              % ("全市场等权", ms["n"], ms["mean"], ms["median"], ms["win"],
                 ms["best"], ms["worst"]))
        ma = stat(mkt, "a")
        print("  （全市场口径A 昨收→今收 均值=%+.3f 中位=%+.3f 胜率=%.1f%%）"
              % (ma["mean"], ma["median"], ma["win"]))
        pb = stat([dict(b=r["b"]) for r in picks], "b")
        print("  选出15只 相对全市场超额（同口径B）: %+.3f pp" % (pb["mean"] - ms["mean"]))

    print("\n=== 口径 A（信号日收盘→次日收盘）===")
    for label, rows in (("选出15只", [dict(a=r["a"]) for r in picks]),
                        ("池内未选", not_picked), ("共识池82只", poolrows)):
        s = stat(rows, "a")
        print("  %-12s n=%-3d 均值=%+6.3f 中位=%+6.3f 胜率=%5.1f%%" %
              (label, s["n"], s["mean"], s["median"], s["win"]))

    print("\n=== 评分分层（口径 B）===")
    for label, seg in (("Top1-5", picks[:5]), ("Top6-10", picks[5:10]),
                       ("Top11-15", picks[10:15])):
        s = stat([dict(b=r["b"]) for r in seg], "b")
        print("  %-10s n=%d 均值=%+6.3f 中位=%+6.3f 胜率=%5.1f%%" %
              (label, s["n"], s["mean"], s["median"], s["win"]))

    print("\n=== 评分 vs 收益 秩相关（全池，n=%d）===" % len(poolrows))
    print("  score ~ 开盘浮盈 rho =", spearman([r["score"] for r in poolrows],
                                             [r["b"] for r in poolrows]))
    print("  score ~ 信号次日 rho =", spearman([r["score"] for r in poolrows],
                                              [r["a"] for r in poolrows]))

    bmean = stat([dict(b=r["b"]) for r in picks], "b")["mean"]
    print("\n=== 扣成本后（双边 %.2f%%）===" % COST_PCT)
    print("  选出15只 开盘浮盈均值 %+.3f%% → 扣成本 %+.3f%%" % (bmean, bmean - COST_PCT))
    print("  注意：T+1，11-12 开盘买入当日不可卖出，B 是浮盈不是已实现收益。")

    # 秩相关与分层必须落盘：它们是「评分排序是否反向」这条观测的全部证据，
    # 只打印在屏幕上就等于没留证据，事后无法复算、也无法进事件账。
    rho_b = spearman([r["score"] for r in poolrows], [r["b"] for r in poolrows])
    rho_a = spearman([r["score"] for r in poolrows], [r["a"] for r in poolrows])
    tiers = {}
    for label, seg in (("Top1-5", picks[:5]), ("Top6-10", picks[5:10]),
                       ("Top11-15", picks[10:15])):
        tiers[label] = stat([dict(b=r["b"]) for r in seg], "b")

    # 落 JSON 备查
    out = dict(signal_day=PREV, exec_day=TARGET, cost_pct=COST_PCT,
               picks=picks, missing=missing, pool_missing=pool_missing,
               market_n=len(mkt), limit_note=limit_note,
               market_b=stat(mkt, "b") if mkt else None,
               market_a=stat(mkt, "a") if mkt else None,
               rank_ic=dict(n=len(poolrows), method="spearman",
                            score_vs_open_to_close=rho_b,
                            score_vs_prev_close_to_close=rho_a,
                            note="b=开盘买入→当日收盘；a=信号日收盘→执行日收盘。"
                                 "单日、单行情状态，|rho|<0.15 只作描述，不做显著性宣称。"),
               tiers=tiers,
               summary=dict(
                   picks_b=stat([dict(b=r["b"], a=r["a"]) for r in picks], "b"),
                   pool_b=stat(poolrows, "b"),
                   not_picked_b=stat(not_picked, "b")))
    dest = os.path.join(ROOT, "var", "audit", "review_picks_20261112.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=float)
    print("\n落盘:", dest)


if __name__ == "__main__":
    main()
