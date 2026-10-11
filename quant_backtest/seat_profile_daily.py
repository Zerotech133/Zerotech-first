# -*- coding: utf-8 -*-
"""游资席位画像日更管线（K-STOCK-123，开发道挂账 #4）。

每日收盘数据同步（tushare_sync 23:30）后跑：ts_top_inst 近 N 个有数据
交易日 → 每席位画像（活跃度/风格/胜率/常做票/游资词表命中）→
portal/data/seat_profiles.json（tempfile+os.replace 原子写），
/bt/seat 无参页读取渲染画像榜。

口径（循 kg_seat_winrate.py 判例，K14 同源）：
  dir  = sign(net_buy)               上榜方向
  ret1 = 次一交易日 close/close - 1  次日收益（ts_daily shift(-1)）
  win  = dir * ret1 > 0              上榜行为对次日方向的判断力
胜率非真实盈亏；卖出侧样本偏多（kg_build_cl1 口径警示）。

用法：python seat_profile_daily.py [--days 30] [--top 80] [--json]
退出码：0 成功 / 1 失败（stderr 落原因，供计划任务 alert_fail 告警）。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "..", "portal", "data", "seat_profiles.json")

# 游资词表复用 kg_build_cl1.YOUZI（(主体名, [席位regex], kind)）；
# 导入失败（如 linreg 缺依赖）不阻断画像，仅退化为无标注
YOUZI = []
try:
    from kg_build_cl1 import YOUZI as _YZ  # noqa: N813
    YOUZI = _YZ
except Exception:  # pragma: no cover
    pass


def _youzi_tag(exalter):
    """席位名命中游资词表 → 主体名（首个命中），否则空串。"""
    for name, pats, _kind in YOUZI:
        for p in pats:
            try:
                if re.search(p, exalter):
                    return name
            except re.error:
                continue
    return ""


def main():
    ap = argparse.ArgumentParser(description="游资席位画像日更")
    ap.add_argument("--days", type=int, default=30,
                    help="画像窗口=近 N 个有数据交易日（默认 30）")
    ap.add_argument("--top", type=int, default=80,
                    help="画像榜保留席位数（按上榜日数降序，默认 80）")
    ap.add_argument("--json", action="store_true", help="stdout 打印结果")
    args = ap.parse_args()

    import pandas as pd
    import tdx_read as tdxr

    # ── 取数：近 75 自然日足够覆盖 30 交易日 ──
    start = (dt.date.today() - dt.timedelta(days=75)).strftime("%Y%m%d")
    ti = tdxr.read_range(
        "ts_top_inst", start=start,
        columns=["ts_code", "trade_date", "exalter", "buy", "sell",
                 "net_buy"])
    if ti is None or ti.empty:
        print("ts_top_inst 无数据（start=%s）" % start, file=sys.stderr)
        return 1
    ti = ti.dropna(subset=["exalter"])
    ti["d"] = ti["trade_date"].astype(str).str[:8]
    days = sorted(ti["d"].unique())
    if not days:
        print("ts_top_inst 窗口内无有效交易日", file=sys.stderr)
        return 1
    win = set(days[-args.days:])
    basis = days[-1]
    ti = ti[ti["d"].isin(win)].copy()
    ti["exalter"] = ti["exalter"].astype(str).str.strip()

    # ── 次日收益：ts_daily close shift(-1)（循 kg_seat_winrate）──
    dd = tdxr.read_range(
        "ts_daily", start=days[max(0, len(days) - args.days - 1)],
        codes=set(ti["ts_code"]),
        columns=["ts_code", "trade_date", "close"])
    if dd is None or dd.empty:
        print("ts_daily 无数据，胜率字段置空", file=sys.stderr)
        ret_map = {}
    else:
        dd = dd.copy()
        dd["d"] = dd["trade_date"].astype(str).str[:8]
        dd = dd.sort_values(["ts_code", "d"])
        dd["ret1"] = dd.groupby("ts_code")["close"].shift(-1) \
            / dd["close"] - 1.0
        ret_map = {(r.ts_code, r.d): float(r.ret1)
                   for r in dd[["ts_code", "d", "ret1"]]
                   .itertuples(index=False) if pd.notna(r.ret1)}

    # 票名（ts_top_list name，供常做票展示）
    nm = {}
    tl = tdxr.read_range("ts_top_list", start=start,
                         columns=["ts_code", "trade_date", "name"])
    if tl is not None and not tl.empty:
        tl = tl.dropna(subset=["name"])
        for r in tl[["ts_code", "name"]].itertuples(index=False):
            nm.setdefault(r.ts_code, str(r.name))

    # ── 每席位画像 ──
    rows = []
    for seat, g in ti.groupby("exalter"):
        buy = float(g["buy"].fillna(0).sum())
        sell = float(g["sell"].fillna(0).sum())
        net = float(g["net_buy"].fillna(0).sum())
        n_dir_pos = int((g["net_buy"].fillna(0) > 0).sum())
        n_dir_neg = int((g["net_buy"].fillna(0) < 0).sum())
        n_ev = n_dir_pos + n_dir_neg
        if n_ev:
            pos_ratio = n_dir_pos / n_ev
            style = "主买" if pos_ratio >= 0.7 else (
                "主卖" if pos_ratio <= 0.3 else "双向")
        else:
            pos_ratio = 0.0
            style = "—"
        # 胜率：dir×ret1（无次日/停牌自然剔除）
        wins = n_wr = 0
        dr_sum = 0.0
        for r in g.itertuples(index=False):
            nb = float(r.net_buy) if pd.notna(r.net_buy) else 0.0
            if nb == 0:
                continue
            ret1 = ret_map.get((r.ts_code, r.d))
            if ret1 is None:
                continue
            d = 1 if nb > 0 else -1
            n_wr += 1
            if d * ret1 > 0:
                wins += 1
            dr_sum += d * ret1
        # 常做票 top3
        stock_cnt = g.groupby("ts_code").size().sort_values(
            ascending=False).head(3)
        top_stocks = [{"code": c, "name": nm.get(c, ""),
                       "cnt": int(n)} for c, n in stock_cnt.items()]
        rows.append({
            "seat": seat,
            "youzi": _youzi_tag(seat),
            "n_days": int(g["d"].nunique()),
            "n_ev": n_ev,
            "n_stocks": int(g["ts_code"].nunique()),
            "buy_yi": round(buy / 1e8, 2),
            "sell_yi": round(sell / 1e8, 2),
            "net_yi": round(net / 1e8, 2),
            "pos_ratio": round(pos_ratio, 2),
            "style": style,
            "wr_n": n_wr,
            "wr_win_rate": round(wins / n_wr, 3) if n_wr >= 10 else None,
            "wr_avg_ret": round(dr_sum / n_wr, 4) if n_wr >= 10 else None,
            "top_stocks": top_stocks,
            "last_date": g["d"].max(),
        })
    rows.sort(key=lambda r: (r["n_days"], r["net_yi"]), reverse=True)
    rows = rows[:args.top]

    out = {
        "date": dt.date.today().isoformat(),
        "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basis": "%s-%s-%s" % (basis[:4], basis[4:6], basis[6:8]),
        "window_days": args.days,
        "n_seats": len(rows),
        "note": "胜率=上榜方向×次日收益>0 占比（n≥10 才给值），"
                "非真实盈亏；卖出侧样本偏多，净额≠主体盈亏",
        "seats": rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(
        os.path.abspath(OUT)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("seat_profiles.json basis=%s seats=%d -> %s"
              % (out["basis"], out["n_seats"], os.path.abspath(OUT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
