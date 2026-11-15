# -*- coding: utf-8 -*-
"""D1 写接口 SLO（从 portal_api access log 补测，2026-11-16 WO-D1-WRITE-SLO）。

为什么需要它：Q2 的 3 个写接口（`d1.decision.create` / `d1.handoff.create` /
`d1.meeting.create`）在 11-07~11-14 窗口内 POST 数为 0，采样器不做合成写
（`skipped_write`），所以只能判 NOT_MEASURED。而 2026-11-16 起 access log
行尾已带耗时（`... 200 - 10.6ms`），写接口因此第一次可以被真实测量。

判定口径（与 `tools/d1_slo_window.py` 一致，阈值只从 interface registry 读）：
- 样本数 < `--min-samples`（默认 20）→ **INSUFFICIENT_SAMPLES**（不冒充 measured）；
- 否则按 slo_class 的 `latency_ms_p95` / `error_rate_max` / `availability` 判 PASS/FAIL。

用法::

    python -X utf8 tools/d1_write_slo.py --since 2026-11-16T00:00:00+08:00 --json
    python -X utf8 tools/d1_write_slo.py --log C:\\Temp\\portal_api_access.log --min-samples 5
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import d1_slo_window as slo  # noqa: E402  复用阈值读取与分位函数

DEFAULT_LOG = Path(r"C:\Temp\portal_api_access.log")
LINE_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \S+ '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>\d{3}) \S+'
    r'(?: (?P<lat>[\d.]+)ms)?\s*$'
)


def parse_line(line: str):
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    lat = m.group("lat")
    return {
        "ts": m.group("ts"),
        "method": m.group("method"),
        "path": m.group("path"),
        "status": int(m.group("status")),
        "latency_ms": float(lat) if lat is not None else None,
    }


def collect(log_path: Path, since: dt.datetime | None, interfaces: list[dict]):
    writes = {(i["method"], i["path"]): i for i in interfaces if i["method"] != "GET"}
    buckets: dict[str, dict] = {}
    legacy = 0
    scanned = 0
    if not log_path.is_file():
        return buckets, {"scanned": 0, "legacy_no_latency": 0, "error": "log_missing"}
    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        row = parse_line(raw)
        if not row:
            continue
        scanned += 1
        if since is not None:
            try:
                ts = dt.datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=slo.TZ)
            except ValueError:
                continue
            if ts < since:
                continue
        spec = writes.get((row["method"], row["path"]))
        if not spec:
            continue
        b = buckets.setdefault(spec["interface_id"], {
            "interface_id": spec["interface_id"], "method": spec["method"],
            "path": spec["path"], "owner": spec["owner"],
            "slo_class": spec["slo_class"], "target": spec["target"],
            "latencies": [], "statuses": [],
        })
        b["statuses"].append(row["status"])
        if row["latency_ms"] is None:
            legacy += 1
        else:
            b["latencies"].append(row["latency_ms"])
    meta = {"scanned": scanned, "legacy_no_latency": legacy, "error": ""}
    return buckets, meta


def judge(bucket: dict, min_samples: int) -> dict:
    """判定口径（2026-11-16 修正）：SLO 的 error_rate 只统计 **5xx 服务端错误**；
    4xx 是调用方参数错误，单独计数（`client_errors`），不计入错误率/可用性——
    否则一次调用方写错参数就会把接口判成 FAIL，与 registry 的 0.001–0.02 阈值口径不符。
    """
    target = bucket["target"]
    lat = sorted(bucket["latencies"])
    statuses = bucket["statuses"]
    n = len(statuses)
    out = {
        "interface_id": bucket["interface_id"], "method": bucket["method"],
        "path": bucket["path"], "owner": bucket["owner"], "slo_class": bucket["slo_class"],
        "target": target, "requests": n, "latency_samples": len(lat),
    }
    if n < min_samples:
        out.update({"verdict": "INSUFFICIENT_SAMPLES", "reason":
                    "POST 样本 %d < %d（不做合成写，不冒充 measured）" % (n, min_samples)})
        return out
    server_err = [s for s in statuses if 500 <= s <= 599]
    client_err = [s for s in statuses if 400 <= s <= 499]
    ok = [s for s in statuses if 200 <= s < 400]
    err_rate = round(len(server_err) / float(n), 4)
    p95 = slo._quantile(lat, 0.95)
    p50 = slo._quantile(lat, 0.50)
    p99 = slo._quantile(lat, 0.99)
    # availability 与 error_rate 同口径：4xx 记为 client_errors（服务正常拒绝），
    # 只有 5xx / 连接失败才算「不可用」。
    availability = round((n - len(server_err)) / float(n), 4)
    fail = []
    if p95 is not None and p95 > float(target["latency_ms_p95"]):
        fail.append("p95 %.1f > %.1f" % (p95, float(target["latency_ms_p95"])))
    if err_rate > float(target["error_rate_max"]):
        fail.append("error_rate(5xx) %.4f > %.4f" % (err_rate, float(target["error_rate_max"])))
    if availability < float(target["availability"]):
        fail.append("availability %.4f < %.4f" % (availability, float(target["availability"])))
    out.update({"p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
                "error_rate": err_rate, "client_errors": len(client_err),
                "availability": availability,
                "verdict": "FAIL" if fail else "PASS", "reason": "；".join(fail)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 写接口 SLO（access log 补测）")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--since", default="2026-11-16T00:00:00+08:00")
    ap.add_argument("--window-end", default=None)
    ap.add_argument("--min-samples", type=int, default=slo.MIN_WINDOW_SAMPLES)
    ap.add_argument("--registry", default=str(slo.DEFAULT_REGISTRY))
    ap.add_argument("--out", default=None, help="写 JSON 到该路径")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    since = dt.datetime.fromisoformat(args.since)
    if since.tzinfo is None:
        since = since.replace(tzinfo=slo.TZ)
    end = dt.datetime.fromisoformat(args.window_end) if args.window_end else None
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=slo.TZ)

    interfaces = slo.resolve_interfaces(args.registry)
    buckets, meta = collect(Path(args.log), since, interfaces)
    checks = [judge(b, args.min_samples) for b in buckets.values()]
    for iid in [i["interface_id"] for i in interfaces if i["method"] != "GET"]:
        if iid not in {c["interface_id"] for c in checks}:
            spec = next(i for i in interfaces if i["interface_id"] == iid)
            checks.append({"interface_id": iid, "method": spec["method"],
                           "path": spec["path"], "owner": spec["owner"],
                           "slo_class": spec["slo_class"], "target": spec["target"],
                           "requests": 0, "latency_samples": 0,
                           "verdict": "INSUFFICIENT_SAMPLES",
                           "reason": "窗口内 0 次真实 POST"})
    payload = {
        "schema": "zerobound/d1_write_slo/v1",
        "generated_at": dt.datetime.now(slo.TZ).isoformat(timespec="seconds"),
        "window_start": since.isoformat(timespec="seconds"),
        "window_end": (end or dt.datetime.now(slo.TZ)).isoformat(timespec="seconds"),
        "log": str(args.log), "min_samples": args.min_samples,
        "scan": meta, "checks": checks,
        "paper_only": True, "not_a_trade": True,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("log=%s scanned=%d legacy_no_latency=%d since=%s"
              % (args.log, meta["scanned"], meta["legacy_no_latency"], payload["window_start"]))
        for c in checks:
            print("  %-22s %-5s req=%-4d lat_n=%-4d %-22s %s" % (
                c["interface_id"], c["method"], c["requests"], c["latency_samples"],
                c["verdict"], c.get("reason") or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
