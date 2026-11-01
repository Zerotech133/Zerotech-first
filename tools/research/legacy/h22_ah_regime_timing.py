# -*- coding: utf-8 -*-
"""H22 · A/H 高离散 regime 择时。

依据 H16：A/H 溢价横截面离散度高时（PIT 250 日分位 rank≥0.5），溢价收敛更强
（高离散组 OOS -13.9% vs 低离散 -2.3%）。

策略：每日横截面按 60 日 z(premium) 选 top-tercile「高溢价」pair，做「空 A / 多 H」
（收益 = -rel_ret，rel_ret = A 相对 H 收益）；仅在离散 regime on 时持仓；成本 30bp/换手。
对照：always-on（每天都做）与 regime-off。

数据：daily + hk_daily + fx（2023-09 起）；IS ≤2024-12-31 / OOS ≥2025-01-01。
产出：research/reports/H22_ah_regime_timing_20261030.{md,json}；台账 H22。
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
ah = importlib.import_module("ah_premium")
ah.START = "20230918"
ah.END = "20261029"
ah.IS_END = "20241231"
ah.COST = 0.003

OUT = REPO / "research" / "reports"
IS_CUT = "20241231"
COST = 0.003
DISP_WIN = 250
Z_WIN = 60
MIN_PAIRS = 20


def perf(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) < 30:
        return {"n": int(len(ret))}
    nav = (1 + ret).cumprod()
    ann = float(nav.iloc[-1] ** (252 / len(ret)) - 1)
    vol = float(ret.std(ddof=1) * np.sqrt(252))
    sharpe = float(ret.mean() / ret.std(ddof=1) * np.sqrt(252)) if ret.std(ddof=1) else 0.0
    dd = float((nav / nav.cummax() - 1).min())
    t = float(ret.mean() / ret.std(ddof=1) * np.sqrt(len(ret)))
    return {"n": int(len(ret)), "ann_pct": round(ann * 100, 2), "vol_pct": round(vol * 100, 2),
            "sharpe": round(sharpe, 2), "mdd_pct": round(dd * 100, 2), "t": round(t, 2),
            "win_pct": round(float((ret > 0).mean()) * 100, 1)}


def main() -> int:
    pairs = ah.load_pairs()
    fx = ah.load_fx()
    panel = ah.build_panel(pairs, fx)
    print("panel rows:", len(panel), "pairs:", panel["name"].nunique(),
          "dates:", panel["trade_date"].nunique(), flush=True)
    prem = panel.pivot_table(index="trade_date", columns="name", values="premium")
    rel = panel.pivot_table(index="trade_date", columns="name", values="rel_ret")
    dates = list(prem.index)
    # PIT：60 日 z
    z = (prem - prem.rolling(Z_WIN, min_periods=40).mean()) / prem.rolling(Z_WIN, min_periods=40).std()
    # 离散度 regime：横截面 std(premium)，PIT 250 日分位 rank
    disp = prem.std(axis=1)
    disp_rank = disp.rolling(DISP_WIN, min_periods=120).rank(pct=True)
    regime = (disp_rank >= 0.5)
    print("dispersion regime on days:", int(regime.sum()), "/", len(regime), flush=True)

    # 高溢价 top-tercile 等权「空A/多H」组合日收益（T+1 入场：用当日 z 决定，次日收益）
    daily_ret, holdings = [], []
    for i, dt in enumerate(dates[:-1]):
        zz = z.loc[dt].dropna()
        if len(zz) < MIN_PAIRS:
            daily_ret.append(0.0)
            holdings.append(0)
            continue
        top = zz[zz >= zz.quantile(0.67)].index
        top = [c for c in top if c in rel.columns]
        if not top:
            daily_ret.append(0.0)
            holdings.append(0)
            continue
        nxt = dates[i + 2] if i + 2 < len(dates) else None
        if nxt is None:
            daily_ret.append(0.0)
            holdings.append(0)
            continue
        r = -rel.loc[nxt, top].mean()  # 空A/多H（信号T收盘→T+1收盘执行，赚T+2起）
        daily_ret.append(0.0 if pd.isna(r) else float(r))
        holdings.append(len(top))
    strat = pd.Series(daily_ret, index=[d for d in dates[:-2]] + [dates[-2]])  # 最后一日置0占位
    strat.iloc[-1] = 0.0
    hold = pd.Series(holdings, index=dates[:-1])
    # 只在 regime on 时持仓（当日 regime 已知→次日执行；为简化按当日 regime）
    on = pd.Series(regime.reindex(dates[:-1]).fillna(False).values, index=dates[:-1])
    ret_timing = strat.where(on, 0.0)
    # 换手成本：仓位变化（0↔1 切换）按 30bp
    in_pos = on.astype(int)
    turn = in_pos.diff().abs().fillna(in_pos.iloc[0])
    ret_timing = ret_timing - turn * COST
    ret_always = strat - strat.abs().fillna(0) * 0  # always-on 无切换成本
    ret_off = strat.where(~on, 0.0)

    def split(r: pd.Series):
        return {"all": perf(r),
                "IS": perf(r[r.index <= IS_CUT]),
                "OOS": perf(r[r.index > IS_CUT])}

    res = {"meta": {"pairs": int(panel["name"].nunique()), "rows": int(len(panel)),
                    "start": ah.START, "end": ah.END, "is_end": IS_CUT, "cost": COST,
                    "disp_win": DISP_WIN, "z_win": Z_WIN, "min_pairs": MIN_PAIRS,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "regime_days": int(regime.sum()),
           "avg_holdings": round(float(hold.mean()), 1),
           "timing": split(ret_timing), "always": split(ret_always), "off": split(ret_off)}
    oos = res["timing"]["OOS"]
    timing_ok = oos.get("ann_pct", -99) > 0 and (oos.get("t") or 0) >= 2
    beats_always = (oos.get("sharpe") or 0) > (res["always"]["OOS"].get("sharpe") or -99)
    # 假设不只看「择时为正」，必须优于 always-on（否则 regime 无增量价值）
    status = "backtested" if (timing_ok and beats_always) else "rejected"
    res["status"] = status
    res["beats_always_sharpe"] = bool(beats_always)

    js = OUT / "H22_ah_regime_timing_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "年化 %s%% / 夏普 %s / MDD %s%% / t=%s" % (
            x.get("ann_pct"), x.get("sharpe"), x.get("mdd_pct"), x.get("t")) if x.get("n", 0) >= 30 else "n<30"

    L = ["# H22 · A/H 高离散 regime 择时", "",
         "- 样本：%s ~ %s；%d 对；离散 regime（PIT %d 日分位 rank≥0.5）on %d 天；策略=高溢价(z≥0.67) pair 空A/多H，仅 regime on 持仓；成本 30bp/切换" % (
             ah.START, ah.END, res["meta"]["pairs"], DISP_WIN, res["regime_days"]),
         "- 平均持仓 %s 对/日" % res["avg_holdings"], "",
         "| 策略 | 全样本 | IS≤2024 | OOS≥2025 |", "|---|---|---|---|"]
    for name, key in (("Regime 择时（on 时持仓）", "timing"), ("Always-on", "always"), ("Regime off（仅对照）", "off")):
        L.append("| %s | %s | %s | %s |" % (name, f(res[key]["all"]), f(res[key]["IS"]), f(res[key]["OOS"])))
    L += ["", "## 结论", "- 状态：**%s**（OOS：%s）" % (status, f(oos)),
          "- 择时 vs Always-on（OOS 夏普）：%s vs %s → %s" % (
              oos.get("sharpe"), res["always"]["OOS"].get("sharpe"),
              "择时更优" if beats_always else "择时未更优"), "",
          "## 局限", "- hk_daily 2023-09 起；regime 用当日收盘计算（实盘次日触发，简化）；",
          "- 未按行业中性；空 A 需融券可得性；成本按切换 30bp 未计冲击。"]
    (OUT / "H22_ah_regime_timing_20261030.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"status": status, "timing_OOS": oos, "always_OOS": res["always"]["OOS"],
                      "beats_always": beats_always, "md": str(OUT / "H22_ah_regime_timing_20261030.md")},
                     ensure_ascii=False, indent=1), flush=True)

    import sqlite3
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    exists = con.execute("SELECT id FROM hypotheses WHERE id='H22'").fetchone()
    row = {"id": "H22", "title": "A/H 高离散 regime 择时",
           "question": "A/H 溢价横截面离散高时再做「空A/多H」溢价收敛，能否提升风险收益？",
           "data_sources": "daily+hk_daily+fx_daily",
           "method": "PIT 250日离散分位 rank>=0.5 择时；60日z top-tercile 空A/多H；IS<=2024/OOS>=2025；30bp切换成本",
           "gates": "PIT=部分（当日收盘信号次日执行简化）；OOS=独立；成本=30bp；容量=A/H配对",
           "status": status, "owner": "DSH-Lead",
           "conclusion": "OOS 择时 %s；Always-on OOS 夏普 %s；择时%s → %s" % (
               f(oos), res["always"]["OOS"].get("sharpe"), "更优" if beats_always else "未更优", status),
           "evidence_path": "research/reports/H22_ah_regime_timing_20261030.md",
           "created_at": now, "updated_at": now}
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    keys = [k for k in row if k in cols]
    if exists:
        con.execute("UPDATE hypotheses SET %s WHERE id='H22'" % ",".join("%s=?" % k for k in keys if k != "id"),
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print("H22 已写台账", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
