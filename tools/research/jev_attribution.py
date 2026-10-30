# -*- coding: utf-8 -*-
"""H6 JEV 决策归因：bias / event_type / playbook / regime × 收益与胜率。

数据：
- `agent_platform/data/jev_audit/jev_audit.db::dec_audit`（判断→后验收益 ret/excess/hit）
- `zhice-db:5446 zhice_os_v2.aw_decision`（163 万条决策，含 fwd_ret_5d/playbook_id/action/regime）

输出：research/reports/H6_jev_attribution_<date>.md + .json
用法：python tools/research/jev_attribution.py [--json]
退出码：0=成功 / 2=数据源不可用。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_MAIN = Path(r"C:\项目\老田的AI助手")
AUDIT_DB = ROOT / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"
if not AUDIT_DB.exists():
    AUDIT_DB = _MAIN / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"
REPORT_DIR = ROOT / "research" / "reports"
ZHICE_URL = "postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_os_v2"


def _pct_stats(vals: list[float]) -> dict:
    n = len(vals)
    if not n:
        return {"n": 0}
    mean = sum(vals) / n
    win = sum(1 for v in vals if v > 0) / n
    return {"n": n, "mean": round(mean, 4), "win_rate": round(win, 4)}


def audit_attribution(db: Path | None = None) -> dict:
    """dec_audit：按 bias / event_type / source 汇总 ret/excess/hit。"""
    p = Path(db) if db is not None else AUDIT_DB
    if not p.exists():
        return {"ok": False, "error": "audit_db_missing"}
    con = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True, timeout=30)
    rows = con.execute(
        "SELECT bias, event_type, source, ret, excess, hit FROM dec_audit "
        "WHERE ret IS NOT NULL OR excess IS NOT NULL OR hit IS NOT NULL").fetchall()
    total = con.execute("SELECT count(*) FROM dec_audit").fetchone()[0]
    con.close()
    by_bias: dict[str, dict] = {}
    by_event: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    rets, excesses, hits = [], [], []
    for bias, ev, src, ret, exc, hit in rows:
        bias = bias or "unknown"
        ev = ev or "unknown"
        src = src or "unknown"
        for bucket, key in ((by_bias, bias), (by_event, ev), (by_source, src)):
            d = bucket.setdefault(key, {"n": 0, "rets": [], "excess": [], "hits": []})
            d["n"] += 1
            if ret is not None:
                d["rets"].append(float(ret))
            if exc is not None:
                d["excess"].append(float(exc))
            if hit is not None:
                d["hits"].append(float(hit))
        if ret is not None:
            rets.append(float(ret))
        if exc is not None:
            excesses.append(float(exc))
        if hit is not None:
            hits.append(float(hit))

    def summarize(bucket):
        out = []
        for k, v in bucket.items():
            out.append({"key": k, "n": v["n"], "ret": _pct_stats(v["rets"]),
                        "excess": _pct_stats(v["excess"]),
                        "hit_rate": round(sum(v["hits"]) / len(v["hits"]), 4) if v["hits"] else None})
        out.sort(key=lambda x: -x["n"])
        return out

    return {"ok": True, "total_rows": total, "labeled_rows": len(rows),
            "overall": {"ret": _pct_stats(rets), "excess": _pct_stats(excesses),
                        "hit_rate": round(sum(hits) / len(hits), 4) if hits else None},
            "by_bias": summarize(by_bias), "by_event_type": summarize(by_event),
            "by_source": summarize(by_source)}


def _psql(sql: str) -> list[list[str]]:
    r = subprocess.run(["docker", "exec", "zhice-db", "psql", ZHICE_URL, "-Atc", sql],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        return []
    return [ln.split("|") for ln in (r.stdout or "").strip().splitlines() if ln.strip()]


def _aw_query(sql: str) -> list[list[str]]:
    return _psql(sql)


def aw_attribution() -> dict:
    """aw_decision：按 action / playbook / regime / status 汇总 fwd_ret_5d。

    口径说明（2026-10-28 实测）：fwd_ret_5d 为**小数收益**（0.01=1%），
    中位数=0.0000、68% 样本恰为 0（未持仓/无动作），因此同时给：
    总体（含零）/ 非零样本均值 / 非零样本胜率，避免「胜率 3%」误读。
    """
    overall_sql = (
        "SELECT count(*)||'|'||count(fwd_ret_5d)||'|'||"
        "coalesce(round(avg(fwd_ret_5d)::numeric,6),0)||'|'||"
        "coalesce(round(avg(CASE WHEN fwd_ret_5d>0 THEN 1.0 ELSE 0.0 END)::numeric,4),0)||'|'||"
        "sum(CASE WHEN fwd_ret_5d=0 THEN 1 ELSE 0 END)||'|'||"
        "coalesce(round(avg(CASE WHEN fwd_ret_5d<>0 THEN fwd_ret_5d END)::numeric,6),0)||'|'||"
        "coalesce(round(avg(CASE WHEN fwd_ret_5d<>0 AND fwd_ret_5d>0 THEN 1.0 "
        "WHEN fwd_ret_5d<>0 THEN 0.0 END)::numeric,4),0) "
        "FROM aw_decision WHERE fwd_ret_5d IS NOT NULL;")
    o = _psql(overall_sql)
    overall = {}
    if o and o[0]:
        segs = "|".join(o[0]).split("|")
        try:
            overall = {"total": int(segs[0]), "labeled": int(segs[1]),
                       "mean": float(segs[2]), "win_rate_all": float(segs[3]),
                       "zero_count": int(segs[4]), "mean_nonzero": float(segs[5]),
                       "win_rate_nonzero": float(segs[6])}
        except (ValueError, IndexError):
            overall = {}

    def groups(dim: str, limit: int = 30) -> list[dict]:
        sql = (
            "SELECT * FROM (SELECT %s, count(*) n, round(avg(fwd_ret_5d)::numeric,6) m, "  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
            "round(avg(CASE WHEN fwd_ret_5d>0 THEN 1.0 ELSE 0.0 END)::numeric,4) w, "
            "round(avg(CASE WHEN fwd_ret_5d<>0 THEN fwd_ret_5d END)::numeric,6) mn, "
            "round(avg(CASE WHEN fwd_ret_5d<>0 AND fwd_ret_5d>0 THEN 1.0 "
            "WHEN fwd_ret_5d<>0 THEN 0.0 END)::numeric,4) wn "
            "FROM aw_decision WHERE fwd_ret_5d IS NOT NULL GROUP BY %s) s "
            "ORDER BY n DESC LIMIT %d;" % (dim, dim, limit))
        out = []
        for row in _psql(sql):
            parts = "|".join(row).split("|")
            if len(parts) < 6:
                continue
            try:
                out.append({"key": "|".join(parts[:-5]), "n": int(parts[-5]),
                            "mean": float(parts[-4]), "win_rate_all": float(parts[-3]),
                            "mean_nonzero": float(parts[-2]), "win_rate_nonzero": float(parts[-1])})
            except ValueError:
                continue
        return out

    return {"ok": True, "overall": overall,
            "by_action": groups("action"),
            "by_playbook": groups("coalesce(playbook_id,'-')", 20),
            "by_regime": groups("coalesce(regime_risk_off::text,'-')", 5),
            "by_status": groups("coalesce(status,'-')", 8)}


def market_fallback(since: str = "") -> dict:
    """无 companies 行的市场方向兜底归因。

    口径：companies 为空的历史行无法计算个股收益，但可用沪深300 当日涨跌判断
    「方向对错」：bull→bench>0、bear→bench<0、neutral→|bench|<=band（默认 1.0）。
    这不是个股 alpha，只是**判断方向的市场级替代指标**，用于在 82% 空公司行上
    找回可评估信息。
    """
    import pyarrow.parquet as pq
    import glob
    data = ROOT / "quant_backtest" / "data"
    if not (data / "tushare").exists():
        data = Path(r"C:\项目\老田的AI助手\quant_backtest\data")
    bench = {}
    pats = [str(data / "tushare_curated" / "index_daily.parquet"),
            str(data / "tushare" / "index_daily" / "*.parquet")]
    for pat in pats:
        files = sorted(glob.glob(pat))
        if not files:
            continue
        try:
            tb = pq.read_table(files[-1], columns=["ts_code", "trade_date", "pct_chg"])
        except Exception:  # noqa: BLE001
            continue
        for c, d, p in zip(tb.column("ts_code").to_pylist(), tb.column("trade_date").to_pylist(),
                           tb.column("pct_chg").to_pylist()):
            if str(c) in ("000300.SH", "399300.SZ") and p is not None:
                bench[str(d)] = float(p)
        if bench:
            break
    if not bench:
        return {"ok": False, "error": "bench_missing"}
    band = 1.0
    con = sqlite3.connect("file:%s?mode=ro" % str(AUDIT_DB).replace("\\", "/"), uri=True, timeout=30)
    sql = ("SELECT bias, trade_day FROM dec_audit "
           "WHERE (companies IS NULL OR companies IN ('','[]'))")
    params = ()
    if since:
        sql += " AND ts>=?"
        params = (since,)
    rows = con.execute(sql, params).fetchall()
    con.close()
    total = correct = scored = 0
    by_bias = {}
    for bias, td in rows:
        total += 1
        if not td or td not in bench:
            continue
        b = bench[td]
        if bias == "bull":
            hit = b > 0
        elif bias == "bear":
            hit = b < 0
        elif bias == "neutral":
            hit = abs(b) <= band
        else:
            continue
        scored += 1
        correct += 1 if hit else 0
        d = by_bias.setdefault(bias, {"n": 0, "hit": 0})
        d["n"] += 1
        d["hit"] += 1 if hit else 0
    return {"ok": True, "bench_days": len(bench), "rows_total": total, "rows_scored": scored,
            "coverage_pct": round(scored * 100.0 / total, 2) if total else 0,
            "direction_accuracy": round(correct / scored, 4) if scored else None,
            "by_bias": {k: {"n": v["n"], "hit_rate": round(v["hit"] / v["n"], 4)}
                        for k, v in by_bias.items()}}


def render_md(audit: dict, aw: dict) -> str:
    lines = ["# H6 JEV 决策归因（第一版）", "",
             "- 生成：%s" % datetime.now().astimezone().isoformat(timespec="seconds"),
             "- 数据源：jev_audit.dec_audit（判断→后验收益）+ zhice aw_decision（决策→5 日收益）", ""]
    if audit.get("ok"):
        o = audit["overall"]
        lines += ["## dec_audit 总览（%d/%d 行有后验）" % (audit["labeled_rows"], audit["total_rows"]), "",
                  "- ret 均值 %s / 胜率 %s；excess 均值 %s；hit 率 %s" % (
                      o["ret"].get("mean"), o["ret"].get("win_rate"),
                      o["excess"].get("mean"), o["hit_rate"]), "",
                  "### 按 bias", "",
                  "| bias | n | ret均值 | 胜率 | excess均值 | hit率 |", "|---|---|---|---|---|---|"]
        for r in audit["by_bias"]:
            lines.append("| %s | %d | %s | %s | %s | %s |" % (
                r["key"], r["n"], r["ret"].get("mean"), r["ret"].get("win_rate"),
                r["excess"].get("mean"), r["hit_rate"]))
        lines += ["", "### 按 event_type（前 10）", "",
                  "| event_type | n | ret均值 | 胜率 | hit率 |", "|---|---|---|---|---|"]
        for r in audit["by_event_type"][:10]:
            lines.append("| %s | %d | %s | %s | %s |" % (
                r["key"], r["n"], r["ret"].get("mean"), r["ret"].get("win_rate"), r["hit_rate"]))
        lines += ["", "### 按 source", "", "| source | n | ret均值 | 胜率 |", "|---|---|---|---|"]
        for r in audit["by_source"][:8]:
            lines.append("| %s | %d | %s | %s |" % (
                r["key"], r["n"], r["ret"].get("mean"), r["ret"].get("win_rate")))
    else:
        lines.append("⚠ dec_audit 不可用：%s" % audit.get("error"))
    lines += ["", "## aw_decision 归因（fwd_ret_5d）", ""]
    if aw.get("ok") and aw.get("overall"):
        o = aw["overall"]
        labeled = o.get("labeled") or 0
        zero = o.get("zero_count") or 0
        lines += [
            "- 总决策 %s 条；有 5 日收益 %s 条（其中恰为 0 的 %s 条，占 %.1f%%）" % (
                o.get("total"), labeled, zero, (zero / labeled * 100 if labeled else 0)),
            "- 含零均值 %s；**非零样本均值 %s / 胜率 %s**" % (
                o.get("mean"), o.get("mean_nonzero"), o.get("win_rate_nonzero")),
            "- 口径：fwd_ret_5d 为小数收益（0.01=1%）；零值多为未持仓/无动作，胜率须看非零样本。",
            ""]
    for title, key in (("按 action", "by_action"), ("按 playbook", "by_playbook"),
                       ("按 regime_risk_off", "by_regime"), ("按 status", "by_status")):
        rows = aw.get(key) or []
        if not rows:
            continue
        lines += ["### " + title, "",
                  "| 组 | n | 均值(含零) | 非零均值 | 非零胜率 |", "|---|---|---|---|---|"]
        for g in rows[:10]:
            lines.append("| %s | %d | %s | %s | %s |" % (
                g["key"], g["n"], g["mean"], g["mean_nonzero"], g["win_rate_nonzero"]))
        lines.append("")
    mf = aw.get("_market_fallback") if isinstance(aw, dict) else None
    if mf and mf.get("ok"):
        lines += ["", "## 无 companies 行的市场方向兜底（沪深300）", "",
                  "- 空公司行 %d 行，其中可评分 %d 行（%.1f%%）；" % (
                      mf["rows_total"], mf["rows_scored"], mf["coverage_pct"]),
                  "- **方向准确率 = %.1f%%**（市场级替代指标，非个股 alpha）" % (
                      (mf.get("direction_accuracy") or 0) * 100), ""]
        for k, v in mf["by_bias"].items():
            lines.append("- %s：n=%d 命中率=%s" % (k, v["n"], v["hit_rate"]))
    lines += ["", "## 局限（第一版）",
              "1. dec_audit 的后验收益口径（ret/excess/hit）由 JEV 回填流程决定，覆盖不全；",
              "2. aw_decision 的 fwd_ret_5d 未扣成本，且样本以产线决策为主（选择偏差）；",
              "3. 未区分市场/板块/持有期；分组样本量差异大。",
              "",
              "> 结论口径：本版用于定位「哪些组值得深挖」，不直接作为交易结论。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H6 JEV 决策归因")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    audit = audit_attribution()
    aw = aw_attribution()
    try:
        mf = market_fallback()
    except Exception as e:  # noqa: BLE001
        mf = {"ok": False, "error": str(e)[:80]}
    if isinstance(aw, dict):
        aw["_market_fallback"] = mf
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md = REPORT_DIR / ("H6_jev_attribution_%s.md" % day)
    js = REPORT_DIR / ("H6_jev_attribution_%s.json" % day)
    md.write_text(render_md(audit, aw), encoding="utf-8")
    js.write_text(json.dumps({"audit": audit, "aw_decision": aw}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    if args.json:
        print(json.dumps({"audit_ok": audit.get("ok"), "audit_labeled": audit.get("labeled_rows"),
                          "aw_ok": aw.get("ok"), "aw_overall": aw.get("overall")},
                         ensure_ascii=False, indent=1))
    else:
        print(render_md(audit, aw))
    print("报告：%s" % md)
    return 0 if (audit.get("ok") or aw.get("ok")) else 2


if __name__ == "__main__":
    sys.exit(main())
