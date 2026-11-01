# -*- coding: utf-8 -*-
"""H24-H26 · 量学知识单元 → 可计算特征 → 事件研究。

来源：knowledge_docs/量学_伏击涨停与涨停密码_OCR摘要.md 与 knowledge_units.jsonl
特征（日线，2021-01-01 ~ 2026-10-29）：
  H24 倍量柱：量比5≥1.9 且 收阳 → 后续 CAR？
  H25 价升量缩：收阳且量<前日0.8、价创新高方向 → 反转？
  H26 极阴次阳：前日跌幅≤-5% 且 今日收阳收复前日实体≥50%
口径：T+1 收盘入场，持有 5/10/20 日，相对等权市场 CAR，成本 30bp；
      IS≤2023-12-31 / OOS≥2024-01-01；事件级 t + 按日聚合 block bootstrap（stat_utils）。
产出：research/reports/H24-26_liangxue_features_20261031.{md,json}；台账 H24-H26。
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
HORIZONS = (5, 10, 20)


def load_daily() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "open", "close", "pre_close", "pct_chg", "vol"])
        df = tb.to_pandas()
        df = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    d = d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return d


def main() -> int:
    df = load_daily()
    print("rows", len(df), "codes", df["ts_code"].nunique(), "dates", df["trade_date"].nunique(), flush=True)
    g = df.groupby("ts_code", sort=False)
    df["vol_ma5"] = g["vol"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    df["vol_ratio5"] = df["vol"] / df["vol_ma5"].replace(0, np.nan)
    df["ma10"] = g["close"].transform(lambda s: s.rolling(10, min_periods=5).mean())
    df["hi20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=10).max())
    df["prev_vol"] = g["vol"].shift(1)
    df["prev_pct"] = g["pct_chg"].shift(1)
    df["prev_open"] = g["open"].shift(1)
    df["prev_close"] = g["pre_close"].shift(1)

    # ---- 特征（T 日收盘可知）----
    df["sig_double_vol"] = (df["vol_ratio5"] >= 1.9) & (df["pct_chg"] > 0)
    df["sig_price_up_vol_down"] = (df["pct_chg"] > 0) & (df["vol"] < df["prev_vol"] * 0.8)
    body_mid = (df["prev_open"] + df["prev_close"]) / 2
    df["sig_yin_yang"] = (df["prev_pct"] <= -5) & (df["pct_chg"] > 0) & (df["close"] >= body_mid)
    df["sig_near_high_vol"] = (df["close"] >= df["hi20"] * 0.98) & (df["vol_ratio5"] >= 1.5)

    # ---- beta（60 日滚动，相对等权市场）----
    df["ret"] = g["close"].pct_change()
    mkt_ret_by_date = df.groupby("trade_date")["pct_chg"].mean() / 100.0
    df["mkt_ret"] = df["trade_date"].map(mkt_ret_by_date)

    def roll_beta(sub: pd.DataFrame) -> pd.Series:
        cov = sub["ret"].rolling(60, min_periods=40).cov(sub["mkt_ret"])
        var = sub["mkt_ret"].rolling(60, min_periods=40).var()
        return cov / var.replace(0, np.nan)

    df["beta"] = df.groupby("ts_code", group_keys=False).apply(roll_beta).reindex(df.index)

    # ---- 未来收益（T+1 收盘入场）----
    for h in HORIZONS:
        df[f"fwd{h}"] = g["close"].shift(-(1 + h)) / g["close"].shift(-1) - 1
    mkt = df.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod()
    # 每个日期索引映射
    date_idx = {d: i for i, d in enumerate(sorted(df["trade_date"].unique()))}
    dates = sorted(df["trade_date"].unique())
    cum_arr = mkt_cum.reindex(dates).ffill().to_numpy()

    def market_ret(t: str, h: int) -> float:
        i = date_idx.get(t)
        if i is None or i + 1 + h >= len(dates):
            return np.nan
        return cum_arr[i + 1 + h] / cum_arr[i + 1] - 1

    for h in HORIZONS:
        # 按行计算市场收益（向量化：映射日期到索引）
        idxs = df["trade_date"].map(date_idx).to_numpy()
        valid = idxs + 1 + h < len(dates)
        m = np.full(len(df), np.nan)
        m[valid] = cum_arr[idxs[valid] + 1 + h] / cum_arr[idxs[valid] + 1] - 1
        df[f"car{h}"] = df[f"fwd{h}"] - m - COST
        beta = df["beta"].fillna(1.0).to_numpy()
        df[f"car_adj{h}"] = df[f"fwd{h}"] - beta * m - COST

    sigs = {
        "H24_double_vol": "sig_double_vol",
        "H25_price_up_vol_down": "sig_price_up_vol_down",
        "H26_yin_yang": "sig_yin_yang",
        "H27_near_high_vol": "sig_near_high_vol",
    }
    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "rows": int(len(df)), "codes": int(df["ts_code"].nunique()),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "hypotheses": {}}
    for hid, col in sigs.items():
        sub = df[df[col]].copy()
        sub = sub[sub["trade_date"] <= END]
        grp = {}
        for h in HORIZONS:
            s = sub[["trade_date", f"car_adj{h}", f"car{h}"]].dropna()
            raw_daily = s.groupby("trade_date")[f"car{h}"].mean()
            s = s.rename(columns={"trade_date": "date", f"car_adj{h}": "car"}).drop(columns=[f"car{h}"])
            s["split"] = np.where(s["date"] <= IS_END, "IS", "OOS")
            # 按日聚合（先日内均值，降低横截面相关性）
            daily = s.groupby("date")["car"].mean()

            def stat(x: pd.Series):
                x = pd.Series(x, dtype=float).dropna()
                if len(x) < 20:
                    return {"n": int(len(x))}
                return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                        "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}
            grp[f"h{h}"] = {
                "events": int(len(s)),
                "event_t": {"all": stat(s["car"]), "IS": stat(s[s["split"] == "IS"]["car"]),
                            "OOS": stat(s[s["split"] == "OOS"]["car"])},
                "daily": {"all": stat(daily), "IS": stat(daily[daily.index <= IS_END]),
                          "OOS": stat(daily[daily.index > IS_END])},
                "bootstrap_oos": stat_utils.block_bootstrap_p(daily[daily.index > IS_END], block=5, n_boot=300),
                "raw_daily": {"all": stat(raw_daily), "IS": stat(raw_daily[raw_daily.index <= IS_END]),
                              "OOS": stat(raw_daily[raw_daily.index > IS_END])},
            }
            print(hid, f"h{h}", "events", len(s), "daily_OOS", grp[f"h{h}"]["daily"]["OOS"], flush=True)
        # 判定：10 日为主
        oos = grp["h10"]["daily"]["OOS"]
        isd = grp["h10"]["daily"]["IS"]
        # 这些是「看涨形态」，方向必须为正；显著为负 = 原假设不成立（但可能反向有效，另立假设）
        ok = (oos.get("t") is not None and oos["t"] >= 2
              and (oos.get("mean_pct", 0) or 0) > 0
              and isd.get("t") is not None and isd["t"] >= 1.5
              and (isd.get("mean_pct", 0) or 0) > 0)
        grp["status"] = "backtested" if ok else "rejected"
        grp["reverse_signal"] = bool(oos.get("t") is not None and oos["t"] <= -2)
        rep["hypotheses"][hid] = grp

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H24-26_liangxue_features_20261031.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# 量学知识单元 → 特征 → 事件研究（H24–H27）", "",
         f"- 样本：{START}~{END}，{rep['meta']['codes']} 只，{rep['meta']['rows']:,} 行；T+1 收盘入场；相对等权市场 CAR；成本 30bp；IS≤2023 / OOS≥2024",
         f"- 来源知识：`knowledge_docs/量学_伏击涨停与涨停密码_OCR摘要.md`（知识单元→特征映射）", "",
         "| 假设 | 特征 | 事件数 | 10日 IS(β调整) | 10日 OOS(β调整) | 10日 OOS(原始) | bootstrap p(≤0) | 判定 | 反向显著 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for hid, grp in rep["hypotheses"].items():
        def f(x):
            return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")
        bs = grp["h10"]["bootstrap_oos"]
        L.append(f"| {hid} | {grp['h10']['events']} | {f(grp['h10']['daily']['IS'])} | {f(grp['h10']['daily']['OOS'])} | "
                 f"{f(grp['h10']['raw_daily']['OOS'])} | p(≤0)={bs.get('p_le_0')} | **{grp['status']}** | {'是' if grp.get('reverse_signal') else '否'} |")
    L += ["", "## 结论", "- 事件研究以「按日聚合均值」计算 t（降低横截面相关），并用 block bootstrap 复核 OOS；",
          "- 判定：OOS |t|≥2 且 IS/OOS 同向且 IS |t|≥1.5 → backtested；否则 rejected；",
          "- 本结果只验证「知识单元描述的形态」是否有效；通过者也须过成本/容量/容量/容量（详见 research/MEETING.md 复核清单）。"]
    (OUT / "H24-26_liangxue_features_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: {"status": v["status"], "h10_oos": v["h10"]["daily"]["OOS"]} for k, v in rep["hypotheses"].items()},
                     ensure_ascii=False, indent=1), flush=True)

    # 台账
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    titles = {"H24_double_vol": "倍量柱（量比5≥1.9收阳）后续收益",
              "H25_price_up_vol_down": "价升量缩后续反转",
              "H26_yin_yang": "极阴次阳反弹",
              "H27_near_high_vol": "兵临城下（近20日高+放量）"}
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    for hid, grp in rep["hypotheses"].items():
        oos = grp["h10"]["daily"]["OOS"]
        row = {"id": hid.split("_")[0], "title": titles[hid],
               "question": "量学形态（" + titles[hid] + "）是否带来可交易超额？",
               "data_sources": "daily 2021-2026",
               "method": "特征事件研究；T+1 入场；5/10/20 日 CAR；相对等权市场；IS≤2023/OOS≥2024；30bp；按日聚合 t + block bootstrap",
               "gates": "PIT=是；OOS=独立；成本=30bp；容量=待评估",
               "status": grp["status"], "owner": "DSH-Lead",
               "conclusion": "事件 %d；10日 OOS %s；bootstrap p=%s → %s" % (
                   grp["h10"]["events"], json.dumps(oos, ensure_ascii=False),
                   grp["h10"]["bootstrap_oos"].get("p_le_0"), grp["status"]),
               "evidence_path": "research/reports/H24-26_liangxue_features_20261031.md",
               "created_at": now, "updated_at": now}
        keys = [k for k in row if k in cols]
        exists = con.execute("SELECT id FROM hypotheses WHERE id=?", (row["id"],)).fetchone()
        if exists:
            con.execute("UPDATE hypotheses SET %s WHERE id=?" % ",".join("%s=?" % k for k in keys if k != "id"),
                        [row[k] for k in keys if k != "id"] + [row["id"]])
        else:
            con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                        [row[k] for k in keys])
    con.commit()
    con.close()
    print("台账已写 H24-H27", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
