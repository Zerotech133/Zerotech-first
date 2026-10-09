# -*- coding: utf-8 -*-
"""qd_daily_snapshot.py — 全市场基础数据每日快照（K-STOCK-028）

老田定：每天选完股以后，全市场基础数据要跑一道。
时序：14:40 尾盘扫描（影子层）→ 14:55 本脚本（基础数据层）落当日档案。

数据源组合（口径对齐 engine/tools.make_context）：
  - 腾讯批量实时行情 qt.gtimg.cn（60 只/批，全市场约 92 请求）：
    name/close/change_pct/turnover_pct/mktcap_yi/vol(手)
  - 特征湖 parquet（本地只读 close/volume）：MA5/10/20 排列、
    近5日涨跌、距20日高、5日均量 → 量比=实时vol/湖5日均量
输出：portal/qd_daily_data/snap_YYYYMMDD.json（每日一份档案）
性质：纯数据快照，零信号产出、零影子链路触碰；指数文件按代码规则
（sh→6开头/sz→0,3开头）自动排除。

用法（须 venv 解释器，需 pandas）：
    envs/default/Scripts/python.exe portal/qd_daily_snapshot.py [--date YYYYMMDD]
"""
import argparse
import glob
import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
ASHARE = ROOT / "quant_backtest" / "data" / "ashare"
OUT_DIR = Path(__file__).parent / "qd_daily_data"

import urllib.request
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理直连

BATCH = 60  # 腾讯批量行情每批票数


def is_stock(code_plain):
    """指数过滤（T-009 同款规则）：sh 必须 6 开头，sz 必须 0/3 开头"""
    return (code_plain.startswith("6") if "_SH" in code_plain
            else code_plain[0] in ("0", "3"))


def fetch_quotes(plain_codes):
    """腾讯批量行情：{plain_code: {name,close,change_pct,turnover_pct,mktcap_yi,vol}}"""
    out = {}
    for i in range(0, len(plain_codes), BATCH):
        batch = plain_codes[i:i + BATCH]
        syms = [("sh" if c.startswith("6") else "sz") + c for c in batch]
        try:
            raw = _op.open("https://qt.gtimg.cn/q=" + ",".join(syms),
                           timeout=15).read().decode("gbk", "ignore")
        except Exception:
            continue
        for ln in raw.strip().split(";"):
            ln = ln.strip()
            if "=" not in ln:
                continue
            sym = ln.split("=")[0].replace("v_", "").strip()
            f = ln.split("~")
            if len(f) < 46:
                continue
            def _f(idx):
                try:
                    return float(f[idx])
                except (ValueError, IndexError):
                    return 0.0
            out[sym[2:]] = {
                "name": f[1],
                "close": round(_f(3), 2),
                "change_pct": round(_f(32), 2),
                "turnover_pct": round(_f(38), 2),
                "mktcap_yi": round(_f(45), 1),
                "vol": _f(36),  # 手（实时累计）
            }
        if (i // BATCH) % 20 == 0:
            print("[quote] %d/%d 批 %.0fs" % (i // BATCH + 1,
                  (len(plain_codes) + BATCH - 1) // BATCH, time.time() - t0),
                  flush=True)
    return out


def tech_from_lake(fp, live_vol):
    """湖算技术状态摘要（口径=make_context）：排列/近5日/距20日高/量比"""
    df = pd.read_parquet(fp, columns=["close", "volume"])
    closes = df["close"].tolist()
    vols = df["volume"].tolist()
    if len(closes) < 21:
        return None
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    arrange = ("多头排列" if ma5 > ma10 > ma20
               else "空头排列" if ma5 < ma10 < ma20 else "纠缠")
    chg5 = round((closes[-1] / closes[-6] - 1) * 100, 2)
    pos20 = round((closes[-1] / max(closes[-20:]) - 1) * 100, 2)
    avg5 = sum(vols[-5:]) / 5
    vol_ratio = round(live_vol / avg5, 2) if (avg5 and live_vol) else None
    parts = [arrange, "近5日%+.2f%%" % chg5]
    if vol_ratio is not None:
        parts.append("量比%.2f" % vol_ratio)
    parts.append("距20日高%+.2f%%" % pos20)
    return " · ".join(parts), vol_ratio


def main():
    global t0, pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="档案日期 YYYYMMDD（默认今天）")
    args = ap.parse_args()
    t0 = time.time()
    date = args.date or datetime.now().strftime("%Y%m%d")

    import pandas as pd  # 顶部不 import：--help 不依赖 venv

    files = sorted(glob.glob(str(ASHARE / "*.parquet")))
    # 指数排除：文件名 600519_SH / 000001_SZ；sh 必须 6 开头，sz 必须 0/3 开头
    codes = [f for f in files
             if (Path(f).stem.endswith("_SH") and Path(f).stem[:1] == "6")
             or (Path(f).stem.endswith("_SZ") and Path(f).stem[:1] in ("0", "3"))]
    plain_all = [Path(f).stem.split("_")[0] for f in codes]
    print("[lake] 股票文件 %d（指数已排除，共 %d 文件）" % (len(codes), len(files)), flush=True)

    quotes = fetch_quotes(plain_all)
    print("[quote] 拿到行情 %d/%d，耗时 %.0fs" % (len(quotes), len(plain_all),
          time.time() - t0), flush=True)

    rows, miss_tech = {}, 0
    for fp, plain in zip(codes, plain_all):
        q = quotes.get(plain)
        if q is None:
            continue
        try:
            tech, vol_ratio = tech_from_lake(fp, q["vol"]) or (None, None)
        except Exception:
            tech, vol_ratio = None, None
            miss_tech += 1
        rows[plain + ("_SH" if plain.startswith("6") else "_SZ")] = {
            "name": q["name"],
            "close": q["close"],
            "change_pct": q["change_pct"],
            "turnover_pct": q["turnover_pct"],
            "mktcap_yi": q["mktcap_yi"],
            "vol_ratio": vol_ratio,
            "tech": tech or "",
        }

    OUT_DIR.mkdir(exist_ok=True)
    out_p = OUT_DIR / ("snap_%s.json" % date)
    doc = {
        "date": date,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "n": len(rows),
        "miss_tech": miss_tech,
        "source": "腾讯批量实时行情 + 特征湖 parquet 技术派生（口径=make_context）",
        "rows": rows,
    }
    out_p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    up = sum(1 for r in rows.values() if r["change_pct"] > 0)
    print("[done] %s 基础数据 %d 只（涨 %d / 跌 %d，技术状态缺失 %d），耗时 %.0fs → %s"
          % (date, len(rows), up, len(rows) - up, miss_tech,
             time.time() - t0, out_p), flush=True)


if __name__ == "__main__":
    main()
