# -*- coding: utf-8 -*-
"""H15 · A/H 溢价收敛研究（汇率换算 + 港股日线 + A 股日线）。

思路：同一公司 A 股 vs H 股（H 股按 当日 HKD→CNY 汇率换算）存在溢价；
测试「溢价水平/溢价 z-score」是否可预测未来相对收益（收敛）。

数据：tushare/stock_basic + hk_basic（名称配对，收益相关过滤）、daily、hk_daily、
      fx_daily（东财 USDCNH/USDHKD，2010-2026，已补齐）。
门槛：PIT（T 收盘信号，T+1 收盘执行）、OOS 独立、成本 0.3%/次换手、样本≥20 对/日。
产出：research/reports/AH_premium_20261030.{md,json}；H15 写入 hypotheses.db。
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20230918", "20261029"
IS_END = "20250630"
COST = 0.003
MIN_PAIRS = 20


def _norm(name: str) -> str:
    s = str(name or "").strip().upper()
    for a, b in (("Ａ", "A"), (" ", ""), ("\u3000", ""), ("-", ""), ("－", ""), ("*", ""), ("ＳＴ", "ST")):
        s = s.replace(a, b)
    return s


def load_pairs() -> pd.DataFrame:
    hk = pq.read_table(glob.glob(str(DATA / "hk_basic" / "*.parquet"))[0]).to_pandas()
    a = pq.read_table(glob.glob(str(DATA / "stock_basic" / "*.parquet"))[0]).to_pandas()
    hk["k"] = hk["name"].map(_norm)
    a["k"] = a["name"].map(_norm)
    m = a.merge(hk, on="k", suffixes=("_a", "_h"))
    out = m[["ts_code_a", "ts_code_h", "name_a"]].copy()
    out.columns = ["a_code", "h_code", "name"]
    print("名称配对:", len(out), flush=True)
    return out


def load_daily(codes: set[str], prefix: str, cols=("ts_code", "trade_date", "close")) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / prefix / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=list(cols))
        mask = pc.is_in(tb["ts_code"], value_set=__import__("pyarrow").array(list(codes)))
        tb = tb.filter(mask)
        if tb.num_rows:
            frames.append(tb.to_pandas())
    if not frames:
        return pd.DataFrame(columns=list(cols))
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def load_fx() -> pd.DataFrame:
    cn = pq.read_table(DATA / "fx_daily" / "USDCNH_FXCM.parquet").to_pandas()
    hk = pq.read_table(DATA / "fx_daily" / "USDHKD_FXCM.parquet").to_pandas()
    cn["trade_date"] = cn["trade_date"].astype(str)
    hk["trade_date"] = hk["trade_date"].astype(str)
    m = cn.merge(hk, on="trade_date", suffixes=("_cn", "_hk"))
    m["hkd_cny"] = m["close_cn"] / m["close_hk"]
    return m[["trade_date", "hkd_cny"]]


def build_panel(pairs: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    a = load_daily(set(pairs["a_code"]), "daily")
    h = load_daily(set(pairs["h_code"]), "hk_daily")
    a = a.rename(columns={"ts_code": "a_code", "close": "a_close"})
    h = h.rename(columns={"ts_code": "h_code", "close": "h_close"})
    df = a.merge(h, on="trade_date", how="inner")
    df = df.merge(fx, on="trade_date", how="inner")
    df = df[df["a_close"].notna() & df["h_close"].notna() & df["hkd_cny"].notna()]
    # 只保留有效的 A/H 配对
    df = df.merge(pairs, on=["a_code", "h_code"], how="inner")
    df["h_cny"] = df["h_close"] * df["hkd_cny"]
    df["premium"] = df["a_close"] / df["h_cny"] - 1.0
    df = df.sort_values(["name", "trade_date"])
    g = df.groupby("name")
    df["a_ret"] = g["a_close"].pct_change()
    df["h_ret_cny"] = g["h_cny"].pct_change()
    df["rel_ret"] = df["a_ret"] - df["h_ret_cny"]
    # 每对收益相关性过滤（配对正确性校验）
    corr = df.groupby("name")[["a_ret", "h_ret_cny"]].apply(
        lambda x: x["a_ret"].corr(x["h_ret_cny"]) if len(x) > 30 else np.nan)
    good = set(corr[corr >= 0.3].index)
    print("配对收益相关>=0.3:", len(good), "/", df["name"].nunique(), flush=True)
    return df[df["name"].isin(good)].copy()


def run() -> dict:
    pairs = load_pairs()
    fx = load_fx()
    df = build_panel(pairs, fx)
    print("面板 rows:", len(df), "pairs:", df["name"].nunique(),
          "dates:", df["trade_date"].nunique(), flush=True)
    prem = df.pivot_table(index="trade_date", columns="name", values="premium")
    rel = df.pivot_table(index="trade_date", columns="name", values="rel_ret")
    # 60 日 z-score（PIT：只用当日及以前）
    mean = prem.rolling(60, min_periods=40).mean()
    std = prem.rolling(60, min_periods=40).std()
    z = (prem - mean) / std
    # 累加相对收益，用于 T+1 收盘入场后的 5/20 日远期相对收益
    cum = (1 + rel.fillna(0)).cumprod()
    res = {"meta": {"pairs": int(df["name"].nunique()), "rows": int(len(df)),
                    "start": str(df["trade_date"].min()), "end": str(df["trade_date"].max()),
                    "is_end": IS_END, "cost": COST, "min_pairs": MIN_PAIRS,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}}
    res["premium_stats"] = {"mean": float(prem.stack().mean()), "median": float(prem.stack().median()),
                            "p25": float(prem.stack().quantile(0.25)), "p75": float(prem.stack().quantile(0.75))}
    for horizon in (5, 20):
        fwd = cum.shift(-(horizon + 1)) / cum.shift(-1) - 1
        # 每日横截面 IC：z 越高（溢价越高）→ 预期 A 相对 H 越弱（负相关=收敛）
        ics, ics_dates = [], []
        spreads, spr_dates = [], []
        for dt in fwd.index:
            if dt not in z.index:
                continue
            zz = z.loc[dt].dropna()
            ff = fwd.loc[dt].dropna()
            common = zz.index.intersection(ff.index)
            if len(common) < MIN_PAIRS:
                continue
            ics.append(float(pd.Series(zz[common]).corr(pd.Series(ff[common]), method="spearman")))
            ics_dates.append(str(dt))
            # 按「溢价 z」分组（高溢价组 vs 低溢价组），而非按未来收益分组
            top = zz[common].quantile(0.67)
            bot = zz[common].quantile(0.33)
            hi = common[zz[common] >= top]
            lo = common[zz[common] <= bot]
            if len(hi) >= 3 and len(lo) >= 3:
                # 低溢价组做多A空H - 高溢价组做多A空H（收敛方向）
                spreads.append(float(ff[lo].mean() - ff[hi].mean()))
                spr_dates.append(str(dt))
        ics = pd.Series(ics, index=ics_dates, dtype=float)
        spr = pd.Series(spreads, index=spr_dates, dtype=float)
        cut = IS_END.replace("-", "")
        def stat(s):
            if len(s) < 20:
                return {"n": 0}
            t = s.mean() / s.std(ddof=1) * np.sqrt(len(s))
            return {"n": int(len(s)), "mean": float(s.mean()), "t": float(t)}
        out = {"ic": {"all": stat(ics), "is": stat(ics[[i for i in ics.index if i <= cut]]),
                      "oos": stat(ics[[i for i in ics.index if i > cut]])},
               "ls_spread": {"all": stat(spr), "is": stat(spr[[i for i in spr.index if i <= cut]]),
                             "oos": stat(spr[[i for i in spr.index if i > cut]])}}
        # 成本后年化（20日窗口换手：年化=均值/持有天数*252 - 成本*换手频率）
        if len(spr):
            gross_ann = float(spr.mean()) * 252.0 / horizon
            net_ann = gross_ann - COST * (252.0 / horizon)
            out["ls_annual"] = {"gross": round(gross_ann * 100, 2), "net_pct": round(net_ann * 100, 2),
                                "cost_pct_per_turn": COST * 100}
        res[f"h{horizon}"] = out
    return res


def register_h15(res: dict, evidence: str) -> None:
    db = REPO / "research" / "hypotheses.db"
    con = sqlite3.connect(str(db))
    cols = [r[1] for r in con.execute("PRAGMA table_info(hypotheses)")]
    h20 = res.get("h20", {})
    oos_t = (h20.get("ic", {}).get("oos") or {}).get("t")
    status = "backtested" if (oos_t is not None and abs(oos_t) >= 2) else "rejected"
    conclusion = ("A/H 溢价收敛 OOS IC t=%s" % (round(oos_t, 2) if oos_t is not None else "NA"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    row = {"id": "H15", "title": "A/H 溢价收敛（汇率换算后）",
           "question": "A/H 溢价水平（z-score）能否预测未来相对收益（收敛）？",
           "data_sources": "daily+hk_daily+fx_daily(东财 USDCNH/USDHKD 2010-2026)+stock_basic+hk_basic",
           "method": "名称配对156对（收益相关≥0.3）；溢价=A/(H×HKDCNY)-1；60日z；T收盘信号T+1收盘执行；5/20日远期相对收益；IS 2023-09-18..2025-06-30 / OOS 2025-07-01..2026-10-29；成本0.3%/次",
           "gates": "PIT=是；OOS=独立；成本=0.3%；容量≈150对（小）",
           "status": status, "owner": "DSH-Lead", "conclusion": conclusion,
           "evidence_path": evidence, "created_at": now, "updated_at": now}
    keys = [k for k in row if k in cols]
    existing = con.execute("SELECT id FROM hypotheses WHERE id='H15'").fetchone()
    if existing:
        con.execute("UPDATE hypotheses SET %s WHERE id='H15'" % ",".join("%s=?" % k for k in keys if k != "id"),  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                    [row[k] for k in keys])
    con.commit()
    con.close()


def main(argv=None) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    res = run()
    stamp = "20261030"
    js = OUT / ("AH_premium_%s.json" % stamp)
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    h5, h20 = res.get("h5", {}), res.get("h20", {})
    md = OUT / ("AH_premium_%s.md" % stamp)
    lines = [
        "# H15 · A/H 溢价收敛研究（%s）" % stamp,
        "",
        "## 数据口径",
        "- 配对：stock_basic × hk_basic 名称匹配 %d 对，收益相关≥0.3 过滤" % res["meta"]["pairs"],
        "- 样本：%s ~ %s，%d 行；H 股价格按当日 HKD→CNY（USDCNH/USDHKD）换算" % (
            res["meta"]["start"], res["meta"]["end"], res["meta"]["rows"]),
        "- 溢价 = A收盘 / (H收盘 × HKDCNY) − 1；信号：60 日滚动 z-score（PIT）",
        "- 执行：T 收盘信号，T+1 收盘执行；远期 5/20 日相对收益（A−H，人民币）",
        "- 成本：0.3%/次换手；每日样本 ≥20 对",
        "",
        "## 溢价水平",
        "均值 %.2f%% / 中位 %.2f%% / P25 %.2f%% / P75 %.2f%%" % tuple(
            [res["premium_stats"][k] * 100 for k in ("mean", "median", "p25", "p75")]),
        "",
        "## 结果",
        "| 窗口 | IC 均值(t) | IS t | OOS t | L/S 年化毛收益 | 成本后年化 |",
        "|---|---|---|---|---|---|",
    ]
    for h, name in ((5, "5 日"), (20, "20 日")):
        r = res.get("h%d" % h, {})
        ic, ls = r.get("ic", {}), r.get("ls_spread", {})  # noqa: F841 - 既有未使用变量（批2不改行为）
        ann = r.get("ls_annual", {})
        def f(x):
            return "%.3f(%s)" % (x["mean"], round(x["t"], 2)) if x and x.get("n") else "—"
        lines.append("| %s | %s | %s | %s | %s%% | %s%% |" % (
            name, f(ic.get("all")), f(ic.get("is")), f(ic.get("oos")),
            ann.get("gross", "—"), ann.get("net_pct", "—")))
    lines += ["", "## 结论", "- 见 H15 台账（hypotheses.db）与 JSON 明细。"]
    md.write_text("\n".join(lines), encoding="utf-8")
    register_h15(res, str(md.relative_to(REPO)).replace("\\", "/"))
    print(json.dumps({"json": str(js), "md": str(md),
                      "h5_ic_oos": (h5.get("ic", {}).get("oos") or {}).get("t"),
                      "h20_ic_oos": (h20.get("ic", {}).get("oos") or {}).get("t"),
                      "premium_mean": res["premium_stats"]["mean"]}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
