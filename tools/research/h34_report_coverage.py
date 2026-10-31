# -*- coding: utf-8 -*-
"""H34-H36 · 研报覆盖/关注度因子（research_report 元数据，无需解析 PDF）。

数据集：quant_backtest/data/tushare/research_report（36 shard，个股研报，含 ts_code/inst_csname/trade_date）
信号（T 日收盘可知，T+1 收盘入场）：
  H34 关注度峰值反转：过去 30 日个股研报数 横截面分位 ≥0.9（高关注）→ 预期未来 20 日跑输
  H35 首次覆盖：过去 180 日无研报 → 今日首次出现（initiation）→ 预期未来 20 日跑赢
  H36 机构广度：过去 30 日覆盖机构数 ≥3（多家机构关注）→ 预期未来 20 日跑赢
口径：20/60 日 CAR（相对等权市场、成本 30bp）；按日聚合 t + block bootstrap；IS≤2023 / OOS≥2024。
产出：research/reports/H34-36_report_coverage_20261031.{md,json}；台账 H34-H36。
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
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(REPO / "tools" / "research"))
import stat_utils  # noqa: E402

START, END = "20210101", "20261029"
IS_END = "20231231"
COST = 0.003
HORIZONS = (20, 60)


def load_reports() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "research_report" / "*.parquet"))):
        df = pq.read_table(f).to_pandas()
        df = df[df["ts_code"].notna() & df["trade_date"].astype(str).str.match(r"^\d{8}$")]
        frames.append(df[["ts_code", "trade_date", "inst_csname", "title"]])
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    d = d[(d["trade_date"] >= "20200601") & (d["trade_date"] <= END)]
    d = d.drop_duplicates(subset=["ts_code", "trade_date", "inst_csname", "title"])
    return d


def load_daily():
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not ("20190101" <= dt <= "20261130"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg", "amount"])
        df = tb.to_pandas()
        df = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def main() -> int:
    rep = load_reports()
    px = load_daily()
    print("reports:", len(rep), "codes:", rep["ts_code"].nunique(), "daily rows:", len(px), flush=True)
    # 覆盖期守卫：研报数据必须覆盖≥18个月且起点早于 END-12个月，否则不做 IS/OOS 检验
    dmin, dmax = rep["trade_date"].min(), rep["trade_date"].max()
    months = (int(dmax[:4]) - int(dmin[:4])) * 12 + (int(dmax[4:6]) - int(dmin[4:6]))
    print("report coverage:", dmin, "~", dmax, "months=", months, flush=True)
    if months < 18:
        print("DATA_INSUFFICIENT: 研报数据仅覆盖 %s~%s（%d 个月），无法做多年 IS/OOS 检验，已停止。" % (dmin, dmax, months), flush=True)
        out = OUT / "H34-36_report_coverage_20261031.json"
        OUT.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"meta": {"data_insufficient": True, "coverage": [dmin, dmax], "months": months,
                                           "reports": int(len(rep)), "codes": int(rep["ts_code"].nunique()),
                                           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
                                    "hypotheses": {}}, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0

    # 每股票每日研报计数/机构集合
    gd = rep.groupby(["ts_code", "trade_date"]).agg(n_rep=("title", "count"),
                                                     insts=("inst_csname", lambda s: tuple(sorted(set(s.dropna())))))
    gd = gd.reset_index()
    # 与交易日对齐：只保留有行情的股票日
    codes = px[["ts_code"]].drop_duplicates()
    gd = gd.merge(codes, on="ts_code", how="inner")

    # 逐股票的滚动聚合（30d 报告数/机构数、180d 是否有研报）
    rep_daily = px[["ts_code", "trade_date"]].merge(gd, on=["ts_code", "trade_date"], how="left")
    rep_daily["n_rep"] = rep_daily["n_rep"].fillna(0).astype(int)
    rep_daily["insts"] = rep_daily["insts"].apply(lambda x: x if isinstance(x, tuple) else ())
    g = rep_daily.groupby("ts_code", sort=False)
    rep_daily["rep_30"] = g["n_rep"].transform(lambda s: s.rolling(30, min_periods=1).sum())
    rep_daily["rep_180"] = g["n_rep"].transform(lambda s: s.rolling(180, min_periods=1).sum())
    # 30 日机构广度：展开机构出现次数（用 set 大小近似：每日机构数累加去重成本高，改用出现记录）
    # 简化：30 日覆盖机构数 ≈ 30 日窗口内 unique 机构数（用 rep 表按日期倒查实现）
    inst_daily = rep.assign(inst=rep["inst_csname"]).groupby(["ts_code", "trade_date"])["inst"].apply(lambda s: set(s.dropna()))
    inst30 = {}
    for code, s in inst_daily.groupby(level=0):
        dates = s.index.get_level_values(1).tolist()
        vals = s.tolist()
        # 30 交易日窗口近似：用交易日索引查前面 30 个有记录日（记录日稀疏 → 用最后 30 个自然记录）
        cur = set()
        buf = []
        for i, v in enumerate(vals):
            buf.append((dates[i], v))
            if len(buf) > 30:
                buf.pop(0)
            u = set()
            for _, st in buf:
                u |= st
            inst30[(code, dates[i])] = len(u)
    rep_daily["inst30"] = [inst30.get((c, d), 0) for c, d in zip(rep_daily["ts_code"], rep_daily["trade_date"])]
    # 首次覆盖：过去 180 日无研报 & 今日有研报
    rep_daily["first_cov"] = (rep_daily["rep_180"] == rep_daily["n_rep"]) & (rep_daily["n_rep"] > 0)

    # 横截面分位（每日）：30 日研报数
    rep_daily["rep30_pct"] = rep_daily.groupby("trade_date")["rep_30"].rank(pct=True)
    # 只保留 2021+ 信号日
    rd = rep_daily[(rep_daily["trade_date"] >= START)].copy()

    # 未来收益（T+1 收盘入场）
    base = px[["ts_code", "trade_date", "close", "pct_chg", "amount"]].sort_values(["ts_code", "trade_date"])
    gb = base.groupby("ts_code", sort=False)
    for h in HORIZONS:
        base[f"fwd{h}"] = gb["close"].shift(-(1 + h)) / gb["close"].shift(-1) - 1
    mkt = base.groupby("trade_date")["pct_chg"].mean() / 100.0
    dates = sorted(base["trade_date"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    cum = (1 + mkt.fillna(0)).cumprod().reindex(dates).ffill().to_numpy()
    idxs = base["trade_date"].map(idx).to_numpy()
    for h in HORIZONS:
        valid = idxs + 1 + h < len(dates)
        m = np.full(len(base), np.nan)
        m[valid] = cum[idxs[valid] + 1 + h] / cum[idxs[valid] + 1] - 1
        base[f"car{h}"] = base[f"fwd{h}"] - m - COST
    data = rd.merge(base[["ts_code", "trade_date", "car20", "car60", "amount"]], on=["ts_code", "trade_date"], how="inner")

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 20:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    signals = {
        "H34_attention_peak": (data["rep30_pct"] >= 0.9, -1, "高关注 → 反转跑输"),
        "H35_first_coverage": (data["first_cov"], +1, "首次覆盖 → 跑赢"),
        "H36_inst_breadth": (data["inst30"] >= 3, +1, "机构广度≥3 → 跑赢"),
    }
    rep_out = {"meta": {"start": START, "end": END, "is_end": IS_END, "cost": COST,
                        "reports": int(len(rep)), "codes": int(rep["ts_code"].nunique()),
                        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
               "hypotheses": {}}
    for hid, (mask, direction, desc) in signals.items():
        sub = data[mask.fillna(False)]
        grp = {"events": int(len(sub)), "expect": desc, "direction": direction}
        for h in HORIZONS:
            s = sub[["trade_date", f"car{h}"]].dropna()
            s.columns = ["date", "car"]
            s["split"] = np.where(s["date"] <= IS_END, "IS", "OOS")
            daily = s.groupby("date")["car"].mean()
            grp[f"h{h}"] = {"daily": {"all": stat(daily), "IS": stat(daily[daily.index <= IS_END]),
                                      "OOS": stat(daily[daily.index > IS_END])},
                            "bootstrap": stat_utils.block_bootstrap_p(daily[daily.index > IS_END], block=5, n_boot=300),
                            "avg_events_per_day": round(len(s) / max(1, len(daily)), 2)}
        # 判定：20 日为主，方向一致（H34 期望负，H35/36 期望正）且 IS/OOS 双显著
        def ok_h(h):
            oos = grp[f"h{h}"]["daily"]["OOS"]
            isd = grp[f"h{h}"]["daily"]["IS"]
            if oos.get("t") is None or isd.get("t") is None:
                return False
            sign = direction
            return (sign * oos["t"] >= 2 and sign * isd["t"] >= 1.5
                    and sign * oos["mean_pct"] > 0 and sign * isd["mean_pct"] > 0)
        best = next((h for h in HORIZONS if ok_h(h)), None)
        grp["status"] = "backtested" if best else "rejected"
        grp["best_horizon"] = best
        rep_out["hypotheses"][hid] = grp
        print(hid, "events", len(sub), "h20 OOS", grp["h20"]["daily"]["OOS"], "IS", grp["h20"]["daily"]["IS"], "->", grp["status"], flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H34-36_report_coverage_20261031.json"
    js.write_text(json.dumps(rep_out, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")
    L = ["# 研报覆盖/关注度因子（H34–H36）", "",
         f"- 数据：research_report 元数据（{rep_out['meta']['reports']:,} 条个股研报，{rep_out['meta']['codes']} 只）；T+1 收盘入场；20/60 日 CAR（相对等权、30bp）；IS≤2023/OOS≥2024",
         f"- 事件日均值见下；判断方向已事先声明（H34 负、H35/H36 正），只有方向一致且 IS/OOS 双显著才算 backtested", "",
         "| 假设 | 预期 | 事件数 | 20日 IS | 20日 OOS | 判定 |", "|---|---|---|---|---|---|"]
    for hid, grp in rep_out["hypotheses"].items():
        L.append(f"| {hid} | {grp['expect']} | {grp['events']:,} | {f(grp['h20']['daily']['IS'])} | {f(grp['h20']['daily']['OOS'])} | **{grp['status']}** |")
    L += ["", "## 结论",
          "- 关注度/覆盖因子是常见「拥挤度/反转」来源；若 H34 显著为负、H35/H36 不显著 → 说明研报覆盖更多是拥挤信号；",
          "- 局限：数据集只有标题/机构元数据（无评级/目标价/EPS），后续解析研报 PDF 才能做预期修正类因子。"]
    (OUT / "H34-36_report_coverage_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: v["status"] for k, v in rep_out["hypotheses"].items()}, ensure_ascii=False), flush=True)

    # 台账
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    titles = {"H34_attention_peak": "研报关注度峰值（30日覆盖分位≥0.9）→ 未来20日反转？",
              "H35_first_coverage": "首次覆盖（180日无研报→首次出现）→ 未来20日跑赢？",
              "H36_inst_breadth": "研报机构广度（30日覆盖机构≥3）→ 未来20日跑赢？"}

    def upsert(row):
        keys = [k for k in row if k in cols]
        if con.execute("SELECT id FROM hypotheses WHERE id=?", (row["id"],)).fetchone():
            con.execute("UPDATE hypotheses SET %s WHERE id=?" % ",".join("%s=?" % k for k in keys if k != "id"),
                        [row[k] for k in keys if k != "id"] + [row["id"]])
        else:
            con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                        [row[k] for k in keys])

    for hid, grp in rep_out["hypotheses"].items():
        upsert({"id": hid, "title": titles[hid], "question": titles[hid],
                "data_sources": "research_report 元数据 + daily",
                "method": "30日/180日覆盖滚动；日度事件；20/60日 CAR（相对等权、30bp）；按日聚合 t + block bootstrap；IS≤2023/OOS≥2024",
                "gates": "PIT=是（研报发布日）；OOS=独立；成本=30bp；容量=待评估",
                "status": grp["status"], "owner": "DSH-Lead",
                "conclusion": "事件 %d；20日 IS %s / OOS %s；最佳窗口 %s → %s" % (
                    grp["events"], f(grp["h20"]["daily"]["IS"]), f(grp["h20"]["daily"]["OOS"]),
                    grp.get("best_horizon"), grp["status"]),
                "evidence_path": "research/reports/H34-36_report_coverage_20261031.md",
                "created_at": now, "updated_at": now})
    con.commit()
    con.close()
    print("台账 H34-H36 已写", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
