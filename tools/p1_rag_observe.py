# -*- coding: utf-8 -*-
"""p1_rag_observe.py — P1-2 观察期汇总（只读 grounding_usage.jsonl）。

输出：检索条数、rag_enabled 比例、来源数分布、空检索率、延迟 P50/P95/P99。
不写文件、不联网；供 2026-11-03 观察小结 / M2 决策。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_REL = Path("work") / "p1" / "rag" / "grounding_usage.jsonl"
INDEX_STALE_DAYS = 7
EMPTY_RATE_HIGH = 0.30
P95_HIGH_MS = 800.0
RAG_ENABLED_MIN = 0.90
SOURCE_AVG_MIN = 1.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    rag = sum(1 for r in rows if r.get("rag_enabled"))
    source_counts = [int(r.get("source_count") or 0) for r in rows]
    empty = sum(1 for c in source_counts if c == 0)
    lat = [float(r.get("elapsed_ms") or 0) for r in rows]
    return {
        "total": total,
        "rag_enabled": rag,
        "rag_enabled_rate": round(rag / total, 4) if total else 0,
        "empty_retrieval": empty,
        "empty_rate": round(empty / total, 4) if total else 0,
        "source_count_avg": round(sum(source_counts) / total, 2) if total else 0,
        "source_count_histogram": {str(c): source_counts.count(c) for c in sorted(set(source_counts))},
        "latency_ms": {
            "p50": _percentile(lat, 50),
            "p95": _percentile(lat, 95),
            "p99": _percentile(lat, 99),
            "max": round(max(lat), 1) if lat else 0,
        },
    }


def load(path: Path | None = None) -> list[dict]:
    path = Path(path) if path else (REPO_ROOT / LOG_REL)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _index_info() -> dict:
    try:
        tools = str(Path(__file__).resolve().parent)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import zhuge_rag  # noqa: E402
        return zhuge_rag.check()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exists": False, "error": str(exc)[:200]}


def evaluate(summary: dict, index_info: dict | None = None) -> dict:
    """阈值巡检：返回 alerts（high/medium/info）与建议动作。"""
    alerts = []
    total = int(summary.get("total") or 0)
    if total == 0:
        alerts.append({"code": "no_traffic", "severity": "info",
                       "message": "尚无真实咨询流量；观察期继续，暂不判定"})
    else:
        if float(summary.get("rag_enabled_rate") or 0) < RAG_ENABLED_MIN and total >= 5:
            alerts.append({"code": "rag_not_enabled", "severity": "high",
                           "value": summary.get("rag_enabled_rate"),
                           "message": "rag_enabled 比例过低：检查 ZHUGE_RAG=1 是否注入 8091"})
        if float(summary.get("empty_rate") or 0) > EMPTY_RATE_HIGH:
            alerts.append({"code": "empty_retrieval_high", "severity": "high",
                           "value": summary.get("empty_rate"),
                           "message": "空检索率过高：检查索引/同义词/查询词，必要时回退关键词"})
        if float((summary.get("latency_ms") or {}).get("p95") or 0) > P95_HIGH_MS:
            alerts.append({"code": "latency_p95_high", "severity": "medium",
                           "value": (summary.get("latency_ms") or {}).get("p95"),
                           "message": "P95 检索延迟超标：检查索引缓存/机器负载，必要时回退"})
        if float(summary.get("source_count_avg") or 0) < SOURCE_AVG_MIN and total >= 5:
            alerts.append({"code": "source_count_low", "severity": "medium",
                           "value": summary.get("source_count_avg"),
                           "message": "平均来源数过低：疑似检索质量下降"})
    if index_info is not None:
        if not index_info.get("exists"):
            alerts.append({"code": "index_missing", "severity": "medium",
                           "message": "索引不存在：运行 python tools/zhuge_rag.py --build"})
        else:
            built = str(index_info.get("built_at") or "")
            try:
                age_days = (datetime.now(timezone(timedelta(hours=8)))
                            - datetime.strptime(built[:10], "%Y-%m-%d")).days
            except (ValueError, TypeError):
                age_days = None
            if age_days is not None and age_days > INDEX_STALE_DAYS:
                alerts.append({"code": "index_stale", "severity": "medium",
                               "value": age_days,
                               "message": "索引超过 %d 天未重建：docs 变更后需 --build" % INDEX_STALE_DAYS})
    high = sum(1 for a in alerts if a["severity"] == "high")
    medium = sum(1 for a in alerts if a["severity"] == "medium")
    return {"alerts": alerts, "high": high, "medium": medium,
            "action_required": high > 0,
            "suggested_rollback": high > 0,
            "rollback": "从 portal/start_portal_api.ps1 删除 ZHUGE_RAG='1'（或设 0）→ 重启 8091" if high else ""}


def _exit_code(alerts: list[dict]) -> int:
    return 1 if any(a["severity"] == "high" for a in alerts) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1-2 RAG usage observe")
    ap.add_argument("--log", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="按阈值巡检并返回告警")
    args = ap.parse_args(argv)
    path = Path(args.log) if args.log else (REPO_ROOT / LOG_REL)
    rows = load(path)
    summary = summarize(rows)
    out = {"log": str(path), "exists": path.is_file(), **summary}
    if args.check:
        out["watch"] = evaluate(summary, _index_info())
    print(json.dumps(out, ensure_ascii=False, indent=2) if not args.json
          else json.dumps(out, ensure_ascii=False))
    return _exit_code(out.get("watch", {}).get("alerts", [])) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
