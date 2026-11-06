# -*- coding: utf-8 -*-
"""market_analysis.py — 单交易日市场结构分析（可重复、只读、fail-closed）。

定位
----
把 2026-11-05 那次一次性分析固化成每天可跑的一条命令：
宽度 / 涨跌停 / 量能 / 申万一级结构 / 资金集中度 / 数据质量自检。

与 daily_screening 的分工
------------------------
daily_screening 管「选哪些票」；本脚本管「市场处在什么状态」。
两者互不调用，可独立重跑；本脚本不选股、不给买卖建议。

数据口径（三条都是踩过坑的）
--------------------------
1. `limit_list_d` 常年空壳（`os.path.exists` 即跳过，见 limit_list_fix_sync.py 头注释），
   因此涨跌停**不读 limit_list_d**，改按板块涨跌停规则 + pct_chg 判定：
   北交所 30%、创业板/科创板 20%、主板 ST 5%、其余 10%，容差 0.15pct。
2. 涨幅分桶的标签必须写实际区间（">=9.9%"），不能叫"涨停"——创业板涨 15% 会落进去。
3. `index_daily` 可能缺当日（11-05 实测缺），缺了就标 degraded，不静默用次日数据。

退出码
------
0 = 产出完整（可能含 degraded 项）
1 = --strict 且存在 degraded 项，或必需表缺失
2 = 参数错误 / 数据根不可读

用法
----
    python -X utf8 tools/research/market_analysis.py                    # 最新交易日
    python -X utf8 tools/research/market_analysis.py --date 20261105
    python -X utf8 tools/research/market_analysis.py --date 20261105 --strict
    python -X utf8 tools/research/market_analysis.py --date 20261105 --print-json

产物
----
    research/reports/market_analysis_<date>.json
    research/reports/market_analysis_<date>.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "quant_backtest" / "data" / "tushare"
OUT_DIR = ROOT / "research" / "reports"
SCHEMA = "market_analysis/v1"

DIST_BINS = [-101, -9.9, -7, -5, -3, -1, 0, 1, 3, 5, 7, 9.9, 101]
DIST_LABELS = ["<=-9.9%", "-9.9~-7%", "-7~-5%", "-5~-3%", "-3~-1%", "-1~0%",
               "0~1%", "1~3%", "3~5%", "5~7%", "7~9.9%", ">=9.9%"]

# 必需表：缺了没法算，直接失败
REQUIRED = ["daily"]
# 可选表：缺了降级，--strict 下算失败
OPTIONAL = ["daily_basic", "index_daily", "limit_list_d", "stock_basic",
            "index_member_all", "sw_classify_l1"]


# ---------------------------------------------------------------------------
# 纯函数（测试直接调）
# ---------------------------------------------------------------------------
def limit_pct(ts_code: str, name: str) -> float:
    """按板块返回涨跌停幅度（%）。判错会直接影响涨停家数，规则写死在这里。"""
    code = str(ts_code)
    if code.endswith(".BJ"):
        return 30.0
    if code[:3] in ("300", "301", "688", "689"):
        return 20.0
    return 5.0 if "ST" in str(name).upper() else 10.0


def breadth(df: pd.DataFrame, tol: float = 0.15) -> dict:
    """宽度统计。df 需含 ts_code / name / pct_chg。"""
    chg = pd.to_numeric(df["pct_chg"], errors="coerce")
    name = df["name"].astype(str).str.upper() if "name" in df.columns else pd.Series([""] * len(df))
    lims = [limit_pct(c, n) for c, n in zip(df["ts_code"], name)]
    lim = pd.Series(lims, index=df.index)
    return {
        "n": int(len(df)),
        "up": int((chg > 0).sum()),
        "down": int((chg < 0).sum()),
        "flat": int((chg == 0).sum()),
        "limit_up": int((chg >= lim - tol).sum()),
        "limit_down": int((chg <= -lim + tol).sum()),
        "ge_9_9": int((chg >= 9.9).sum()),
        "le_-9_9": int((chg <= -9.9).sum()),
        "median_pct": round(float(chg.median()), 3),
        "mean_pct": round(float(chg.mean()), 3),
        "amount_yi": round(float(pd.to_numeric(df["amount"], errors="coerce").sum()) / 1e5, 1),
    }


def distribution(chg: pd.Series) -> dict:
    """涨幅分桶。键名即实际区间，避免"涨停"这种会误导的别名。"""
    chg = pd.to_numeric(chg, errors="coerce")
    cut = pd.cut(chg, bins=DIST_BINS, labels=DIST_LABELS, right=False)
    vc = cut.value_counts().reindex(DIST_LABELS).fillna(0).astype(int)
    return {str(k): int(v) for k, v in vc.items()}


def industry_stats(df: pd.DataFrame, member: pd.DataFrame, classify: pd.DataFrame,
                   min_n: int = 5) -> list[dict]:
    """申万一级等权涨幅。member 需含 ts_code / l1_code / is_new / out_date。"""
    m = member.copy()
    if "is_new" in m.columns:
        m = m[m["is_new"].astype(str).str.upper() == "Y"]
    if "out_date" in m.columns:
        m = m[m["out_date"].isna()]
    c = classify.drop_duplicates("index_code")[["index_code", "industry_name"]]
    m = m.merge(c, left_on="l1_code", right_on="index_code", how="left")
    m = m[["ts_code", "industry_name"]].dropna(subset=["industry_name"])
    j = df.merge(m, on="ts_code", how="inner")
    g = j.groupby("industry_name").agg(
        n=("ts_code", "size"),
        pct=("pct_chg", "mean"),
        amt=("amount", lambda s: pd.to_numeric(s, errors="coerce").sum() / 1e5),
    )
    g = g[g["n"] >= min_n].sort_values("pct", ascending=False)
    return [{"name": i, "n": int(r.n), "pct": round(float(r.pct), 2),
             "amount_yi": round(float(r.amt), 1)} for i, r in g.iterrows()]


def concentration(df: pd.DataFrame, topn: int = 10) -> dict:
    """资金集中度：前 N 只成交占比 + 前 3 行业占比（行业缺失时只给个股口径）。"""
    amt = pd.to_numeric(df["amount"], errors="coerce")
    total = float(amt.sum())
    if total <= 0:
        return {"total_amount_yi": 0.0}
    top = amt.nlargest(topn)
    out = {
        "total_amount_yi": round(total / 1e5, 1),
        "top%d_amount_yi" % topn: round(float(top.sum()) / 1e5, 1),
        "top%d_share_pct" % topn: round(float(top.sum()) / total * 100, 1),
    }
    if "industry" in df.columns:
        gi = df.assign(_amt=amt).groupby("industry")["_amt"].sum().sort_values(ascending=False)
        out["top3_industry_share_pct"] = round(float(gi.head(3).sum()) / total * 100, 1)
        out["top3_industry"] = [{"name": k, "amount_yi": round(float(v) / 1e5, 1)}
                                for k, v in gi.head(3).items()]
    return out


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------
def read_table(data_root: Path, table: str, date: str | None = None,
               allow_empty: bool = False) -> pd.DataFrame | None:
    """按 by_date 或 part 布局读表；不存在返回 None（调用方决定降级还是失败）。

    allow_empty=True 时，空壳文件返回空 DataFrame 而不是 None —— 体检要能区分
    「文件不存在」和「文件在但 0 行」，后者才是 limit_list_d 那种静默故障。
    """
    d = data_root / table
    if not d.is_dir():
        return None

    def _keep(df: pd.DataFrame) -> pd.DataFrame | None:
        return df if (allow_empty or len(df)) else None

    if date and (d / ("d_%s.parquet" % date)).is_file():
        return _keep(pd.read_parquet(d / ("d_%s.parquet" % date)))
    parts = sorted(glob.glob(str(d / "part_*.parquet")))
    if parts:
        return _keep(pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True))
    return None


def latest_date(data_root: Path) -> str | None:
    fs = glob.glob(str(data_root / "daily" / "d_*.parquet"))
    days = [os.path.basename(f)[2:10] for f in fs]
    return max(days) if days else None


def quality(data_root: Path, date: str) -> tuple[list[dict], list[str]]:
    """逐表体检。返回 (明细, degraded 项名)。空表也算 degraded —— 空壳比缺失更危险。"""
    rows, degraded = [], []
    for t in REQUIRED:
        df = read_table(data_root, t, date, allow_empty=True)
        n = 0 if df is None else len(df)
        status = "ok" if n > 0 else ("empty" if df is not None else "missing")
        rows.append({"table": t, "tier": "required", "status": status, "rows": n})
        if n == 0:
            degraded.append(t)
    for t in OPTIONAL:
        if t in ("stock_basic", "index_member_all", "sw_classify_l1"):
            df = read_table(data_root, t, allow_empty=True)   # 静态表，不按日
        else:
            df = read_table(data_root, t, date, allow_empty=True)  # 日表，按日
        n = 0 if df is None else len(df)
        status = "ok" if n > 0 else ("empty" if df is not None else "missing")
        rows.append({"table": t, "tier": "optional", "status": status, "rows": n})
        if n == 0:
            degraded.append(t)
    return rows, degraded


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build(date: str, data_root: Path) -> dict:
    qrows, degraded = quality(data_root, date)
    if "daily" in degraded:
        raise SystemExit("★必需表 daily 缺失/为空：%s（date=%s）" % (data_root, date))

    daily = read_table(data_root, "daily", date)
    sb = read_table(data_root, "stock_basic")
    if sb is not None:
        daily = daily.merge(sb[["ts_code", "name", "industry"]].drop_duplicates("ts_code"),
                            on="ts_code", how="left")
    else:
        daily["name"] = ""

    res: dict = {
        "schema": SCHEMA,
        "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "degraded": degraded,
        "quality": qrows,
        "breadth": breadth(daily),
        "distribution": distribution(daily["pct_chg"]),
        "concentration": concentration(daily),
    }

    member = read_table(data_root, "index_member_all")
    classify = read_table(data_root, "sw_classify_l1")
    if member is not None and classify is not None:
        res["industry"] = industry_stats(daily, member, classify)
    else:
        res["industry"] = []

    # 指数：只取当日，缺则不出数（不回退昨日，避免"用昨天的数据冒充今天"）
    idx = read_table(data_root, "index_daily", date)
    res["index"] = []
    if idx is not None:
        keep = idx[idx["ts_code"].isin(["000001.SH", "399001.SZ", "399006.SZ",
                                        "000688.SH", "932000.CSI"])]
        res["index"] = [{k: (str(v) if k in ("ts_code", "trade_date") else round(float(v), 4))
                         for k, v in r.items()}
                        for r in keep[["ts_code", "trade_date", "close", "pct_chg"]].to_dict("records")]

    # 量能环比（前一交易日）
    prev = _prev_day(data_root, date)
    if prev:
        p = read_table(data_root, "daily", prev)
        if p is not None:
            cur_amt = float(pd.to_numeric(daily["amount"], errors="coerce").sum()) / 1e5
            prev_amt = float(pd.to_numeric(p["amount"], errors="coerce").sum()) / 1e5
            res["amount_dod"] = {
                "prev_date": prev,
                "prev_amount_yi": round(prev_amt, 1),
                "cur_amount_yi": round(cur_amt, 1),
                "chg_pct": round((cur_amt / prev_amt - 1) * 100, 1) if prev_amt else None,
            }
    return res


def _prev_day(data_root: Path, date: str) -> str | None:
    fs = sorted(glob.glob(str(data_root / "daily" / "d_*.parquet")))
    days = [os.path.basename(f)[2:10] for f in fs]
    earlier = [d for d in days if d < date]
    return max(earlier) if earlier else None


def to_md(r: dict) -> str:
    b = r["breadth"]
    L = []
    L.append("# 市场结构分析（数据日 %s）" % r["date"])
    L.append("")
    L.append("> 生成 %s｜脚本 `tools/research/market_analysis.py`｜**研究输出，不构成投资建议**"
             % r["generated_at"])
    L.append("")
    if r["degraded"]:
        L.append("## ⚠ 数据降级")
        L.append("")
        L.append("以下表缺失或为空，相关结论不可用或为推算值：**%s**" % "、".join(r["degraded"]))
        L.append("")
    L.append("## 一、宽度")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 样本 | %d 只 |" % b["n"])
    L.append("| 上涨 / 下跌 / 平盘 | %d / %d / %d |" % (b["up"], b["down"], b["flat"]))
    L.append("| 上涨占比 | %.1f%% |" % (b["up"] / b["n"] * 100 if b["n"] else 0))
    L.append("| 涨停 / 跌停（规则判定） | %d / %d |" % (b["limit_up"], b["limit_down"]))
    L.append("| 涨幅中位 / 均值 | %+.2f%% / %+.2f%% |" % (b["median_pct"], b["mean_pct"]))
    L.append("| 成交额 | %.1f 亿 |" % b["amount_yi"])
    if r.get("amount_dod"):
        a = r["amount_dod"]
        L.append("| 成交额环比（vs %s） | %+.1f%% |" % (a["prev_date"], a["chg_pct"] or 0))
    L.append("")
    L.append("## 二、涨幅分布")
    L.append("")
    L.append("| 区间 | 家数 |")
    L.append("|---|---|")
    for k in DIST_LABELS:
        L.append("| %s | %d |" % (k, r["distribution"].get(k, 0)))
    L.append("")
    if r["industry"]:
        L.append("## 三、申万一级（等权）")
        L.append("")
        L.append("| 行业 | 成分 | 涨幅 | 成交额(亿) |")
        L.append("|---|---|---|---|")
        for i in r["industry"]:
            L.append("| %s | %d | %+.2f%% | %.1f |" % (i["name"], i["n"], i["pct"], i["amount_yi"]))
        L.append("")
    c = r["concentration"]
    L.append("## 四、资金集中度")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    for k in ("total_amount_yi", "top10_amount_yi", "top10_share_pct", "top3_industry_share_pct"):
        if k in c:
            L.append("| %s | %s |" % (k, c[k]))
    L.append("")
    if r["index"]:
        L.append("## 五、指数")
        L.append("")
        L.append("| 指数 | 收盘 | 涨跌 |")
        L.append("|---|---|---|")
        for i in r["index"]:
            L.append("| %s | %.2f | %+.2f%% |" % (i["ts_code"], i["close"], i["pct_chg"]))
        L.append("")
    else:
        L.append("## 五、指数")
        L.append("")
        L.append("当日无指数数据（`index_daily` 缺），不做回退。")
        L.append("")
    L.append("## 六、数据体检")
    L.append("")
    L.append("| 表 | 层级 | 状态 | 行数 |")
    L.append("|---|---|---|---|")
    for q in r["quality"]:
        L.append("| %s | %s | %s | %d |" % (q["table"], q["tier"], q["status"], q["rows"]))
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="单交易日市场结构分析")
    ap.add_argument("--date", default="", help="交易日 YYYYMMDD，默认最新")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA), help="tushare 数据根")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="产物目录")
    ap.add_argument("--strict", action="store_true", help="存在 degraded 即返回 1")
    ap.add_argument("--print-json", action="store_true", help="stdout 打印完整 JSON")
    ap.add_argument("--no-write", action="store_true", help="只算不写文件（试跑）")
    args = ap.parse_args(argv)

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        print("★数据根不存在: %s" % data_root, file=sys.stderr)
        return 2
    date = args.date or latest_date(data_root)
    if not date:
        print("★找不到任何 daily/d_*.parquet", file=sys.stderr)
        return 2

    res = build(date, data_root)

    if not args.no_write:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / ("market_analysis_%s.json" % date)
        mp = out / ("market_analysis_%s.md" % date)
        jp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        mp.write_text(to_md(res), encoding="utf-8")
        print("JSON %s" % jp)
        print("MD   %s" % mp)

    if args.print_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        b = res["breadth"]
        print("[%s] n=%d up=%.1f%% median=%+.2f%% limit_up=%d limit_down=%d amount=%.0f亿 degraded=%s"
              % (date, b["n"], b["up"] / b["n"] * 100 if b["n"] else 0, b["median_pct"],
                 b["limit_up"], b["limit_down"], b["amount_yi"], ",".join(res["degraded"]) or "-"))

    if args.strict and res["degraded"]:
        print("★strict: degraded=%s" % ",".join(res["degraded"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
