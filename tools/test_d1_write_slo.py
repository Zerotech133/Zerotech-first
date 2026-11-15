# -*- coding: utf-8 -*-
"""写接口 SLO 补测工具测试（2026-11-16 WO-D1-WRITE-SLO）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "d1_write_slo_t", REPO / "tools" / "d1_write_slo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ws = _load()

TARGET = {"latency_ms_p95": 100.0, "error_rate_max": 0.005, "availability": 0.999}


def _bucket(statuses, latencies):
    return {"interface_id": "d1.decision.create", "method": "POST",
            "path": "/api/d1/decision", "owner": "D1", "slo_class": "write_ledger",
            "target": TARGET, "statuses": statuses, "latencies": latencies}


def test_parse_line_with_latency():
    row = ws.parse_line('2026-11-16 04:00:58 127.0.0.1 "POST /api/d1/decision HTTP/1.1" 200 - 10.6ms')
    assert row["method"] == "POST" and row["path"] == "/api/d1/decision"
    assert row["status"] == 200 and row["latency_ms"] == 10.6


def test_parse_line_without_latency_is_legacy():
    row = ws.parse_line('2026-11-16 03:33:07 127.0.0.1 "GET /health HTTP/1.1" 200 -')
    assert row is not None and row["latency_ms"] is None


def test_parse_line_garbage():
    assert ws.parse_line("not a log line") is None


def test_insufficient_samples_is_not_measured():
    out = ws.judge(_bucket([200, 200], [5.0, 6.0]), min_samples=20)
    assert out["verdict"] == "INSUFFICIENT_SAMPLES"
    assert "不冒充 measured" in out["reason"]


def test_5xx_counts_as_error_and_fails():
    out = ws.judge(_bucket([200] * 18 + [500, 500], [10.0] * 20), min_samples=2)
    assert out["error_rate"] == 0.1
    assert out["verdict"] == "FAIL"


def test_4xx_is_client_error_not_downtime():
    """4xx = 调用方写错参数，服务正常拒绝；不算错误率、不算不可用。"""
    out = ws.judge(_bucket([200] * 8 + [400, 400], [9.0] * 10), min_samples=2)
    assert out["error_rate"] == 0.0
    assert out["client_errors"] == 2
    assert out["availability"] == 1.0
    assert out["verdict"] == "PASS"


def test_p95_over_threshold_fails():
    out = ws.judge(_bucket([200] * 10, [10.0] * 9 + [500.0]), min_samples=2)
    assert out["p95_ms"] is not None and out["p95_ms"] > 100.0
    assert out["verdict"] == "FAIL"


def test_collect_only_write_paths(tmp_path):
    log = tmp_path / "access.log"
    log.write_text(
        '2026-11-16 04:00:58 127.0.0.1 "POST /api/d1/decision HTTP/1.1" 200 - 10.6ms\n'
        '2026-11-16 04:00:58 127.0.0.1 "GET /api/d1/decisions HTTP/1.1" 200 - 2.0ms\n'
        '2026-11-16 04:00:58 127.0.0.1 "POST /api/d1/handoff HTTP/1.1" 200 - 31.2ms\n',
        encoding="utf-8")
    interfaces = [
        {"interface_id": "d1.decision.create", "method": "POST", "path": "/api/d1/decision",
         "owner": "D1", "slo_class": "write_ledger", "target": TARGET, "probe": "http_get"},
        {"interface_id": "d1.decision.list", "method": "GET", "path": "/api/d1/decisions",
         "owner": "D1", "slo_class": "read_fast", "target": TARGET, "probe": "http_get"},
        {"interface_id": "d1.handoff.create", "method": "POST", "path": "/api/d1/handoff",
         "owner": "D1", "slo_class": "write_ledger", "target": TARGET, "probe": "http_get"},
    ]
    buckets, meta = ws.collect(log, None, interfaces)
    assert set(buckets) == {"d1.decision.create", "d1.handoff.create"}
    assert buckets["d1.decision.create"]["latencies"] == [10.6]
    assert meta["legacy_no_latency"] == 0
