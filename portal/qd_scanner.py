# -*- coding: utf-8 -*-
"""量子双刀全市场扫描器（研究预览版，K-STOCK-022）

用法（须 venv 解释器，需 numpy/pandas）：
    C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe \\
        portal/qd_scanner.py [--top N]

数据源：quant_backtest/data/ashare/*.parquet（tushare 口径，amount=千元——
与引擎 strict 阈值 3e6（30 亿）直接吻合，无需换算）
输出：portal/qd_scan_cache.json（ts/耗时/扫描数/信号池/regime 分布/近5日信号）
性质：只读特征湖 + 只 import engine 信号模块；零影子链路触碰；纯研究预览，
      未回测验证（T-20261009-009 结论前不构成选股依据）。
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))

from quant_double_knife import quant_double_knife  # noqa: E402
from tools import quote_snapshot, make_context  # noqa: E402

ASHARE = ROOT / "quant_backtest" / "data" / "ashare"
CACHE = Path(__file__).parent / "qd_scan_cache.json"


def scan_one(fp):
    """单票扫描：返回 (code, dict) 或 None（缺列/数据不足/无信号时摘要返回）"""
    code = os.path.basename(fp).replace(".parquet", "").replace("_", ".")
    df = pd.read_parquet(fp, columns=["trade_date", "open", "high", "low", "close", "amount"])
    if len(df) < 100:  # 次新股/数据不足：MA54+TSI60 warmup 不够
        return code, None
    df = df.rename(columns={"trade_date": "date"}).set_index("date")
    out = quant_double_knife(df, profile="strict")
    hits = out[out["double_knife_signal"]]
    last = out.iloc[-1]
    recent_days = sorted(hits.index[-5:]) if len(hits) else []
    info = {
        "code": code,
        "signal_now": bool(last["double_knife_signal"]),
        "regime": str(last["regime_label"]),
        "close": round(float(last.name and df["close"].iloc[-1]), 2),
        "recent_signals": recent_days,
        "n_recent": int(out["double_knife_signal"].tail(5).sum()),
        # 尾盘两段式候选（K-STOCK-025）：末行为单日十字星（双刀第一刀已落，
        # 今日/次日或成第二刀）→ 14:40 盘中用实时 bar 复核
        "star_yesterday": bool(last["cross_star"]),
    }
    return code, info


def intraday_recheck(candidates):
    """尾盘两段式复核（K-STOCK-025）：候选票拉腾讯日 K（盘中最后一根=当日
    实时 bar，qfqday 行为）→ 完整八重重算 → 当日触发即尾盘买信号。
    量级：候选=昨日单日十字星票（通常几十~几百只），逐票 1 请求可承受。
    返回 (tail_signals, tail_checked, tail_note)
    """
    import pandas as pd  # noqa: F401  scan_one 依赖由调用方保证
    from tools import kline_daily
    today = datetime.now().strftime("%Y%m%d")
    tail_signals, checked = [], 0
    note = ""
    for code in candidates:
        plain = code.split(".")[0]
        try:
            kl = kline_daily(plain, n=150)
        except Exception:
            continue
        if len(kl) < 100:
            continue
        last_day = str(kl[-1][0]).replace("-", "")
        if last_day != today:
            note = "腾讯源末根=%s 非当日（非盘中/接口口径变化），本段结果不可信" % last_day
            continue
        checked += 1
        d = pd.DataFrame(kl).iloc[:, :6]
        d.columns = ["date", "open", "close", "high", "low", "vol"]
        for c in ("open", "close", "high", "low", "vol"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        # 腾讯日 K 无成交额：vol(手)*close/1000≈千元（当日 bar 近似，影响
        # 20 日成交额和约 1/20≈5%，候选复核可容忍；正式口径仍以湖 amt 为准）
        d["amount"] = d["vol"] * d["close"] / 1000.0
        try:
            out = quant_double_knife(d.set_index("date"), profile="strict")
        except Exception:
            continue
        last = out.iloc[-1]
        if bool(last["double_knife_signal"]):
            tail_signals.append({
                "code": code,
                "close": round(float(d["close"].iloc[-1]), 2),
                "regime": str(last["regime_label"]),
                "note": "盘中实时 bar 复核通过 · 14:55 执行窗口",
            })
    return tail_signals, checked, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent-window", type=int, default=5,
                    help="近 N 日信号窗口（默认 5）")
    ap.add_argument("--intraday", action="store_true",
                    help="尾盘模式（14:40）：候选池+当日实时 bar 复核，"
                         "产出 14:55 执行窗口的尾盘买信号")
    args = ap.parse_args()
    intraday = args.intraday

    t0 = time.time()
    files = sorted(glob.glob(str(ASHARE / "*.parquet")))
    sig_now, recent, regime_now, skipped = [], [], {}, 0
    star_pool = []  # 昨日单日十字星全量候选池（尾盘两段式第一段）
    for i, fp in enumerate(files):
        try:
            code, info = scan_one(fp)
        except Exception:
            skipped += 1
            continue
        if info is not None and info.get("star_yesterday"):
            star_pool.append(info["code"])
        if info is None:
            skipped += 1
            continue
        regime_now[info["regime"]] = regime_now.get(info["regime"], 0) + 1
        if info["signal_now"]:
            sig_now.append(info)
        elif info["n_recent"] > 0:
            recent.append(info)
        if (i + 1) % 1000 == 0:
            print("[scan] %d/%d 已扫 %.0fs" % (i + 1, len(files), time.time() - t0), flush=True)

    # 尾盘两段式（K-STOCK-025）：14:40 盘中触发时，用「昨日单日十字星」
    # 候选池 + 当日实时 bar 复核，产出今日尾盘买信号（14:55 执行窗口）。
    # 非盘中（腾讯末根≠当日）自动退化为空并留 note，不误报。
    tail_signals, tail_checked, tail_note = [], 0, ""
    star_candidates = [info["code"] for info in recent + sig_now
                       if info.get("star_yesterday")]
    # 候选池不足时补扫一遍湖里昨日单日十字星票（recent 仅含已触发票）
    if intraday and star_pool:
        star_candidates = sorted(set(star_candidates) | set(star_pool))
    if intraday:
        tail_signals, tail_checked, tail_note = intraday_recheck(star_candidates)
    cache = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "scanned": len(files) - skipped,
        "skipped": skipped,
        "signals_now": sig_now,
        "signals_recent": recent,
        "regime_now": regime_now,
        "star_pool_n": len(star_pool),
        "intraday_mode": intraday,
        "tail_signals": tail_signals,
        "tail_checked": tail_checked,
        "tail_note": tail_note,
        "disclaimer": "研究态：未回测验证（T-20261009-009 结论前不构成选股依据）",
    }
    # 信号票补详情（K-STOCK-026 老田反馈：名称/涨跌/市值/换手）
    for info in sig_now + recent:
        try:
            # norm_symbol 不认带点后缀（600887.SZ→sh600887. 坏 URL），先剥
            plain = info["code"].split(".")[0]
            q = quote_snapshot(plain)
            info["name"] = q["name"]
            info["change_pct"] = q["change_pct"]
            info["turnover_pct"] = q["turnover_pct"]
            info["mktcap_yi"] = q["mktcap_yi"]
        except Exception:
            info["name"] = info.get("name", "")
    # 当日信号票加技术状态摘要（近5日票不拉，省请求）
    for info in sig_now:
        try:
            ctx = make_context(info["code"].split(".")[0])
            mt = ctx.get("market_technical", "")
            import re as _re
            arr = _re.search(r"(多头排列|空头排列|纠缠)", mt)
            c5 = _re.search(r"近5日涨跌 ([+\-]?[\d.]+)%", mt)
            vr = _re.search(r"量比\(对5日均量\) ([\d.]+)", mt)
            p20 = _re.search(r"现价距20日高点 ([+\-]?[\d.]+)%", mt)
            parts = []
            if arr:
                parts.append(arr.group(1))
            if c5:
                parts.append("近5日%s%%" % c5.group(1))
            if vr:
                parts.append("量比%s" % vr.group(1))
            if p20:
                parts.append("距20日高%s%%" % p20.group(1))
            info["tech"] = " · ".join(parts)
        except Exception:
            info["tech"] = ""

    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[done] 扫描 %d 只（跳过 %d）：当日信号 %d，近 %d 日有信号 %d，"
          "尾盘候选池 %d，尾盘复核 %d 只→信号 %d %s，耗时 %.0fs → %s"
          % (cache["scanned"], skipped, len(sig_now), args.recent_window,
             len(recent), len(star_pool), tail_checked, len(tail_signals),
             ("(%s)" % tail_note) if tail_note else "", cache["elapsed_s"], CACHE))


if __name__ == "__main__":
    import pandas as pd  # noqa: E402  顶部不 import：让 --help 也不依赖 venv
    main()
