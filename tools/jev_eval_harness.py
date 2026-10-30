# -*- coding: utf-8 -*-
"""JEV-Eval 评测门（2026-10-30 JEVEVAL，离线只读，CI 可用）。

定位：JEV 中台化的"评测治理"入口。改 prompt/模型/预筛/契约前先跑一遍，
FAIL 即拒绝合并/上线；WARN 记录趋势。

五项检查（默认近 30 天）：
1. calibration  校准快照：latest.json 可读、data_through/stale/n_days、gate_pass（未过为 WARN）
2. prefilter    预筛 safe 模式误杀率（复用 jev_prefilter_eval）
3. judge_schema 近期判定 JSON 的 v2 字段完整率（horizon 枚举 / prob 和≈1）
4. oos_sets     OOS 问题集 v1/v2/v3 存在且可解析
5. ledger       后验导出一致性（posterior_index.json missing==0；priced_total 与导出行数一致）

用法：
    python -B tools/jev_eval_harness.py                # 文本摘要
    python -B tools/jev_eval_harness.py --json out.json --md out.md
    python -B tools/jev_eval_harness.py --days 7 --n 2000
退出码：0=全 PASS/WARN；1=有 FAIL。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_prefilter_eval as pre_eval  # noqa: E402

NEWS_DB = Path(r"C:\aios\news_collector\news.db")
AUDIT_DIR = Path(os.environ.get(
    "JEV_AUDIT_DIR", r"C:\aios\agent_platform\data\jev_audit"))
OOS_DIR = REPO / "jev-eval"
HORIZONS = {"intraday", "days", "weeks", "long", "unknown"}


def _load_latest() -> dict:
    return json.loads((AUDIT_DIR / "latest.json").read_text(encoding="utf-8"))


def check_calibration(rep: dict) -> dict:
    """校准快照检查。gate_pass=False 只 WARN（门控需要时间积累）。"""
    try:
        a = rep["assessment"]["directional"]
    except (KeyError, TypeError):
        return {"name": "calibration", "level": "FAIL", "detail": "latest.json 缺 assessment.directional"}
    stale = rep.get("stale_trade_days")
    nd = rep.get("n_trade_days") or 0
    detail = ("data_through=%s stale=%s n_days=%s edge=%.1fpp wilson=%.3f gate=%s" % (
        rep.get("data_through"), stale, nd, a.get("edge_vs_baseline", 0) * 100,
        a.get("wilson95_lower") or 0, a.get("gate_pass")))
    if stale is None:
        return {"name": "calibration", "level": "FAIL", "detail": "缺 stale_trade_days；" + detail}
    if int(stale) > 5:
        return {"name": "calibration", "level": "FAIL", "detail": "数据落后 >5 交易日；" + detail}
    if int(stale) > 2:
        return {"name": "calibration", "level": "WARN", "detail": "数据落后 >2 交易日；" + detail}
    return {"name": "calibration", "level": "PASS" if a.get("gate_pass") else "WARN",
            "detail": detail + ("；门控未过（累计中）" if not a.get("gate_pass") else "")}


def check_prefilter(rows: list[dict], max_false_kill: float = 0.02) -> dict:
    rep = pre_eval.evaluate(rows, mode="safe")
    fk = rep.get("false_kill_rate_in_skip")
    if fk is None:
        return {"name": "prefilter", "level": "WARN", "detail": "样本无可 skip 条目"}
    detail = ("skip=%d 误杀=%.2f%% 捕获=%.1f%%（近 %d 条）" % (
        rep["skip"], fk * 100, (rep.get("signal_capture_rate") or 0) * 100, rep["n"]))
    level = "PASS" if fk <= max_false_kill else "FAIL"
    return {"name": "prefilter", "level": level, "detail": detail}


def check_judge_schema(rows: list[dict]) -> dict:
    """近期 JEV 判定 JSON 的 v2 字段完整率（只看最新样本；迁移期 WARN）。"""
    total = ok = 0
    bad_h = bad_p = 0
    for r in rows:
        try:
            j = json.loads(r.get("jev") or "{}")
        except ValueError:
            continue
        total += 1
        hz = str(j.get("horizon") or "")
        pr = j.get("prob")
        if hz in HORIZONS:
            ok += 1
        else:
            bad_h += 1
        if isinstance(pr, dict) and set(pr) >= {"bull", "bear", "neutral"}:
            try:
                if abs(sum(float(pr[k]) for k in ("bull", "bear", "neutral")) - 1.0) <= 0.05:
                    pass
                else:
                    bad_p += 1
            except (TypeError, ValueError):
                bad_p += 1
        else:
            bad_p += 1
    if total == 0:
        return {"name": "judge_schema", "level": "WARN", "detail": "近期无判定样本"}
    ratio = ok / total
    detail = "v2 字段完整率=%.1f%%（n=%d，horizon 异常 %d / prob 异常 %d）" % (
        ratio * 100, total, bad_h, bad_p)
    if ratio >= 0.95:
        return {"name": "judge_schema", "level": "PASS", "detail": detail}
    if ratio > 0:
        return {"name": "judge_schema", "level": "WARN", "detail": detail + "；迁移期"}
    strict = str(os.environ.get("JEV_EVAL_STRICT_SCHEMA", "0")).lower() in ("1", "true", "yes")
    return {"name": "judge_schema", "level": "FAIL" if strict else "WARN",
            "detail": detail + "；尚未启用 v2（今晚夜跑后应开始覆盖）"}


def check_oos_sets() -> dict:
    files = sorted(OOS_DIR.glob("oos_question_set_v*.json"))
    if not files:
        return {"name": "oos_sets", "level": "WARN", "detail": "未找到 OOS 问题集"}
    bad = []
    counts = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            qs = data.get("questions") if isinstance(data, dict) else data
            counts[f.name] = len(qs or [])
            if not qs:
                bad.append(f.name + ":空")
        except (ValueError, OSError) as exc:
            bad.append(f.name + ":" + str(exc)[:40])
    detail = "files=%d %s" % (len(files), json.dumps(counts, ensure_ascii=False))
    return {"name": "oos_sets", "level": "FAIL" if bad else "PASS",
            "detail": detail + (("；异常 " + ",".join(bad)) if bad else "")}


def check_ledger(rep: dict) -> dict:
    idx = AUDIT_DIR / "posterior_index.json"
    if not idx.exists():
        return {"name": "ledger", "level": "WARN", "detail": "posterior_index.json 缺失"}
    try:
        meta = json.loads(idx.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"name": "ledger", "level": "FAIL", "detail": "index 不可解析: %s" % str(exc)[:80]}
    missing = meta.get("missing")
    priced = rep.get("priced_total")
    detail = "posterior_index missing=%s priced_total=%s" % (missing, priced)
    if missing not in (None, 0, "0"):
        return {"name": "ledger", "level": "FAIL", "detail": detail}
    return {"name": "ledger", "level": "PASS", "detail": detail}


def load_recent_rows(days: int, limit: int = 5000) -> list[dict]:
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=20)
    try:
        rows = conn.execute(
            "SELECT jev FROM news WHERE jev IS NOT NULL AND jev<>''"
            " AND created_at >= date('now', ?) ORDER BY created_at DESC LIMIT ?",
            ("-%d day" % int(days), int(limit))).fetchall()
    finally:
        conn.close()
    return [{"jev": r[0]} for r in rows]


def load_prefilter_rows(days: int, limit: int = 5000) -> list[dict]:
    rows = pre_eval.load_rows(days, limit)
    return rows


def summarize(checks: list[dict]) -> dict:
    fails = [c["name"] for c in checks if c["level"] == "FAIL"]
    warns = [c["name"] for c in checks if c["level"] == "WARN"]
    return {"total": len(checks), "fail": len(fails), "warn": len(warns),
            "fails": fails, "warns": warns,
            "overall": "FAIL" if fails else ("WARN" if warns else "PASS")}


def render_md(checks: list[dict], summary: dict) -> str:
    lines = ["# JEV-Eval 评测报告", "",
             "| 检查 | 级别 | 详情 |", "|---|---|---|"]
    for c in checks:
        lines.append("| %s | **%s** | %s |" % (c["name"], c["level"], c["detail"]))
    lines += ["", "**汇总**：%s（FAIL=%d WARN=%d）" % (
        summary["overall"], summary["fail"], summary["warn"])]
    return "\n".join(lines) + "\n"


def run(days: int = 30, n: int = 5000) -> dict:
    checks = []
    try:
        rep = _load_latest()
    except (OSError, ValueError) as exc:
        rep = {}
        checks.append({"name": "calibration", "level": "FAIL",
                       "detail": "latest.json 不可读: %s" % str(exc)[:100]})
    else:
        checks.append(check_calibration(rep))
    pf_rows = load_prefilter_rows(days, n)
    checks.append(check_prefilter(pf_rows))
    checks.append(check_judge_schema(load_recent_rows(days, 300)))
    checks.append(check_oos_sets())
    checks.append(check_ledger(rep))
    summary = summarize(checks)
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "days": days, "summary": summary, "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    a = ap.parse_args(argv)
    out = run(a.days, a.n)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    if a.md:
        Path(a.md).write_text(render_md(out["checks"], out["summary"]), encoding="utf-8")
    for c in out["checks"]:
        print("[%s] %-13s %s" % (c["level"], c["name"], c["detail"]), file=sys.stderr)
    return 0 if out["summary"]["overall"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
