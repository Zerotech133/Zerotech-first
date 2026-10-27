# -*- coding: utf-8 -*-
"""strong 观察窗复盘脚本（v4 P0-4 / 10-13 复盘用，2026-10-09 建）。

背景：S1 strong 阈值 +10pp 偏宽（10-09 名单 402 只，观察组只落盘不推送），
3 交易日观察窗（10-09 入选 → 10-12/10-13 后验），10-13 晚复盘判定收敛否。

用法（10-13 收盘后湖更新后跑）:
    python strong_review.py                       # 默认最新名单，3 交易日窗
    python strong_review.py --days 2              # 只看 2 交易日
    python strong_review.py --save reports/strong_review.json
数据不足时如实标注（湖只到哪天算到哪天），不硬凑。

口径：基准 = 名单 ts 当日的上一收盘（晨间 06:30 产物，入选时点最近收盘）；
收益 = 基准收盘 → N 个交易日后收盘（后复权湖价）；基线 = 全湖抽样同口径；
判据（供人工拍板参考，非自动改阈值）：
    超额 >= +1pp  → 偏宽但有效（名单有信息量，维持）
    超额 <= -1pp  → 偏宽失效（噪声主导，建议收紧）
    其余          → 中性（边际，结合分层看）
"""

import argparse
import glob
import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASHARE = HERE / "data" / "ashare"
REPORTS = HERE / "reports"


def latest_list_file() -> Path:
    files = sorted(REPORTS.glob("strong_list_*.jsonl"))
    if not files:
        print("[exit 2] reports/ 下无 strong_list_*.jsonl", file=sys.stderr)
        sys.exit(2)
    return files[-1]


def load_list(path: Path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        print("[exit 2] 名单为空: %s" % path, file=sys.stderr)
        sys.exit(2)
    return rows


def pq_path(code: str) -> Path:
    return ASHARE / (code.replace(".", "_") + ".parquet")


def close_series(code: str):
    """返回 (trade_dates, closes)；无湖文件返回 None。"""
    f = pq_path(code)
    if not f.exists():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(f, columns=["trade_date", "close"])
    except Exception as ex:  # A5: 复盘样本静默缺失会污染 5 日窗统计——留痕
        print("[strong_review] parquet 读取失败 %s：%s" % (f, str(ex)[:60]))
        return None
    if df.empty:
        return None
    df = df.sort_values("trade_date")
    return list(df["trade_date"]), list(df["close"])


def window_return(series, asof_day: str, days: int):
    """asof_day（YYYYMMDD）为基准日：取 <=asof_day 最后收盘，往后数 days 个
    交易日收盘。数据不足返回 (基准日, None, 实际可得天数)。"""
    dates, closes = series
    i = len(dates) - 1
    while i >= 0 and dates[i] > asof_day:
        i -= 1
    if i < 0:
        return None, None, 0
    base_i = i
    j = min(i + days, len(dates) - 1)
    got = j - base_i
    ret = (closes[j] / closes[base_i] - 1.0) * 100.0 if closes[base_i] else None
    return dates[base_i], ret, got


def pick_baseline(asof_day: str, days: int, sample: int, exclude: set, seed=2026):
    files = [f for f in glob.glob(str(ASHARE / "*.parquet"))]
    codes = [Path(f).stem.replace("_", ".") for f in files]
    codes = [c for c in codes if c not in exclude]
    random.Random(seed).shuffle(codes)
    rets = []
    base_used = None
    short = 0
    for code in codes[: sample * 2]:  # 多备一倍弥补停牌/缺文件
        s = close_series(code)
        if s is None:
            continue
        d, r, got = window_return(s, asof_day, days)
        if d is None:
            continue
        base_used = base_used or d
        if got < days or r is None:
            short += 1
            continue
        rets.append(r)
        if len(rets) >= sample:
            break
    return rets, base_used, short


def stats_block(rets):
    if not rets:
        return {"n": 0}
    return {
        "n": len(rets),
        "median_pp": round(statistics.median(rets), 3),
        "mean_pp": round(statistics.mean(rets), 3),
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4),
        "p25_pp": round(sorted(rets)[len(rets) // 4], 3),
        "p75_pp": round(sorted(rets)[(len(rets) * 3) // 4], 3),
    }


def main():
    ap = argparse.ArgumentParser(description="strong 观察窗复盘")
    ap.add_argument("--list", default="", help="strong_list_*.jsonl（默认最新）")
    ap.add_argument("--days", type=int, default=3, help="观察窗交易日数（默认 3）")
    ap.add_argument("--sample", type=int, default=500, help="基线抽样只数")
    ap.add_argument(
        "--asof",
        default="",
        help="基准日 YYYYMMDD（默认=名单 ts 日的前一交易日，自动取名单首个 ts 日期）",
    )
    ap.add_argument("--save", default="", help="结果另存 json 路径")
    a = ap.parse_args()
    if a.days < 1:
        print(f"[exit 2] --days 必须 >=1（收到 {a.days}）：0/负数会产出无意义窗口")
        sys.exit(2)
    if a.sample < 1:
        print(f"[exit 2] --sample 必须 >=1（收到 {a.sample}）")
        sys.exit(2)

    path = Path(a.list) if a.list else latest_list_file()
    rows = load_list(path)
    codes = [r["code"] for r in rows]
    # 基准日：名单 ts（晨间时点）→ 当日；取湖内 <=当日 的最后收盘
    asof = a.asof or rows[0]["ts"][:10].replace("-", "")
    print("名单: %s（%d 只，ts=%s）" % (path.name, len(codes), rows[0]["ts"]))

    rets, base_day, missing = [], None, 0
    per_code = []
    for code in codes:
        s = close_series(code)
        if s is None:
            missing += 1
            continue
        d, r, got = window_return(s, asof, a.days)
        if d is None or r is None:
            missing += 1
            continue
        base_day = base_day or d
        got_days = got
        if got < a.days:
            short_flag = True
        else:
            short_flag = False
        if not short_flag:
            rets.append(r)
        per_code.append(
            {"code": code, "base_day": d, "ret_pp": r, "days_got": got_days}
        )

    if base_day is None:
        print(
            "[warn] 湖内无任何可用基准数据（asof=%s）——晨批产物当日傍晚前湖未更新属正常"
            % asof
        )
        sys.exit(0)

    full = sum(1 for p in per_code if p["days_got"] >= a.days)
    print(
        "基准日: %s | 观察窗: %d 交易日 | 完整窗口: %d/%d（缺湖/停牌 %d）"
        % (base_day, a.days, full, len(per_code), missing)
    )

    watch = stats_block(rets)
    exclude = set(codes)
    base_rets, base_day2, base_short = pick_baseline(asof, a.days, a.sample, exclude)
    base = stats_block(base_rets)
    print(
        "基线（抽样 %d 只同窗口）: %s"
        % (a.sample, json.dumps(base, ensure_ascii=False))
    )

    result = {
        "list_file": path.name,
        "asof": asof,
        "base_day": base_day,
        "days": a.days,
        "full_window": full,
        "watch": watch,
        "baseline": base,
    }
    if watch.get("n") and base.get("n"):
        excess = round(watch["median_pp"] - base["median_pp"], 3)
        result["excess_median_pp"] = excess
        if excess >= 1.0:
            verdict = "偏宽但有效（超额 >= +1pp，名单有信息量，倾向维持）"
        elif excess <= -1.0:
            verdict = "偏宽失效（超额 <= -1pp，噪声主导，建议收紧）"
        else:
            verdict = "中性（边际，结合 ex250 分层人工判断）"
        result["verdict"] = verdict
        print("超额（观察组中位 - 基线中位）: %+.3fpp → %s" % (excess, verdict))

    # ex250 分层（阈值边际效应：越宽的部分质量是否越差）
    by_code_ex = {r["code"]: r.get("ex250") for r in rows}
    tier_map = {"tight_10_15": [], "mid_15_25": [], "wide_25p": []}
    for p in per_code:
        if p["days_got"] < a.days or p["ret_pp"] is None:
            continue
        ex = by_code_ex.get(p["code"]) or 0.0
        key = "tight_10_15" if ex < 15 else ("mid_15_25" if ex <= 25 else "wide_25p")
        tier_map[key].append(p["ret_pp"])
    result["ex250_tiers"] = {k: stats_block(v) for k, v in tier_map.items()}
    for k, v in result["ex250_tiers"].items():
        if v.get("n"):
            print(
                "  ex250 分层 %s: n=%d 中位 %+.3fpp 胜率 %.1f%%"
                % (k, v["n"], v["median_pp"], v["win_rate"] * 100)
            )

    if a.save:
        Path(a.save).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("已保存: %s" % a.save)


if __name__ == "__main__":
    main()
