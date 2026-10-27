# -*- coding: utf-8 -*-
"""xq_portrait.py — 雪球分类存量池数据画像（K-ORG portrait 工位，2026-10-18）。

目的
----
给 calib-baseline 工位的异动校准产出解释底数：xq_status_classified 里这批
存量回填行（2017~2023 爬取帖、2026-10-18 当日打标）到底长什么样、为什么
触发不了「同标的单日 bearish≥MIN」异动信号。

五节画像
----
1) sentiment × event_type × content_type 三维边缘分布 + 两个交叉表；
2) 月度时间趋势（按帖子日 created_at 逐月，2019-01 起连续补零 + 各月 bearish 占比）；
3) 标的集中度（top 20 标的 + 整池带码率）；
4) 版本血缘（prompt_version 分档 → content_type 完备度，验证补标只补形态列
   不重判 event_type/sentiment 的 lineage 诚实设计在数据上的形态）；
5) 异动对话：单日同标的 bearish 帖数分布（p50/p90/max）——BEARISH_MIN 的作用面。

纪律
----
只读库（mode=ro + busy_timeout=5000），零 LLM（不调 Ollama），绝不写库。
聚合逻辑全部纯函数（可注入假行单测，见 tools/tests/test_xq_portrait.py）。
快照 JSON 落 C:/aios/automation_runs/xq_intel/portrait_<date>.json。

⚠ 口径：月度趋势与异动作用面均用 xq_status.created_at（帖子发布日），与
xq_anomaly_calibration 的取数面一致；labeled_at 是打标时间（全部集中在
2026-10-18），不代表帖子时间分布。

用法
----
    python tools/research/xq_portrait.py                # 全量画像 + JSON 快照
    python tools/research/xq_portrait.py --json-only    # 只落 JSON 不打印长报告
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DB_PATH = ROOT / "data" / "xueqiu" / "xueqiu_info.db"
OUT_DIR = Path(r"C:\aios\automation_runs") / "xq_intel"

BUSY_TIMEOUT_MS = 5000
TOP_TICKERS = 20


# ---------------------------------------------------------------- 数据读取


def read_rows(con: sqlite3.Connection) -> list:
    """读全部已打标行（含 degraded），JOIN 原表取帖子日。

    返回纯 dict 列表（聚合函数的统一输入形态，可被假行替代单测）。
    """
    q = (
        "SELECT c.event_type, c.sentiment, c.content_type, c.tickers,"
        "       c.prompt_version, c.parse_ok, s.created_at"
        "  FROM xq_status_classified c"
        "  LEFT JOIN xq_status s ON s.status_id = c.status_id"
    )
    rows = []
    for et, st, ct, tk, pv, ok, created_at in con.execute(q):
        try:
            tickers = json.loads(tk) if tk else []
        except Exception:
            tickers = []
        rows.append(
            {
                "event_type": et,
                "sentiment": st,
                "content_type": ct,
                "tickers": tickers if isinstance(tickers, list) else [],
                "prompt_version": pv,
                "parse_ok": ok,
                "day": (created_at or "")[:10] or None,
            }
        )
    return rows


def open_db_ro() -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.as_posix(), uri=True)
    con.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    return con


# ---------------------------------------------------------------- 纯聚合函数


def percentile(sorted_vals: list, p: float):
    """线性插值百分位（输入须升序）。空表返回 None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _cross(rows, key_a: str, key_b: str) -> dict:
    tab = defaultdict(Counter)
    for r in rows:
        a = r[key_a] if r[key_a] is not None else "(空)"
        b = r[key_b] if r[key_b] is not None else "(空)"
        tab[a][b] += 1
    return {a: dict(c) for a, c in tab.items()}


def marginals(rows: list) -> dict:
    """节1：三维边缘分布 + sentiment×event_type、content_type×sentiment 交叉表。"""
    return {
        "sentiment": dict(Counter((r["sentiment"] or "(空)") for r in rows)),
        "event_type": dict(Counter((r["event_type"] or "(空)") for r in rows)),
        "content_type": dict(Counter((r["content_type"] or "(空)") for r in rows)),
        "crosstab_event_sentiment": _cross(rows, "event_type", "sentiment"),
        "crosstab_content_sentiment": _cross(rows, "content_type", "sentiment"),
    }


def monthly_trend(rows: list) -> list:
    """节2：按帖子月逐月计数 + bearish 占比；2019-01 起连续补零。

    行无帖子日（created_at 缺失）单独计入 unattributed，不进月份序列。
    """
    n = Counter()
    bear = Counter()
    unattributed = 0
    for r in rows:
        d = r["day"]
        if not d:
            unattributed += 1
            continue
        m = d[:7]
        n[m] += 1
        if r["sentiment"] == "bearish":
            bear[m] += 1
    if not n:
        return []
    months = sorted(n)
    y, mo = int(months[0][:4]), int(months[0][5:7])
    first = "%04d-%02d" % (y, mo)
    # 序列起点固定从数据首月（2019-01 类）连续排到末月
    out = []
    cur = first
    while True:
        b, t = bear.get(cur, 0), n.get(cur, 0)
        out.append(
            {
                "month": cur,
                "n": t,
                "bearish": b,
                "bearish_share": (b / t) if t else None,
            }
        )
        if cur == months[-1]:
            break
        y, mo = int(cur[:4]), int(cur[5:7])
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
        cur = "%04d-%02d" % (y, mo)
    return out


def ticker_concentration(rows: list) -> dict:
    """节3：top 20 标的（带码行）+ 整池带码率。"""
    total = len(rows)
    per = defaultdict(Counter)  # ticker -> Counter(sentiment)
    with_ticker = 0
    for r in rows:
        if r["tickers"]:
            with_ticker += 1
            for t in r["tickers"]:
                per[t][r["sentiment"] or "(空)"] += 1
    top = sorted(per.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))[
        :TOP_TICKERS
    ]
    top_list = [
        {
            "ticker": t,
            "n": sum(c.values()),
            "bearish": c.get("bearish", 0),
            "bullish": c.get("bullish", 0),
            "bearish_share": (c.get("bearish", 0) / sum(c.values()))
            if sum(c.values())
            else None,
        }
        for t, c in top
    ]
    return {
        "total": total,
        "with_ticker": with_ticker,
        "with_ticker_rate": (with_ticker / total) if total else None,
        "distinct_tickers": len(per),
        "top20": top_list,
    }


def lineage(rows: list) -> dict:
    """节4：prompt_version 分档血缘 → 各版本 content_type 完备度。

    诚实设计验证：补标批次（v1.2-re，系 v1 判 event_type/sentiment 后补
    content_type）若 content_type 未全覆盖即进 incomplete_versions 点名。
    event_type/sentiment 全列完备由 parse_ok=1 契约保证，此处一并给出完备数。
    """
    versions = defaultdict(Counter)
    for r in rows:
        pv = r["prompt_version"] or "(空)"
        versions[pv]["n"] += 1
        if r["content_type"] is not None:
            versions[pv]["content_type_filled"] += 1
        if r["event_type"] is not None:
            versions[pv]["event_type_filled"] += 1
        if r["sentiment"] is not None:
            versions[pv]["sentiment_filled"] += 1
    out = {}
    incomplete = []
    for pv, c in sorted(versions.items()):
        n = c["n"]
        rate = c["content_type_filled"] / n if n else None
        out[pv] = {
            "n": n,
            "content_type_filled": c["content_type_filled"],
            "content_type_rate": rate,
            "event_type_filled": c["event_type_filled"],
            "sentiment_filled": c["sentiment_filled"],
        }
        if rate is not None and rate < 1.0:
            incomplete.append(pv)
    return {"versions": out, "incomplete_versions": incomplete}


def daily_ticker_bearish(rows: list) -> dict:
    """节5（对校准）：单日同标的 bearish 帖数分布。

    作用面与 xq_anomaly_calibration 完全一致：
    parse_ok=1 ∧ sentiment=bearish ∧ 带码，按（帖子日 × 标的）计数。
    BEARISH_MIN≥2 的触发前提是存在 count≥2 的 (day,ticker) 对——
    本分布直接回答存量池能不能够到这条线。
    """
    pair = Counter()
    for r in rows:
        if r["parse_ok"] != 1 or r["sentiment"] != "bearish":
            continue
        d = r["day"]
        if not d:
            continue
        for t in r["tickers"]:
            pair[(d, t)] += 1
    counts = sorted(pair.values())
    n_pairs = len(counts)
    n_ge2 = sum(1 for c in counts if c >= 2)
    top_pairs = [
        {"day": d, "ticker": t, "n": n}
        for (d, t), n in sorted(pair.items(), key=lambda kv: -kv[1])[:5]
    ]
    return {
        "n_pairs": n_pairs,
        "pair_counts": counts,
        "p50": percentile(counts, 50),
        "p90": percentile(counts, 90),
        "max": counts[-1] if counts else None,
        "n_pairs_ge2": n_ge2,
        "ge2_share": (n_ge2 / n_pairs) if n_pairs else None,
        "top_pairs": top_pairs,
    }


# ---------------------------------------------------------------- 主流程


def build_snapshot(rows: list) -> dict:
    ok_rows = [r for r in rows if r["parse_ok"] == 1]
    days = sorted(r["day"] for r in ok_rows if r["day"])
    return {
        "meta": {
            "snapshot_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "db_path": str(DB_PATH),
            "total_classified": len(rows),
            "parse_ok_1": len(ok_rows),
            "degraded": len(rows) - len(ok_rows),
            "post_day_min": days[0] if days else None,
            "post_day_max": days[-1] if days else None,
            "note": "存量回填快照：帖子为 2017~2023 爬取存量，labeled_at 集中在打标当日；"
            "cls_2026=0（实时采集帖未进分类池），不代表实时分布。",
        },
        "marginals": marginals(rows),
        "monthly_trend": monthly_trend(ok_rows),
        "ticker_concentration": ticker_concentration(ok_rows),
        "lineage": lineage(rows),
        "anomaly_dialogue": daily_ticker_bearish(ok_rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球分类存量池数据画像（只读）")
    ap.add_argument("--json-only", action="store_true", help="只落 JSON 不打印长报告")
    a = ap.parse_args()

    con = open_db_ro()
    try:
        rows = read_rows(con)
    finally:
        con.close()

    snap = build_snapshot(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / ("portrait_%s.json" % datetime.now().strftime("%Y-%m-%d"))
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    if a.json_only:
        print("snapshot -> %s" % out_path)
        return 0

    m = snap["marginals"]
    tc = snap["ticker_concentration"]
    ad = snap["anomaly_dialogue"]
    lin = snap["lineage"]
    print("== 雪球分类存量池画像 %s ==" % snap["meta"]["snapshot_at"])
    print(
        "total=%d ok=%d degraded=%d 帖子日 %s ~ %s"
        % (
            snap["meta"]["total_classified"],
            snap["meta"]["parse_ok_1"],
            snap["meta"]["degraded"],
            snap["meta"]["post_day_min"],
            snap["meta"]["post_day_max"],
        )
    )
    print("\n[1] sentiment: %s" % m["sentiment"])
    print("    event_type: %s" % m["event_type"])
    print("    content_type: %s" % m["content_type"])
    print("    event×sentiment: %s" % m["crosstab_event_sentiment"])
    print("    content×sentiment: %s" % m["crosstab_content_sentiment"])
    print("\n[2] 月度趋势（n / bearish_share）:")
    for mo in snap["monthly_trend"]:
        share = mo["bearish_share"]
        print(
            "    %s  n=%-5d bearish_share=%s"
            % (mo["month"], mo["n"], ("%.3f" % share) if share is not None else "-")
        )
    print(
        "\n[3] 带码率: %d/%d (%.3f)  distinct=%d"
        % (
            tc["with_ticker"],
            tc["total"],
            tc["with_ticker_rate"] or 0,
            tc["distinct_tickers"],
        )
    )
    for t in tc["top20"]:
        print(
            "    %-8s n=%-4d bear=%-4d bull=%-4d share=%.3f"
            % (t["ticker"], t["n"], t["bearish"], t["bullish"], t["bearish_share"] or 0)
        )
    print("\n[4] 血缘:")
    for pv, v in lin["versions"].items():
        print(
            "    %-10s n=%-5d content_type_rate=%s"
            % (
                pv,
                v["n"],
                ("%.3f" % v["content_type_rate"])
                if v["content_type_rate"] is not None
                else "-",
            )
        )
    print("    补标未全覆盖版本: %s" % (lin["incomplete_versions"] or "无"))
    print("\n[5] 单日同标的 bearish 分布（BEARISH_MIN 作用面）:")
    print(
        "    pairs=%d p50=%s p90=%s max=%s pairs_ge2=%d ge2_share=%s"
        % (
            ad["n_pairs"],
            ad["p50"],
            ad["p90"],
            ad["max"],
            ad["n_pairs_ge2"],
            ("%.3f" % ad["ge2_share"]) if ad["ge2_share"] is not None else "-",
        )
    )
    for p in ad["top_pairs"]:
        print("    top: %s %s n=%d" % (p["day"], p["ticker"], p["n"]))
    print("\nsnapshot -> %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
