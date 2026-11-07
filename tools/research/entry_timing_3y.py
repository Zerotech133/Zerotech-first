# -*- coding: utf-8 -*-
"""入场择时诊断：-26% 回撤到底能不能靠入场择时救（K-RES-20261108-TIMING）。

问题
----
出场规则全试过（持满/固定止损/移动止损/分型止损/止盈），选股规则也试过
（剔3买、限只数、type 排序），**没有任何方案把最大回撤压到 -20% 以内**。
用户判断：入场择时才是瓶颈，不是出场止损。

这个脚本分三步回答，不预设结论：

A. 回撤归因
   最大回撤区间在哪？区间内开仓的笔贡献多少？同期上证跌多少？
   → 如果回撤 80% 来自 2 个月的系统性暴跌，那就是 beta，不是选股错。

B. 事前可见的择时因子分档
   每个因子都只用「开仓日之前」的信息（严格 PIT）：
     ma20gap / ma60gap  上证收盘相对均线
     idxvol20           上证 20 日已实现波动（事前）
     breadth            前一交易日全市场上涨家数占比
     sigdens            前一交易日信号数 / 前 20 日信号数均值（拥挤度）
     drawdown           上证相对前 60 日高点的回撤（事前）
   按分档看下一批（开仓批）的日组均收益、命中率、同期指数。

C. 叠加过滤后的组合层
   只保留高档（或不剔低档）的开仓日，跑共用引擎，看回撤与年化。
   判据：回撤改善 > 5pp 且年化损失 < 3pp 才算"择时有效"。
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portfolio_mtm import Engine, Pick, build_ratio, load_price_panel  # noqa: E402

TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
# 权威池 v2（2026-11-08 重建：修 _find_lag 的 MIN_BARS 下限）。
# 账户层绝对数字跨池版本不可比，任何结论都要标注池子版本。
POOL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_v2.jsonl")
OUT = os.path.join(ROOT, "portal", "entry_timing_evidence.json")

BUY = ("1买", "2买", "3买")
HOLD = 20
TYPE_RANK = {"1买": 0, "2买": 1, "3买": 2}
SEED2 = 20261108


def _norm(s):
    return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def load_pool(drop3=True, since=None):
    """since: 开仓日下限（8 位）。默认剔掉 2023-09-25 门限堆叠段。

    为什么要下限：扩展池数据从 2023-04-03 截断，MIN_BARS=120 让「所有票首个可
    扫描日」收敛到 2023-09-25，当天堆了 1,724 笔（占 2.6%）。这是数据截断 +
    门限的合成伪影（真实市场里这些票有长历史，信号不会推到 9-25），不是行情。
    """
    rows = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY or r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("price") and r.get("entry_date")):
            continue
        if drop3 and r["type"] == "3买":
            continue
        d8 = r["entry_date"].replace("-", "")[:8]
        if since and d8 < since:
            continue
        rows.append({
            "code": r["code"], "type": r["type"],
            "d": d8,
            "x": (r.get("exit_date") or "").replace("-", "")[:8],
            "entry_px": float(r["entry_px"]), "exit_px": float(r["exit_px"]),
            "ret": float(r["ret"]), "alpha": r.get("alpha"), "idx": r.get("idx_ret"),
        })
    return rows


def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            d["trade_date"] = _norm(d["trade_date"])
            out.append(d)
    idx = (pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
           .sort_values("trade_date"))
    return idx.reset_index(drop=True)


def load_breadth(dates):
    """每日上涨家数占比（收盘后可得，所以用时必须前移一天）。"""
    up, tot = defaultdict(int), defaultdict(int)
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d.sort_values(["ts_code", "trade_date"])
        d["chg"] = d.groupby("ts_code", sort=False)["close"].pct_change()
        g = d.dropna(subset=["chg"]).groupby("trade_date")["chg"]
        for dt, s in g:
            up[dt] += int((s > 0).sum())
            tot[dt] += int(len(s))
    return {d: up[d] / tot[d] for d in tot if tot[d] > 50}


def _lim_pct(code):
    """按板块返回涨跌停幅度。ST 单独 5%，但缺 ST 标记，只能按板块近似——
    影响面有限：ST 只在极端恐慌日贡献跌停家数，且因子用的是「家数」不是名单。"""
    c = str(code)
    if c.startswith(("688", "689")):
        return 0.20          # 科创板
    if c.startswith(("300", "301")):
        return 0.20          # 创业板
    if c.startswith(("8", "4", "920")):
        return 0.30          # 北交所
    return 0.10


def load_market_struct(axis):
    """市场结构类事前因子：涨跌停家数、连板高度、成交额分位。

    与前 6 个因子（全是「大盘状态」：均线位置、波动、宽度）的根本区别：
    这些量描述的是**投机资金的行为**，不是价格水平。三年回撤期上证只跌 7.78%，
    策略跌 27.99%，说明下跌市里选股失效——失效机制更可能藏在情绪结构里
    （涨停家数塌缩、连板高度断裂），而不是指数位置里。
    """
    want = set(axis)

    # (a) 涨停家数 + 最高连板高度：同花顺涨停池（2023-11 起，覆盖 729 日）
    zt, mboard = defaultdict(int), {}
    for p in sorted(glob.glob(os.path.join(TDX, "ts_limit_list_ths", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["trade_date", "limit_type", "tag"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[d["trade_date"].isin(want)]
        if not len(d):
            continue
        for dt, g in d.groupby("trade_date"):
            zt[dt] += int((g["limit_type"] == "涨停池").sum())
            mx = 1
            for t in g["tag"].dropna():
                # tag 形如「3天5板」/「首板」；取「板」前面的数字
                tt = str(t)
                if tt == "首板":
                    continue
                if "板" in tt:
                    num = tt.split("板")[0].split("天")[-1]
                    if num.isdigit():
                        mx = max(mx, int(num))
            mboard[dt] = mx

    # (b) 跌停家数 + 全市场成交额：ts_daily 原始涨跌幅按板块幅度判
    dt_cnt, amt = defaultdict(int), defaultdict(float)
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "pct_chg", "amount"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[d["trade_date"].isin(want)]
        if not len(d):
            continue
        lim = d["ts_code"].map(_lim_pct).to_numpy(float) * 100.0 - 0.5
        down = (d["pct_chg"].to_numpy(float) <= -lim)
        d = d.assign(_dn=down)
        g = d.groupby("trade_date")
        for dt, sub in g:
            dt_cnt[dt] += int(sub["_dn"].sum())
            amt[dt] += float(np.nansum(sub["amount"].to_numpy(float)))

    zt_s = pd.Series({d: float(zt.get(d, np.nan)) for d in axis})
    dt_s = pd.Series({d: float(dt_cnt.get(d, np.nan)) for d in axis})
    mb_s = pd.Series({d: float(mboard.get(d, np.nan)) for d in axis})
    am_s = pd.Series({d: amt.get(d, np.nan) for d in axis}, dtype=float)
    out = {
        "zt_cnt": zt_s,
        # 涨停家数密度：当日 / 前 20 日均值（与 sigdens 同型，但用全市场情绪而非自家信号）
        "zt_dens": zt_s / zt_s.rolling(20).mean().shift(1),
        "dt_cnt": dt_s,
        "board_max": mb_s,
        "amt_pct": am_s.rolling(61).apply(
            lambda w: float((w[:-1] <= w[-1]).mean()) if len(w) > 1 else np.nan, raw=True),
        "zt_dt": zt_s / (zt_s + dt_s).replace(0, np.nan),
    }
    for k, v in out.items():
        print("    [struct] %-9s 可算 %d 日，非空 %d"
              % (k, int(v.notna().sum()), int(v.notna().sum())))
    return out


def build_axis(rows, idx):
    d0, d1 = min(r["d"] for r in rows), max(r["x"] for r in rows)
    ax = [d for d in idx["trade_date"].tolist() if d0 <= d <= d1]
    return ax


def main():
    global POOL
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--boot", type=int, default=50)
    ap.add_argument("--only", default="",
                    help="只跑名字含该子串的因子（逗号分隔），空=全部")
    ap.add_argument("--out", default=OUT, help="证据 JSON 输出路径")
    ap.add_argument("--pool", default="", help="扩展池路径（默认权威池 v2）")
    ap.add_argument("--since", default="20231009",
                    help="开仓日下限 YYYYMMDD（默认 20231009：剔 2023-09-25 门限堆叠）")
    a = ap.parse_args()
    if a.pool:
        POOL = a.pool
    say = (lambda *x, **k: None) if a.json else print

    rows = load_pool(since=(a.since.replace("-", "")[:8] or None))
    idx = load_index()
    axis = build_axis(rows, idx)
    ap_ = {d: i for i, d in enumerate(axis)}
    icl = dict(zip(idx["trade_date"], idx["close"].astype(float)))
    say("池 %s：%d 笔（已剔3买）/ 日历 %d 日 %s~%s"
        % (os.path.basename(POOL), len(rows), len(axis), axis[0], axis[-1]))

    # 日组均与指数
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r)
    days = sorted(by)
    day_mean = {d: float(np.mean([r["ret"] for r in by[d]])) for d in days}
    day_n = {d: len(by[d]) for d in days}

    # ---------- 引擎：基线（全买，剔3买） ----------
    P = load_price_panel({r["code"] for r in rows}, TDX)
    for r in rows:
        p = P.get(r["code"])      # 池里混了指数代码（399001.SZ 等）
        if p is None:
            r["ratio"] = None
            continue
        ei, xi = p["pos"].get(r["d"]), p["pos"].get(r["x"])
        r["ratio"] = (build_ratio(p, ei, xi, r["entry_px"], r["exit_px"], axis, ap_)
                      if ei is not None and xi is not None and xi > ei else None)
    rows = [r for r in rows if r["ratio"] is not None]
    days = sorted({r["d"] for r in rows})
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r)

    eng = Engine(axis, hold=HOLD)

    def run_dayfilter(keep_days, label=""):
        picks = {d: [Pick(r["code"], ap_[r["d"]], ap_[r["x"]], r["ratio"], r["ret"])
                     for r in by[d]] for d in keep_days}
        v = eng.run(picks)
        return v

    v0 = run_dayfilter(days, "baseline")
    say("\n基线（剔3买全买）: 累计 %.2f%%  年化 %.2f%%  回撤 %.2f%%  夏普 %.2f  波动 %.2f%%"
        % (v0["total"], v0["cagr"] or 0, v0["mdd"], v0["sharpe"], v0["vol"]))
    res = {"meta": {"n": len(rows), "days": len(days), "axis": len(axis),
                    "window": [axis[0], axis[-1]],
                    # 池子版本必须落盘：跨版本账户层绝对数字不可比
                    "pool": os.path.basename(POOL)}, "baseline": v0}

    # ---------- A. 回撤归因 ----------
    cur = np.array(v0["curve"] or [], dtype=float)
    # curve 在 Engine 里超过 4000 点会置 None，axis 约 660 点，正常有值
    dd = cur / np.maximum.accumulate(cur) - 1.0
    t = int(np.argmin(dd))
    pk = int(np.argmax(cur[:t + 1])) if t > 0 else 0
    seg = axis[pk:t + 1]
    say("\n" + "=" * 100)
    say("A. 最大回撤归因")
    say("=" * 100)
    say("  峰值 %s → 谷底 %s（%d 个交易日）  回撤 %.2f%%"
        % (axis[pk], axis[t], len(seg) - 1, dd[t] * 100))
    say("  同期上证 %+.2f%%" % ((icl[axis[t]] / icl[axis[pk]] - 1) * 100))
    seg_rows = [r for r in rows if axis[pk] <= r["d"] <= axis[t]]
    n_all = len(rows)
    say("  区间内开仓 %d 笔（占全部 %.1f%%），日均 %d 笔"
        % (len(seg_rows), len(seg_rows) / n_all * 100,
           len(seg_rows) / max(1, len({r["d"] for r in seg_rows}))))
    if seg_rows:
        say("  区间内笔均 %+.2f%%，区间外笔均 %+.2f%%"
            % (np.mean([r["ret"] for r in seg_rows]) * 100,
               np.mean([r["ret"] for r in rows if r not in seg_rows]) * 100))
    # 逐月净值变化
    say("\n  逐月净值（找亏损集中月）")
    say("  %-8s %10s %10s %10s" % ("月份", "净值变动%", "上证%", "开仓笔数"))
    mv = []
    months = sorted({axis[i][:6] for i in range(pk + 1, t + 1)})
    for m in months:
        ii = [i for i in range(pk + 1, t + 1) if axis[i][:6] == m]
        if not ii:
            continue
        r0, r1 = cur[ii[0] - 1], cur[ii[-1]]
        ix0, ix1 = icl[axis[ii[0] - 1]], icl[axis[ii[-1]]]
        nn = sum(1 for r in seg_rows if r["d"][:6] == m)
        mv.append({"month": m, "nav": round((r1 / r0 - 1) * 100, 2),
                   "idx": round((ix1 / ix0 - 1) * 100, 2), "n": nn})
        say("  %-8s %+9.2f%% %+9.2f%% %10d" % (m, mv[-1]["nav"], mv[-1]["idx"], nn))
    res["drawdown_attr"] = {"peak": axis[pk], "trough": axis[t], "mdd": round(float(dd[t]) * 100, 2),
                            "idx_same": round(float((icl[axis[t]] / icl[axis[pk]] - 1) * 100), 2),
                            "n_in_seg": len(seg_rows), "months": mv}

    # ---------- B. 事前可见择时因子 ----------
    say("\n" + "=" * 100)
    say("B. 事前可见因子分档（全部 PIT：只用开仓日之前的数据）")
    say("=" * 100)

    ic = idx.set_index("trade_date")["close"].astype(float)
    ic = ic.reindex(axis)
    ma20 = ic.rolling(20).mean()
    ma60 = ic.rolling(60).mean()
    gap20 = ic / ma20 - 1.0
    gap60 = ic / ma60 - 1.0
    rvol = ic.pct_change().rolling(20).std()
    hi60 = ic.rolling(60).max()
    dd60 = ic / hi60 - 1.0
    # 全部前移一格：开仓日用的是「昨日收盘」为止的信息
    F = {"ma20gap": gap20.shift(1), "ma60gap": gap60.shift(1),
         "idxvol20": rvol.shift(1), "dd60": dd60.shift(1)}
    say("  算宽度（全市场上涨家数占比）…")
    br = load_breadth(axis)
    bser = pd.Series({d: br.get(d, np.nan) for d in axis})
    F["breadth"] = bser.shift(1)
    say("  算市场结构（涨跌停 / 连板 / 成交额）…")
    for k, v in load_market_struct(axis).items():
        # 涨停家数等是「当日收盘后可算」，但开仓也在收盘，为了不留任何当日
        # 内信息渗漏（比如尾盘涨停是否封住），统一再前移一格。
        F[k] = v.shift(1)
    # 信号密度：前一日信号数 / 前 20 日信号数均值
    sn = pd.Series({d: day_n.get(d, 0) for d in axis}, dtype=float)
    F["sigdens"] = (sn.shift(1) / sn.shift(1).rolling(20).mean())

    if a.only:
        keep = [x.strip() for x in a.only.split(",") if x.strip()]
        F = {k: v for k, v in F.items() if any(t in k for t in keep)}
        say("  只跑因子：%s" % ", ".join(sorted(F)))

    day_ret = pd.Series({d: day_mean.get(d, np.nan) for d in axis})
    res["factors"] = {}
    for name, ser in F.items():
        sub = pd.DataFrame({"f": ser, "y": day_ret, "d": axis}).dropna()
        if len(sub) < 100:
            continue
        q = pd.qcut(sub["f"], 5, labels=False, duplicates="drop")
        rec = []
        say("\n  [%s]  覆盖 %d 个开仓日" % (name, len(sub)))
        say("    %-8s %6s %10s %10s %10s" % ("分位", "日数", "日组均%", "胜率%", "同期上证%"))
        for qi in sorted(set(q.dropna().astype(int))):
            s2 = sub[q == qi]
            y = s2["y"].dropna()
            ix = [r["idx"] for d in s2["d"] for r in by.get(d, []) if r["idx"] is not None]
            rec.append({"q": int(qi) + 1, "days": len(s2),
                        "day_mean": round(float(y.mean()) * 100, 3),
                        "win": round(float((y > 0).mean()) * 100, 1),
                        "idx": round(float(np.mean(ix)) * 100, 2) if ix else None,
                        "lo": round(float(s2["f"].min()), 4),
                        "hi": round(float(s2["f"].max()), 4)})
            say("    %-8d %6d %+9.3f%% %9.1f%% %+9.2f%%"
                % (rec[-1]["q"], rec[-1]["days"], rec[-1]["day_mean"],
                   rec[-1]["win"], rec[-1]["idx"] or 0))
        # 单调性：Q1 与 Q5 差 + Spearman
        if len(rec) >= 5:
            hi_q, lo_q = rec[-1], rec[0]
            rho = float(pd.Series(sub["f"]).corr(pd.Series(sub["y"]), method="spearman"))
            rec_note = {"q_hi": hi_q["day_mean"], "q_lo": lo_q["day_mean"],
                        "diff": round(hi_q["day_mean"] - lo_q["day_mean"], 3),
                        "rho": round(rho, 3)}
            say("    Q5-Q1 = %+.3f%%   Spearman rho = %+.3f"
                % (rec_note["diff"], rec_note["rho"]))
            # 分年拆解：**必须落盘**。全样本 Q5-Q1 看着漂亮、拆开却逐年翻转，
            # 是判定「结构类因子无效」的决定性证据（2026-11-08 复核时发现
            # 上一轮这组数字是临时算的、没进证据文件，等于不可复现）。
            # 分档沿用全样本 qcut 的边界（同一定义跨年可比），不按年重切。
            sub2 = sub.assign(qq=q.values)
            byy = {}
            for y, g in sub2.groupby(sub2["d"].str[:4]):
                m = {int(qi): round(float(g[g["qq"] == qi]["y"].mean()) * 100, 2)
                     for qi in g["qq"].dropna().unique()}
                if 0 in m and 4 in m:
                    byy[y] = {"q1": m[0], "q5": m[4],
                              "diff": round(m[4] - m[0], 2), "days": int(len(g))}
            if len(byy) >= 2:
                flips = len({1 if v["diff"] > 0 else -1 for v in byy.values()}) > 1
                rec_note["by_year"] = byy
                rec_note["sign_flip"] = bool(flips)
                say("    分年 Q5-Q1: " + "  ".join(
                    "%s %+.2f(%dd)" % (y, v["diff"], v["days"])
                    for y, v in sorted(byy.items()))
                    + ("   ← 方向翻转" if flips else "   （方向一致）"))
            res["factors"][name] = {"bins": rec, **rec_note}
        else:
            res["factors"][name] = {"bins": rec}

    # ---------- C. 叠加过滤后的组合层 ----------
    say("\n" + "=" * 100)
    say("C. 把择时因子做成开仓日过滤器（组合层）")
    say("     判据：回撤改善 > 5pp 且年化损失 < 3pp")
    say("=" * 100)
    say("  %-28s %6s %9s %9s %9s %8s"
        % ("过滤器", "开仓日", "累计", "年化", "最大回撤", "夏普"))
    cands = []
    for name, ser in F.items():
        s = ser.dropna()
        if len(s) < 200:
            continue
        qs = s.quantile([0.2, 0.4, 0.6, 0.8]).tolist()
        # 两个方向都试：高档留 / 低档留
        for direction, keep_side in (("high", "hi"), ("low", "lo")):
            for cut_lab, thr in (("P20", qs[0]), ("P50", s.median()), ("P80", qs[3])):
                if keep_side == "hi":
                    kd = [d for d in days if d in s.index and s[d] >= thr]
                else:
                    kd = [d for d in days if d in s.index and s[d] <= thr]
                if len(kd) < 60:
                    continue
                v = run_dayfilter(kd, name)
                lab = "%s %s>=%s" % (name, direction, cut_lab) if keep_side == "hi" \
                    else "%s %s<=%s" % (name, direction, cut_lab)
                cands.append({"filter": lab, "days": len(kd), "total": v["total"],
                              "cagr": v["cagr"], "mdd": v["mdd"], "sharpe": v["sharpe"],
                              "vol": v["vol"]})
                say("  %-28s %6d %8.2f%% %8.2f%% %8.2f%% %7.2f"
                    % (lab, len(kd), v["total"], v["cagr"] or 0, v["mdd"], v["sharpe"]))
    res["filters"] = cands

    # 汇总判据
    ok = [c for c in cands
          if (c["mdd"] - v0["mdd"]) > 5.0
          and (c["cagr"] is not None and v0["cagr"] is not None
               and c["cagr"] > v0["cagr"] - 3.0)]
    ok.sort(key=lambda c: -c["mdd"])
    res["pass"] = ok
    say("\n  满足「回撤改善>5pp 且年化损失<3pp」：%d 个" % len(ok))
    for c in ok[:8]:
        say("    %-28s 回撤 %+.2fpp  年化 %+.2fpp"
            % (c["filter"], c["mdd"] - v0["mdd"], (c["cagr"] or 0) - (v0["cagr"] or 0)))
    if not ok:
        say("    无 —— 入场择时救不了回撤，-%.0f%% 是 beta 风险" % abs(v0["mdd"]))

    # ---------- D. 随机对照：保留同样天数，看回撤的零分布 ----------
    # 这是本脚本最重要的一节。所有"降回撤"的过滤器都在减少开仓日数，
    # 而**少交易本身就会机械性降低回撤**（在场时间短 = 暴露少）。
    # 必须证明真实过滤器的回撤好于"随机抽同样天数"，否则它没有择时能力。
    say("\n" + "=" * 100)
    say("D. 随机对照：只保留同样天数、随机抽（%d 次），看回撤零分布" % a.boot)
    say("     若真实回撤落在随机分布内，说明降回撤只是「少交易」，不是择时")
    say("=" * 100)
    say("  %-24s %5s %10s %10s %10s %s"
        % ("过滤器", "天数", "真实回撤", "随机中位", "随机95%", "判定"))
    rng2 = np.random.default_rng(SEED2)
    res["null"] = []
    # 候选来源改成「回撤改善 > 5pp」的 top10，**不再叠加年化门槛**。
    # 原因：v2 池基线年化 19.82%，任何减仓过滤器的年化损失都在 8pp 以上，
    # 若按「年化损失<3pp」筛候选，ok 恒为空 → 这一节退化成 0/0，白跑。
    # 随机对照本身就是为了判「降回撤是否只是少交易」，跟年化门槛无关，
    # 所以这里放宽到只看回撤改善，判据（真实值须优于随机 95% 分位）不变。
    null_cands = [c for c in cands if (c["mdd"] - v0["mdd"]) > 5.0]
    res["null_note"] = ("候选=回撤改善>5pp 的 top10（不叠加年化门槛，见代码注释）")
    for c in sorted(null_cands, key=lambda z: z["mdd"])[:10]:
        n_d = c["days"]
        mdds = []
        for _ in range(a.boot):
            kd = list(rng2.choice(days, n_d, replace=False))
            mdds.append(run_dayfilter(kd)["mdd"])
        p95 = float(np.percentile(mdds, 95))
        p50 = float(np.percentile(mdds, 50))
        # 判据必须严：真实回撤要优于「随机抽同样天数」里最好的 5%，
        # 才算真有择时能力。宽松地用 p5（优于最差 5%）会把「少交易」
        # 误判成「择时有效」——实测 ma20gap low<=P20 真实 -8.91% 比随机
        # 中位 -6.90% 还差，却会因 >p5 被判有效。
        if c["mdd"] > p95:
            verdict = "有效"
        elif c["mdd"] > p50:
            verdict = "弱（略优于随机中位，未过 5% 显著性）"
        else:
            verdict = "无效（不如随机，纯属少交易）"
        rec = {"filter": c["filter"], "days": n_d, "real": c["mdd"],
               "null_p50": round(p50, 2), "null_p95": round(p95, 2),
               "cagr": c["cagr"], "verdict": verdict}
        res["null"].append(rec)
        say("  %-24s %5d %9.2f%% %9.2f%% %9.2f%% %s"
            % (c["filter"], n_d, c["mdd"], p50, p95, verdict))
    n_ok = sum(1 for r in res["null"] if r["verdict"].startswith("有效"))
    say("\n  通过随机对照的过滤器：%d / %d" % (n_ok, len(res["null"])))
    if n_ok == 0:
        say("  → 入场择时没有证据支持：所有降回撤都可由「随机少开仓」复现")

    if a.only:
        res["only"] = a.only
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
