# -*- coding: utf-8 -*-
"""JEV 后验校准 POC v2（K-DATA-20261026-JEVCAL，JEV × yz-substrate P0）。

只读：news.db 已判 JEV 的新闻 + 行情湖日线 → 计算 T+1 超额收益，
输出 JEV 的预测力证据（这是 yz-substrate V14 baseline_report 的 JEV 版）：

  口径 v2（待老田确认）：
    · 信号日 = 新闻 ts 日期；持有日 = 其后第一个交易日（T+1 收盘）
    · 标的收益 = 关联 A 股 T+1 pct_chg 等权均值
    · 超额 = 标的收益 − 沪深300 同日 pct_chg
    · 方向类（bull/bear）：方向准确率 = sign(超额) 是否与 bias 一致
    · 中性类（neutral）：中性带命中 = |超额| ≤ NEUTRAL_BAND
    · conf 校准曲线 **只在方向类上算**（中性没有方向可分，混算会稀释信号）
  不做任何写库操作。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import logging

ROOT = Path(r"C:\项目\老田的AI助手")
NEWS_DB = ROOT / "news_collector" / "news.db"
CURATED = ROOT / "quant_backtest" / "data" / "tushare_curated"
REPORT_DIR = ROOT / "quant_backtest" / "reports"
BENCH_CODE = "000300.SH"
NEUTRAL_BAND = 1.0          # 中性带 ±1%
CALIB_MIN_N = 20
CONF_BUCKETS = [(0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 1.01)]


def norm_ts_code(code: str) -> str | None:
    s = str(code or "").strip()
    if not s.isdigit() or len(s) != 6:
        return None
    if s[0] == "6":
        return s + ".SH"
    if s[0] in ("0", "3"):
        return s + ".SZ"
    if s[0] in ("8", "4"):
        return s + ".BJ"
    return None


def load_open_days() -> list[str]:
    t = pq.read_table(CURATED / "trade_cal.parquet",
                      columns=["exchange", "cal_date", "is_open"],
                      filters=[("exchange", "=", "SSE"), ("is_open", "=", 1)])
    return sorted(str(x) for x in t.column("cal_date").to_pylist())


def next_trade_day(day: str, opens: list[str]) -> str | None:
    lo, hi = 0, len(opens)
    while lo < hi:
        mid = (lo + hi) // 2
        if opens[mid] <= day:
            lo = mid + 1
        else:
            hi = mid
    return opens[lo] if lo < len(opens) else None


def load_judged(days: int) -> list[dict]:
    conn = sqlite3.connect(NEWS_DB, timeout=10)
    try:
        since = conn.execute("SELECT date('now', ?)", ("-%d day" % days,)).fetchone()[0]
        rows = conn.execute(
            "SELECT id, ts, source, stocks, jev FROM news "
            "WHERE jev IS NOT NULL AND jev<>'' AND ts>=? ORDER BY ts", (since,)).fetchall()
    finally:
        conn.close()
    out = []
    for nid, ts, source, stocks, jev in rows:
        try:
            j = json.loads(jev or "{}")
        except ValueError:
            continue
        if not isinstance(j, dict) or j.get("bias") not in ("bull", "bear", "neutral"):
            continue
        codes = []
        try:
            codes += [norm_ts_code(c) for c in (json.loads(stocks or "[]") or [])]
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_posterior_poc.py", exc)
        codes += [norm_ts_code(c) for c in (j.get("companies") or [])]
        codes = sorted({c for c in codes if c})
        out.append({"id": nid, "ts": str(ts), "source": source, "codes": codes,
                    "bias": j.get("bias"), "conf": float(j.get("conf") or 0.0),
                    "event_type": j.get("event_type") or "unknown"})
    return out


def load_returns(dates: set[str], codes: set[str]) -> tuple[dict, dict]:
    stock: dict = {}
    bench: dict = {}
    if not dates:
        return stock, bench
    t = pq.read_table(CURATED / "daily.parquet",
                      columns=["ts_code", "trade_date", "pct_chg"])
    mask = pc.and_(pc.is_in(t["trade_date"], value_set=pa.array(sorted(dates))),
                   pc.is_in(t["ts_code"], value_set=pa.array(sorted(codes))))
    t = t.filter(mask)
    for c, d, p in zip(t.column("ts_code").to_pylist(), t.column("trade_date").to_pylist(),
                       t.column("pct_chg").to_pylist()):
        if p is not None:
            stock[(str(c), str(d))] = float(p)
    ti = pq.read_table(CURATED / "index_daily.parquet",
                       columns=["ts_code", "trade_date", "pct_chg"])
    mi = pc.and_(pc.equal(ti["ts_code"], BENCH_CODE),
                 pc.is_in(ti["trade_date"], value_set=pa.array(sorted(dates))))
    ti = ti.filter(mi)
    for d, p in zip(ti.column("trade_date").to_pylist(), ti.column("pct_chg").to_pylist()):
        if p is not None:
            bench[str(d)] = float(p)
    return stock, bench


def bucket_of(conf: float) -> str:
    for lo, hi in CONF_BUCKETS:
        if lo <= conf < hi:
            return "[%.2f,%.2f)" % (lo, hi)
    return "[0.85,1.01)"


def wilson(p: float, n: int, z: float = 1.96) -> list[float]:
    if n <= 0:
        return [0.0, 0.0]
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def rate(rows: list, pred) -> dict:
    n = len(rows)
    hits = sum(1 for r in rows if pred(r))
    return {"n": n, "hits": hits, "rate": round(hits / n, 3) if n else None,
            "wilson95": wilson(hits / n, n) if n else [0.0, 0.0]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="JEV 后验校准 POC v2（只读）")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--band", type=float, default=NEUTRAL_BAND)
    args = ap.parse_args(argv)
    band = args.band
    t0 = time.time()

    print("[1/4] 读已判新闻…", flush=True)
    judged = load_judged(args.days)
    with_codes = [r for r in judged if r["codes"]]
    print("  已判 %d；带标的 %d（%.1f%%）" % (
        len(judged), len(with_codes), 100.0 * len(with_codes) / max(len(judged), 1)), flush=True)

    print("[2/4] T+1 日…", flush=True)
    opens = load_open_days()
    for r in with_codes:
        r["trade_day"] = next_trade_day(r["ts"][:10].replace("-", ""), opens)
    with_codes = [r for r in with_codes if r.get("trade_day")]
    dates = {r["trade_day"] for r in with_codes}
    codes = {c for r in with_codes for c in r["codes"]}
    print("  T+1 交易日 %d 个 / 代码 %d 个" % (len(dates), len(codes)), flush=True)

    print("[3/4] 行情…", flush=True)
    stock_px, bench_px = load_returns(dates, codes)
    joined, dropped = [], {"no_bench_future": 0, "no_price": 0}
    for r in with_codes:
        day = r["trade_day"]
        if day not in bench_px:
            dropped["no_bench_future"] += 1
            continue
        vals = [stock_px[(c, day)] for c in r["codes"] if (c, day) in stock_px]
        if not vals:
            dropped["no_price"] += 1
            continue
        ret = sum(vals) / len(vals)
        r["ret"] = round(ret, 3)
        r["excess"] = round(ret - bench_px[day], 3)
        r["dir_ok"] = (r["bias"] == "bull" and r["excess"] > 0) or \
                      (r["bias"] == "bear" and r["excess"] < 0)
        r["neutral_ok"] = abs(r["excess"]) <= band
        joined.append(r)
    print("  可评估 %d（未来交易日 %d / 无行情 %d）" % (
        len(joined), dropped["no_bench_future"], dropped["no_price"]), flush=True)

    print("[4/4] 统计…", flush=True)
    directional = [r for r in joined if r["bias"] in ("bull", "bear")]
    neutral = [r for r in joined if r["bias"] == "neutral"]
    by_bias = {}
    for b in ("bull", "bear", "neutral"):
        sub = [r for r in joined if r["bias"] == b]
        if not sub:
            continue
        if b == "neutral":
            by_bias[b] = {"n": len(sub), "neutral_band_rate": rate(sub, lambda r: r["neutral_ok"])["rate"],
                          "mean_excess": round(sum(r["excess"] for r in sub) / len(sub), 3)}
        else:
            by_bias[b] = {"n": len(sub), "direction_accuracy": rate(sub, lambda r: r["dir_ok"])["rate"],
                          "mean_excess": round(sum(r["excess"] for r in sub) / len(sub), 3)}
    calib = []
    for lo, hi in CONF_BUCKETS:
        label = "[%.2f,%.2f)" % (lo, hi)
        sub = [r for r in directional if lo <= r["conf"] < hi]
        if len(sub) >= CALIB_MIN_N:
            calib.append({"bucket": label, **rate(sub, lambda r: r["dir_ok"])})
    events = {}
    for ev in sorted({r["event_type"] for r in joined}):
        sub = [r for r in joined if r["event_type"] == ev]
        if len(sub) < CALIB_MIN_N:
            continue
        dsub = [r for r in sub if r["bias"] in ("bull", "bear")]
        events[ev] = {"n": len(sub),
                      "direction_accuracy": rate(dsub, lambda r: r["dir_ok"])["rate"] if dsub else None,
                      "mean_excess": round(sum(r["excess"] for r in sub) / len(sub), 3)}

    base_bull = rate(directional, lambda r: r["excess"] > 0)          # 全判 bull
    base_bear = rate(directional, lambda r: r["excess"] < 0)          # 全判 bear
    base_neutral = rate(neutral, lambda r: r["neutral_ok"])           # 全判 neutral
    dir_rate = rate(directional, lambda r: r["dir_ok"])
    mean_ex = {b: by_bias[b]["mean_excess"] for b in by_bias}

    report = {
        "date": time.strftime("%Y%m%d"), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": {"days": args.days, "neutral_band_pct": band, "bench": BENCH_CODE,
                  "return_def": "T+1 close pct_chg mean; excess vs bench"},
        "coverage": {"judged": len(judged), "with_codes": len(with_codes),
                     "evaluable": len(joined), "directional": len(directional),
                     "neutral": len(neutral), "dropped": dropped,
                     "bias_dist": {b: sum(1 for r in joined if r["bias"] == b) for b in ("bull", "bear", "neutral")},
                     "trade_days": len(dates)},
        "overall": {"directional_accuracy": dir_rate,
                    "neutral_band_rate": rate(neutral, lambda r: r["neutral_ok"])},
        "by_bias": by_bias, "mean_excess_by_bias": mean_ex,
        "calibration_directional": calib,
        "by_event_type": events,
        "baselines": {"all_bull_on_directional": base_bull,
                      "all_bear_on_directional": base_bear,
                      "all_neutral_on_neutral": base_neutral},
        "samples": [{k: r[k] for k in ("id", "ts", "bias", "conf", "event_type", "codes",
                                       "trade_day", "ret", "excess", "dir_ok", "neutral_ok")}
                    for r in joined[:300]],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = REPORT_DIR / ("jev_posterior_%s.json" % report["date"])
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    L = [
        "# JEV 后验校准 POC（%s）" % report["generated_at"], "",
        "> K-DATA-20261026-JEVCAL ｜ 只读 ｜ 口径：信号日 T+1 收盘，超额 vs 沪深300，中性带 ±%.1f%%" % band, "",
        "## 覆盖", "",
        "- 已判 %d；带 A 股标的 %d（%.1f%%）；可评估 **%d**（方向类 %d / 中性 %d）" % (
            len(judged), len(with_codes), 100.0 * len(with_codes) / max(len(judged), 1),
            len(joined), len(directional), len(neutral)),
        "- 未来交易日暂不可评估 %d；无行情 %d；参与评估的交易日 %d 个" % (
            dropped["no_bench_future"], dropped["no_price"], len(dates)), "",
        "## 核心结果", "",
        "| 指标 | 值 | 样本 |", "|---|---|---|",
        "| 方向类准确率（bull/bear 方向对） | **%.1f%%** | %d |" % (
            (dir_rate["rate"] or 0) * 100, dir_rate["n"]),
        "| 中性带命中（neutral, \\|超额\\|≤%.1f%%） | %.1f%% | %d |" % (
            band, (rate(neutral, lambda r: r["neutral_ok"])["rate"] or 0) * 100, len(neutral)),
        "",
        "## 按 bias", "",
        "| bias | n | 方向准确率/中性带命中 | 平均超额(%) |", "|---|---|---|---|",
    ]
    for b, s in by_bias.items():
        metric = s.get("direction_accuracy", s.get("neutral_band_rate"))
        L.append("| %s | %d | %s | %s |" % (b, s["n"], ("%.1f%%" % (metric * 100)) if metric is not None else "-", s["mean_excess"]))
    L += ["", "## conf 校准曲线（仅方向类，n≥%d）" % CALIB_MIN_N, "",
          "| conf 桶 | n | 方向准确率 | 95% CI |", "|---|---|---|---|"]
    for c in calib:
        L.append("| %s | %d | %.1f%% | %.1f–%.1f%% |" % (
            c["bucket"], c["n"], (c["rate"] or 0) * 100,
            c["wilson95"][0] * 100, c["wilson95"][1] * 100))
    if not calib:
        L.append("| （样本不足） | - | - | - |")
    L += ["", "## 朴素基线（同分母对照）", "",
          "| 基线 | 命中/准确率 | n |", "|---|---|---|",
          "| JEV 方向判读 | %.1f%% | %d |" % ((dir_rate["rate"] or 0) * 100, dir_rate["n"]),
          "| 全判 bull | %.1f%% | %d |" % ((base_bull["rate"] or 0) * 100, base_bull["n"]),
          "| 全判 bear | %.1f%% | %d |" % ((base_bear["rate"] or 0) * 100, base_bear["n"]),
          "| 全判 neutral（中性带） | %.1f%% | %d |" % ((base_neutral["rate"] or 0) * 100, base_neutral["n"]),
          "", "## 按事件类型（n≥%d）" % CALIB_MIN_N, "",
          "| event_type | n | 方向准确率 | 平均超额(%) |", "|---|---|---|---|"]
    for ev, s in events.items():
        da = s["direction_accuracy"]
        L.append("| %s | %d | %s | %s |" % (ev, s["n"], ("%.1f%%" % (da * 100)) if da is not None else "-", s["mean_excess"]))
    L += ["", "## 口径与局限", "",
          "- T+1 收盘口径；盘中/盘后新闻未区分（简化）。",
          "- 仅覆盖带关联 A 股标的的判读（12%），macro 类事件未纳入。",
          "- conf 校准曲线只算方向类（中性类没有方向误差，混入会稀释信号）。",
          "- 单日样本受当日新闻量影响，样本量见各表 n。", ""]
    out_md = REPORT_DIR / ("jev_posterior_%s.md" % report["date"])
    out_md.write_text("\n".join(L), encoding="utf-8")

    print("\n== 结果 ==", flush=True)
    print("  方向准确率: %.1f%% (n=%d, 全判bull=%.1f%%/全判bear=%.1f%%)" % (
        (dir_rate["rate"] or 0) * 100, dir_rate["n"],
        (base_bull["rate"] or 0) * 100, (base_bear["rate"] or 0) * 100), flush=True)
    print("  平均超额:", mean_ex, flush=True)
    print("  校准曲线:", [(c["bucket"], c["n"], c["rate"]) for c in calib], flush=True)
    print("  报告:", out_json, "| 耗时 %.1fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
