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
import logging

try:  # ENCPROT 编码保护
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_audit.py", exc)

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

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
    # 2026-10-30 BATCH1：原实现只收裸 6 位，拒绝 "600519.SH"/"000001.SZ" 与
    # 北交所 920 段（DB 中 1,081 行可回收代码、175 个 920 token 被丢）。
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    if not s.isdigit() or len(s) != 6:
        return None
    if s[0] == "6":
        return s + ".SH"
    if s[0] in ("0", "3"):
        return s + ".SZ"
    if s[0] in ("8", "4") or s.startswith("920"):
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
            "SELECT id, ts, source, stocks, jev, created_at FROM news "
            "WHERE jev IS NOT NULL AND jev<>'' AND ts>=? ORDER BY ts", (since,)).fetchall()
    finally:
        conn.close()
    out = []
    for nid, ts, source, stocks, jev, created_at in rows:
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
            "news_id": str(nid), "ts": str(ts), "created_at": str(created_at or ""),
            "source": str(source or ""),
            "codes": codes, "bias": j.get("bias"),
            "conf": float(j.get("conf") or 0.0),
            "event_type": j.get("event_type") or "unknown",
                "model": str((j.get("meter") or {}).get("model") or "unknown"),
            "prompt_version": str((j.get("meter") or {}).get("prompt") or "unknown"),
            "horizon": str(j.get("horizon") or "unknown"),
            "prob": json.dumps(j.get("prob") or {}, ensure_ascii=False, sort_keys=True),
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
    ts TEXT, created_at TEXT, trade_day TEXT, source TEXT,
    bias TEXT, conf REAL, event_type TEXT, companies TEXT,
    model TEXT, prompt_version TEXT,
    horizon TEXT, prob TEXT,
    ret REAL, bench REAL, excess REAL, hit INTEGER,
    posterior_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dec_audit_trade_day ON dec_audit(trade_day);
"""


def open_db() -> sqlite3.Connection:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUDIT_DB, timeout=15)
    conn.executescript(SCHEMA)
    # 2026-10-30 BATCH1：老库迁移（created_at 用于 T+1 信号日口径与溯源）
    cols = {row[1] for row in conn.execute("PRAGMA table_info(dec_audit)")}
    if "created_at" not in cols:
        conn.execute("ALTER TABLE dec_audit ADD COLUMN created_at TEXT")
    # 2026-10-30 JEVV2：契约 v2 字段（horizon/prob）落账本，便于按时间跨度分组评估
    if "horizon" not in cols:
        conn.execute("ALTER TABLE dec_audit ADD COLUMN horizon TEXT")
    if "prob" not in cols:
        conn.execute("ALTER TABLE dec_audit ADD COLUMN prob TEXT")
    conn.commit()
    return conn


def _signal_base_day(ts: str, created_at: str) -> str:
    """信息可得日的近似口径（2026-10-30 BATCH1）。

    news.ts 常是「发布日/占位日」（公告常写成次日 00:00），而 created_at 是采集时间；
    当 created_at 日期早于 ts 日期时（9,749 行实证），说明信息实际在更早的交易日已可得，
    用 created_at 日做 T+1 信号日基准（否则会晚一个交易日、把开市日收益算丢）。
    """
    ts_day = str(ts or "")[:10]
    ca_day = str(created_at or "")[:10]
    if ca_day and ts_day and ca_day < ts_day:
        return ca_day
    return ts_day


def upsert_judged(conn: sqlite3.Connection, rows: list[dict], opens: list[str]) -> int:
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    n = 0
    for r in rows:
        day = _signal_base_day(r.get("ts"), r.get("created_at")).replace("-", "")
        trade_day = next_trade_day(day, opens)
        old = conn.execute(
            "SELECT bias, companies, trade_day FROM dec_audit WHERE news_id=?",
            (r["news_id"],)).fetchone()
        companies = json.dumps(r["codes"])
        if old is not None and (old[0] != r["bias"] or old[1] != companies
                                or old[2] != trade_day):
            # 判定/口径变化 → 清空旧后验（否则 fill_posterior 因 posterior_at 非空不再算）
            conn.execute(
                "UPDATE dec_audit SET ret=NULL, bench=NULL, excess=NULL, hit=NULL,"
                " posterior_at=NULL WHERE news_id=?", (r["news_id"],))
        conn.execute(
            "INSERT INTO dec_audit(news_id, ts, created_at, trade_day, source, bias, conf,"
            " event_type, companies, model, prompt_version, horizon, prob, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(news_id) DO UPDATE SET"
            " ts=excluded.ts, created_at=excluded.created_at,"
            " trade_day=excluded.trade_day, source=excluded.source,"
            " bias=excluded.bias, conf=excluded.conf, event_type=excluded.event_type,"
            " companies=excluded.companies, model=excluded.model,"
            " prompt_version=excluded.prompt_version, horizon=excluded.horizon,"
            " prob=excluded.prob, updated_at=excluded.updated_at",
            (r["news_id"], r["ts"], r.get("created_at") or "", trade_day, r["source"],
             r["bias"], r["conf"], r["event_type"], companies, r["model"],
             r["prompt_version"], r.get("horizon") or "unknown",
             (r.get("prob") if isinstance(r.get("prob"), str)
              else json.dumps(r.get("prob") or {}, ensure_ascii=False, sort_keys=True)), now))
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


def export_posterior_jsonl(conn: sqlite3.Connection, day: str | None = None) -> Path:
    """供 yz-substrate refill 消费的文件契约（按本地日期整日确定性写入）。

    2026-10-30 BATCH1 修复：
    - 旧实现用 SQLite UTC `date('now')` 过滤、却用本地 `strftime` 命名/写 recorded_at：
      早间运行时会把「昨天的后验」写进「今天的文件名」，同日再跑又把文件覆盖成更小集合
      （实证：posterior_20261026 与 _27 news_id 集合完全相同；DB 6,659 行 vs 文件并集
      4,640，缺 2,019 行且无法自动恢复）。
    - 现按「本地日期」过滤 posterior_at，并把该日全部行按 news_id 排序后**整日重写**
      （重复运行结果幂等，不会丢失当天更早写入的行）。
    """
    day = day or time.strftime("%Y%m%d")
    day_iso = "%s-%s-%s" % (day[:4], day[4:6], day[6:8])
    rows = conn.execute(
        "SELECT news_id, ts, trade_day, companies, bias, conf, event_type, ret, bench,"
        " excess, hit FROM dec_audit"
        " WHERE posterior_at IS NOT NULL AND substr(posterior_at,1,10)=?"
        " ORDER BY news_id", (day_iso,)).fetchall()
    out = AUDIT_DIR / ("posterior_%s.jsonl" % day)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({
                "news_id": r[0], "point_id": "jev.bias", "ts": r[1], "trade_day": r[2],
                "codes": json.loads(r[3] or "[]"), "bias": r[4], "conf": r[5],
                "event_type": r[6], "ret": r[7], "bench": r[8], "excess": r[9],
                "hit": r[10], "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }, ensure_ascii=False) + "\n")
    return out


def repair_posterior_exports(conn: sqlite3.Connection) -> dict:
    """按 DB 里出现过的所有 posterior 本地日期，整日重写 jsonl 并产出索引。

    用途：修复 2026-10-30 前「同日覆盖/UTC 错位」造成的缺行；索引给出
    DB 总量、文件并集、缺失量，供 yz refill 与健康卡核对。
    """
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(posterior_at,1,10) FROM dec_audit"
        " WHERE posterior_at IS NOT NULL ORDER BY 1").fetchall()]
    by_day = {}
    for day_iso in days:
        out = export_posterior_jsonl(conn, day=day_iso.replace("-", ""))
        with open(out, encoding="utf-8") as fh:
            by_day[day_iso] = sum(1 for _ in fh)
    db_ids = {r[0] for r in conn.execute(
        "SELECT news_id FROM dec_audit WHERE posterior_at IS NOT NULL").fetchall()}
    union_ids = set()
    for fp in sorted(AUDIT_DIR.glob("posterior_*.jsonl")):
        try:
            with open(fp, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        union_ids.add(json.loads(line)["news_id"])
                    except (ValueError, KeyError):
                        continue
        except OSError:
            continue
    index = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_db_posterior": len(db_ids),
        "exported_union": len(union_ids),
        "missing": len(db_ids - union_ids),
        "missing_sample": sorted(db_ids - union_ids)[:20],
        "by_day": by_day,
    }
    (AUDIT_DIR / "posterior_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def build_report(conn: sqlite3.Connection, band: float) -> dict:
    rows = conn.execute(
        "SELECT bias, conf, event_type, excess, hit, trade_day FROM dec_audit"
        " WHERE posterior_at IS NOT NULL").fetchall()
    counts = conn.execute(
        "SELECT count(*),"
        " sum(CASE WHEN companies IS NOT NULL AND companies<>'[]' THEN 1 ELSE 0 END)"
        " FROM dec_audit").fetchone()
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
    # 2026-10-30 BATCH1：覆盖率与数据截止日必须显式披露（否则读者把 6,659 当全量、
    # 把 10-23 冻结的样本当 10-30 的当前值）。
    judged_total, linked_total = int(counts[0] or 0), int(counts[1] or 0)
    try:
        opens = load_open_days()
    except Exception:  # noqa: BLE001 - 报告不因行情文件缺失失败，标 null
        opens = []
    data_through = max((r[5] for r in rows if r[5]), default=None)
    # 2026-10-30 STALEFIX：不能用交易日历的最大日期（日历含未来，会把"落后"算成 293）。
    # 只统计「已实现」交易日：<= 今天的最后一个开市日。
    today_day = dt.date.today().strftime("%Y%m%d")
    realized = [d for d in opens if d <= today_day]
    latest_trade_day = realized[-1] if realized else None
    stale_trade_days = None
    if data_through and latest_trade_day:
        stale_trade_days = sum(1 for d in opens if data_through < d <= latest_trade_day)
    by_trade_day = []
    for day in sorted({r[5] for r in rows if r[5]}):
        sub = [r for r in rows if r[5] == day]
        dir_sub = [r for r in sub if r[0] in ("bull", "bear")]
        if not dir_sub:
            continue
        acc_sub = acc(dir_sub)
        hit_rate = acc_sub["rate"]
        by_trade_day.append({
            "trade_day": day, "n": len(dir_sub), "accuracy": hit_rate,
            "edge_vs_baseline": (round(hit_rate - all_bull["rate"], 3)
                                 if hit_rate is not None and all_bull["rate"] is not None else None),
        })
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": {"band_pct": band, "bench": BENCH_CODE,
                  "return_def": "T+1 close pct_chg mean; excess vs bench"},
        "judged_total": judged_total,
        "linked_total": linked_total,
        "priced_total": len(rows),
        "coverage": {"linked": round(linked_total / judged_total, 4) if judged_total else None,
                     "priced": round(len(rows) / judged_total, 4) if judged_total else None},
        "data_through": data_through,
        "latest_trade_day": latest_trade_day,
        "stale_trade_days": stale_trade_days,
        "n_trade_days": len(by_trade_day),
        "ledger_total": len(rows),
        "by_trade_day": by_trade_day,
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
# 2026-10-30 BATCH1：拍板要求「edge≥2pp 且 Wilson 下界>基线 且 n_days≥20」；
# 原实现只看点估计 edge，5 个交易日就能「变绿」。
ASSESS_MIN_DAYS = 20
# 2026-10-29 PRICE-DEMOTE：事件类型样本门槛与低信号阈值（n>=100 且 acc<50% → low_signal）
ASSESS_EVENT_MIN_N = 100
ASSESS_EVENT_LOW_RATE = 0.50


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
    # 事件类型可用性（2026-10-29 PRICE-DEMOTE；决策见 docs/plans 判定中台拍板文档）
    event_flags = {}
    for ev, sub in (report.get("by_event_type") or {}).items():
        ev_n = int(sub.get("n") or 0)
        ev_rate = sub.get("rate")
        if ev_n < ASSESS_EVENT_MIN_N or ev_rate is None:
            continue
        if ev_rate < ASSESS_EVENT_LOW_RATE:
            event_flags[ev] = {"n": ev_n, "accuracy": ev_rate, "use": "low_signal",
                               "note": "方向准确率<50%，不作方向信号（价格类等噪声事件）"}
    return {
        "min_n": ASSESS_MIN_N,
        "edge_pp": ASSESS_EDGE_PP,
        "directional": {
            "n": n, "accuracy": rate, "baseline_all_bull": base_rate,
            "edge_vs_baseline": (round(rate - base_rate, 3)
                                 if rate is not None and base_rate is not None else None),
            "wilson95_lower": ((report.get("directional") or {}).get("wilson95") or [None, None])[0],
            "n_days": report.get("n_trade_days"),
            "gate": "edge>=2pp AND wilson_lower>baseline AND n_days>=20",
            "gate_pass": bool(
                rate is not None and base_rate is not None and n >= ASSESS_MIN_N
                and (rate - base_rate) >= ASSESS_EDGE_PP
                and (((report.get("directional") or {}).get("wilson95") or [0])[0] > base_rate)
                and (report.get("n_trade_days") or 0) >= ASSESS_MIN_DAYS),
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
        "event_types": event_flags,
        "low_signal_event_types": sorted(event_flags),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JEV 判读账本 + 后验回填 + 校准报告（只读 news.db）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--band", type=float, default=DEFAULT_BAND)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--repair-exports", action="store_true",
                    help="按 DB 内所有 posterior 日期整日重写 jsonl 并生成 posterior_index.json")
    args = ap.parse_args(argv)
    t0 = time.time()
    conn = open_db()
    if not args.report_only:
        opens = load_open_days()
        judged = load_judged(args.days)
        n_up = upsert_judged(conn, judged, opens)
        n_post = fill_posterior(conn, args.band)
        if args.repair_exports:
            idx = repair_posterior_exports(conn)
            jsonl = AUDIT_DIR / "posterior_index.json"
            print("[1/2] 导出修复: DB后验 %d / 文件并集 %d / 缺失 %d（by_day=%s）"
                  % (idx["total_db_posterior"], idx["exported_union"], idx["missing"],
                     json.dumps(idx["by_day"], ensure_ascii=False)), flush=True)
        else:
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
    print("[2/2] 报告: 已判 %d / 带码 %d / 可定价 %d；方向类 %d 条 准确率 %s%% | 全判 bull 基线 %s%%"
          " | data_through=%s n_days=%s" % (
        report["judged_total"], report["linked_total"], report["priced_total"], d["n"],
        round((d["rate"] or 0) * 100, 1), round((d["baseline_all_bull"]["rate"] or 0) * 100, 1),
        report.get("data_through"), report.get("n_trade_days")),
        flush=True)
    print("  jsonl:", jsonl or "(report-only)", flush=True)
    print("  report:", out, "| 耗时 %.1fs" % (time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
