# -*- coding: utf-8 -*-
"""选股信号后验计算器：历史池子里的票，信号日后 1/3/5 交易日的真实反应。

数据地基：portal/data/picks_history/todays_<YYYYMMDD>.json（build_todays_picks.py 每日归档）。
口径：信号日 = 归档的 scan_run_date（数据基准收盘日）；从 T+1 起算窗口收益，
      湖价（ts_daily，未复权原价口径与页面行情列一致）；对照 = 同窗口全市场中位数。
输出：portal/data/pick_verdicts.json
      summary: 按窗口的整体命中率/均值/超额 + 按策略分层
      per_code: 每票最近一次信号的后验（前端详情卡直接消费）
窗口未满（湖还没走到 T+N）如实标 pending，不硬凑。
用法: "C:/Program Files/Python311/python.exe" portal/build_pick_verdicts.py
"""
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
QB = os.path.join(ROOT, "quant_backtest")
HIST_DIR = os.path.join(HERE, "data", "picks_history")
OUT = os.path.join(HERE, "data", "pick_verdicts.json")
WINDOWS = (1, 3, 5)


def _trading_days(cal):
    """湖全市场交易日序列（升序 YYYYMMDD 字符串）。"""
    return sorted(cal)


def _code_to_ts(code):
    """600540.SH → ts_daily 的 ts_code 同形态（湖本身就是 .SH 风格）。"""
    return (code or "").strip().upper()


def main():
    sys.path.insert(0, QB)
    from tdx_read import read_day, latest_date  # noqa: E402

    archs = sorted(glob.glob(os.path.join(HIST_DIR, "todays_*.json")))
    if not archs:
        print("[verdicts] 无归档可算（等 build_todays_picks 首份归档）")
        sys.exit(0)
    today = latest_date("ts_daily")
    if not today:
        print("[verdicts] 行情湖不可达，跳过")
        sys.exit(0)

    # 全市场日收益中位数作对照基准（一次load当日全市场）
    day_cache = {}

    def day_mid(dd):
        if dd not in day_cache:
            try:
                df = read_day("ts_daily", dd, columns=["ts_code", "pct_chg"])
                vals = [v for v in
                        (None if df is None else df["pct_chg"].tolist())
                        or [] if v is not None]
                vals.sort()
                day_cache[dd] = vals[len(vals) // 2] if vals else None
            except Exception:  # noqa: BLE001
                day_cache[dd] = None
        return day_cache[dd]

    # 全市场交易日历：任取一只宽基票的交易日序列
    cal_df = read_day("ts_daily", today, columns=["ts_code"])
    cal_days = _trading_days(set())  # 占位
    # 交易日历从逐日 latest 回推不现实——改从某只票全史读：
    spy = cal_df["ts_code"].iloc[0] if cal_df is not None and len(cal_df) else None
    trade_days = []
    if spy:
        try:
            from tdx_read import read_range
            # ⚠ read_range 带 codes 时 columns 必须含 ts_code（否则 code 列
            # 被裁掉、过滤恒空——tdx_read 实测暗坑）
            df_all = read_range("ts_daily", "20200101", today,
                                codes=[spy],
                                columns=["ts_code", "trade_date"])
            trade_days = sorted({str(d) for d in
                                 (df_all["trade_date"].tolist()
                                  if df_all is not None else [])})
        except Exception as ex:  # noqa: BLE001
            print("[verdicts] 交易日历读取降级：%s" % str(ex)[:60])

    def after_days(sig_day, n):
        """信号日(YYYYMMDD)后第 n 个交易日（严格大于信号日）。"""
        try:
            i = trade_days.index(sig_day)
        except ValueError:
            # 信号日非交易日 → 找首个 >sig 的
            later = [d for d in trade_days if d > sig_day]
            if not later:
                return None
            i = trade_days.index(later[0]) - 1  # 回退一格统一用 index+n
        j = i + n
        return trade_days[j] if j < len(trade_days) else None

    def pct_between(code, d0, d1):
        """d0 收盘 → d1 收盘 的百分比收益（湖价）。"""
        try:
            from tdx_read import read_range
            df = read_range("ts_daily", d0, d1, codes=[code],
                            columns=["ts_code", "trade_date", "close"])
            if df is None or len(df) < 2:
                return None
            df = df.sort_values("trade_date")
            c0, c1 = float(df["close"].iloc[0]), float(df["close"].iloc[-1])
            return round((c1 / c0 - 1) * 100, 2) if c0 else None
        except Exception:  # noqa: BLE001
            return None

    per_code, buckets = {}, {w: [] for w in WINDOWS}
    strat_buckets = {}
    n_arch = 0
    for fp in archs:
        try:
            doc = json.load(open(fp, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        sig = (doc.get("scan_run_date") or "").replace("-", "")
        if not sig or sig > today:
            continue
        n_arch += 1
        for r in doc.get("stocks") or []:
            code = _code_to_ts(r.get("code"))
            if not code or "." not in code:
                continue  # 后验口径当前只覆盖 A 股（湖 ts_daily）
            rec = per_code.setdefault(code, {
                "name": r.get("name"), "tier": r.get("tier"),
                "date": sig, "strategies": sorted({s.get("s") for s in
                                                   (r.get("sources") or [])
                                                   if s.get("s")}),
                "win": {}})
            if sig < rec["date"]:
                continue  # 只保留每票最近一次信号
            for w in WINDOWS:
                if w in rec["win"]:
                    continue
                d1 = after_days(sig, w)
                if d1 is None:
                    rec["win"][w] = None  # 窗口未满 → pending
                    continue
                r1 = pct_between(code, sig, d1)
                b1 = day_mid(d1)
                rec["win"][w] = {"r": r1, "base": b1, "ex": (
                    round(r1 - b1, 2) if r1 is not None and b1 is not None
                    else None)}
                if r1 is not None:
                    buckets[w].append(r1)
                    for s in rec["strategies"]:
                        strat_buckets.setdefault(s, {}).setdefault(
                            w, []).append(r1)
    # 汇总
    def blk(vals, w):
        if not vals:
            return {"n": 0, "hit": None, "avg": None, "win_rt": None}
        vals_s = sorted(vals)
        return {"n": len(vals),
                "hit": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                "avg": round(sum(vals) / len(vals), 2),
                "win_rt": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                "med": vals_s[len(vals_s) // 2]}
    summary = {
        "asof": today, "archives": n_arch,
        "windows": {str(w): blk(buckets[w], w) for w in WINDOWS},
        "by_strategy": {s: {str(w): blk(b.get(w) or [], w) for w in WINDOWS}
                        for s, b in sorted(strat_buckets.items())},
    }
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "summary": summary, "per_code": per_code}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("[verdicts] 归档 %d 份 | 覆盖 %d 票 | asof %s" %
          (n_arch, len(per_code), today))
    for w in WINDOWS:
        b = summary["windows"][str(w)]
        print("  T+%d: n=%s 命中率=%s%% 均值=%s%%" %
              (w, b["n"], b["hit"], b["avg"]))


if __name__ == "__main__":
    main()
