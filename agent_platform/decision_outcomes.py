# -*- coding: utf-8 -*-
"""D1 决策 outcome 台账（只读 sidecar，2026-10-29 · 鲁肃复盘整改 #2）。

读影子账本 ``agent_platform/data/shadow/shadow_ledger.jsonl``，按决策日后的
首个交易日收盘价计算次日收益/方向命中，写 sidecar
``agent_platform/data/shadow/decision_outcomes.jsonl``（**不改原账本**）。

字段：ts, run_id, decision_id, date, symbol, recommendation, confidence,
      price_at_decision, next_trade_date, next_close, next_day_return, hit,
      horizon_days, model, fallback（legacy 行可能为 null；legacy 行无
      decision_id 不报错，按缺失处理）。

decision_id（K-ORG-20261031-D1WRITER）：与 ``engine/decision_policy.py::
decision_id`` 同一公式 ``D1-<yyyymmdd>-sha256("symbol|date|run_id")[:10]``；
延迟 import 单一真源，引擎不可用时回退同公式本地实现。重复运行按 run_id
幂等去重，不重复追加。

``--since YYYY-MM-DD``（2026-10-31 WO-D1-OUTCOME-DAILY）：只回填该日期（含）
之后缺失的 outcome，用于日常增量；不带则补全历史缺失。输出新增行数 ``new``
与按日期分布 ``dates``。历史 outcome 行缺 ``decision_id``/``model`` 时按下表
从 shadow_ledger 同 run_id 补齐（decision_id 优先取账本行同名字段，否则按同
公式复算；model 取账本行值）；账本行缺失/字段不全 → 落 null，**不伪造**。
写盘改为「临时文件 + os.replace」原子重写，只在有新增或补字段时落盘。

用法::

    python -X utf8 agent_platform/decision_outcomes.py --compute
    python -X utf8 agent_platform/decision_outcomes.py --report --week 2026-W44
    python -X utf8 agent_platform/decision_outcomes.py --json
    python -X utf8 agent_platform/decision_outcomes.py --since 2026-10-30
    python -X utf8 agent_platform/decision_outcomes.py --since 2026-10-30 --no-write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(HERE, "data", "shadow", "shadow_ledger.jsonl")
DEFAULT_OUT = os.path.join(HERE, "data", "shadow", "decision_outcomes.jsonl")
DIRECTION = {"BUY": 1, "STRONG_BUY": 1, "SELL": -1, "STRONG_SELL": -1}

_POLICY_DECISION_ID = None


def _fallback_decision_id(symbol, date, run_id):
    """engine/decision_policy.py 不可用时的同公式回退（tests 断言两者一致）。"""
    raw = "%s|%s|%s" % (symbol, date, run_id)
    return "D1-%s-%s" % (str(date).replace("-", ""),
                         hashlib.sha256(raw.encode()).hexdigest()[:10])


def decision_id(symbol, date, run_id):
    """D1 decision_id：优先复用 engine/decision_policy.py 单一真源，失败回退同公式。

    延迟 import（脚本/测试无需预先配置 engine 路径）；不写盘、不联网。
    """
    global _POLICY_DECISION_ID
    if _POLICY_DECISION_ID is None:
        engine = os.path.join(HERE, "engine")
        if engine not in sys.path:
            sys.path.insert(0, engine)
        try:
            from decision_policy import decision_id as _policy_fn  # noqa: E402
        except Exception:  # noqa: BLE001 - 引擎目录不可用时按同公式回退
            _policy_fn = _fallback_decision_id
        _POLICY_DECISION_ID = _policy_fn
    return _POLICY_DECISION_ID(symbol, date, run_id)


def _ledger_by_run_id(rows):
    """shadow_ledger 行按 run_id 建索引（同 run_id 后写覆盖；空 run_id 跳过）。"""
    index = {}
    for row in rows or []:
        rid = str(row.get("run_id") or "")
        if rid:
            index[rid] = row
    return index


def _decision_id_from_ledger_row(ledger_row):
    """用 shadow_ledger 行复算 decision_id；字段不全/异常时返回 None（不伪造）。"""
    if not isinstance(ledger_row, dict):
        return None
    did = ledger_row.get("decision_id")
    if did not in (None, ""):
        return str(did)
    symbol = str(ledger_row.get("symbol") or "")
    date = str(ledger_row.get("date") or "")
    run_id = str(ledger_row.get("run_id") or "")
    if not (symbol and date and run_id):
        return None
    try:
        return decision_id(symbol, date, run_id)
    except Exception:  # noqa: BLE001 - 引擎异常不外溢（缺就 null）
        return None


def enrich_outcome_row(row, ledger_index):
    """按 run_id 从 shadow_ledger 补 outcome 行缺的 decision_id/model。

    - 只补空值（None/缺键），已有值一律不动；
    - decision_id 优先取账本行同名字段，否则按同公式复算；
    - model 取账本行值；账本行缺失/字段不全 → 落 null，**绝不编造**。
    返回实际补到值的字段名列表（用于 repaired 计数）。
    """
    filled = []
    rid = str(row.get("run_id") or "")
    ledger_row = ledger_index.get(rid) if rid else None
    if row.get("decision_id") in (None, ""):
        computed = _decision_id_from_ledger_row(ledger_row)
        if "decision_id" not in row or row.get("decision_id") != computed:
            row["decision_id"] = computed
        if computed is not None:
            filled.append("decision_id")
    if row.get("model") in (None, ""):
        model = (ledger_row.get("model")
                 if isinstance(ledger_row, dict) else None)
        if model not in (None, ""):
            row["model"] = model
            filled.append("model")
        elif "model" not in row:
            row["model"] = None
    return filled


def repair_outcomes(out_rows, ledger_rows):
    """补历史 outcome 行缺失字段（行数/顺序不变）；返回 (rows, report)。"""
    index = _ledger_by_run_id(ledger_rows)
    fields = {"decision_id": 0, "model": 0}
    repaired = 0
    rows = []
    for row in out_rows or []:
        new_row = dict(row)
        filled = enrich_outcome_row(new_row, index)
        if filled:
            repaired += 1
            for name in filled:
                fields[name] = fields.get(name, 0) + 1
        rows.append(new_row)
    return rows, {"repaired": repaired, "fields": fields}


def _write_jsonl_atomic(path, rows):
    """原子重写 JSONL（同目录临时文件 + os.replace），避免半行/半文件。"""
    path = str(path)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def first_next_bar(bars, date):
    """bars: [[date, open, close, high, low, volume], ...]；返回 date 后首个交易日。"""
    for b in bars or []:
        if len(b) >= 3 and str(b[0]) > str(date):
            try:
                return str(b[0]), float(b[2])
            except (TypeError, ValueError):
                continue
    return None, None


def compute_outcome(row, price_fetcher):
    """单行 outcome；无 run_id/价格/次日 bar 时返回 None。"""
    run_id = str(row.get("run_id") or "")
    date = str(row.get("date") or "")
    symbol = str(row.get("symbol") or "")
    if not run_id or not date or not symbol:
        return None
    try:
        price = float(row.get("price_at_decision"))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    try:
        bars = price_fetcher(symbol, 60) or []
    except Exception:  # noqa: BLE001
        return None
    next_date, next_close = first_next_bar(bars, date)
    if not next_date or next_close is None:
        return None
    ret = (next_close / price) - 1.0
    rec = str(row.get("recommendation") or "").upper()
    d = DIRECTION.get(rec)
    hit = None
    if d is not None:
        hit = 1 if (ret > 0 if d > 0 else ret < 0) else 0
    try:
        conf = float(row.get("confidence"))
    except (TypeError, ValueError):
        conf = None
    return {
        "ts": time.time(),
        "run_id": run_id,
        "decision_id": decision_id(symbol, date, run_id),
        "date": date,
        "symbol": symbol,
        "recommendation": rec or None,
        "confidence": conf,
        "price_at_decision": price,
        "next_trade_date": next_date,
        "next_close": round(next_close, 4),
        "next_day_return": round(ret, 6),
        "hit": hit,
        "horizon_days": 1,
        "model": row.get("model"),
        "fallback": row.get("fallback"),
    }


def compute_all(rows, price_fetcher, existing_run_ids=None, since=None):
    """按 run_id 幂等计算 outcome；since（YYYY-MM-DD）只算该日（含）之后的行。"""
    done_ids = set(existing_run_ids or [])
    out = []
    for r in rows:
        if r.get("status") != "done":
            continue
        if since and str(r.get("date") or "") < str(since):
            continue
        rid = str(r.get("run_id") or "")
        if not rid or rid in done_ids:
            continue
        o = compute_outcome(r, price_fetcher)
        if o is not None:
            out.append(o)
            done_ids.add(rid)
    return out


def run_backfill(ledger_path, out_path, since=None, price_fetcher=None,
                 write=True, repair=True, week=None):
    """回填 outcome sidecar：只补缺失（run_id 幂等）+ 补历史行缺字段。

    返回 {'new','dates','since','repaired','repaired_fields','written',
    'out_path','outcomes','summary'}；write=False 时零写盘（dry-run，但仍报告
    「本可补行数」便于运维预演）。价格取不到的行不写、不伪造。
    """
    ledger_rows = _load_jsonl(ledger_path)
    out_rows = _load_jsonl(out_path)
    out_path = str(out_path)
    fetcher = price_fetcher if price_fetcher is not None else _default_price_fetcher
    existing = {str(o.get("run_id")) for o in out_rows if o.get("run_id")}
    new_rows = compute_all(ledger_rows, fetcher, existing, since=since)
    merged = list(out_rows) + new_rows
    repaired_report = {"repaired": 0, "fields": {"decision_id": 0, "model": 0}}
    if repair:
        merged, repaired_report = repair_outcomes(merged, ledger_rows)
    written = False
    if write and (new_rows or repaired_report["repaired"]):
        _write_jsonl_atomic(out_path, merged)
        written = True
    summary = summarize(merged, ledger_rows, week=week)
    dates = {}
    for row in new_rows:
        day = str(row.get("date") or "")
        dates[day] = dates.get(day, 0) + 1
    return {"new": len(new_rows), "dates": dict(sorted(dates.items())),
            "since": since, "repaired": repaired_report["repaired"],
            "repaired_fields": repaired_report["fields"],
            "written": written, "out_path": out_path,
            "outcomes": len(merged), "summary": summary}


def summarize(outcomes, ledger_rows=None, week=None):
    ledger_rows = ledger_rows or []
    done = [r for r in ledger_rows if r.get("status") == "done"]
    directional = [o for o in outcomes if o.get("hit") is not None]
    hits = sum(1 for o in directional if o["hit"] == 1)
    rets = [o["next_day_return"] for o in outcomes
            if isinstance(o.get("next_day_return"), (int, float))]
    brier_terms = []
    for o in directional:
        c = o.get("confidence")
        if isinstance(c, (int, float)):
            brier_terms.append((float(c) - o["hit"]) ** 2)
    lat = sorted(float(r["latency_s"]) for r in done
                 if isinstance(r.get("latency_s"), (int, float)))

    def _pct(arr, p):
        if not arr:
            return None
        i = min(len(arr) - 1, max(0, int(round((len(arr) - 1) * p))))
        return round(arr[i], 1)

    rec_counts = {}
    for o in outcomes:
        k = o.get("recommendation") or "UNKNOWN"
        rec_counts[k] = rec_counts.get(k, 0) + 1
    model_counts = {}
    fallback_counts = {"true": 0, "false": 0, "unknown": 0}
    for r in done:
        m = str(r.get("model") or "unknown")
        model_counts[m] = model_counts.get(m, 0) + 1
        fb = r.get("fallback")
        if fb is True:
            fallback_counts["true"] += 1
        elif fb is False:
            fallback_counts["false"] += 1
        else:
            fallback_counts["unknown"] += 1
    return {
        "week": week,
        "ledger_done": len(done),
        "outcomes": len(outcomes),
        "coverage": round(len(outcomes) / len(done), 3) if done else None,
        "parse_ok_rate": round(
            sum(1 for r in done if r.get("parse_ok")) / len(done), 3) if done else None,
        "directional_n": len(directional),
        "hit_rate": round(hits / len(directional), 3) if directional else None,
        "avg_return": round(sum(rets) / len(rets), 6) if rets else None,
        "brier": round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None,
        "watch_ratio": round(
            sum(1 for r in done if r.get("recommendation") == "WATCH") / len(done), 3)
        if done else None,
        "latency_p50": _pct(lat, 0.5),
        "latency_p95": _pct(lat, 0.95),
        "by_recommendation": rec_counts,
        "models": model_counts,
        "fallback": fallback_counts,
        "warn_only": True,
    }


def render_markdown(s):
    lines = ["## D1 决策 outcome（只读 sidecar，不阻断）", ""]
    lines.append("> 周：**%s** ｜ 账本 done=**%d** ｜ outcome=**%d** ｜ 覆盖率=**%s**" % (
        s.get("week") or "—", s.get("ledger_done") or 0, s.get("outcomes") or 0,
        "—" if s.get("coverage") is None else "%.0f%%" % (s["coverage"] * 100)))
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---:|")
    lines.append("| parse_ok 率 | %s |" % ("—" if s.get("parse_ok_rate") is None
                                          else "%.1f%%" % (s["parse_ok_rate"] * 100)))
    lines.append("| 方向命中率 | %s |" % ("—" if s.get("hit_rate") is None
                                         else "%.1f%%（n=%d）" % (s["hit_rate"] * 100,
                                                                  s.get("directional_n") or 0)))
    lines.append("| 平均次日收益 | %s |" % ("—" if s.get("avg_return") is None
                                          else "%.3f%%" % (s["avg_return"] * 100)))
    lines.append("| Brier（越低越好） | %s |" % ("—" if s.get("brier") is None
                                              else "%.4f" % s["brier"]))
    lines.append("| WATCH 占比 | %s |" % ("—" if s.get("watch_ratio") is None
                                        else "%.0f%%" % (s["watch_ratio"] * 100)))
    lines.append("| 决策时延 P50 / P95 | %s / %s s |" % (
        s.get("latency_p50") or "—", s.get("latency_p95") or "—"))
    lines.append("")
    lines.append("**处理纪律**：只读统计，不自动改决策/不阻断；命中率仅对有方向决策有意义（HOLD/WATCH 不计 hit）。")
    return "\n".join(lines)


def _default_price_fetcher(symbol, n=60):
    engine = os.path.join(HERE, "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from tools import kline_daily  # noqa: E402
    return kline_daily(symbol, n)


def _since_arg(value):
    """--since 参数校验（YYYY-MM-DD；非法即 argparse 报错退出 2）。"""
    try:
        time.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            "--since 需 YYYY-MM-DD 格式（如 2026-10-30）")
    return str(value)


def main(argv=None):
    ap = argparse.ArgumentParser(description="D1 决策 outcome 台账（只读 sidecar）")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--week", default=None)
    ap.add_argument("--since", type=_since_arg, default=None, metavar="YYYY-MM-DD",
                    help="只回填该日期（含）之后缺失的 outcome（默认补全历史缺失）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="只报告，不写 sidecar")
    # 兼容文档里的旧入口（2026-10-31 审计：文档写 --compute/--report，实际不支持）
    ap.add_argument("--compute", action="store_true", help="兼容旧入口（默认即计算）")
    ap.add_argument("--report", action="store_true", help="兼容旧入口（默认即报告）")
    args = ap.parse_args(argv)
    rep = run_backfill(args.ledger, args.out, since=args.since,
                       write=not args.no_write, week=args.week)
    summary = rep["summary"]
    if args.json:
        payload = {"new": rep["new"], "dates": rep["dates"],
                   "since": rep["since"], "repaired": rep["repaired"],
                   "repaired_fields": rep["repaired_fields"],
                   "written": rep["written"], "summary": summary}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        days = ",".join("%s:%d" % (d, n) for d, n in rep["dates"].items()) or "-"
        state = ("（--no-write 预演，未写盘）" if args.no_write
                 else ("已写 sidecar" if rep["written"] else "无新增（幂等）"))
        print("[decision_outcomes] since=%s new=%d dates=%s repaired=%d %s：%s"
              % (args.since or "-", rep["new"], days, rep["repaired"],
                 state, args.out))
        print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
