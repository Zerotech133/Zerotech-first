# -*- coding: utf-8 -*-
"""JEV 判读账本 + 后验回填 + 校准报告（K-DATA-20261026-JEVAUDIT，P1-lite）。

设计约束（安全第一）：
  · **只读 news.db**（`mode=ro`），不碰今晚夜跑的写入路径；
  · 只写自己的派生库 `agent_platform/data/jev_audit/jev_audit.db`（gitignore）；
  · 由 `\\JevDriftDaily` 07:10 在夜跑收工后调用，不阻塞退出码。

产物：
  · `jev_audit.db`    —— 判定账本（含 T+1 posterior：ret/bench/excess/hit）
  · `posterior_<date>.jsonl` —— 与 yz-substrate 的 refill 文件契约
      每行 {news_id, point_id:"jev.bias", ts, trade_day, codes, bias, conf,
            event_type, ret, bench, excess, hit, recorded_at}
  · `baseline_<date>.json` / `latest.json` —— 校准报告（方向准确率、按 conf 分桶、
      朴素基线、按事件类型），供后续 V14 baseline_report 消费。

口径（与 POC v2 一致，待老田最终确认）：
  信号日 = news.ts 日期；持有日 = 其后第一个 SSE 交易日（T+1 收盘）；
  标的收益 = 关联 A 股等权 pct_chg 均值；超额 = 标的 − 沪深300；
  命中：bull→excess>0 / bear→excess<0 / neutral→|excess|<=band（默认 1%）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

try:  # ENCPROT 编码保护
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_audit.py", exc)

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import logging

ROOT = Path(r"C:\项目\老田的AI助手")
NEWS_DB = Path(r"C:\aios\news_collector\news.db")
CURATED = ROOT / "quant_backtest" / "data" / "tushare_curated"
AUDIT_DIR = ROOT / "agent_platform" / "data" / "jev_audit"
AUDIT_DB = AUDIT_DIR / "jev_audit.db"
BENCH_CODE = "000300.SH"
DEFAULT_DAYS = 45
DEFAULT_BAND = 2.5  # 2026-10-26 JEVCAL2：neutral |excess| P70≈2.55%，1% 带宽过窄（命中仅 32.8%）
CALIB_MIN_N = 20
CONF_BUCKETS = [(0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 1.01)]


# ── 纯函数（可单测） ──────────────────────────────────────────
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


def next_trade_day(day: str, opens: list[str]) -> str | None:
    lo, hi = 0, len(opens)
    while lo < hi:
        mid = (lo + hi) // 2
        if opens[mid] <= day:
            lo = mid + 1
        else:
            hi = mid
    return opens[lo] if lo < len(opens) else None


def hit_of(bias: str, excess: float, band: float = DEFAULT_BAND) -> int:
    if bias == "bull":
        return 1 if excess > 0 else 0
    if bias == "bear":
        return 1 if excess < 0 else 0
    return 1 if abs(excess) <= band else 0


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


def rate(n_hit: int, n: int) -> dict:
    r = round(n_hit / n, 3) if n else None
    return {"n": n, "hits": n_hit, "rate": r, "wilson95": wilson(n_hit / n, n) if n else [0.0, 0.0]}


# ── 数据读取 ─────────────────────────────────────────────────
def load_open_days() -> list[str]:
    t = pq.read_table(CURATED / "trade_cal.parquet",
                      columns=["exchange", "cal_date", "is_open"],
                      filters=[("exchange", "=", "SSE"), ("is_open", "=", 1)])
    return sorted(str(x) for x in t.column("cal_date").to_pylist())


def load_judged(days: int) -> list[dict]:
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=10)
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
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_audit.py", exc)
        codes += [norm_ts_code(c) for c in (j.get("companies") or [])]
        codes = sorted({c for c in codes if c})
        out.append({
            "news_id": str(nid), "ts": str(ts), "source": str(source or ""),
            "codes": codes, "bias": j.get("bias"),
            "conf": float(j.get("conf") or 0.0),
            "event_type": j.get("event_type") or "unknown",
            "model": str((j.get("meter") or {}).get("model") or "unknown"),
            "prompt_version": str((j.get("meter") or {}).get("prompt") or "unknown"),
        })
    return out


def load_returns(dates: set[str], codes: set[str]) -> tuple[dict, dict]:
    stock: dict = {}
    bench: dict = {}
    if not dates:
        return stock, bench
    t = pq.read_table(CURATED / "daily.parquet", columns=["ts_code", "trade_date", "pct_chg"])
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


# ── 账本 ─────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS dec_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT NOT NULL UNIQUE,
    ts TEXT, trade_day TEXT, source TEXT,
    bias TEXT, conf REAL, event_type TEXT, companies TEXT,
    model TEXT, prompt_version TEXT,
    ret REAL, bench REAL, excess REAL, hit INTEGER,
    posterior_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dec_audit_trade_day ON dec_audit(trade_day);
"""


def open_db() -> sqlite3.Connection:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUDIT_DB, timeout=15)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_judged(conn: sqlite3.Connection, rows: list[dict], opens: list[str]) -> int:
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    n = 0
    for r in rows:
        day = r["ts"][:10].replace("-", "")
        trade_day = next_trade_day(day, opens)
        conn.execute(
            "INSERT INTO dec_audit(news_id, ts, trade_day, source, bias, conf, event_type,"
            " companies, model, prompt_version, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(news_id) DO UPDATE SET"
            " ts=excluded.ts, trade_day=excluded.trade_day, source=excluded.source,"
            " bias=excluded.bias, conf=excluded.conf, event_type=excluded.event_type,"
            " companies=excluded.companies, model=excluded.model,"
            " prompt_version=excluded.prompt_version, updated_at=excluded.updated_at",
            (r["news_id"], r["ts"], trade_day, r["source"], r["bias"], r["conf"],
             r["event_type"], json.dumps(r["codes"]), r["model"], r["prompt_version"], now))
        n += 1
    conn.commit()
    return n


def fill_posterior(conn: sqlite3.Connection, band: float) -> int:
    rows = conn.execute(
        "SELECT seq, news_id, trade_day, bias, companies FROM dec_audit"
        " WHERE posterior_at IS NULL AND trade_day IS NOT NULL").fetchall()
    if not rows:
        return 0
    dates = {r[2] for r in rows}
    codes = set()
    for r in rows:
        try:
            codes.update(json.loads(r[4] or "[]"))
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_audit.py", exc)
    stock, bench = load_returns(dates, codes)
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    n = 0
    for seq, _nid, trade_day, bias, companies in rows:
        if trade_day not in bench:
            continue
        try:
            codes_list = json.loads(companies or "[]")
        except ValueError:
            continue
        vals = [stock[(c, trade_day)] for c in codes_list if (c, trade_day) in stock]
        if not vals:
            continue
        ret = sum(vals) / len(vals)
        excess = ret - bench[trade_day]
        conn.execute(
            "UPDATE dec_audit SET ret=?, bench=?, excess=?, hit=?, posterior_at=?"
            " WHERE seq=?", (round(ret, 3), round(bench[trade_day], 3),
                             round(excess, 3), hit_of(bias, excess, band), now, seq))
        n += 1
    # 带宽变化时重算 neutral 命中（bull/bear 与带宽无关，不动）
    conn.execute(
        "UPDATE dec_audit SET hit=CASE WHEN abs(excess)<=? THEN 1 ELSE 0 END"
        " WHERE posterior_at IS NOT NULL AND bias='neutral' AND excess IS NOT NULL",
        (band,))
    conn.commit()
    return n


def export_posterior_jsonl(conn: sqlite3.Connection) -> Path:
    """供 yz-substrate refill 消费的文件契约（当日后验，覆盖写）。"""
    today = time.strftime("%Y%m%d")
    rows = conn.execute(
        "SELECT news_id, ts, trade_day, companies, bias, conf, event_type, ret, bench, excess, hit"
        # 只导出「今天新回填」的后验（增量契约），避免每日重复全量
        " FROM dec_audit WHERE posterior_at IS NOT NULL AND date(posterior_at)=date('now')").fetchall()
    out = AUDIT_DIR / ("posterior_%s.jsonl" % today)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({
                "news_id": r[0], "point_id": "jev.bias", "ts": r[1], "trade_day": r[2],
                "codes": json.loads(r[3] or "[]"), "bias": r[4], "conf": r[5],
                "event_type": r[6], "ret": r[7], "bench": r[8], "excess": r[9],
                "hit": r[10], "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }, ensure_ascii=False) + "\n")
    return out


def build_report(conn: sqlite3.Connection, band: float) -> dict:
    rows = conn.execute(
        "SELECT bias, conf, event_type, excess, hit FROM dec_audit"
        " WHERE posterior_at IS NOT NULL").fetchall()
    directional = [r for r in rows if r[0] in ("bull", "bear")]
    neutral = [r for r in rows if r[0] == "neutral"]

    def acc(sub):
        return rate(sum(1 for r in sub if r[4]), len(sub))

    by_bias = {}
    for b in ("bull", "bear", "neutral"):
        sub = [r for r in rows if r[0] == b]
        if sub:
            by_bias[b] = {"n": len(sub), "accuracy": acc(sub)["rate"],
                          "mean_excess": round(sum(r[3] for r in sub) / len(sub), 3)}
    calib = []
    for lo, hi in CONF_BUCKETS:
        sub = [r for r in directional if lo <= r[1] < hi]
        if len(sub) >= CALIB_MIN_N:
            calib.append({"bucket": "[%.2f,%.2f)" % (lo, hi), **acc(sub)})
    events = {}
    for ev in sorted({r[2] for r in rows}):
        sub = [r for r in directional if r[2] == ev]
        if len(sub) >= CALIB_MIN_N:
            events[ev] = acc(sub)
    all_bull = rate(sum(1 for r in directional if r[3] > 0), len(directional))
    all_bear = rate(sum(1 for r in directional if r[3] < 0), len(directional))
    neutral_hit = acc(neutral)["rate"]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": {"band_pct": band, "bench": BENCH_CODE,
                  "return_def": "T+1 close pct_chg mean; excess vs bench"},
        "ledger_total": len(rows),
        "directional": {"n": len(directional), **acc(directional),
                        "baseline_all_bull": all_bull, "baseline_all_bear": all_bear},
        "neutral": {"n": len(neutral), "band_hit_rate": acc(neutral)["rate"]},
        "by_bias": by_bias,
        "calibration_directional": calib,
        "by_event_type": events,
    }
    report["neutral"]["highvol_share"] = (
        round(1.0 - neutral_hit, 3) if neutral_hit is not None else None)
    report["assessment"] = assess_report(report)
    return report


# 标定阈值（2026-10-26 JEVCAL2 数据版决策；样本门槛先保守取 1000）
ASSESS_MIN_N = 1000
ASSESS_BEAR_MAX_ACC = 0.53
ASSESS_EDGE_PP = 0.02


def assess_report(report: dict) -> dict:
    """把「能不能当信号用」从数据里判出来，供报告/健康卡消费（不改判读提示词）。"""
    d = report.get("directional") or {}
    base = d.get("baseline_all_bull") or {}
    n = int(d.get("n") or 0)
    rate = d.get("rate")
    base_rate = base.get("rate")
    by_bias = report.get("by_bias") or {}
    bull = by_bias.get("bull") or {}
    bear = by_bias.get("bear") or {}
    neutral = report.get("neutral") or {}
    band = (report.get("scope") or {}).get("band_pct")

    def _usable(sub, min_n, threshold):
        if not sub or (sub.get("n") or 0) < min_n or sub.get("accuracy") is None:
            return False, "样本不足(需≥%d)" % min_n
        if sub["accuracy"] < threshold:
            return False, "准确率 %.1f%% < %.1f%%" % (sub["accuracy"] * 100, threshold * 100)
        return True, "达标"

    bull_ok, bull_why = _usable(bull, ASSESS_MIN_N,
                                (base_rate or 0.5) + ASSESS_EDGE_PP)
    bear_ok, bear_why = _usable(bear, ASSESS_MIN_N, ASSESS_BEAR_MAX_ACC)
    return {
        "min_n": ASSESS_MIN_N,
        "edge_pp": ASSESS_EDGE_PP,
        "directional": {
            "n": n, "accuracy": rate, "baseline_all_bull": base_rate,
            "edge_vs_baseline": (round(rate - base_rate, 3)
                                 if rate is not None and base_rate is not None else None),
        },
        "bull": {"n": bull.get("n"), "accuracy": bull.get("accuracy"),
                 "usable": bull_ok, "reason": bull_why},
        "bear": {"n": bear.get("n"), "accuracy": bear.get("accuracy"),
                 "usable": bear_ok, "reason": bear_why},
        "neutral": {"band_pct": band,
                    "band_hit_rate": neutral.get("band_hit_rate"),
                    "highvol_share": neutral.get("highvol_share"),
                    "usable_as_direction": False,
                    "note": "neutral 仅作无方向判定；超出带宽者标记为高波动事件，不构成方向信号"},
        "confidence": {"decision_weight": False,
                       "note": "conf 分桶准确率非单调、corr(conf,hit)≈0：保留入账但退出决策权重"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JEV 判读账本 + 后验回填 + 校准报告（只读 news.db）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--band", type=float, default=DEFAULT_BAND)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    conn = open_db()
    if not args.report_only:
        opens = load_open_days()
        judged = load_judged(args.days)
        n_up = upsert_judged(conn, judged, opens)
        n_post = fill_posterior(conn, args.band)
        jsonl = export_posterior_jsonl(conn)
        print("[1/2] 账本: 判读 %d 条写入（新增/更新），后验回填 %d 条" % (n_up, n_post), flush=True)
    else:
        jsonl = None
    report = build_report(conn, args.band)
    today = time.strftime("%Y%m%d")
    out = AUDIT_DIR / ("baseline_%s.json" % today)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (AUDIT_DIR / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    d = report["directional"]
    print("[2/2] 报告: 账本 %d 条；方向类 %d 条 准确率 %s%% | 全判 bull 基线 %s%%" % (
        report["ledger_total"], d["n"],
        round((d["rate"] or 0) * 100, 1), round((d["baseline_all_bull"]["rate"] or 0) * 100, 1)),
        flush=True)
    print("  jsonl:", jsonl or "(report-only)", flush=True)
    print("  report:", out, "| 耗时 %.1fs" % (time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
