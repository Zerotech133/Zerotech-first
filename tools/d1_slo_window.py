#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D1 P0-02 SLO 7d 窗口采样与收窗报告（只读）。

窗口：2026-11-07 00:00:00 → 2026-11-14 00:00:00 Asia/Shanghai。

设计：
- 按 INTERFACE_REGISTRY_v1.yaml 的 slo_class 给 D1 9 个 active HTTP 路径建探针；
- GET 接口直接只读 HTTP 采样；POST 写接口不做合成写，标记 skipped_write，
  收窗报告显式 not_measured（需要 access log，不伪造 write SLO）；
- 每 5 分钟由计划任务跑 --sample，按天追加原始 JSON；
- 11-14 00:00 收窗后 --report 汇总 p50/p95/p99、错误率、可用性，
  按 class 阈值判 GO/NO_GO，并把当日 raw 一起 zip 归档。

用法：
  python -X utf8 tools/d1_slo_window.py --sample
  python -X utf8 tools/d1_slo_window.py --sample --force   # 窗口外演练
  python -X utf8 tools/d1_slo_window.py --report --json
  python -X utf8 tools/d1_slo_window.py --report --archive
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else dt.timezone(dt.timedelta(hours=8))
SCHEMA_SAMPLE = "zerobound/d1_slo_sample/v1"
SCHEMA_REPORT = "zerobound/d1_slo_report/v1"
BASE_URL = "http://127.0.0.1:8091"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "deliverables" / "d1_q2" / "slo_window"
DEFAULT_REGISTRY = ROOT / "docs" / "standards" / "INTERFACE_REGISTRY_v1.yaml"
DEFAULT_WINDOW_START = dt.datetime(2026, 11, 7, 0, 0, 0, tzinfo=TZ)
DEFAULT_WINDOW_END = dt.datetime(2026, 11, 14, 0, 0, 0, tzinfo=TZ)
MIN_WINDOW_SAMPLES = 20
PROBE_TIMEOUT_SEC = 5.0

# 9 个 active HTTP 路径按 method 展开为 10 个 interface（meeting 有 GET/POST；
# decision/handoff 只有 POST）。id/class 从 interface registry 读取，不在代码里猜阈值。
ACTIVE_INTERFACES = [
    ("d1.research.get", "GET", "/api/d1/research"),
    ("d1.decision.create", "POST", "/api/d1/decision"),
    ("d1.decision.list", "GET", "/api/d1/decisions"),
    ("d1.meeting.get", "GET", "/api/d1/meeting"),
    ("d1.meeting.create", "POST", "/api/d1/meeting"),
    ("d1.handoff.create", "POST", "/api/d1/handoff"),
    ("d1.handoff.list", "GET", "/api/d1/handoffs"),
    ("d1.watch_plans.list", "GET", "/api/d1/watch-plans"),
    ("training.episode.get", "GET", "/api/d1/training/episodes"),
    ("war_room.snapshot.get", "GET", "/api/war-room/snapshot"),
]


def now_iso() -> str:
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def parse_iso(value: str) -> dt.datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def load_slo_classes(registry_path: str | Path) -> dict[str, dict[str, Any]]:
    import yaml  # PyYAML；仓库 requirements 已含

    doc = yaml.safe_load(Path(registry_path).read_text(encoding="utf-8")) or {}
    raw = doc.get("slo_classes") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        out[name] = {
            "latency_ms_p95": float(spec.get("latency_ms_p95")),
            "error_rate_max": float(spec.get("error_rate_max")),
            "availability": float(spec.get("availability")),
        }
    return out


def resolve_interfaces(registry_path: str | Path) -> list[dict[str, Any]]:
    import yaml

    doc = yaml.safe_load(Path(registry_path).read_text(encoding="utf-8")) or {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in doc.get("interfaces") or []:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
    classes = load_slo_classes(registry_path)
    interfaces: list[dict[str, Any]] = []
    for iid, method, path in ACTIVE_INTERFACES:
        meta = by_id.get(iid)
        if meta is None:
            raise ValueError(f"interface registry 缺少 {iid}")
        slo_class = str(meta.get("slo_class") or "")
        if slo_class not in classes:
            raise ValueError(f"{iid} slo_class={slo_class!r} 无阈值")
        interfaces.append({
            "interface_id": iid,
            "method": method,
            "path": path,
            "owner": str(meta.get("owner") or ""),
            "slo_class": slo_class,
            "target": classes[slo_class],
            "probe": "http_get" if method == "GET" else "skipped_write",
        })
    return interfaces


def _http_get(url: str) -> tuple[int | None, str | None]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SEC) as resp:
            resp.read(1024 * 1024)  # 消费有限响应，避免连接泄漏
            return int(resp.status), None
    except urllib.error.HTTPError as exc:
        try:
            exc.read(1024 * 1024)
        except Exception:
            pass
        return int(exc.code), None
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__ + ":" + str(exc)[:160]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def sample_once(out_dir: str | Path, *, force: bool = False,
                window_start: dt.datetime = DEFAULT_WINDOW_START,
                window_end: dt.datetime = DEFAULT_WINDOW_END,
                registry_path: str | Path = DEFAULT_REGISTRY,
                http_get=_http_get) -> dict[str, Any]:
    out = Path(out_dir)
    now = dt.datetime.now(TZ)
    if not force and not (window_start <= now < window_end):
        raise RuntimeError(
            "outside window %s..%s (now=%s); use --force only for drill"
            % (window_start.isoformat(), window_end.isoformat(), now.isoformat()))
    interfaces = resolve_interfaces(registry_path)
    samples: list[dict[str, Any]] = []
    for spec in interfaces:
        base = {
            "interface_id": spec["interface_id"],
            "method": spec["method"],
            "path": spec["path"],
            "owner": spec["owner"],
            "slo_class": spec["slo_class"],
            "ts": now.isoformat(timespec="seconds"),
        }
        if spec["probe"] != "http_get":
            samples.append(dict(base, probe="skipped_write", ok=None,
                                status=None, latency_ms=None,
                                reason="write endpoint: no synthetic write"))
            continue
        started = time.perf_counter()
        status, error = http_get(BASE_URL + spec["path"])
        latency = round((time.perf_counter() - started) * 1000.0, 1)
        ok = status is not None and 200 <= status < 300
        samples.append(dict(base, probe="http_get", ok=bool(ok),
                            status=status, latency_ms=latency, error=error))
    day = now.date().isoformat()
    path = out / f"{day}.json"
    payload: dict[str, Any] = {
        "schema": SCHEMA_SAMPLE,
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": window_end.isoformat(timespec="seconds"),
        "day": day,
        "samples": [],
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing.get("samples"), list):
                payload["samples"] = existing["samples"]
        except Exception:
            # 不覆盖坏文件；改名留证，避免静默丢样本
            bad = path.with_name(path.name + ".bad-" + now.strftime("%H%M%S"))
            path.replace(bad)
    payload["samples"].extend(samples)
    payload["updated_at"] = now_iso()
    _atomic_write_json(path, payload)
    return {"path": str(path), "sampled_at": now.isoformat(timespec="seconds"),
            "added": len(samples), "samples_total": len(payload["samples"])}


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, int(math.ceil(q * len(ordered))) - 1)
    return round(float(ordered[min(idx, len(ordered) - 1)]), 1)


def collect_samples(out_dir: str | Path, window_start: dt.datetime,
                    window_end: dt.datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(out_dir).glob("*.json")):
        if path.name.startswith(("slo_report", "verdict")):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("schema") != SCHEMA_SAMPLE:
            continue
        for row in payload.get("samples") or []:
            if not isinstance(row, dict):
                continue
            try:
                ts = parse_iso(row.get("ts") or "")
            except Exception:
                continue
            if window_start <= ts < window_end:
                rows.append(row)
    return rows


def build_report(out_dir: str | Path, *, registry_path: str | Path,
                 window_start: dt.datetime = DEFAULT_WINDOW_START,
                 window_end: dt.datetime = DEFAULT_WINDOW_END,
                 min_samples: int = MIN_WINDOW_SAMPLES) -> dict[str, Any]:
    interfaces = resolve_interfaces(registry_path)
    rows = collect_samples(out_dir, window_start, window_end)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("interface_id")), []).append(row)

    checks: list[dict[str, Any]] = []
    gaps: list[str] = []
    for spec in interfaces:
        iid = spec["interface_id"]
        base = {k: spec[k] for k in ("interface_id", "method", "path",
                                     "owner", "slo_class", "target", "probe")}
        if spec["probe"] == "skipped_write":
            checks.append(dict(base, status="not_measured", verdict="NOT_MEASURED",
                               reason="write endpoint: requires access-log measurement"))
            gaps.append(iid)
            continue
        samples = [r for r in by_id.get(iid, []) if r.get("probe") == "http_get"]
        if len(samples) < min_samples:
            checks.append(dict(base, status="not_measured", verdict="NOT_MEASURED",
                               samples=len(samples),
                               reason=f"samples={len(samples)} < {min_samples}"))
            gaps.append(iid)
            continue
        latencies = [float(r["latency_ms"]) for r in samples
                     if isinstance(r.get("latency_ms"), (int, float))]
        errors = sum(1 for r in samples
                     if not (isinstance(r.get("status"), int)
                             and 200 <= int(r["status"]) < 300))
        count = len(samples)
        observed = {
            "samples": count,
            "p50_ms": _quantile(latencies, 0.50),
            "p95_ms": _quantile(latencies, 0.95),
            "p99_ms": _quantile(latencies, 0.99),
            "error_rate": round(errors / count, 4),
            "availability": round((count - errors) / count, 4),
        }
        t = spec["target"]
        breaches = []
        if observed["p95_ms"] is not None and observed["p95_ms"] > t["latency_ms_p95"]:
            breaches.append(f"p95={observed['p95_ms']}>{t['latency_ms_p95']}")
        if observed["error_rate"] > t["error_rate_max"]:
            breaches.append(f"error_rate={observed['error_rate']}>{t['error_rate_max']}")
        if observed["availability"] < t["availability"]:
            breaches.append(f"availability={observed['availability']}<{t['availability']}")
        checks.append(dict(base, status="measured",
                           verdict="PASS" if not breaches else "FAIL",
                           observed=observed, breaches=breaches))

    measured = [c for c in checks if c["status"] == "measured"]
    failed = [c["interface_id"] for c in measured if c["verdict"] == "FAIL"]
    verdict = "GO" if not gaps and not failed else "NO_GO"
    reasons: list[str] = []
    if failed:
        reasons.append("threshold breaches: " + ", ".join(failed))
    if gaps:
        reasons.append("not measured: " + ", ".join(gaps))
    return {
        "schema": SCHEMA_REPORT,
        "generated_at": now_iso(),
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": window_end.isoformat(timespec="seconds"),
        "verdict": verdict,
        "reasons": reasons,
        "coverage": {"measured": len(measured), "total": len(checks),
                     "gaps": gaps},
        "summary": {
            "pass": sum(1 for c in measured if c["verdict"] == "PASS"),
            "fail": len(failed),
            "not_measured": len(gaps),
        },
        "checks": checks,
    }


def report_to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# D1 P0-02 SLO 7d 窗口报告",
        "",
        f"- 窗口：{report['window_start']} → {report['window_end']}",
        f"- 生成：{report['generated_at']}",
        f"- 判定：**{report['verdict']}**",
        f"- 覆盖：{report['coverage']['measured']}/{report['coverage']['total']}",
        "",
        "| interface | class | samples | p50 | p95 | p99 | error | avail | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for c in report["checks"]:
        o = c.get("observed") or {}
        lines.append(
            "| {iid} | {cls} | {n} | {p50} | {p95} | {p99} | {err} | {av} | {v} |".format(
                iid=c["interface_id"], cls=c["slo_class"],
                n=o.get("samples", "-"), p50=o.get("p50_ms", "-"),
                p95=o.get("p95_ms", "-"), p99=o.get("p99_ms", "-"),
                err=o.get("error_rate", "-"), av=o.get("availability", "-"),
                v=c["verdict"]))
    if report.get("reasons"):
        lines += ["", "## 说明", ""] + [f"- {r}" for r in report["reasons"]]
    lines += ["", "写接口（POST decision/meeting/handoff）未做合成写，需在收窗时用 access log 补测；在此之前 verdict 保守为 NO_GO。", ""]
    return "\n".join(lines)


def write_report(report: dict[str, Any], out_dir: str | Path,
                 *, archive: bool = False) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report_json = out / "2026-11-14_slo_report.json"
    report_md = out / "2026-11-14_slo_report.md"
    _atomic_write_json(report_json, report)
    report_md.write_text(report_to_markdown(report), encoding="utf-8", newline="\n")
    result = {"json": str(report_json), "md": str(report_md)}
    if archive:
        zip_path = out / "2026-11-14_slo_window_archive.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out.glob("*.json")):
                if path.name.startswith(("2026-11-14_slo_report", "verdict")):
                    continue
                zf.write(path, arcname=path.name)
            zf.write(report_json, arcname=report_json.name)
            zf.write(report_md, arcname=report_md.name)
        result["archive"] = str(zip_path)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 P0-02 SLO 7d 窗口采样/报告")
    ap.add_argument("--sample", action="store_true", help="采样一次（计划任务每 5 分钟调用）")
    ap.add_argument("--report", action="store_true", help="汇总窗口生成报告")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--window-start", default=DEFAULT_WINDOW_START.isoformat())
    ap.add_argument("--window-end", default=DEFAULT_WINDOW_END.isoformat())
    ap.add_argument("--min-samples", type=int, default=MIN_WINDOW_SAMPLES)
    ap.add_argument("--force", action="store_true", help="窗口外演练（sample/report）")
    ap.add_argument("--archive", action="store_true", help="报告同时 zip 原始日文件")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    start = parse_iso(args.window_start)
    end = parse_iso(args.window_end)
    if not (args.sample or args.report):
        ap.error("需要 --sample 或 --report")
    if args.sample:
        try:
            result = sample_once(args.out_dir, force=args.force,
                                 window_start=start, window_end=end,
                                 registry_path=args.registry)
        except Exception as exc:  # noqa: BLE001
            print(f"sample_error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False) if args.json else
              f"sample_ok path={result['path']} added={result['added']} total={result['samples_total']}")
        return 0
    now = dt.datetime.now(TZ)
    if not args.force and now < end:
        print(f"report_error: window not closed (now={now.isoformat()})", file=sys.stderr)
        return 2
    report = build_report(args.out_dir, registry_path=args.registry,
                          window_start=start, window_end=end,
                          min_samples=args.min_samples)
    paths = write_report(report, args.out_dir, archive=args.archive)
    if args.json:
        print(json.dumps({"report": report, "paths": paths}, ensure_ascii=False, indent=2))
    else:
        print(f"D1 P0-02 SLO: {report['verdict']} "
              f"coverage={report['coverage']['measured']}/{report['coverage']['total']} "
              f"json={paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
