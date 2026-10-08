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
from tools import quote_snapshot  # noqa: E402  信号票拉名称（K-STOCK-024）

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
    }
    return code, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent-window", type=int, default=5,
                    help="近 N 日信号窗口（默认 5）")
    args = ap.parse_args()

    t0 = time.time()
    files = sorted(glob.glob(str(ASHARE / "*.parquet")))
    sig_now, recent, regime_now, skipped = [], [], {}, 0
    for i, fp in enumerate(files):
        try:
            code, info = scan_one(fp)
        except Exception:
            skipped += 1
            continue
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

    cache = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "scanned": len(files) - skipped,
        "skipped": skipped,
        "signals_now": sig_now,
        "signals_recent": recent,
        "regime_now": regime_now,
        "disclaimer": "研究态：未回测验证（T-20261009-009 结论前不构成选股依据）",
    }
    # 信号票补名称（对齐青萍之末/缠论池行展示；仅信号票逐个拉快照，量小）
    for info in sig_now + recent:
        try:
            # norm_symbol 不认带点后缀（600887.SZ→sh600887. 坏 URL），先剥
            info["name"] = quote_snapshot(info["code"].split(".")[0])["name"]
        except Exception:
            info["name"] = ""
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[done] 扫描 %d 只（跳过 %d）：当日信号 %d，近 %d 日有信号 %d，耗时 %.0fs → %s"
          % (cache["scanned"], skipped, len(sig_now), args.recent_window,
             len(recent), cache["elapsed_s"], CACHE))


if __name__ == "__main__":
    import pandas as pd  # noqa: E402  顶部不 import：让 --help 也不依赖 venv
    main()
